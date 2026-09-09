# LocalTuya Hardware Lab

Independent virtual-hardware certification lab for `MattiaFontana997/localtuya`.

The lab intentionally does **not** mock LocalTuya internals. It creates real TCP/UDP peers and drives wire-level scenarios so LocalTuya sees behaviour closer to physical Tuya devices: fragmented packets, coalesced packets, unsolicited traffic, disconnects, latency, gateway child routing, and malformed traffic.

## Goals

- Exercise LocalTuya against real sockets, not only mocked transports.
- Reproduce direct-device and gateway/sub-device behaviour.
- Keep one failing virtual device from hiding failures in the rest of the matrix.
- Produce machine-readable evidence tied to an exact LocalTuya commit SHA.
- Never publish Device IDs, IPs, local keys, account IDs or tokens in reports.

## V1 scenarios

- direct device baseline
- fragmented response
- coalesced responses
- delayed response
- connection reset
- malformed frame injection
- unsolicited status while a request is in flight
- gateway with five independent child CIDs
- one child failure while the other four continue
- gateway disconnect/reconnect lifecycle

Protocol-specific 3.1/3.3/3.4/3.5 fixtures are added as independent wire captures/vectors. The harness itself remains protocol-agnostic so it can replay real-device traffic exactly.

## Run locally

```bash
python -m unittest discover -s tests -v
PYTHONPATH=src python scripts/run_lab.py scenarios/gateway_5_children.json
```

## CI model

The GitHub workflow checks out this repository and `MattiaFontana997/localtuya@develop` separately. The test lab therefore never imports a copied LocalTuya implementation from itself.

A future release gate can require:

1. LocalTuya unit/regression tests green.
2. HACS/Hassfest green.
3. Hardware Lab green for the exact same LocalTuya SHA.
4. Real-user hardware evidence for entries marked `verified` in the compatibility matrix.
