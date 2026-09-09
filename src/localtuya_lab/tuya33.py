"""Independent Tuya 3.3 virtual device codec and real TCP peer.

This module deliberately does not import LocalTuya/pytuya. It implements the
minimum 3.3 LAN wire behaviour needed for black-box interoperability tests.
"""

from __future__ import annotations

import asyncio
import binascii
import json
import struct
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PREFIX = 0x000055AA
SUFFIX = 0x0000AA55
HEADER = struct.Struct(">4I")
END = struct.Struct(">2I")
DP_QUERY = 0x0A
CONTROL = 0x07
PROTOCOL_33_HEADER = b"3.3" + (12 * b"\x00")


@dataclass(slots=True, frozen=True)
class TuyaRequest:
    seqno: int
    cmd: int
    payload: bytes


@dataclass(slots=True)
class Tuya33Transcript:
    connections: int = 0
    requests: int = 0
    status_queries: int = 0
    controls: int = 0
    seen_commands: list[int] = field(default_factory=list)


def _pkcs7_pad(data: bytes) -> bytes:
    pad = 16 - (len(data) % 16)
    return data + bytes([pad]) * pad


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise ValueError("empty AES payload")
    pad = data[-1]
    if pad < 1 or pad > 16 or data[-pad:] != bytes([pad]) * pad:
        raise ValueError("invalid PKCS#7 padding")
    return data[:-pad]


def aes_ecb_encrypt(key: bytes, plaintext: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    padded = _pkcs7_pad(plaintext)
    return encryptor.update(padded) + encryptor.finalize()


def aes_ecb_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    if not ciphertext or len(ciphertext) % 16:
        raise ValueError("invalid AES ciphertext length")
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    return _pkcs7_unpad(padded)


def parse_55aa_request(frame: bytes) -> TuyaRequest:
    """Parse a client request frame (requests do not carry a retcode)."""
    if len(frame) < HEADER.size + END.size:
        raise ValueError("short 55AA frame")
    prefix, seqno, cmd, length = HEADER.unpack_from(frame)
    if prefix != PREFIX:
        raise ValueError("invalid 55AA prefix")
    total = HEADER.size + length
    if len(frame) != total:
        raise ValueError("55AA frame length mismatch")
    body = frame[HEADER.size:total]
    payload = body[:-END.size]
    crc, suffix = END.unpack(body[-END.size:])
    if suffix != SUFFIX:
        raise ValueError("invalid 55AA suffix")
    expected_crc = binascii.crc32(frame[:-END.size]) & 0xFFFFFFFF
    if crc != expected_crc:
        raise ValueError("invalid 55AA CRC")
    return TuyaRequest(seqno=seqno, cmd=cmd, payload=payload)


def build_55aa_response(seqno: int, cmd: int, payload: bytes, *, retcode: int = 0) -> bytes:
    """Build a device response. Device responses include a 4-byte retcode."""
    body = struct.pack(">I", retcode) + payload
    length = len(body) + END.size
    head = HEADER.pack(PREFIX, seqno, cmd, length)
    partial = head + body
    crc = binascii.crc32(partial) & 0xFFFFFFFF
    return partial + END.pack(crc, SUFFIX)


def encode_json_payload(local_key: bytes, value: dict[str, Any]) -> bytes:
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return aes_ecb_encrypt(local_key, raw)


def decode_request_json(local_key: bytes, request: TuyaRequest) -> dict[str, Any]:
    payload = request.payload
    if request.cmd == CONTROL and payload.startswith(PROTOCOL_33_HEADER):
        payload = payload[len(PROTOCOL_33_HEADER):]
    decoded = aes_ecb_decrypt(local_key, payload)
    value = json.loads(decoded.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Tuya request JSON must be an object")
    return value


class VirtualTuya33Device:
    """Minimal stateful protocol-3.3 device on a real asyncio TCP socket."""

    def __init__(
        self,
        local_key: str,
        *,
        dps: dict[str, Any] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        response_chunks: int = 1,
        inter_chunk_delay: float = 0.0,
    ) -> None:
        key = local_key.encode("latin1")
        if len(key) not in {16, 24, 32}:
            raise ValueError("AES local key must be 16, 24 or 32 bytes")
        self.local_key = key
        self.host = host
        self.requested_port = port
        self.dps = {str(k): v for k, v in (dps or {"1": True}).items()}
        self.response_chunks = max(1, int(response_chunks))
        self.inter_chunk_delay = max(0.0, float(inter_chunk_delay))
        self.transcript = Tuya33Transcript()
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("virtual device is not running")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> "VirtualTuya33Device":
        self._server = await asyncio.start_server(self._handle_client, self.host, self.requested_port)
        return self

    async def stop(self) -> None:
        for writer in list(self._writers):
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        self._writers.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> "VirtualTuya33Device":
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.transcript.connections += 1
        self._writers.add(writer)
        buffer = b""
        try:
            while not reader.at_eof():
                chunk = await reader.read(65536)
                if not chunk:
                    break
                buffer += chunk
                while True:
                    if len(buffer) < HEADER.size:
                        break
                    prefix, _seqno, _cmd, length = HEADER.unpack_from(buffer)
                    if prefix != PREFIX:
                        raise ValueError("client sent non-55AA frame to 3.3 device")
                    total = HEADER.size + length
                    if len(buffer) < total:
                        break
                    frame, buffer = buffer[:total], buffer[total:]
                    await self._process_frame(parse_55aa_request(frame), writer)
        finally:
            self._writers.discard(writer)
            if not writer.is_closing():
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

    async def _process_frame(self, request: TuyaRequest, writer: asyncio.StreamWriter) -> None:
        self.transcript.requests += 1
        self.transcript.seen_commands.append(request.cmd)
        request_json = decode_request_json(self.local_key, request)

        if request.cmd == DP_QUERY:
            self.transcript.status_queries += 1
            response_json = {"dps": dict(self.dps)}
        elif request.cmd == CONTROL:
            self.transcript.controls += 1
            changes = request_json.get("dps", {})
            if isinstance(changes, dict):
                self.dps.update({str(k): v for k, v in changes.items()})
            response_json = {"dps": dict(self.dps)}
        else:
            response_json = {}

        encrypted = encode_json_payload(self.local_key, response_json)
        response = build_55aa_response(request.seqno, request.cmd, encrypted)
        await self._send_response(writer, response)

    async def _send_response(self, writer: asyncio.StreamWriter, payload: bytes) -> None:
        parts = _split(payload, self.response_chunks)
        for part in parts:
            writer.write(part)
            await writer.drain()
            if self.inter_chunk_delay:
                await asyncio.sleep(self.inter_chunk_delay)


def _split(payload: bytes, count: int) -> tuple[bytes, ...]:
    if count <= 1 or len(payload) <= 1:
        return (payload,)
    count = min(count, len(payload))
    base, extra = divmod(len(payload), count)
    result: list[bytes] = []
    index = 0
    for pos in range(count):
        size = base + (1 if pos < extra else 0)
        result.append(payload[index:index + size])
        index += size
    return tuple(result)
