from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localtuya_lab.tuya31 import VirtualTuya31Device
from localtuya_lab.tuya34 import DP_QUERY_NEW as QUERY34, parse_hmac_request
from localtuya_lab.tuya35 import DP_QUERY_NEW as QUERY35, derive_35_session_key, parse_6699_request, VirtualTuya35Device
from localtuya_lab.tuya34 import VirtualTuya34Device
from localtuya_lab.tuya33 import aes_ecb_encrypt


class ProtocolCodecTests(unittest.TestCase):
    def test_34_hmac_request_parser_rejects_tamper(self):
        key = b"0123456789abcdef"
        import hmac
        from hashlib import sha256
        from localtuya_lab.tuya34 import HEADER, END_HMAC, PREFIX, SUFFIX

        payload = aes_ecb_encrypt(key, b"{}")
        head = HEADER.pack(PREFIX, 7, QUERY34, len(payload) + END_HMAC.size)
        partial = head + payload
        frame = partial + END_HMAC.pack(hmac.new(key, partial, sha256).digest(), SUFFIX)
        req = parse_hmac_request(frame, key)
        self.assertEqual(req.seqno, 7)
        self.assertEqual(req.cmd, QUERY34)
        broken = bytearray(frame)
        broken[-10] ^= 1
        with self.assertRaises(ValueError):
            parse_hmac_request(bytes(broken), key)

    def test_35_gcm_request_parser_and_session_derivation(self):
        key = b"0123456789abcdef"
        local = b"LOCAL-NONCE-1234"
        remote = b"REMOTE-NONCE1234"
        self.assertEqual(len(derive_35_session_key(key, local, remote)), 16)

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from localtuya_lab.tuya35 import HEADER, PREFIX, SUFFIX

        payload = b"{}"
        iv = b"123456789012"
        length = 12 + len(payload) + 16
        head = HEADER.pack(PREFIX, 0, 9, QUERY35, length)
        enc = AESGCM(key).encrypt(iv, payload, head[4:])
        frame = head + iv + enc + SUFFIX
        req = parse_6699_request(frame, key)
        self.assertEqual(req.seqno, 9)
        self.assertEqual(req.payload, payload)
        tampered = bytearray(frame)
        tampered[-6] ^= 1
        with self.assertRaises(Exception):
            parse_6699_request(bytes(tampered), key)

    def test_device_classes_bind_real_tcp_ports(self):
        async def run():
            async with VirtualTuya31Device("0123456789abcdef") as d31:
                self.assertGreater(d31.port, 0)
            async with VirtualTuya34Device("0123456789abcdef") as d34:
                self.assertGreater(d34.port, 0)
            async with VirtualTuya35Device("0123456789abcdef") as d35:
                self.assertGreater(d35.port, 0)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
