"""Independent Tuya 3.5/6699 virtual hardware, including gateway child routing."""
from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import struct
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = 0x00006699
SUFFIX = b"\x00\x00\x99\x66"
HEADER = struct.Struct(">IHIII")
RET = struct.Struct(">I")
SESS_START = 0x03
SESS_RESP = 0x04
SESS_FINISH = 0x05
HEARTBEAT = 0x09
STATUS = 0x08
DP_QUERY_NEW = 0x10
CONTROL_NEW = 0x0D
VERSION_HEADER = b"3.5" + (12 * b"\x00")


@dataclass(slots=True, frozen=True)
class GcmRequest:
    seqno: int
    cmd: int
    payload: bytes


@dataclass(slots=True)
class Tuya35Transcript:
    connections: int = 0
    handshakes: int = 0
    queries: int = 0
    controls: int = 0
    heartbeats: int = 0
    fallback_rejections: int = 0
    seen_cids: list[str] = field(default_factory=list)
    seen_commands: list[int] = field(default_factory=list)


def parse_6699_request(frame: bytes, key: bytes) -> GcmRequest:
    if len(frame) < HEADER.size + 28 + len(SUFFIX):
        raise ValueError("short Tuya 3.5 frame")
    prefix, _unknown, seqno, cmd, length = HEADER.unpack_from(frame)
    if prefix != PREFIX:
        raise ValueError("invalid Tuya 3.5 prefix")
    total = HEADER.size + length + len(SUFFIX)
    if len(frame) != total:
        raise ValueError("Tuya 3.5 frame length mismatch")
    body_end = HEADER.size + length
    if frame[body_end:total] != SUFFIX:
        raise ValueError("invalid Tuya 3.5 suffix")
    body = frame[HEADER.size:body_end]
    iv, encrypted = body[:12], body[12:]
    plaintext = AESGCM(key).decrypt(iv, encrypted, frame[4:HEADER.size])
    return GcmRequest(seqno, cmd, plaintext)


def build_6699_response(
    seqno: int,
    cmd: int,
    payload: bytes,
    key: bytes,
    *,
    retcode: int = 0,
    iv: bytes | None = None,
) -> bytes:
    if iv is None:
        iv = secrets.token_bytes(12)
    plaintext = RET.pack(retcode) + payload
    length = 12 + len(plaintext) + 16
    header = HEADER.pack(PREFIX, 0, seqno, cmd, length)
    encrypted = AESGCM(key).encrypt(iv, plaintext, header[4:])
    return header + iv + encrypted + SUFFIX


def derive_35_session_key(real_key: bytes, local_nonce: bytes, remote_nonce: bytes) -> bytes:
    seed = bytes(a ^ b for a, b in zip(local_nonce, remote_nonce))
    return AESGCM(real_key).encrypt(local_nonce[:12], seed, None)[:16]


class VirtualTuya35Device:
    def __init__(
        self,
        local_key: str,
        *,
        dps: dict[str, Any] | None = None,
        children: dict[str, dict[str, Any]] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        response_chunks: int = 1,
        inter_chunk_delay: float = 0.0,
        require_explicit_dps_query: bool = False,
        remote_nonce: bytes = b"TUYA35-REMOTEKEY",
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
        self.require_explicit_dps_query = bool(require_explicit_dps_query)
        self.dps = {str(k): v for k, v in (dps or {"1": True}).items()}
        self.children = {
            str(cid): {str(k): v for k, v in child_dps.items()}
            for cid, child_dps in (children or {}).items()
        }
        self.transcript = Tuya35Transcript()
        self.corrupt_next_response = False
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._sessions: dict[asyncio.StreamWriter, dict[str, Any]] = {}
        self._session_ready = asyncio.Event()
        self._server_seq = 1000

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("virtual device is not running")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> "VirtualTuya35Device":
        self._server = await asyncio.start_server(self._handle_client, self.host, self.requested_port)
        return self

    async def stop(self) -> None:
        await self.drop_connections()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def drop_connections(self) -> None:
        for writer in list(self._writers):
            transport = writer.transport
            if transport is not None:
                transport.abort()
        await asyncio.sleep(0)

    async def wait_for_session(self, timeout: float = 2.0) -> None:
        await asyncio.wait_for(self._session_ready.wait(), timeout=timeout)

    async def __aenter__(self) -> "VirtualTuya35Device":
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.transcript.connections += 1
        self._writers.add(writer)
        state = {"local_nonce": None, "session_key": None, "ready": False, "fallback_rejected": False}
        self._sessions[writer] = state
        buffer = b""
        try:
            while not reader.at_eof():
                data = await reader.read(65536)
                if not data:
                    break
                buffer += data
                while len(buffer) >= HEADER.size:
                    prefix, _unknown, _seq, cmd, length = HEADER.unpack_from(buffer)
                    if prefix != PREFIX:
                        raise ValueError("client sent non-6699 frame to 3.5 device")
                    total = HEADER.size + length + len(SUFFIX)
                    if len(buffer) < total:
                        break
                    frame, buffer = buffer[:total], buffer[total:]
                    key = self.real_key if cmd in {SESS_START, SESS_FINISH} or not state["ready"] else state["session_key"]
                    request = parse_6699_request(frame, key)
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

    async def _process(self, request: GcmRequest, writer: asyncio.StreamWriter, state: dict[str, Any]) -> None:
        self.transcript.seen_commands.append(request.cmd)
        if request.cmd == SESS_START:
            local_nonce = request.payload
            if len(local_nonce) != 16:
                raise ValueError("invalid 3.5 client nonce")
            state["local_nonce"] = local_nonce
            state["session_key"] = derive_35_session_key(self.real_key, local_nonce, self.remote_nonce)
            proof = hmac.new(self.real_key, local_nonce, sha256).digest()
            response = build_6699_response(request.seqno, SESS_RESP, self.remote_nonce + proof, self.real_key)
            await self._send(writer, response)
            return

        if request.cmd == SESS_FINISH:
            expected = hmac.new(self.real_key, self.remote_nonce, sha256).digest()
            if not hmac.compare_digest(request.payload, expected):
                raise ValueError("invalid 3.5 finish proof")
            state["ready"] = True
            self.transcript.handshakes += 1
            self._session_ready.set()
            return

        if not state["ready"]:
            raise ValueError("3.5 data command before session negotiation")
        session_key: bytes = state["session_key"]
        if request.cmd == HEARTBEAT:
            self.transcript.heartbeats += 1
            await self._send_response(writer, request.seqno, request.cmd, b"", session_key)
            return

        raw = request.payload
        if raw.startswith(VERSION_HEADER):
            raw = raw[len(VERSION_HEADER):]
        value = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(value, dict):
            raise ValueError("3.5 request JSON must be an object")
        cid = value.get("cid")
        nested = value.get("data")
        if not cid and isinstance(nested, dict):
            cid = nested.get("cid")
        if cid:
            cid = str(cid)
            self.transcript.seen_cids.append(cid)

        if request.cmd == DP_QUERY_NEW:
            self.transcript.queries += 1
            explicit = isinstance(nested, dict) and isinstance(nested.get("dps"), dict)
            if self.require_explicit_dps_query and not state["fallback_rejected"] and not cid and not explicit:
                state["fallback_rejected"] = True
                self.transcript.fallback_rejections += 1
                await self._send_response(writer, request.seqno, request.cmd, b"data unvalid", session_key)
                return
        elif request.cmd == CONTROL_NEW:
            self.transcript.controls += 1
            changes = nested.get("dps", {}) if isinstance(nested, dict) else {}
            target = self.children.setdefault(cid, {}) if cid else self.dps
            if isinstance(changes, dict):
                target.update({str(k): v for k, v in changes.items()})

        target = self.children.get(cid, {}) if cid else self.dps
        if cid:
            response_obj = {"data": {"cid": cid, "dps": dict(target)}}
        else:
            response_obj = {"dps": dict(target)}
        response = json.dumps(response_obj, separators=(",", ":")).encode()
        await self._send_response(writer, request.seqno, request.cmd, response, session_key)

    async def _send_response(self, writer: asyncio.StreamWriter, seqno: int, cmd: int, payload: bytes, key: bytes) -> None:
        frame = build_6699_response(seqno, cmd, payload, key)
        if self.corrupt_next_response:
            self.corrupt_next_response = False
            pos = HEADER.size + 13
            frame = frame[:pos] + bytes([frame[pos] ^ 0x01]) + frame[pos + 1:]
        await self._send(writer, frame)

    async def send_unsolicited(self, cid: str, dps: dict[str, Any]) -> None:
        await self.wait_for_session()
        writers = [w for w in self._writers if not w.is_closing()]
        if not writers:
            raise RuntimeError("no active gateway connection")
        writer = writers[0]
        state = self._sessions[writer]
        payload = json.dumps(
            {"data": {"cid": str(cid), "dps": {str(k): v for k, v in dps.items()}}},
            separators=(",", ":"),
        ).encode()
        self._server_seq += 1
        await self._send_response(writer, self._server_seq, STATUS, payload, state["session_key"])

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
