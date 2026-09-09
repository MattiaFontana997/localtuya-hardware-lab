# LocalTuya Hardware Lab gate policy

A LocalTuya commit is considered virtually hardware-certified only when the lab exercises the real LocalTuya target checkout over real TCP/UDP sockets and all required scenarios pass.

The gate includes direct protocols 3.1 through 3.5, malformed and fragmented wire traffic, protocol auto-detection, wrong-key/offline rejection, LAN host recovery, real Add Device Options Flow through the explicit Finish action, persisted-config reconnect/control, and gateway/sub-device routing with five children plus isolated child failure.

Virtual protocol validation is not the same as physical hardware evidence. The lab must never set `hardware_tested: true`; that flag is reserved for observations from real devices.
