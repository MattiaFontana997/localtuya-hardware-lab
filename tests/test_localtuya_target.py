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

from localtuya_lab.tuya31 import VirtualTuya31Device
from localtuya_lab.tuya33 import VirtualTuya33Device
from localtuya_lab.tuya34 import VirtualTuya34Device
from localtuya_lab.tuya35 import VirtualTuya35Device


def _target_root() -> Path:
    root = os.environ.get("LOCALTUYA_ROOT")
    if not root:
        raise unittest.SkipTest("LOCALTUYA_ROOT is not set")
    result = Path(root)
    if not result.is_dir():
        raise AssertionError(f"LocalTuya root not found: {result}")
    return result


def _load_file(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_target_pytuya():
    path = _target_root() / "custom_components/localtuya/pytuya/__init__.py"
    if not path.is_file():
        raise AssertionError(f"LocalTuya pytuya not found: {path}")
    return _load_file("localtuya_target_pytuya", path)


def _load_target_gateway_transport():
    root = _target_root() / "custom_components/localtuya"
    custom = sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
    custom.__path__ = [str(root.parent)]
    package = types.ModuleType("custom_components.localtuya")
    package.__path__ = [str(root)]
    sys.modules["custom_components.localtuya"] = package
    pytuya = _load_file("custom_components.localtuya.pytuya", root / "pytuya/__init__.py")
    _load_file("custom_components.localtuya.const", root / "const.py")
    gateway = _load_file("custom_components.localtuya.gateway_transport", root / "gateway_transport.py")
    return pytuya, gateway


class RecordingListener:
    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.disconnects = 0

    def status_updated(self, status):
        self.updates.append(dict(status))

    def disconnected(self):
        self.disconnects += 1


class LocalTuyaTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_pytuya_31_status_control(self):
        pytuya = _load_target_pytuya()
        local_key = "0123456789abcdef"
        async with VirtualTuya31Device(
            local_key,
            dps={"1": True, "2": 31},
            response_chunks=3,
        ) as device:
            protocol = await pytuya.connect(
                device.host, "virtual-31", local_key, "3.1", False,
                port=device.port, timeout=2,
            )
            try:
                status = await protocol.status()
                self.assertEqual(status, {"1": True, "2": 31})
                await protocol.set_dp(False, 1)
                self.assertIs(device.dps["1"], False)
                self.assertIs((await protocol.status())["1"], False)
            finally:
                await protocol.close()

    async def test_real_pytuya_33_status_control_and_fragmented_response(self):
        pytuya = _load_target_pytuya()
        local_key = "0123456789abcdef"
        async with VirtualTuya33Device(
            local_key,
            dps={"1": True, "2": 42},
            response_chunks=7,
            inter_chunk_delay=0.001,
        ) as device:
            protocol = await pytuya.connect(
                device.host, "virtual-device-0001", local_key, "3.3", False,
                port=device.port, timeout=2,
            )
            try:
                status = await protocol.status()
                self.assertIs(status["1"], True)
                self.assertEqual(status["2"], 42)
                await protocol.set_dp(False, 1)
                status = await protocol.status()
                self.assertIs(status["1"], False)
                self.assertEqual(device.dps["1"], False)
                self.assertEqual(device.transcript.connections, 1)
            finally:
                await protocol.close()

    async def test_real_pytuya_34_handshake_status_control(self):
        pytuya = _load_target_pytuya()
        local_key = "0123456789abcdef"
        async with VirtualTuya34Device(
            local_key,
            dps={"1": True, "2": 34},
            response_chunks=5,
            inter_chunk_delay=0.001,
        ) as device:
            protocol = await pytuya.connect(
                device.host, "virtual-34", local_key, "3.4", False,
                port=device.port, timeout=2,
            )
            try:
                status = await protocol.status()
                self.assertEqual(status, {"1": True, "2": 34})
                self.assertEqual(device.transcript.handshakes, 1)
                await protocol.set_dp(False, 1)
                self.assertIs((await protocol.status())["1"], False)
                self.assertEqual(device.transcript.handshakes, 1)
            finally:
                await protocol.close()

    async def test_real_pytuya_35_handshake_fallback_control_and_auth_failure(self):
        pytuya = _load_target_pytuya()
        local_key = "0123456789abcdef"
        async with VirtualTuya35Device(
            local_key,
            dps={"1": True, "2": 35},
            response_chunks=9,
            inter_chunk_delay=0.001,
            require_explicit_dps_query=True,
        ) as device:
            protocol = await pytuya.connect(
                device.host, "virtual-35", local_key, "3.5", False,
                port=device.port, timeout=2,
            )
            try:
                status = await protocol.status()
                self.assertEqual(status, {"1": True, "2": 35})
                self.assertEqual(device.transcript.handshakes, 1)
                self.assertEqual(device.transcript.fallback_rejections, 1)
                await protocol.set_dp(False, 1)
                self.assertIs((await protocol.status())["1"], False)

                device.corrupt_next_response = True
                with self.assertRaises(Exception):
                    await protocol.status()
                self.assertIs((await protocol.status())["1"], False)
            finally:
                await protocol.close()

    async def test_real_gateway_transport_five_children_one_socket_unsolicited_and_reconnect(self):
        _pytuya, gateway = _load_target_gateway_transport()
        local_key = "0123456789abcdef"
        child_state = {
            f"cid-{i}": {"1": True, "20": i}
            for i in range(1, 6)
        }
        async with VirtualTuya35Device(
            local_key,
            children=child_state,
            port=6668,
            response_chunks=6,
            inter_chunk_delay=0.001,
        ) as device:
            pool = gateway.GatewayTransportPool()
            listeners = [RecordingListener() for _ in range(5)]
            children = []
            for index in range(1, 6):
                child = await pool.acquire(
                    host="127.0.0.1",
                    gateway_id="virtual-gateway",
                    local_key=local_key,
                    protocol_version=3.5,
                    enable_debug=False,
                    device_id=f"virtual-child-{index}",
                    cid=f"cid-{index}",
                    listener=listeners[index - 1],
                )
                children.append(child)

            try:
                statuses = [await child.status() for child in children]
                self.assertEqual([s["20"] for s in statuses], [1, 2, 3, 4, 5])
                self.assertEqual(device.transcript.connections, 1)
                self.assertEqual(device.transcript.handshakes, 1)

                await children[2].set_dp(False, 1)
                self.assertIs(device.children["cid-3"]["1"], False)
                self.assertIs(device.children["cid-2"]["1"], True)

                await device.send_unsolicited("cid-4", {"1": False, "20": 404})
                await asyncio.sleep(0.05)
                self.assertTrue(listeners[3].updates)
                self.assertEqual(listeners[3].updates[-1]["20"], 404)
                self.assertFalse(any(
                    update.get("20") == 404
                    for listener in listeners[:3] + listeners[4:]
                    for update in listener.updates
                ))

                await children[0].close()
                self.assertEqual((await children[1].status())["20"], 2)
                self.assertEqual(device.transcript.connections, 1)

                await device.drop_connections()
                for _ in range(50):
                    if listeners[1].disconnects:
                        break
                    await asyncio.sleep(0.01)
                self.assertGreaterEqual(listeners[1].disconnects, 1)

                replacement_listener = RecordingListener()
                replacement = await pool.acquire(
                    host="127.0.0.1",
                    gateway_id="virtual-gateway",
                    local_key=local_key,
                    protocol_version=3.5,
                    enable_debug=False,
                    device_id="virtual-child-2b",
                    cid="cid-2",
                    listener=replacement_listener,
                )
                self.assertEqual((await replacement.status())["20"], 2)
                self.assertEqual(device.transcript.connections, 2)
                self.assertEqual(device.transcript.handshakes, 2)
                await replacement.close()
            finally:
                for child in children[1:]:
                    await child.close()
                await pool.close()


if __name__ == "__main__":
    unittest.main()
