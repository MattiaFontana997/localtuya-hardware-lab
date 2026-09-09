"""Privacy-safe certification report helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class LabResult:
    scenario: str
    localtuya_sha: str
    protocol: str
    transport: str
    status: str
    checks: tuple[str, ...]

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["checks"] = list(self.checks)
        return data


PRIVATE_KEYS = {
    "device_id",
    "host",
    "ip",
    "local_key",
    "token",
    "access_token",
    "refresh_token",
    "user_id",
    "uid",
}


def assert_privacy_safe(value: Any) -> None:
    """Recursively reject private field names before a report is published."""
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() in PRIVATE_KEYS:
                raise ValueError(f"private report field: {key}")
            assert_privacy_safe(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            assert_privacy_safe(child)
