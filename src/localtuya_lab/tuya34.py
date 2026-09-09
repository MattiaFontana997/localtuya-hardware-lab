"""Independent Tuya protocol 3.4 virtual device with real session negotiation."""
from __future__ import annotations

import asyncio
import hmac
import json
import struct
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from .tuya33 import aes_ecb_decrypt, aes_ecb_encrypt

PREFIX = 0x000055AA
SUFFIX = 0x0000AA55
HEADER = struct.Struct(">4I")
END_HMAC = struct.Struct(">32sI")
RET = struct.Struct(">I")
SESS_START = 0x03
SESS_RESP = 0x04
SESS_FINISH = 0x05
HEARTBEAT = 0x09
DP_QUERY_NEW = 0x10
CONTROL_NEW = 0x0D
VERSION_HEADER = b"3.4" + (12 * b"\x00")


@dataclass(slots=True, frozen=True)
class SecureRequest:
    seqno: int
    cmd: int
    payload: bytes


@dataclass(slots=True)
class Tuya34Transcript:
    connections: int = 0
    handshakes: int = 0
    queries: int = 0
    controls: int = 0
    heartbeats: int = 0
    seen_commands: list[int] = field(default_factory=list)


def parse_hmac_request(frame: bytes, key: bytes) -> SecureRequest:
    if len(frame) < HEADER.size + END_HMAC.size:
        raise ValueError("short Tuya 3.4 frame")
    prefix, seqno, cmd, length = HEADER.unpack_from(frame)
    if prefix != PREFIX:
        raise ValueError("invalid Tuya 3.4 prefix")
    total = HEADER.size + length
    if len(frame) != total:
        raise ValueError("Tuya 3.4 frame length mismatch")
    payload_end = total - END_HMAC.size
    received_hmac, suffix = END_HMAC.unpack(frame[payload_end:])
    if suffix != SUFFIX:
        raise ValueError("invalid Tuya 3.4 suffix")
    expected = hmac.new(key, frame[:payload_end], sha256).digest()
    if not hmac.compare_digest(received_hmac, expected):
        raise ValueError("invalid Tuya 3.4 HMAC")
    return SecureRequest(seqno, cmd, frame[HEADER.size:payload_end])


def build_hmac_response(seqno: int, cmd: int, payload: bytes, key: bytes, *, retcode: int = 0) -> bytes:
    body = RET.pack(retcode) + payload
    length = len(body) + END_HMAC.size
    head = HEADER.pack(PREFIX, seqno, cmd, length)
    partial = head + body
    tag = hmac.new(key, partial, sha256).digest()
    return partial + END_HMAC.pack(tag, SUFFIX)


def _xor16(left: bytes, right: bytes) -> bytes:
    if len(left) != 16 or len(right) != 16:
        raise ValueError("session nonces must be 16 bytes")
    return bytes(a ^ b for a, b in zip(left, right))


def _aes_ecb_block_encrypt(key: bytes, block: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(block) + encryptor.finalize()


class VirtualTuya34Device:
    def __init__(
        self,
        local_key: str,
        *,
        dps: dict[str, Any] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        response_chunks: int = 1,
        inter_chunk_delay: float = 0.0,
        remote_nonce: bytes = b"TUYA34-REMOTEKEY",
    ) -> None:
        key = local_key.encode("latin1")
        if len(key) not in {16, 24, 32}:
            raise ValueError("AES local key must be 16, 24 or 32 bytes")
        if len(remote_nonce) != 16:
            raise ValueError("remote nonce must be 16 bytes")
        self.real_key = key
        self.remote_nonce = remote_nonce
        self.host = host
        self.requested_port = port
        self.response_chunks = max(1, int(response_chunks))
        self.inter_chunk_delay = max(0.0, float(inter_chunk_delay))
        self.dps = {str(k): v for k, v in (dps or {"1": True}).items()}
        self.transcript = Tuya34Transcript()
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._sessions: dict[asyncio.StreamWriter, dict[str, Any]] = {}
        self.corrupt_next_response = False

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("virtual device is not running")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> "VirtualTuya34Device":
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
        self._sessions.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> "VirtualTuya34Device":
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.transcript.connections += 1
        self._writers.add(writer)
        self._sessions[writer] = {"local_nonce": None, "session_key": None, "ready": False}
        buffer = b""
        try:
            while not reader.at_eof():
                data = await reader.read(65536)
                if not data:
                    break
                buffer += data
                while len(buffer) >= HEADER.size:
                    prefix, _seq, cmd, length = HEADER.unpack_from(buffer)
                    if prefix != PREFIX:
                        raise ValueError("client sent non-55AA frame to 3.4 device")
                    total = HEADER.size + length
                    if len(buffer) < total:
                        break
                    frame, buffer = buffer[:total], buffer[total:]
                    state = self._sessions[writer]
                    key = self.real_key if cmd in {SESS_START, SESS_FINISH} or not state["ready"] else state["session_key"]
                    request = parse_hmac_request(frame, key)
                    await self._process(request, writer, state)
        finally:
            self._sessions.pop(writer, None)
            self._writers.discard(writer)
            if not writer.is_closing():
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

    async def _process(self, request: SecureRequest, writer: asyncio.StreamWriter, state: dict[str, Any]) -> None:
        self.transcript.seen_commands.append(request.cmd)
        if request.cmd == SESS_START:
            local_nonce = aes_ecb_decrypt(self.real_key, request.payload)
            if len(local_nonce) != 16:
                raise ValueError("invalid 3.4 client nonce")
            state["local_nonce"] = local_nonce
            seed = _xor16(local_nonce, self.remote_nonce)
            state["session_key"] = _aes_ecb_block_encrypt(self.real_key, seed)
            proof = hmac.new(self.real_key, local_nonce, sha256).digest()
            encrypted = aes_ecb_encrypt(self.real_key, self.remote_nonce + proof)
            response = build_hmac_response(request.seqno, SESS_RESP, encrypted, self.real_key)
            await self._send(writer, response)
            return

        if request.cmd == SESS_FINISH:
            finish = aes_ecb_decrypt(self.real_key, request.payload)
            expected = hmac.new(self.real_key, self.remote_nonce, sha256).digest()
            if not hmac.compare_digest(finish, expected):
                raise ValueError("invalid 3.4 finish proof")
            state["ready"] = True
            self.transcript.handshakes += 1
            return

        if not state["ready"]:
            raise ValueError("3.4 data command before session negotiation")
        session_key: bytes = state["session_key"]
        if request.cmd == HEARTBEAT:
            self.transcript.heartbeats += 1
            await self._send(writer, build_hmac_response(request.seqno, request.cmd, b"", session_key))
            return

        raw = aes_ecb_decrypt(session_key, request.payload)
        if raw.startswith(VERSION_HEADER):
            raw = raw[len(VERSION_HEADER):]
        value = json.loads(raw.decode("utf-8"))
        if request.cmd == DP_QUERY_NEW:
            self.transcript.queries += 1
        elif request.cmd == CONTROL_NEW:
            self.transcript.controls += 1
            changes = value.get("data", {}).get("dps", {}) if isinstance(value, dict) else {}
            if isinstance(changes, dict):
                self.dps.update({str(k): v for k, v in changes.items()})

        response_json = json.dumps({"dps": dict(self.dps)}, separators=(",", ":")).encode()
        encrypted = aes_ecb_encrypt(session_key, response_json)
        frame = build_hmac_response(request.seqno, request.cmd, encrypted, session_key)
        if self.corrupt_next_response:
            self.corrupt_next_response = False
            frame = frame[:-37] + bytes([frame[-37] ^ 0x01]) + frame[-36:]
        await self._send(writer, frame)

    async def _send(self, writer: asyncio.StreamWriter, data: bytes) -> None:
        count = min(self.response_chunks, max(1, len(data)))
        base, extra = divmod(len(data), count)
        offset = 0
        for i in range(count):
            size = base + (1 if i < extra else 0)
            writer.write(data[offset:offset + size])
            await writer.drain()
            offset += size
            if self.inter_chunk_delay:
                await asyncio.sleep(self.inter_chunk_delay)
