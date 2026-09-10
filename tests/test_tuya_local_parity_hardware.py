from __future__ import annotations

import copy
import os
import unittest
from types import SimpleNamespace

from localtuya_lab.tuya35 import VirtualTuya35Device
from test_onboarding_hardware_paths import (
    GATEWAY_ID,
    LAMP_PRODUCT_ID,
    LOCAL_KEY,
    _Cloud,
    _Entry,
    _Hass,
    _child_dps,
    _load,
)


class _SharingManager:
    """Minimal Device Sharing manager returning raw SDK-like device objects."""

    def __init__(self, devices):
        self.device_map = {device.id: device for device in devices}

    def update_device_cache(self):
        return None


def _hub_sdk_record():
    # Device Sharing IP is intentionally WAN-looking: LocalTuya must never use
    # it as proof of the actual LAN endpoint.
    return SimpleNamespace(
        id=GATEWAY_ID,
        name="Virtual Bluetooth Gateway",
        local_key=LOCAL_KEY,
        product_id="virtual-wg2-product",
        product_name="Bluetooth Gateway",
        category="wg2",
        online=True,
        support_local=True,
        sub=False,
        uuid="gateway-uuid",
        ip="198.51.100.200",
    )


def _child_sdk_records(count: int = 5):
    records = []
    for index in range(1, count + 1):
        # Deliberately omit node_id and every gateway/parent-id alias. This is
        # the problematic shape reported by real BLE gateway users: the child
        # is marked as a sub-device and only carries UUID + local key.
        records.append(
            SimpleNamespace(
                id=f"lamp-{index}",
                name=f"Virtual BLE Lamp {index}",
                local_key=LOCAL_KEY,
                product_id=LAMP_PRODUCT_ID,
                product_name="BLE Lamp",
                category="dj",
                online=True,
                support_local=True,
                sub=True,
                uuid=f"cid-{index}",
                ip="",
            )
        )
    return records


@unittest.skipUnless(os.environ.get("LOCALTUYA_ROOT"), "target checkout not available")
class TuyaLocalParityHardwareTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cf = _load("custom_components.localtuya.config_flow")
        self.qr = _load("custom_components.localtuya.qr_onboarding")
        catalog_module = _load("custom_components.localtuya.device_catalog")

        self.entry = _Entry(self.cf)
        self.hass = _Hass(self.cf, self.entry)
        catalog = catalog_module.DeviceCatalog(
            None,
            session=object(),
            store=object(),
        )
        await catalog.async_load_builtin_catalog()
        self.hass.data[self.cf.DOMAIN][self.cf.DATA_DEVICE_CATALOG] = catalog

    async def test_sdk_shape_without_parent_or_node_id_routes_five_ble_children(self):
        """Raw Tuya SDK BLE children become real gateway CID connections."""
        sdk_devices = [_hub_sdk_record(), *_child_sdk_records()]
        provisioning_cloud = self.qr.QrCloudClient(self.hass, {})
        provisioning_cloud._build_manager = lambda: _SharingManager(sdk_devices)

        devices = await provisioning_cloud.async_get_devices()
        self.assertEqual(len(devices), 6)
        self.assertTrue(devices[GATEWAY_ID]["is_hub"])

        for index in range(1, 6):
            child = devices[f"lamp-{index}"]
            self.assertEqual(child["node_id"], f"cid-{index}")
            self.assertEqual(child["gateway_id"], GATEWAY_ID)
            self.assertEqual(child["gateway_local_key"], LOCAL_KEY)
            self.assertTrue(self.qr._qr_is_locally_eligible(child))

        # The cloud/WAN address above is never used. The hardware leg starts
        # only from a validated LAN address, exactly as the real fallback flow.
        cloud = _Cloud()
        async with VirtualTuya35Device(
            LOCAL_KEY,
            children=_child_dps(),
            port=6668,
            response_chunks=5,
        ) as gateway:
            prepared = {}
            for index in range(1, 6):
                device_id = f"lamp-{index}"
                record = copy.deepcopy(devices[device_id])
                data, candidates = await self.qr.async_prepare_qr_device(
                    self.hass,
                    cloud,
                    record,
                    host_override="127.0.0.1",
                )
                prepared[device_id] = data
                self.assertEqual(data[self.cf.CONF_PROTOCOL_VERSION], "3.5")
                self.assertEqual(data["gateway_id"], GATEWAY_ID)
                self.assertEqual(data["node_id"], f"cid-{index}")
                self.assertEqual(data[self.cf.CONF_HOST], "127.0.0.1")
                self.assertIn("20 (value: False)", data[self.cf.CONF_DPS_STRINGS])
                self.assertEqual(candidates, [])
                self.assertEqual(len(data[self.cf.CONF_ENTITIES]), 1)
                self.assertEqual(data[self.cf.CONF_ENTITIES][0]["platform"], "light")

            self.assertEqual(len(prepared), 5)
            self.assertTrue(
                {f"cid-{index}" for index in range(1, 6)}.issubset(
                    set(gateway.transcript.seen_cids)
                )
            )

            # Prove that a child prepared from the raw SDK metadata can also be
            # controlled over the real encrypted gateway transport.
            child = prepared["lamp-3"]
            protocol = await _load("custom_components.localtuya.pytuya").connect(
                child[self.cf.CONF_HOST],
                child[self.cf.CONF_DEVICE_ID],
                child[self.cf.CONF_LOCAL_KEY],
                child[self.cf.CONF_PROTOCOL_VERSION],
                False,
                cid=child["node_id"],
                gateway_id=child["gateway_id"],
                timeout=2,
            )
            try:
                self.assertIs((await protocol.status())["20"], False)
                await protocol.set_dp(True, 20)
                self.assertIs(gateway.children["cid-3"]["20"], True)
                self.assertIs((await protocol.status())["20"], True)
            finally:
                await protocol.close()


if __name__ == "__main__":
    unittest.main()
