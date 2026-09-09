from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localtuya_lab.emulator import ScriptedTcpPeer
from localtuya_lab.scenario import ScenarioLoader


async def main(path: str) -> int:
    scenario = ScenarioLoader.load(path)
    peer = ScriptedTcpPeer(list(scenario.actions))
    async with peer:
        print(json.dumps({
            "scenario": scenario.name,
            "protocol": scenario.protocol,
            "transport": scenario.transport,
            "host": peer.host,
            "port": peer.port,
            "child_count": len(scenario.child_cids),
            "expected": list(scenario.expected),
        }, indent=2))
        await asyncio.sleep(0.05)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_lab.py <scenario.json>")
    raise SystemExit(asyncio.run(main(sys.argv[1])))
