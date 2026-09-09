from __future__ import annotations

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
    return Path(root) / "custom_components/localtuya"


def _load_file(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _install_homeassistant_stubs() -> None:
    ha = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
    ha.__path__ = []
    const = types.ModuleType("homeassistant.const")
    const.CONF_DEVICE_ID = "device_id"
    const.CONF_HOST = "host"
    sys.modules["homeassistant.const"] = const
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    sys.modules["homeassistant.core"] = core


def _load_real_device_probe():
    _install_homeassistant_stubs()
    root = _target_root()
    package_name = "lab_target_preflight"
    package = types.ModuleType(package_name)
    package.__path__ = [str(root)]
    sys.modules[package_name] = package

    pytuya = _load_file(f"{package_name}.pytuya", root / "pytuya/__init__.py")
    _load_file(f"{package_name}.const", root / "const.py")
    _load_file(f"{package_name}.device_health", root / "device_health.py")
    common = types.ModuleType(f"{package_name}.common")
    common.pytuya = pytuya
    sys.modules[f"{package_name}.common"] = common
    probe = _load_file(f"{package_name}.device_probe", root / "device_probe.py")
    # Wrong protocol attempts should fail quickly in the lab. Successful real
    # handshakes complete well below this bound.
    probe.PROTOCOL_PROBE_TIMEOUT = 0.75
    return probe


def _probe_data(local_key: str, protocol: str = "auto") -> dict:
    return {
        "host": "127.0.0.1",
        "device_id": "virtual-preflight-device",
        "local_key": local_key,
        "protocol_version": protocol,
        "enable_debug": False,
    }


class RealPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_auto_detect(self, device, expected_protocol: str, expected_dp: int) -> None:
        probe = _load_real_device_probe()
        async with device:
            report = await probe.async_device_preflight(None, _probe_data("0123456789abcdef"))
        self.assertTrue(report.ok, report.as_dict())
        self.assertEqual(report.resolved_protocol, expected_protocol)
        self.assertIn(expected_dp, report.dp_ids)
        safe = repr(report.as_dict())
        self.assertNotIn("0123456789abcdef", safe)
        self.assertNotIn("127.0.0.1", safe)
        self.assertNotIn("virtual-preflight-device", safe)

    async def test_auto_detects_real_35_wire_protocol(self):
        await self._assert_auto_detect(
            VirtualTuya35Device(
                "0123456789abcdef",
                dps={"1": True, "35": 3500},
                port=6668,
                response_chunks=4,
            ),
            "3.5",
            35,
        )

    async def test_auto_falls_through_to_real_34_wire_protocol(self):
        await self._assert_auto_detect(
            VirtualTuya34Device(
                "0123456789abcdef",
                dps={"1": True, "34": 3400},
                port=6668,
                response_chunks=4,
            ),
            "3.4",
            34,
        )

    async def test_auto_falls_through_to_real_33_wire_protocol(self):
        await self._assert_auto_detect(
            VirtualTuya33Device(
                "0123456789abcdef",
                dps={"1": True, "33": 3300},
                port=6668,
                response_chunks=4,
            ),
            "3.3",
            33,
        )

    async def test_auto_falls_through_to_real_31_wire_protocol(self):
        await self._assert_auto_detect(
            VirtualTuya31Device(
                "0123456789abcdef",
                dps={"1": True, "31": 3100},
                port=6668,
                response_chunks=4,
            ),
            "3.1",
            31,
        )

    async def test_wrong_key_never_produces_ready_report_or_dps(self):
        probe = _load_real_device_probe()
        async with VirtualTuya33Device(
            "0123456789abcdef",
            dps={"1": True},
            port=6668,
        ):
            report = await probe.async_device_preflight(
                None,
                _probe_data("fedcba9876543210", "3.3"),
            )
        self.assertFalse(report.ok)
        self.assertFalse(report.detected_dps)
        safe = repr(report.as_dict())
        self.assertNotIn("fedcba9876543210", safe)
        self.assertNotIn("127.0.0.1", safe)


if __name__ == "__main__":
    unittest.main()
