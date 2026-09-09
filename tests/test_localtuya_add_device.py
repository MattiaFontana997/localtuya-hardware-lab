from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path

from localtuya_lab.tuya31 import VirtualTuya31Device
from localtuya_lab.tuya32 import VirtualTuya32Device
from localtuya_lab.tuya33 import VirtualTuya33Device
from localtuya_lab.tuya34 import VirtualTuya34Device
from localtuya_lab.tuya35 import VirtualTuya35Device

LOCAL_KEY = "0123456789abcdef"
DEVICE_ID = "virtual-add-device"


def _target_root() -> Path:
    raw = os.environ.get("LOCALTUYA_ROOT")
    if not raw:
        raise unittest.SkipTest("LOCALTUYA_ROOT is not set")
    return Path(raw)


def _load_target():
    root = _target_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return importlib.import_module("custom_components.localtuya.config_flow")


class _FakeConfig:
    language = "en"


class _FakeConfigEntries:
    def async_update_entry(self, entry, *, data=None, title=None, **kwargs):
        if data is not None:
            entry.data = data
        if title is not None:
            entry.title = title


class _FakeHass:
    def __init__(self, domain: str):
        self.data = {domain: {}}
        self.config_entries = _FakeConfigEntries()
        self.config = _FakeConfig()

    async def async_add_import_executor_job(self, target, *args):
        return target(*args)

    async def async_add_executor_job(self, target, *args):
        return target(*args)


class _FakeEntry:
    def __init__(self, cf):
        self.entry_id = "virtual-entry"
        self.title = "LocalTuya Hardware Lab"
        self.data = {
            cf.CONF_DEVICES: {},
            cf.CONF_NO_CLOUD: True,
        }


@unittest.skipUnless(os.environ.get("LOCALTUYA_ROOT"), "target checkout not available")
class RealAddDeviceFlowTests(unittest.IsolatedAsyncioTestCase):
    def _flow(self, cf):
        entry = _FakeEntry(cf)
        hass = _FakeHass(cf.DOMAIN)
        flow = cf.LocalTuyaOptionsFlowHandler(entry)
        flow.hass = hass
        flow.context = {}
        return flow, entry

    async def _add_switch(self, device, expected_protocol: str) -> None:
        cf = _load_target()
        flow, entry = self._flow(cf)

        # Exercise the real manual fallback path. Mapping intelligence has its
        # own certification; here we deliberately force the user-driven entity
        # branch so persistence cannot be accidentally dependent on a catalog.
        original_candidates = cf.async_get_entity_candidates

        async def no_candidates(*args, **kwargs):
            return []

        cf.async_get_entity_candidates = no_candidates
        try:
            async with device:
                result = await flow.async_step_add_device(
                    {cf.SELECTED_DEVICE: cf.CUSTOM_DEVICE}
                )
                self.assertEqual(result["step_id"], "configure_device")

                result = await flow.async_step_configure_device(
                    {
                        cf.CONF_FRIENDLY_NAME: "Virtual Switch",
                        cf.CONF_HOST: "127.0.0.1",
                        cf.CONF_DEVICE_ID: DEVICE_ID,
                        cf.CONF_LOCAL_KEY: LOCAL_KEY,
                        cf.CONF_PROTOCOL_VERSION: cf.PROTOCOL_AUTO,
                        cf.CONF_ENABLE_DEBUG: False,
                    }
                )
                self.assertEqual(result["step_id"], "pick_entity_type")
                self.assertEqual(
                    flow.device_data[cf.CONF_PROTOCOL_VERSION],
                    expected_protocol,
                )
                self.assertIn("1 (value: False)", flow.dps_strings)

                result = await flow.async_step_pick_entity_type(
                    {cf.PLATFORM_TO_ADD: "switch"}
                )
                self.assertEqual(result["step_id"], "configure_entity")

                result = await flow.async_step_configure_entity(
                    {
                        cf.CONF_ID: "1 (value: False)",
                        cf.CONF_FRIENDLY_NAME: "Virtual Switch",
                        "restore_on_reconnect": False,
                        "is_passive_entity": False,
                    }
                )
                self.assertEqual(str(result["type"]), "create_entry")
        finally:
            cf.async_get_entity_candidates = original_candidates

        stored = entry.data[cf.CONF_DEVICES][DEVICE_ID]
        self.assertEqual(stored[cf.CONF_HOST], "127.0.0.1")
        self.assertEqual(stored[cf.CONF_PROTOCOL_VERSION], expected_protocol)
        self.assertEqual(len(stored[cf.CONF_ENTITIES]), 1)
        self.assertEqual(stored[cf.CONF_ENTITIES][0][cf.CONF_ID], 1)
        self.assertEqual(stored[cf.CONF_ENTITIES][0][cf.CONF_PLATFORM], "switch")

    async def test_manual_add_device_31(self):
        await self._add_switch(
            VirtualTuya31Device(LOCAL_KEY, dps={"1": False}, port=6668, response_chunks=3),
            "3.1",
        )

    async def test_manual_add_device_32(self):
        await self._add_switch(
            VirtualTuya32Device(LOCAL_KEY, dps={"1": False}, port=6668, response_chunks=3),
            "3.2",
        )

    async def test_manual_add_device_33(self):
        await self._add_switch(
            VirtualTuya33Device(LOCAL_KEY, dps={"1": False}, port=6668, response_chunks=3),
            "3.3",
        )

    async def test_manual_add_device_34(self):
        await self._add_switch(
            VirtualTuya34Device(LOCAL_KEY, dps={"1": False}, port=6668, response_chunks=3),
            "3.4",
        )

    async def test_manual_add_device_35(self):
        await self._add_switch(
            VirtualTuya35Device(LOCAL_KEY, dps={"1": False}, port=6668, response_chunks=3),
            "3.5",
        )

    async def test_wrong_key_is_rejected_without_persisting_device(self):
        cf = _load_target()
        flow, entry = self._flow(cf)
        async with VirtualTuya33Device(LOCAL_KEY, dps={"1": False}, port=6668):
            await flow.async_step_add_device({cf.SELECTED_DEVICE: cf.CUSTOM_DEVICE})
            result = await flow.async_step_configure_device(
                {
                    cf.CONF_FRIENDLY_NAME: "Wrong Key Device",
                    cf.CONF_HOST: "127.0.0.1",
                    cf.CONF_DEVICE_ID: "wrong-key-device",
                    cf.CONF_LOCAL_KEY: "fedcba9876543210",
                    cf.CONF_PROTOCOL_VERSION: "3.3",
                    cf.CONF_ENABLE_DEBUG: False,
                }
            )
        self.assertEqual(result["step_id"], "configure_device")
        self.assertEqual(result["errors"].get("base"), "invalid_auth")
        self.assertEqual(entry.data[cf.CONF_DEVICES], {})

    async def test_offline_device_is_rejected_without_persisting_device(self):
        cf = _load_target()
        flow, entry = self._flow(cf)
        await flow.async_step_add_device({cf.SELECTED_DEVICE: cf.CUSTOM_DEVICE})
        result = await flow.async_step_configure_device(
            {
                cf.CONF_FRIENDLY_NAME: "Offline Device",
                cf.CONF_HOST: "127.0.0.1",
                cf.CONF_DEVICE_ID: "offline-device",
                cf.CONF_LOCAL_KEY: LOCAL_KEY,
                cf.CONF_PROTOCOL_VERSION: "3.3",
                cf.CONF_ENABLE_DEBUG: False,
            }
        )
        self.assertEqual(result["step_id"], "configure_device")
        self.assertEqual(result["errors"].get("base"), "cannot_connect")
        self.assertEqual(entry.data[cf.CONF_DEVICES], {})


if __name__ == "__main__":
    unittest.main()
