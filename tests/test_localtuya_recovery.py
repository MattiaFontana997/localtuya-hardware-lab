from __future__ import annotations

import asyncio
import copy
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localtuya_lab.tuya33 import VirtualTuya33Device


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
    const.CONF_DEVICES = "devices"
    const.CONF_FRIENDLY_NAME = "friendly_name"
    const.CONF_HOST = "host"
    sys.modules["homeassistant.const"] = const


def _load_recovery_target():
    _install_homeassistant_stubs()
    root = _target_root()
    package_name = "lab_target_recovery"
    package = types.ModuleType(package_name)
    package.__path__ = [str(root)]
    sys.modules[package_name] = package
    pytuya = _load_file(f"{package_name}.pytuya", root / "pytuya/__init__.py")
    const = _load_file(f"{package_name}.const", root / "const.py")
    repairs = types.ModuleType(f"{package_name}.repair_issues")
    repairs.async_sync_host_recovery_issue = lambda *args, **kwargs: None
    sys.modules[f"{package_name}.repair_issues"] = repairs
    recovery = _load_file(f"{package_name}.host_recovery", root / "host_recovery.py")
    return pytuya, const, recovery


class FakeEntry:
    def __init__(self, data: dict) -> None:
        self.data = data


class FakeConfigEntries:
    def __init__(self) -> None:
        self.updates = 0

    def async_update_entry(self, entry, *, data) -> None:
        self.updates += 1
        entry.data = data


class FakeHass:
    def __init__(self) -> None:
        self.config_entries = FakeConfigEntries()


def _entry_data(local_key: str = "0123456789abcdef") -> dict:
    return {
        "devices": {
            "virtual-recovery-device": {
                "friendly_name": "Virtual recovery device",
                "host": "127.0.0.1",
                "local_key": local_key,
                "protocol_version": "3.3",
                "enable_debug": False,
            }
        }
    }


class RealHostRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_host_is_authenticated_before_persisting(self):
        pytuya, _const, recovery = _load_recovery_target()
        hass = FakeHass()
        entry = FakeEntry(_entry_data())
        observed_saved_hosts: list[str] = []

        async def validator(_hass, data):
            observed_saved_hosts.append(entry.data["devices"]["virtual-recovery-device"]["host"])
            protocol = await pytuya.connect(
                data["host"],
                data["device_id"],
                data["local_key"],
                data["protocol_version"],
                False,
                timeout=1,
            )
            try:
                status = await protocol.status()
                if not status:
                    raise ValueError("empty status")
                return status
            finally:
                await protocol.close()

        async with VirtualTuya33Device(
            "0123456789abcdef",
            host="127.0.0.2",
            port=6668,
            dps={"1": True, "9": 99},
        ):
            result = await recovery.async_recover_discovered_host(
                hass,
                entry,
                "virtual-recovery-device",
                "127.0.0.2",
                validator=validator,
            )

        self.assertEqual(result.outcome.value, "updated")
        self.assertEqual(observed_saved_hosts, ["127.0.0.1"])
        self.assertEqual(entry.data["devices"]["virtual-recovery-device"]["host"], "127.0.0.2")
        self.assertEqual(hass.config_entries.updates, 1)

    async def test_unreachable_candidate_never_overwrites_working_host(self):
        _pytuya, _const, recovery = _load_recovery_target()
        hass = FakeHass()
        entry = FakeEntry(_entry_data())

        async def validator(_hass, data):
            reader, writer = await asyncio.open_connection(data["host"], 6668)
            writer.close()
            await writer.wait_closed()
            return reader

        result = await recovery.async_recover_discovered_host(
            hass,
            entry,
            "virtual-recovery-device",
            "127.0.0.3",
            validator=validator,
        )
        self.assertEqual(result.outcome.value, "validation_failed")
        self.assertEqual(entry.data["devices"]["virtual-recovery-device"]["host"], "127.0.0.1")
        self.assertEqual(hass.config_entries.updates, 0)
        self.assertTrue(result.validation_error_type)

    async def test_stale_validation_cannot_clobber_newer_host(self):
        pytuya, _const, recovery = _load_recovery_target()
        hass = FakeHass()
        entry = FakeEntry(_entry_data())

        async def validator(_hass, data):
            protocol = await pytuya.connect(
                data["host"], data["device_id"], data["local_key"], "3.3", False,
                timeout=1,
            )
            try:
                await protocol.status()
            finally:
                await protocol.close()
            newer = copy.deepcopy(entry.data)
            newer["devices"]["virtual-recovery-device"]["host"] = "127.0.0.9"
            entry.data = newer
            return True

        async with VirtualTuya33Device(
            "0123456789abcdef",
            host="127.0.0.2",
            port=6668,
            dps={"1": True},
        ):
            result = await recovery.async_recover_discovered_host(
                hass,
                entry,
                "virtual-recovery-device",
                "127.0.0.2",
                validator=validator,
            )

        self.assertEqual(result.outcome.value, "stale")
        self.assertEqual(entry.data["devices"]["virtual-recovery-device"]["host"], "127.0.0.9")
        self.assertEqual(hass.config_entries.updates, 0)

    async def test_wrong_key_validation_cannot_persist_candidate(self):
        pytuya, _const, recovery = _load_recovery_target()
        hass = FakeHass()
        entry = FakeEntry(_entry_data(local_key="fedcba9876543210"))

        async def validator(_hass, data):
            async with asyncio.timeout(0.75):
                protocol = await pytuya.connect(
                    data["host"], data["device_id"], data["local_key"], "3.3", False,
                    timeout=0.5,
                )
                try:
                    return await protocol.status()
                finally:
                    await protocol.close()

        async with VirtualTuya33Device(
            "0123456789abcdef",
            host="127.0.0.2",
            port=6668,
            dps={"1": True},
        ):
            result = await recovery.async_recover_discovered_host(
                hass,
                entry,
                "virtual-recovery-device",
                "127.0.0.2",
                validator=validator,
            )

        self.assertEqual(result.outcome.value, "validation_failed")
        self.assertEqual(entry.data["devices"]["virtual-recovery-device"]["host"], "127.0.0.1")
        self.assertEqual(hass.config_entries.updates, 0)


if __name__ == "__main__":
    unittest.main()
