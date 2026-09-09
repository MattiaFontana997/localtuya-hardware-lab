from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localtuya_lab.tuya33_device22 import CONTROL_NEW, VirtualTuya33Device22


def _load_target_pytuya():
    root = os.environ.get("LOCALTUYA_ROOT")
    if not root:
        raise unittest.SkipTest("LOCALTUYA_ROOT is not set")
    path = Path(root) / "custom_components/localtuya/pytuya/__init__.py"
    spec = importlib.util.spec_from_file_location("localtuya_device22_pytuya", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load LocalTuya pytuya")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Device22TargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_pytuya_switches_to_device22_after_data_unvalid(self):
        pytuya = _load_target_pytuya()
        local_key = "0123456789abcdef"
        async with VirtualTuya33Device22(
            local_key,
            dps={"1": True, "2": 22, "101": "device22"},
            response_chunks=5,
            inter_chunk_delay=0.001,
        ) as device:
            protocol = await pytuya.connect(
                device.host,
                "virtual-device22",
                local_key,
                "3.3",
                False,
                port=device.port,
                timeout=2,
            )
            protocol.add_dps_to_request([1, 2, 101])
            try:
                status = await protocol.status()
                self.assertEqual(status["2"], 22)
                self.assertEqual(status["101"], "device22")
                self.assertEqual(protocol.dev_type, "type_0d")
                self.assertEqual(device.device22_rejections, 1)
                self.assertGreaterEqual(device.device22_queries, 1)
                self.assertIn(CONTROL_NEW, device.transcript.seen_commands)
            finally:
                await protocol.close()


if __name__ == "__main__":
    unittest.main()
