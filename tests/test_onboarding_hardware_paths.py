from __future__ import annotations

import asyncio
import copy
import importlib
import os
import sys
import unittest
from pathlib import Path

from localtuya_lab.tuya35 import VirtualTuya35Device

LOCAL_KEY = "0123456789abcdef"
GATEWAY_ID = "virtual-gateway"
LAMP_PRODUCT_ID = "r7sn2fda7l5hwzvx"


def _target_root() -> Path:
    raw = os.environ.get("LOCALTUYA_ROOT")
    if not raw:
        raise unittest.SkipTest("LOCALTUYA_ROOT is not set")
    return Path(raw)


def _load(name: str):
    root = _target_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return importlib.import_module(name)


class _Config:
    language = "en"


class _Entries:
    def __init__(self, entry):
        self._entries = {entry.entry_id: entry}

    def async_get_known_entry(self, entry_id):
        return self._entries[entry_id]

    def async_update_entry(self, entry, *, data=None, title=None, **kwargs):
        if data is not None:
            entry.data = data
        if title is not None:
            entry.title = title


class _Entry:
    def __init__(self, cf):
        self.entry_id = "virtual-entry"
        self.title = "LocalTuya Hardware Lab"
        self.data = {
            cf.CONF_DEVICES: {},
            cf.CONF_NO_CLOUD: True,
        }


class _Discovery:
    def __init__(self):
        self.devices = {
            GATEWAY_ID: {
                "gwId": GATEWAY_ID,
                "ip": "127.0.0.1",
                "version": "3.5",
            }
        }

    async def async_request_discovery(self):
        return True


class _Cloud:
    auth = {}

    async def async_get_datamodel(self, device_id):
        return []


class _Hass:
    def __init__(self, cf, entry, *, discovery=None):
        self.data = {cf.DOMAIN: {}}
        if discovery is not None:
            self.data[cf.DOMAIN][cf.DATA_DISCOVERY] = discovery
        self.config_entries = _Entries(entry)
        self.config = _Config()
        self.loop = asyncio.get_running_loop()

    async def async_add_import_executor_job(self, target, *args):
        return target(*args)

    async def async_add_executor_job(self, target, *args):
        return target(*args)


def _flow(cf, *, discovery=None):
    entry = _Entry(cf)
    hass = _Hass(cf, entry, discovery=discovery)
    flow = cf.LocalTuyaOptionsFlowHandler(entry)
    flow.hass = hass
    flow.handler = entry.entry_id
    flow.context = {"entry_id": entry.entry_id}
    return flow, entry, hass


def _child_records(count: int = 5):
    return {
        f"lamp-{index}": {
            "id": f"lamp-{index}",
            "name": f"Virtual Lamp {index}",
            "product_id": LAMP_PRODUCT_ID,
            "category": "dj",
            "node_id": f"cid-{index}",
            "gateway_id": GATEWAY_ID,
            "gateway_local_key": LOCAL_KEY,
            "gateway_ip": "127.0.0.1",
            "online": True,
            "support_local": True,
        }
        for index in range(1, count + 1)
    }


def _child_dps(count: int = 5):
    return {
        f"cid-{index}": {
            "20": False,
            "21": "white",
            "22": 500,
            "23": 500,
            "24": "000003e803e8",
        }
        for index in range(1, count + 1)
    }


@unittest.skipUnless(os.environ.get("LOCALTUYA_ROOT"), "target checkout not available")
class PersistedConfigOperationalTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_add_persisted_35_config_reconnects_and_controls_device(self):
        cf = _load("custom_components.localtuya.config_flow")
        pytuya = _load("custom_components.localtuya.pytuya")
        flow, entry, _hass = _flow(cf)

        original_candidates = cf.async_get_entity_candidates

        async def no_candidates(*args, **kwargs):
            return []

        cf.async_get_entity_candidates = no_candidates
        device = VirtualTuya35Device(
            LOCAL_KEY,
            dps={"1": False, "2": 35},
            port=6668,
            response_chunks=7,
            inter_chunk_delay=0.001,
        )
        try:
            async with device:
                await flow.async_step_add_device({cf.SELECTED_DEVICE: cf.CUSTOM_DEVICE})
                result = await flow.async_step_configure_device(
                    {
                        cf.CONF_FRIENDLY_NAME: "Persisted Virtual Switch",
                        cf.CONF_HOST: "127.0.0.1",
                        cf.CONF_DEVICE_ID: "persisted-35",
                        cf.CONF_LOCAL_KEY: LOCAL_KEY,
                        cf.CONF_PROTOCOL_VERSION: cf.PROTOCOL_AUTO,
                        cf.CONF_ENABLE_DEBUG: False,
                    }
                )
                self.assertEqual(result["step_id"], "pick_entity_type")
                await flow.async_step_pick_entity_type({cf.PLATFORM_TO_ADD: "switch"})
                result = await flow.async_step_configure_entity(
                    {
                        cf.CONF_ID: "1 (value: False)",
                        cf.CONF_FRIENDLY_NAME: "Persisted Virtual Switch",
                        "restore_on_reconnect": False,
                        "is_passive_entity": False,
                    }
                )
                self.assertEqual(result["step_id"], "pick_entity_type")
                result = await flow.async_step_pick_entity_type(
                    {cf.NO_ADDITIONAL_ENTITIES: True}
                )
                self.assertEqual(str(result["type"]), "create_entry")

                stored = copy.deepcopy(entry.data[cf.CONF_DEVICES]["persisted-35"])
                self.assertEqual(stored[cf.CONF_PROTOCOL_VERSION], "3.5")

                # Treat the just-persisted entry as runtime input. Nothing here
                # uses the preflight object/connection that created the entry.
                protocol = await pytuya.connect(
                    stored[cf.CONF_HOST],
                    "persisted-35",
                    stored[cf.CONF_LOCAL_KEY],
                    stored[cf.CONF_PROTOCOL_VERSION],
                    False,
                    timeout=2,
                )
                try:
                    self.assertIs((await protocol.status())["1"], False)
                    await protocol.set_dp(True, 1)
                    self.assertIs(device.dps["1"], True)
                    self.assertIs((await protocol.status())["1"], True)
                finally:
                    await protocol.close()
        finally:
            cf.async_get_entity_candidates = original_candidates


@unittest.skipUnless(os.environ.get("LOCALTUYA_ROOT"), "target checkout not available")
class GatewayOnboardingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cf = _load("custom_components.localtuya.config_flow")
        self.qr = _load("custom_components.localtuya.qr_onboarding")
        catalog_module = _load("custom_components.localtuya.device_catalog")
        self.entry = _Entry(self.cf)
        self.discovery = _Discovery()
        self.hass = _Hass(self.cf, self.entry, discovery=self.discovery)
        catalog = catalog_module.DeviceCatalog(
            None,
            session=object(),
            store=object(),
        )
        await catalog.async_load_builtin_catalog()
        self.hass.data[self.cf.DOMAIN][self.cf.DATA_DEVICE_CATALOG] = catalog
        self.cloud = _Cloud()

    async def test_prepare_five_real_gateway_children_and_isolate_missing_cid(self):
        children = _child_dps()
        records = _child_records()
        async with VirtualTuya35Device(
            LOCAL_KEY,
            children=children,
            port=6668,
            response_chunks=5,
        ) as gateway:
            prepared = {}
            for device_id, record in records.items():
                data, candidates = await self.qr.async_prepare_qr_device(
                    self.hass,
                    self.cloud,
                    record,
                    host_override="127.0.0.1",
                )
                prepared[device_id] = data
                self.assertEqual(data[self.cf.CONF_PROTOCOL_VERSION], "3.5")
                self.assertEqual(data["gateway_id"], GATEWAY_ID)
                self.assertEqual(data["node_id"], record["node_id"])
                self.assertIn("20 (value: False)", data[self.cf.CONF_DPS_STRINGS])
                # Bundled verified product mapping must resolve deterministically.
                self.assertEqual(candidates, [])
                self.assertEqual(len(data[self.cf.CONF_ENTITIES]), 1)
                self.assertEqual(data[self.cf.CONF_ENTITIES][0]["platform"], "light")

            self.assertEqual(len(prepared), 5)
            self.assertTrue(set(records[device_id]["node_id"] for device_id in records).issubset(set(gateway.transcript.seen_cids)))

            missing = {
                **records["lamp-1"],
                "id": "lamp-missing",
                "name": "Missing Child",
                "node_id": "cid-missing",
            }
            with self.assertRaises(self.qr.QrProvisioningError) as ctx:
                await self.qr.async_prepare_qr_device(
                    self.hass,
                    self.cloud,
                    missing,
                    host_override="127.0.0.1",
                )
            self.assertEqual(ctx.exception.reason, "empty_dps")

            # Failure of one CID must not poison subsequent child onboarding.
            data, _ = await self.qr.async_prepare_qr_device(
                self.hass,
                self.cloud,
                records["lamp-5"],
                host_override="127.0.0.1",
            )
            self.assertEqual(data["node_id"], "cid-5")
            self.assertEqual(data[self.cf.CONF_PROTOCOL_VERSION], "3.5")

    async def test_bulk_adds_five_gateway_children_and_isolates_one_bad_child(self):
        records = _child_records()
        records["lamp-bad"] = {
            **records["lamp-1"],
            "id": "lamp-bad",
            "name": "Broken Child",
            "node_id": "cid-bad",
        }
        flow = self.cf.LocalTuyaOptionsFlowHandler(self.entry)
        flow.hass = self.hass
        flow.handler = self.entry.entry_id
        flow.context = {"entry_id": self.entry.entry_id}
        flow._qr_devices = records
        flow._qr_cloud = self.cloud

        async with VirtualTuya35Device(
            LOCAL_KEY,
            children=_child_dps(),
            port=6668,
            response_chunks=4,
        ):
            result = await flow.async_step_qr_bulk_choose_devices(
                {
                    self.qr.CONF_QR_BULK_DEVICE_IDS: list(records),
                }
            )

        self.assertEqual(result["step_id"], "qr_bulk_summary")
        summary = flow._qr_bulk_summary
        self.assertEqual(len(summary["added"]), 5)
        self.assertEqual(summary["review_required"], [])
        self.assertEqual(len(summary["failures"]), 1)
        self.assertEqual(summary["failures"][0]["device_id"], "lamp-bad")
        self.assertEqual(summary["failures"][0]["reason"], "empty_dps")

        stored = self.entry.data[self.cf.CONF_DEVICES]
        self.assertEqual(set(stored), set(_child_records()))
        for device_id, device_data in stored.items():
            self.assertEqual(device_data[self.cf.CONF_PROTOCOL_VERSION], "3.5")
            self.assertEqual(device_data["gateway_id"], GATEWAY_ID)
            self.assertEqual(len(device_data[self.cf.CONF_ENTITIES]), 1)
            self.assertEqual(device_data[self.cf.CONF_ENTITIES][0]["platform"], "light")


if __name__ == "__main__":
    unittest.main()
