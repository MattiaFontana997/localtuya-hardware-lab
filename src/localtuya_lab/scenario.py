"""Scenario schema and loader for the virtual hardware lab."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .emulator import TcpAction


@dataclass(slots=True, frozen=True)
class Scenario:
    name: str
    protocol: str
    transport: str
    actions: tuple[TcpAction, ...]
    child_cids: tuple[str, ...] = ()
    expected: tuple[str, ...] = ()


class ScenarioLoader:
    @staticmethod
    def load(path: str | Path) -> Scenario:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("scenario root must be an object")
        name = _required_string(raw, "name")
        protocol = _required_string(raw, "protocol")
        transport = _required_string(raw, "transport")
        if transport not in {"direct", "gateway_child", "wire_fault"}:
            raise ValueError("unsupported transport")
        raw_actions = raw.get("actions", [])
        if not isinstance(raw_actions, list):
            raise ValueError("actions must be a list")
        actions = tuple(_parse_action(item) for item in raw_actions)
        cids = tuple(str(v) for v in raw.get("child_cids", []) if str(v).strip())
        expected = tuple(str(v) for v in raw.get("expected", []) if str(v).strip())
        return Scenario(name, protocol, transport, actions, cids, expected)


def _parse_action(raw: Any) -> TcpAction:
    if not isinstance(raw, dict):
        raise ValueError("action must be an object")
    kind = _required_string(raw, "kind")
    delay = float(raw.get("delay", 0.0) or 0.0)
    if delay < 0 or delay > 30:
        raise ValueError("action delay out of bounds")
    if kind == "send":
        return TcpAction(kind="send", payload=_hex(raw.get("hex", "")), delay=delay)
    if kind == "send_chunks":
        chunks = raw.get("chunks", [])
        if not isinstance(chunks, list) or not chunks:
            raise ValueError("send_chunks requires chunks")
        return TcpAction(
            kind="send_chunks",
            chunks=tuple(_hex(value) for value in chunks),
            delay=delay,
        )
    if kind in {"delay", "close", "abort"}:
        return TcpAction(kind=kind, delay=delay)
    raise ValueError(f"unsupported action kind: {kind}")


def _hex(value: Any) -> bytes:
    text = str(value or "").replace(" ", "").replace("\n", "")
    if len(text) % 2:
        raise ValueError("hex payload must have an even number of characters")
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise ValueError("invalid hex payload") from exc


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = str(raw.get(key, "") or "").strip()
    if not value:
        raise ValueError(f"missing {key}")
    return value
