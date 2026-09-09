"""Write a privacy-safe certification summary after all lab tests pass."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sha_path = ROOT / "reports" / "localtuya-sha.txt"
localtuya_sha = sha_path.read_text(encoding="utf-8").strip()
if len(localtuya_sha) != 40:
    raise SystemExit("invalid LocalTuya SHA")

report = {
    "schema_version": 1,
    "result": "pass",
    "localtuya_sha": localtuya_sha,
    "hardware_lab_sha": os.environ.get("GITHUB_SHA", "local"),
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "certification_gates": {
        "tuya_3_1_real_tcp": "pass",
        "tuya_3_3_real_tcp": "pass",
        "tuya_3_4_session_key_hmac": "pass",
        "tuya_3_5_session_key_gcm": "pass",
        "tuya_3_5_data_unvalid_fallback": "pass",
        "tcp_fragmentation": "pass",
        "authenticated_frame_fail_closed": "pass",
        "gateway_5_children_one_socket": "pass",
        "gateway_unsolicited_cid_routing": "pass",
        "gateway_disconnect_reconnect": "pass",
    },
    "privacy": {
        "contains_device_id": False,
        "contains_local_key": False,
        "contains_ip_address": False,
        "contains_token": False,
    },
}

output = ROOT / "reports" / "certification.json"
output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(output)
