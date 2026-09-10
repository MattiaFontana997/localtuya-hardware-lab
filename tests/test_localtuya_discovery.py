from __future__ import annotations

import asyncio
import importlib.util
import os
import socket
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localtuya_lab.discovery_packets import (
    build_55aa_encrypted_announcement,
    build_6699_announcement,
    build_plain_announcement,
)


def load_discovery():
    repo = os.environ.get("LOCALTUYA_ROOT")
    if not repo:
        raise unittest.SkipTest("LOCALTUYA_ROOT is not set")
    path = Path(repo) / "custom_components/localtuya/discovery.py"

    ha = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
    ha.__path__ = []
    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    sys.modules["homeassistant.components"] = components
    network = types.ModuleType("homeassistant.components.network")

    async def async_get_adapters(_hass):
        return []

    network.async_get_adapters = async_get_adapters
    sys.modules["homeassistant.components.network"] = network
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    sys.modules["homeassistant.core"] = core

    spec = importlib.util.spec_from_file_location("localtuya_target_discovery", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load LocalTuya discovery")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RealDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_udp_listeners_accept_plain_55aa_and_6699_announcements(self):
        discovery_module = load_discovery()
        callbacks = []
        discovery = discovery_module.TuyaDiscovery(callback=callbacks.append, hass=None)
        await discovery.start()
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sender.sendto(build_plain_announcement("virtual-udp-31", "3.1"), ("127.0.0.1", 6666))
            sender.sendto(build_55aa_encrypted_announcement("virtual-udp-33", "3.3"), ("127.0.0.1", 6667))
            sender.sendto(
                build_6699_announcement("virtual-udp-35", "3.5", iv=b"123456789012"),
                ("127.0.0.1", 7000),
            )
            for _ in range(50):
                if len(discovery.devices) == 3:
                    break
                await asyncio.sleep(0.01)

            self.assertEqual(set(discovery.devices), {"virtual-udp-31", "virtual-udp-33", "virtual-udp-35"})
            self.assertEqual(discovery.devices["virtual-udp-31"]["ip"], "127.0.0.1")
            self.assertEqual(discovery.devices["virtual-udp-33"]["version"], "3.3")
            self.assertEqual(discovery.devices["virtual-udp-35"]["version"], "3.5")
            self.assertEqual(len(callbacks), 3)

            corrupted = bytearray(build_6699_announcement("must-not-appear", "3.5", iv=b"abcdefghijkl"))
            corrupted[-8] ^= 0x01
            sender.sendto(bytes(corrupted), ("127.0.0.1", 7000))
            await asyncio.sleep(0.05)
            self.assertNotIn("must-not-appear", discovery.devices)
        finally:
            sender.close()
            discovery.close()
            await asyncio.sleep(0)

    async def test_targeted_find_device_ignores_other_real_udp_and_waits_for_delayed_target(self):
        """Real UDP traffic must be filtered by Device ID, not first responder."""
        discovery_module = load_discovery()

        if not hasattr(discovery_module, "find_device"):
            self.fail("LocalTuya target does not expose targeted find_device()")

        async def delayed_virtual_announcements():
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                # Let find_device() bind all three real Tuya UDP listener ports.
                await asyncio.sleep(0.05)
                sender.sendto(
                    build_55aa_encrypted_announcement(
                        "unrelated-tuya-device",
                        "3.3",
                    ),
                    ("127.0.0.1", 6667),
                )

                # The requested gateway announces later, as a slow 3.5 device
                # would after a later REQ_DEVINFO cycle in the field.
                await asyncio.sleep(0.08)
                sender.sendto(
                    build_6699_announcement(
                        "virtual-slow-gateway",
                        "3.5",
                        iv=b"slowgateway1",
                    ),
                    ("127.0.0.1", 7000),
                )
            finally:
                sender.close()

        producer = asyncio.create_task(delayed_virtual_announcements())
        try:
            result = await discovery_module.find_device(
                "virtual-slow-gateway",
                timeout=1.0,
                rebroadcast_interval=0.10,
                hass=None,
            )
        finally:
            await producer

        self.assertIsNotNone(result)
        self.assertEqual(result["gwId"], "virtual-slow-gateway")
        self.assertEqual(result["ip"], "127.0.0.1")
        self.assertEqual(result["version"], "3.5")


if __name__ == "__main__":
    unittest.main()
