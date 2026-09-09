from __future__ import annotations

import asyncio
import importlib.util
import os
import struct
import sys
import unittest
from pathlib import Path


def load_pytuya():
    repo = os.environ.get("LOCALTUYA_ROOT")
    if not repo:
        raise unittest.SkipTest("LOCALTUYA_ROOT is not set")
    path = Path(repo) / "custom_components/localtuya/pytuya/__init__.py"
    spec = importlib.util.spec_from_file_location("localtuya_malformed_pytuya", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load LocalTuya pytuya")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MalformedWireTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_declared_frame_aborts_exchange_without_timeout(self):
        pytuya = load_pytuya()

        async def peer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                request = await reader.read(4096)
                if len(request) < 16:
                    return
                _prefix, seqno, cmd, _length = struct.unpack(">4I", request[:16])
                # 1001 exceeds LocalTuya's accepted 55AA payload limit. A hostile
                # or corrupted peer must wake the pending request immediately.
                writer.write(struct.pack(">4I", 0x000055AA, seqno, cmd, 1001))
                await writer.drain()
                await asyncio.sleep(0.1)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

        server = await asyncio.start_server(peer, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        protocol = await pytuya.connect(
            "127.0.0.1",
            "malformed-wire-device",
            "0123456789abcdef",
            "3.3",
            False,
            port=port,
            timeout=1,
        )
        try:
            try:
                async with asyncio.timeout(0.5):
                    await protocol.status()
            except TimeoutError as exc:
                self.fail(f"malformed frame left exchange waiting for timeout: {exc}")
            except pytuya.DecodeError:
                pass
        finally:
            await protocol.close()
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
