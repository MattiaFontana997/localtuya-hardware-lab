"""Independent Tuya 3.2/device22-style virtual device."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from .tuya33 import (
    CONTROL,
    DP_QUERY,
    HEADER,
    PREFIX,
    aes_ecb_decrypt,
    build_55aa_response,
    encode_json_payload,
    parse_55aa_request,
)

CONTROL_NEW = 0x0D
PROTOCOL_32_HEADER = b"3.2" + (12 * b"\x00")


@dataclass(slots=True)
class Tuya32Transcript:
    connections: int = 0
    queries: int = 0
    controls: int = 0
    incompatible_queries: int = 0
    seen_commands: list[int] = field(default_factory=list)


class VirtualTuya32Device:
    """3.2 target that requires the type_0d CONTROL_NEW query shape."""

    def __init__(
        self,
        local_key: str,
        *,
        dps: dict[str, Any] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        response_chunks: int = 1,
    ) -> None:
        self.local_key = local_key.encode("latin1")
        self.dps = {str(k): v for k, v in (dps or {"1": True}).items()}
        self.host = host
        self.requested_port = port
        self.response_chunks = max(1, int(response_chunks))
        self.transcript = Tuya32Transcript()
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("virtual device is not running")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self):
        self._server = await asyncio.start_server(self._handle, self.host, self.requested_port)
        return self

    async def stop(self):
        for writer in list(self._writers):
            writer.close()
        self._writers.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb):
        await self.stop()

    async def _handle(self, reader, writer):
        self.transcript.connections += 1
        self._writers.add(writer)
        buffer = b""
        try:
            while not reader.at_eof():
                data = await reader.read(65536)
                if not data:
                    break
                buffer += data
                while len(buffer) >= HEADER.size:
                    prefix, _seq, _cmd, length = HEADER.unpack_from(buffer)
                    if prefix != PREFIX:
                        return
                    total = HEADER.size + length
                    if len(buffer) < total:
                        break
                    frame, buffer = buffer[:total], buffer[total:]
                    try:
                        await self._process(parse_55aa_request(frame), writer)
                    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                        return
        finally:
            self._writers.discard(writer)
            writer.close()

    async def _process(self, request, writer):
        self.transcript.seen_commands.append(request.cmd)
        if request.cmd == DP_QUERY:
            # A 3.3/default probe must not accidentally validate a 3.2 device.
            self.transcript.incompatible_queries += 1
            encrypted = encode_json_payload(self.local_key, {"error": "data unvalid"})
            await self._send(writer, build_55aa_response(request.seqno, request.cmd, encrypted))
            return

        payload = request.payload
        if payload.startswith(PROTOCOL_32_HEADER):
            payload = payload[len(PROTOCOL_32_HEADER):]
        decoded = aes_ecb_decrypt(self.local_key, payload)
        value = json.loads(decoded.decode("utf-8"))

        if request.cmd == CONTROL_NEW:
            requested = value.get("dps") if isinstance(value, dict) else None
            if not isinstance(requested, dict):
                raise ValueError("3.2 query requires explicit DPS map")
            self.transcript.queries += 1
        elif request.cmd == CONTROL:
            self.transcript.controls += 1
            changes = value.get("dps", {}) if isinstance(value, dict) else {}
            if isinstance(changes, dict):
                self.dps.update({str(k): v for k, v in changes.items()})
        else:
            raise ValueError("unsupported 3.2 command")

        encrypted = encode_json_payload(self.local_key, {"dps": dict(self.dps)})
        await self._send(writer, build_55aa_response(request.seqno, request.cmd, encrypted))

    async def _send(self, writer, frame: bytes):
        count = min(self.response_chunks, len(frame))
        base, extra = divmod(len(frame), count)
        offset = 0
        for index in range(count):
            size = base + (1 if index < extra else 0)
            writer.write(frame[offset:offset + size])
            await writer.drain()
            offset += size
