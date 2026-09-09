from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localtuya_lab.tuya33 import VirtualTuya33Device


def _load_target_pytuya():
    root = os.environ.get("LOCALTUYA_ROOT")
    if not root:
        raise unittest.SkipTest("LOCALTUYA_ROOT is not set")
    path = Path(root) / "custom_components/localtuya/pytuya/__init__.py"
    if not path.is_file():
        raise AssertionError(f"LocalTuya pytuya not found: {path}")
    spec = importlib.util.spec_from_file_location("localtuya_target_pytuya", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load target pytuya module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class LocalTuyaTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_pytuya_33_status_control_and_fragmented_response(self):
        pytuya = _load_target_pytuya()
        local_key = "0123456789abcdef"
        async with VirtualTuya33Device(
            local_key,
            dps={"1": True, "2": 42},
            response_chunks=4,
            inter_chunk_delay=0.001,
        ) as device:
            protocol = await pytuya.connect(
                device.host,
                "virtual-device-0001",
                local_key,
                "3.3",
                False,
                port=device.port,
                timeout=2,
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
                self.assertGreaterEqual(device.transcript.status_queries, 2)
                self.assertEqual(device.transcript.controls, 1)
            finally:
                await protocol.close()
