"""Independent Tuya protocol 3.1 virtual device over a real TCP socket."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .tuya33 import (
    CONTROL,
    DP_QUERY,
    HEADER,
    PREFIX,
    TuyaRequest,
    aes_ecb_decrypt,
    build_55aa_response,
    parse_55aa_request,
)


@dataclass(slots=True)
class Tuya31Transcript:
    connections: int = 0
    requests: int = 0
    status_queries: int = 0
    controls: int = 0
    seen_commands: list[int] = field(default_factory=list)


def decode_31_request(local_key: bytes, request: TuyaRequest) -> dict[str, Any]:
    payload = request.payload
    if request.cmd == CONTROL:
        if not payload.startswith(b"3.1") or len(payload) < 19:
            raise ValueError("invalid Tuya 3.1 CONTROL prefix")
        signature = payload[3:19]
        encrypted_b64 = payload[19:]
        expected = hashlib.md5(  # noqa: S324 - protocol compatibility only
            b"data=" + encrypted_b64 + b"||lpv=3.1||" + local_key
        ).hexdigest()[8:24].encode("ascii")
        if signature != expected:
            raise ValueError("invalid Tuya 3.1 MD5 slice")
        encrypted = base64.b64decode(encrypted_b64)
        raw = aes_ecb_decrypt(local_key, encrypted)
    else:
        raw = payload
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Tuya 3.1 request JSON must be an object")
    return value


class VirtualTuya31Device:
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
        self.transcript = Tuya31Transcript()
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("virtual device is not running")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> "VirtualTuya31Device":
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

    async def __aenter__(self) -> "VirtualTuya31Device":
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
                while len(buffer) >= HEADER.size:
                    prefix, _seq, _cmd, length = HEADER.unpack_from(buffer)
                    if prefix != PREFIX:
                        raise ValueError("client sent non-55AA frame to 3.1 device")
                    total = HEADER.size + length
                    if len(buffer) < total:
                        break
                    frame, buffer = buffer[:total], buffer[total:]
                    await self._process(parse_55aa_request(frame), writer)
        finally:
            self._writers.discard(writer)
            if not writer.is_closing():
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

    async def _process(self, request: TuyaRequest, writer: asyncio.StreamWriter) -> None:
        self.transcript.requests += 1
        self.transcript.seen_commands.append(request.cmd)
        value = decode_31_request(self.local_key, request)
        if request.cmd == DP_QUERY:
            self.transcript.status_queries += 1
        elif request.cmd == CONTROL:
            self.transcript.controls += 1
            changes = value.get("dps", {})
            if isinstance(changes, dict):
                self.dps.update({str(k): v for k, v in changes.items()})
        response = json.dumps({"dps": dict(self.dps)}, separators=(",", ":")).encode()
        frame = build_55aa_response(request.seqno, request.cmd, response)
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
