from __future__ import annotations

import asyncio
import binascii
import json
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localtuya_lab.tuya33 import (
    DP_QUERY,
    HEADER,
    PREFIX,
    SUFFIX,
    VirtualTuya33Device,
    aes_ecb_decrypt,
    aes_ecb_encrypt,
    build_55aa_response,
)


class Tuya33CodecTests(unittest.TestCase):
    def test_aes_roundtrip(self):
        key = b"0123456789abcdef"
        raw = b'{"dps":{"1":true}}'
        self.assertEqual(aes_ecb_decrypt(key, aes_ecb_encrypt(key, raw)), raw)

    def test_response_frame_has_valid_crc_and_retcode(self):
        payload = b"ciphertext"
        frame = build_55aa_response(7, DP_QUERY, payload)
        prefix, seqno, cmd, length = HEADER.unpack_from(frame)
        self.assertEqual((prefix, seqno, cmd), (PREFIX, 7, DP_QUERY))
        self.assertEqual(length, len(frame) - HEADER.size)
        self.assertEqual(struct.unpack(">I", frame[HEADER.size:HEADER.size + 4])[0], 0)
        crc, suffix = struct.unpack(">2I", frame[-8:])
        self.assertEqual(suffix, SUFFIX)
        self.assertEqual(crc, binascii.crc32(frame[:-8]) & 0xFFFFFFFF)


class Tuya33SocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_virtual_device_accepts_fragmented_real_tcp_request(self):
        key = b"0123456789abcdef"
        request_json = json.dumps(
            {"gwId": "virtual", "devId": "virtual", "uid": "virtual", "t": "1"},
            separators=(",", ":"),
        ).encode()
        encrypted = aes_ecb_encrypt(key, request_json)
        head = HEADER.pack(PREFIX, 11, DP_QUERY, len(encrypted) + 8)
        partial = head + encrypted
        crc = binascii.crc32(partial) & 0xFFFFFFFF
        request = partial + struct.pack(">2I", crc, SUFFIX)

        async with VirtualTuya33Device(
            key.decode(), dps={"1": True}, response_chunks=3
        ) as device:
            reader, writer = await asyncio.open_connection(device.host, device.port)
            for part in (request[:5], request[5:17], request[17:]):
                writer.write(part)
                await writer.drain()
            response_head = await reader.readexactly(HEADER.size)
            _prefix, _seqno, _cmd, length = HEADER.unpack(response_head)
            await reader.readexactly(length)
            writer.close()
            await writer.wait_closed()
            self.assertEqual(device.transcript.status_queries, 1)
