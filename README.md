# LocalTuya Hardware Lab

Independent black-box virtual-hardware certification lab for `MattiaFontana997/localtuya`.

The lab intentionally does **not** mock LocalTuya protocol internals and its virtual devices do **not** import `pytuya`. They implement the Tuya wire side independently and communicate with the real LocalTuya checkout through operating-system TCP/UDP sockets.

The purpose is to catch protocol, transport and recovery regressions before users see them. A green lab is strong compatibility evidence for the exercised behaviours; it is not a mathematical guarantee for every Tuya firmware ever shipped.

## Current certification surface

### Direct LAN protocols

- Tuya 3.1: real 55AA TCP, status and control.
- Tuya 3.2: type_0d/device22-style query and control.
- Tuya 3.3: normal path plus `data unvalid` / device22 fallback.
- Tuya 3.4: three-way session-key negotiation, AES-ECB payloads and HMAC-SHA256 framing.
- Tuya 3.5: 6699/AES-GCM, three-way session-key negotiation and explicit `data.dps` fallback.
- Protocol auto-detection from 3.5 down through 3.1 against real virtual sockets.
- Wrong local key must never produce a READY preflight or discovered DPS.

### TCP behaviour and fault injection

- responses fragmented across multiple writes
- response split down to single-byte TCP writes
- multiple complete Tuya frames coalesced into one TCP read
- connection reset
- authenticated 3.4 HMAC corruption fails closed
- authenticated 3.5 GCM corruption fails closed
- a following valid exchange remains usable where the protocol permits it

### UDP discovery

The real LocalTuya discovery listener is exercised on the normal Tuya ports:

- UDP 6666: plaintext announcement
- UDP 6667: encrypted 55AA/AES announcement
- UDP 7000: 6699/AES-GCM announcement
- corrupted authenticated announcement is ignored
- source-IP fallback and normalized protocol version are verified

### Gateway / sub-device transport

A protocol-3.5 virtual gateway exposes five independent CIDs over one physical TCP session. The lab verifies:

- one shared gateway socket and one secure-session negotiation
- independent DPS cache/state for five children
- 25 concurrently scheduled child controls without CID cross-talk
- unsolicited STATUS reaches only the matching CID
- closing one child does not close the physical gateway session
- physical disconnect notifies children
- next acquire creates a fresh connection and fresh secure session

### Recovery and safety

The real `host_recovery.py` is exercised against virtual hardware:

- candidate address is authenticated before persistence
- unreachable candidate never replaces the saved host
- wrong local key never allows candidate persistence
- a slow/stale validation cannot overwrite a newer host update

## CI certification model

The workflow separately checks out:

1. this hardware-lab repository;
2. the requested `MattiaFontana997/localtuya` branch/tag/SHA.

It records the exact LocalTuya commit, executes the complete black-box suite on Python 3.14, then emits a privacy-safe `certification.json` artifact. The workflow runs on pushes to `main`/`develop`, pull requests, manual dispatch and nightly against LocalTuya `develop`.

No certification artifact contains real Device IDs, local keys, IP addresses, account identifiers or tokens.

## Release gate

For a LocalTuya stable release we can require all of the following on the intended release SHA:

1. LocalTuya unit/regression suite green.
2. HACS and Hassfest green.
3. Hardware Lab green for that exact SHA.
4. No unresolved high-severity lab regression.
5. Real-hardware evidence remains required before a catalog entry is labelled `verified`.

This keeps the distinction clear: the lab can give very high confidence in protocol and failure behaviour without pretending that unknown vendor firmware has been physically tested.

## Run locally

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
PYTHONPATH=src python scripts/run_lab.py scenarios/gateway_5_children.json
```

To run the black-box target tests locally, set `LOCALTUYA_ROOT` to a checkout of the LocalTuya repository.
