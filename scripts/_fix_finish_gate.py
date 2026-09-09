from pathlib import Path

root = Path(__file__).resolve().parents[1]

p = root / "tests/test_localtuya_add_device.py"
s = p.read_text(encoding="utf-8")
for cls in ("VirtualTuya31Device", "VirtualTuya32Device", "VirtualTuya33Device", "VirtualTuya34Device", "VirtualTuya35Device"):
    old = f'{cls}(LOCAL_KEY, dps={{"1": False}}, port=6668, response_chunks=3)'
    new = f'{cls}(LOCAL_KEY, dps={{"1": False, "2": 42}}, port=6668, response_chunks=3)'
    if old not in s:
        raise RuntimeError(f"missing expected direct-device fixture: {cls}")
    s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")

p = root / "tests/test_onboarding_hardware_paths.py"
s = p.read_text(encoding="utf-8")
old = '''                result = await flow.async_step_configure_entity(
                    {
                        cf.CONF_ID: "1 (value: False)",
                        cf.CONF_FRIENDLY_NAME: "Persisted Virtual Switch",
                        "restore_on_reconnect": False,
                        "is_passive_entity": False,
                    }
                )
                self.assertEqual(str(result["type"]), "create_entry")

                stored = copy.deepcopy(entry.data[cf.CONF_DEVICES]["persisted-35"])
'''
new = '''                result = await flow.async_step_configure_entity(
                    {
                        cf.CONF_ID: "1 (value: False)",
                        cf.CONF_FRIENDLY_NAME: "Persisted Virtual Switch",
                        "restore_on_reconnect": False,
                        "is_passive_entity": False,
                    }
                )
                self.assertEqual(result["step_id"], "pick_entity_type")
                result = await flow.async_step_pick_entity_type(
                    {cf.NO_ADDITIONAL_ENTITIES: True}
                )
                self.assertEqual(str(result["type"]), "create_entry")

                stored = copy.deepcopy(entry.data[cf.CONF_DEVICES]["persisted-35"])
'''
if s.count(old) != 1:
    raise RuntimeError(f"persisted finish block: expected one match, got {s.count(old)}")
s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")

print("Fixed real Options Flow finish sequencing")
