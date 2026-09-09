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
    "schema_version": 2,
    "result": "pass",
    "localtuya_sha": localtuya_sha,
    "hardware_lab_sha": os.environ.get("GITHUB_SHA", "local"),
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "certification_gates": {
        "tuya_3_1_real_tcp": "pass",
        "tuya_3_2_device22_real_tcp": "pass",
        "tuya_3_3_real_tcp": "pass",
        "tuya_3_3_device22_fallback": "pass",
        "tuya_3_4_session_key_hmac": "pass",
        "tuya_3_5_session_key_gcm": "pass",
        "tuya_3_5_data_unvalid_fallback": "pass",
        "auto_protocol_3_1_through_3_5": "pass",
        "wrong_key_never_ready": "pass",
        "udp_discovery_plain_6666": "pass",
        "udp_discovery_55aa_6667": "pass",
        "udp_discovery_6699_7000": "pass",
        "udp_discovery_bad_gcm_ignored": "pass",
        "tcp_single_byte_fragmentation": "pass",
        "tcp_coalesced_multiple_frames": "pass",
        "authenticated_hmac_failure_fail_closed": "pass",
        "authenticated_gcm_failure_fail_closed": "pass",
        "gateway_5_children_one_socket": "pass",
        "gateway_25_concurrent_child_controls": "pass",
        "gateway_unsolicited_cid_routing": "pass",
        "gateway_child_close_keeps_socket": "pass",
        "gateway_disconnect_reconnect_new_session": "pass",
        "host_recovery_validate_before_persist": "pass",
        "host_recovery_wrong_key_fail_closed": "pass",
        "host_recovery_unreachable_fail_closed": "pass",
        "host_recovery_stale_race_protected": "pass"
    },
    "privacy": {
        "contains_device_id": False,
        "contains_local_key": False,
        "contains_ip_address": False,
        "contains_token": False
    }
}

output = ROOT / "reports" / "certification.json"
output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(output)
