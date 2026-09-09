from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localtuya_lab.emulator import ScriptedTcpPeer, TcpAction


class EmulatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_peer_uses_real_socket_and_records_transcript(self):
        async with ScriptedTcpPeer([TcpAction("send", payload=b"pong")]) as peer:
            reader, writer = await asyncio.open_connection(peer.host, peer.port)
            writer.write(b"ping")
            await writer.drain()
            self.assertEqual(await reader.readexactly(4), b"pong")
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0)
            self.assertEqual(peer.transcript.connections, 1)
            self.assertEqual(peer.transcript.received, [b"ping"])

    async def test_fragmented_response_is_sent_as_independent_tcp_writes(self):
        chunks = (b"ab", b"cd", b"ef")
        async with ScriptedTcpPeer([TcpAction("send_chunks", chunks=chunks)]) as peer:
            reader, writer = await asyncio.open_connection(peer.host, peer.port)
            writer.write(b"request")
            await writer.drain()
            self.assertEqual(await reader.readexactly(6), b"abcdef")
            writer.close()
            await writer.wait_closed()
            self.assertEqual(peer.transcript.sent, list(chunks))

    async def test_abort_simulates_connection_reset(self):
        async with ScriptedTcpPeer([TcpAction("abort")]) as peer:
            reader, writer = await asyncio.open_connection(peer.host, peer.port)
            writer.write(b"request")
            await writer.drain()
            await asyncio.sleep(0.02)
            self.assertEqual(await reader.read(), b"")
            writer.close()
