from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localtuya_lab.tuya32 import CONTROL_NEW, VirtualTuya32Device


def load_pytuya():
    root = os.environ.get("LOCALTUYA_ROOT")
    if not root:
        raise unittest.SkipTest("LOCALTUYA_ROOT is not set")
    path = Path(root) / "custom_components/localtuya/pytuya/__init__.py"
    spec = importlib.util.spec_from_file_location("localtuya_32_pytuya", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load LocalTuya pytuya")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Tuya32TargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_pytuya_32_query_and_control(self):
        pytuya = load_pytuya()
        key = "0123456789abcdef"
        async with VirtualTuya32Device(key, dps={"1": True, "2": 32}, response_chunks=5) as device:
            protocol = await pytuya.connect(
                device.host, "virtual-32", key, "3.2", False,
                port=device.port, timeout=2,
            )
            protocol.add_dps_to_request([1, 2])
            try:
                status = await protocol.status()
                self.assertEqual(status["2"], 32)
                self.assertEqual(protocol.dev_type, "type_0d")
                self.assertIn(CONTROL_NEW, device.transcript.seen_commands)
                await protocol.set_dp(False, 1)
                self.assertIs(device.dps["1"], False)
            finally:
                await protocol.close()


if __name__ == "__main__":
    unittest.main()
