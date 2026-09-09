"""Independent Tuya 3.3 device22 variant used to verify LocalTuya fallback."""
from __future__ import annotations

import json

from .tuya33 import (
    DP_QUERY,
    PROTOCOL_33_HEADER,
    VirtualTuya33Device,
    aes_ecb_decrypt,
    build_55aa_response,
    encode_json_payload,
)

CONTROL_NEW = 0x0D


class VirtualTuya33Device22(VirtualTuya33Device):
    """Reject the normal query once, then require CONTROL_NEW with explicit DPS."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.device22_rejections = 0
        self.device22_queries = 0
        self._rejected_default_query = False

    async def _process_frame(self, request, writer) -> None:
        self.transcript.requests += 1
        self.transcript.seen_commands.append(request.cmd)

        if request.cmd == DP_QUERY and not self._rejected_default_query:
            self._rejected_default_query = True
            self.device22_rejections += 1
            encrypted = encode_json_payload(self.local_key, {"error": "data unvalid"})
            response = build_55aa_response(request.seqno, request.cmd, encrypted)
            await self._send_response(writer, response)
            return

        if request.cmd == CONTROL_NEW:
            # In 3.3 the version header is clear and sits in front of ciphertext.
            payload = request.payload
            if not payload.startswith(PROTOCOL_33_HEADER):
                raise ValueError("device22 CONTROL_NEW is missing the 3.3 header")
            decoded = aes_ecb_decrypt(self.local_key, payload[len(PROTOCOL_33_HEADER):])
            request_json = json.loads(decoded.decode("utf-8"))
            if not isinstance(request_json, dict):
                raise ValueError("device22 query JSON must be an object")
            self.device22_queries += 1
            self.transcript.status_queries += 1
            dps_request = request_json.get("dps")
            if not isinstance(dps_request, dict):
                raise ValueError("device22 query must contain an explicit DPS map")
            response_json = {"dps": dict(self.dps)}
            encrypted = encode_json_payload(self.local_key, response_json)
            response = build_55aa_response(request.seqno, request.cmd, encrypted)
            await self._send_response(writer, response)
            return

        await super()._process_frame(request, writer)
