"""Independent Tuya 3.3 device22 variant used to verify LocalTuya fallback."""
from __future__ import annotations

from .tuya33 import (
    DP_QUERY,
    VirtualTuya33Device,
    build_55aa_response,
    decode_request_json,
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
            # A real device22 reports this encrypted error, which should make
            # LocalTuya switch to its type_0d/CONTROL_NEW query path.
            self._rejected_default_query = True
            self.device22_rejections += 1
            encrypted = encode_json_payload(self.local_key, {"error": "data unvalid"})
            # LocalTuya looks for the literal substring after decrypting, so the
            # JSON string deliberately contains the documented phrase.
            response = build_55aa_response(request.seqno, request.cmd, encrypted)
            await self._send_response(writer, response)
            return

        request_json = decode_request_json(self.local_key, request)
        if request.cmd == CONTROL_NEW:
            self.device22_queries += 1
            self.transcript.status_queries += 1
            dps_request = request_json.get("dps")
            if not isinstance(dps_request, dict):
                raise ValueError("device22 query must contain an explicit DPS map")
            response_json = {"dps": dict(self.dps)}
        else:
            await super()._process_frame(request, writer)
            return

        encrypted = encode_json_payload(self.local_key, response_json)
        response = build_55aa_response(request.seqno, request.cmd, encrypted)
        await self._send_response(writer, response)
