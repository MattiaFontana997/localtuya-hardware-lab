from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localtuya_lab.tuya32 import VirtualTuya32Device


def load_file(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_probe():
    repo = os.environ.get("LOCALTUYA_ROOT")
    if not repo:
        raise unittest.SkipTest("LOCALTUYA_ROOT is not set")
    root = Path(repo) / "custom_components/localtuya"

    ha = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
    ha.__path__ = []
    haconst = types.ModuleType("homeassistant.const")
    haconst.CONF_DEVICE_ID = "device_id"
    haconst.CONF_HOST = "host"
    sys.modules["homeassistant.const"] = haconst
    hacore = types.ModuleType("homeassistant.core")
    hacore.HomeAssistant = type("HomeAssistant", (), {})
    sys.modules["homeassistant.core"] = hacore

    package_name = "lab_target_32_preflight"
    package = types.ModuleType(package_name)
    package.__path__ = [str(root)]
    sys.modules[package_name] = package
    pytuya = load_file(f"{package_name}.pytuya", root / "pytuya/__init__.py")
    load_file(f"{package_name}.const", root / "const.py")
    load_file(f"{package_name}.device_health", root / "device_health.py")
    common = types.ModuleType(f"{package_name}.common")
    common.pytuya = pytuya
    sys.modules[f"{package_name}.common"] = common
    probe = load_file(f"{package_name}.device_probe", root / "device_probe.py")
    probe.PROTOCOL_PROBE_TIMEOUT = 0.75
    return probe


class Tuya32PreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_detection_reaches_32_without_false_33_match(self):
        probe = load_probe()
        key = "0123456789abcdef"
        async with VirtualTuya32Device(
            key,
            dps={"1": True, "32": 3200},
            port=6668,
            response_chunks=4,
        ) as device:
            report = await probe.async_device_preflight(
                None,
                {
                    "host": "127.0.0.1",
                    "device_id": "virtual-preflight-32",
                    "local_key": key,
                    "protocol_version": "auto",
                    "enable_debug": False,
                },
            )
        self.assertTrue(report.ok, report.as_dict())
        self.assertEqual(report.resolved_protocol, "3.2")
        self.assertIn(32, report.dp_ids)
        self.assertGreaterEqual(device.transcript.incompatible_queries, 1)


if __name__ == "__main__":
    unittest.main()
