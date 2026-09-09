from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localtuya_lab.tuya35 import VirtualTuya35Device


def load_file(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_gateway():
    repo = os.environ.get("LOCALTUYA_ROOT")
    if not repo:
        raise unittest.SkipTest("LOCALTUYA_ROOT is not set")
    root = Path(repo) / "custom_components/localtuya"
    package_name = "lab_target_gateway_stress"
    package = types.ModuleType(package_name)
    package.__path__ = [str(root)]
    sys.modules[package_name] = package
    pytuya = load_file(f"{package_name}.pytuya", root / "pytuya/__init__.py")
    load_file(f"{package_name}.const", root / "const.py")
    gateway = load_file(f"{package_name}.gateway_transport", root / "gateway_transport.py")
    return pytuya, gateway


class Listener:
    def __init__(self):
        self.updates = []
        self.disconnects = 0

    def status_updated(self, status):
        self.updates.append(dict(status))

    def disconnected(self):
        self.disconnects += 1


class GatewayStressTests(unittest.IsolatedAsyncioTestCase):
    async def test_five_children_concurrent_commands_remain_cid_isolated(self):
        _pytuya, gateway = load_gateway()
        key = "0123456789abcdef"
        initial = {f"cid-{index}": {"1": True, "7": index} for index in range(1, 6)}
        async with VirtualTuya35Device(
            key,
            children=initial,
            port=6668,
            response_chunks=11,
            inter_chunk_delay=0.0005,
        ) as device:
            pool = gateway.GatewayTransportPool()
            children = []
            for index in range(1, 6):
                children.append(
                    await pool.acquire(
                        host="127.0.0.1",
                        gateway_id="stress-gateway",
                        local_key=key,
                        protocol_version=3.5,
                        enable_debug=False,
                        device_id=f"stress-child-{index}",
                        cid=f"cid-{index}",
                        listener=Listener(),
                    )
                )
            try:
                for round_no in range(1, 6):
                    await asyncio.gather(*(
                        child.set_dp(index * 100 + round_no, 7)
                        for index, child in enumerate(children, start=1)
                    ))

                statuses = await asyncio.gather(*(child.status() for child in children))
                self.assertEqual(
                    [status["7"] for status in statuses],
                    [105, 205, 305, 405, 505],
                )
                self.assertEqual(
                    [device.children[f"cid-{i}"]["7"] for i in range(1, 6)],
                    [105, 205, 305, 405, 505],
                )
                self.assertEqual(device.transcript.connections, 1)
                self.assertEqual(device.transcript.handshakes, 1)
                self.assertEqual(device.transcript.controls, 25)
                self.assertTrue(all(cid in device.transcript.seen_cids for cid in initial))

                # A gateway heartbeat races with a child query but must use the
                # same serialization lock and must not leak a child CID.
                heartbeat_result, child_status = await asyncio.gather(
                    children[0].heartbeat(),
                    children[4].status(),
                )
                self.assertIsNone(heartbeat_result)
                self.assertEqual(child_status["7"], 505)
                self.assertEqual(device.transcript.connections, 1)
            finally:
                for child in children:
                    await child.close()
                await pool.close()


if __name__ == "__main__":
    unittest.main()
