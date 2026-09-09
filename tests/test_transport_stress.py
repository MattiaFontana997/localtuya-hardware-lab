from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localtuya_lab.tuya33 import (
    DP_QUERY,
    VirtualTuya33Device,
    build_55aa_response,
    encode_json_payload,
)
from localtuya_lab.tuya34 import VirtualTuya34Device

STATUS = 0x08


def load_pytuya():
    repo = os.environ.get("LOCALTUYA_ROOT")
    if not repo:
        raise unittest.SkipTest("LOCALTUYA_ROOT is not set")
    path = Path(repo) / "custom_components/localtuya/pytuya/__init__.py"
    spec = importlib.util.spec_from_file_location("localtuya_transport_stress_pytuya", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load LocalTuya pytuya")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Listener:
    def __init__(self):
        self.updates = []
        self.disconnects = 0

    def status_updated(self, status):
        self.updates.append(dict(status))

    def disconnected(self):
        self.disconnects += 1


class ConcatenatingTuya33(VirtualTuya33Device):
    async def _process_frame(self, request, writer) -> None:
        if request.cmd != DP_QUERY:
            await super()._process_frame(request, writer)
            return
        self.transcript.requests += 1
        self.transcript.status_queries += 1
        self.transcript.seen_commands.append(request.cmd)
        push = build_55aa_response(
            900,
            STATUS,
            encode_json_payload(self.local_key, {"dps": {"99": "push"}}),
        )
        reply = build_55aa_response(
            request.seqno,
            request.cmd,
            encode_json_payload(self.local_key, {"dps": dict(self.dps)}),
        )
        # One TCP write intentionally contains two complete Tuya frames.
        writer.write(push + reply)
        await writer.drain()


class TransportStressTests(unittest.IsolatedAsyncioTestCase):
    async def test_33_response_split_into_single_byte_tcp_writes(self):
        pytuya = load_pytuya()
        key = "0123456789abcdef"
        async with VirtualTuya33Device(
            key,
            dps={"1": True, "2": 3301},
            response_chunks=10000,
        ) as device:
            protocol = await pytuya.connect(
                device.host, "byte-fragmented-33", key, "3.3", False,
                port=device.port, timeout=2,
            )
            try:
                self.assertEqual((await protocol.status())["2"], 3301)
                await protocol.set_dp(False, 1)
                self.assertIs((await protocol.status())["1"], False)
            finally:
                await protocol.close()

    async def test_33_multiple_frames_in_one_tcp_read_are_both_dispatched(self):
        pytuya = load_pytuya()
        key = "0123456789abcdef"
        listener = Listener()
        async with ConcatenatingTuya33(key, dps={"1": True, "2": 3302}) as device:
            protocol = await pytuya.connect(
                device.host, "coalesced-33", key, "3.3", False,
                listener=listener, port=device.port, timeout=2,
            )
            try:
                status = await protocol.status()
                self.assertEqual(status["2"], 3302)
                self.assertEqual(status["99"], "push")
                self.assertTrue(listener.updates)
                self.assertEqual(listener.updates[-1]["99"], "push")
            finally:
                await protocol.close()

    async def test_34_bad_hmac_aborts_only_current_exchange(self):
        pytuya = load_pytuya()
        key = "0123456789abcdef"
        async with VirtualTuya34Device(key, dps={"1": True, "34": 3401}) as device:
            protocol = await pytuya.connect(
                device.host, "tampered-34", key, "3.4", False,
                port=device.port, timeout=2,
            )
            try:
                self.assertEqual((await protocol.status())["34"], 3401)
                device.corrupt_next_response = True
                with self.assertRaises(Exception):
                    async with asyncio.timeout(1):
                        await protocol.status()
                self.assertEqual((await protocol.status())["34"], 3401)
            finally:
                await protocol.close()


if __name__ == "__main__":
    unittest.main()
