"""Real-socket scripted peers used to emulate hostile and normal Tuya LAN behaviour."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

ActionKind = Literal["send", "send_chunks", "delay", "close", "abort"]


@dataclass(slots=True, frozen=True)
class TcpAction:
    """One action executed after the peer receives data from the client."""

    kind: ActionKind
    payload: bytes = b""
    chunks: tuple[bytes, ...] = ()
    delay: float = 0.0


@dataclass(slots=True)
class PeerTranscript:
    """Bounded transcript containing byte counts and raw bytes for local assertions."""

    connections: int = 0
    received: list[bytes] = field(default_factory=list)
    sent: list[bytes] = field(default_factory=list)
    closes: int = 0


class ScriptedTcpPeer:
    """A deterministic TCP peer backed by a real asyncio listening socket.

    Actions are consumed once per received read. This deliberately exposes the
    client to real TCP behaviour: the client cannot assume one write == one read.
    """

    def __init__(
        self,
        actions: list[TcpAction] | None = None,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        read_size: int = 65536,
    ) -> None:
        self.host = host
        self.requested_port = port
        self.read_size = read_size
        self._actions = list(actions or [])
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self.transcript = PeerTranscript()

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("peer is not running")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> "ScriptedTcpPeer":
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.host,
            port=self.requested_port,
        )
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

    async def __aenter__(self) -> "ScriptedTcpPeer":
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def broadcast(self, payload: bytes, *, chunks: int = 1, delay: float = 0.0) -> None:
        """Send unsolicited traffic to every currently connected client."""
        parts = _split_bytes(payload, chunks)
        for writer in list(self._writers):
            for part in parts:
                writer.write(part)
                await writer.drain()
                self.transcript.sent.append(part)
                if delay:
                    await asyncio.sleep(delay)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self.transcript.connections += 1
        self._writers.add(writer)
        try:
            while not reader.at_eof():
                data = await reader.read(self.read_size)
                if not data:
                    break
                self.transcript.received.append(data)
                if not self._actions:
                    continue
                action = self._actions.pop(0)
                await self._execute(action, writer)
                if action.kind in {"close", "abort"}:
                    break
        finally:
            self._writers.discard(writer)
            if not writer.is_closing():
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

    async def _execute(self, action: TcpAction, writer: asyncio.StreamWriter) -> None:
        if action.delay:
            await asyncio.sleep(action.delay)
        if action.kind == "delay":
            return
        if action.kind == "send":
            writer.write(action.payload)
            await writer.drain()
            self.transcript.sent.append(action.payload)
            return
        if action.kind == "send_chunks":
            for chunk in action.chunks:
                writer.write(chunk)
                await writer.drain()
                self.transcript.sent.append(chunk)
                if action.delay:
                    await asyncio.sleep(action.delay)
            return
        if action.kind == "abort":
            writer.transport.abort()
            self.transcript.closes += 1
            return
        if action.kind == "close":
            writer.close()
            self.transcript.closes += 1
            return
        raise ValueError(f"unsupported action: {action.kind}")


def _split_bytes(payload: bytes, chunks: int) -> tuple[bytes, ...]:
    if chunks <= 1 or len(payload) <= 1:
        return (payload,)
    chunks = min(chunks, len(payload))
    base, extra = divmod(len(payload), chunks)
    result = []
    index = 0
    for i in range(chunks):
        size = base + (1 if i < extra else 0)
        result.append(payload[index : index + size])
        index += size
    return tuple(result)
