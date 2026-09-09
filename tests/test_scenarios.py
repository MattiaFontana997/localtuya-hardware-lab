from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localtuya_lab.report import LabResult, assert_privacy_safe
from localtuya_lab.scenario import ScenarioLoader


class ScenarioTests(unittest.TestCase):
    def test_gateway_profile_has_five_distinct_children(self):
        scenario = ScenarioLoader.load(ROOT / "scenarios/gateway_5_children.json")
        self.assertEqual(scenario.transport, "gateway_child")
        self.assertEqual(len(scenario.child_cids), 5)
        self.assertEqual(len(set(scenario.child_cids)), 5)

    def test_fault_profile_contains_abort(self):
        scenario = ScenarioLoader.load(ROOT / "scenarios/connection_reset.json")
        self.assertEqual(scenario.actions[0].kind, "abort")

    def test_public_report_has_no_private_fields(self):
        result = LabResult(
            scenario="gateway-five-children",
            localtuya_sha="deadbeef",
            protocol="3.5",
            transport="gateway_child",
            status="pass",
            checks=("connect", "routing", "reconnect"),
        ).public_dict()
        assert_privacy_safe(result)
        self.assertEqual(result["status"], "pass")

    def test_private_report_field_is_rejected(self):
        with self.assertRaises(ValueError):
            assert_privacy_safe({"local_key": "must-never-be-published"})
