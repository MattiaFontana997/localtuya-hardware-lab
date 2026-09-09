"""Independent builders for Tuya UDP discovery announcements."""
from __future__ import annotations

import binascii
import hashlib
import json
import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .tuya33 import aes_ecb_encrypt

UDP_KEY = hashlib.md5(b"yGAdlopoPVldABfn").digest()  # noqa: S324 - Tuya protocol constant
PREFIX_55AA = 0x000055AA
SUFFIX_55AA = 0x0000AA55
PREFIX_6699 = 0x00006699
SUFFIX_6699 = b"\x00\x00\x99\x66"
HEADER_55AA = struct.Struct(">4I")
HEADER_6699 = struct.Struct(">IHIII")
END_55AA = struct.Struct(">2I")


def discovery_json(device_id: str, version: str, *, ip: str | None = None) -> bytes:
    value = {"gwId": device_id, "version": version, "productKey": f"product-{version}"}
    if ip is not None:
        value["ip"] = ip
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def build_plain_announcement(device_id: str, version: str = "3.1") -> bytes:
    return discovery_json(device_id, version)


def build_55aa_encrypted_announcement(device_id: str, version: str = "3.3") -> bytes:
    encrypted = aes_ecb_encrypt(UDP_KEY, discovery_json(device_id, version))
    header = HEADER_55AA.pack(PREFIX_55AA, 1, 0x13, len(encrypted) + END_55AA.size)
    partial = header + encrypted
    crc = binascii.crc32(partial) & 0xFFFFFFFF
    return partial + END_55AA.pack(crc, SUFFIX_55AA)


def build_6699_announcement(
    device_id: str,
    version: str = "3.5",
    *,
    iv: bytes | None = None,
) -> bytes:
    if iv is None:
        iv = os.urandom(12)
    if len(iv) != 12:
        raise ValueError("UDP GCM IV must be 12 bytes")
    payload = discovery_json(device_id, version)
    length = 12 + len(payload) + 16
    header = HEADER_6699.pack(PREFIX_6699, 0, 1, 0x13, length)
    encrypted = AESGCM(UDP_KEY).encrypt(iv, payload, header[4:])
    return header + iv + encrypted + SUFFIX_6699
