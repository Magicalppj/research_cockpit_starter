from __future__ import annotations

import shutil
import sys
import unittest
import uuid
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.agent_state import (
    AssignmentRecord,
    assignment_contract_errors,
    load_assignment,
)
from research_cockpit.model import validate_assignments
from research_cockpit.storage import save_yaml
from research_cockpit.types import ResearchNode


class AssignmentRecordContractTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"work_packet_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_legacy_assignment_defaults_and_unknown_fields_round_trip(self) -> None:
        assignment = AssignmentRecord.from_dict(
            {
                "assignment_id": "assign_legacy",
                "agent_id": "agent_legacy",
                "status": "active",
                "root_node": "option_x",
                "current_node": "experiment_x",
                "allowed_subtree": {
                    "root": "option_x",
                    "policy": "descendants_only",
                },
                "future_legacy_field": {"preserve": True},
            }
        )

        self.assertEqual(assignment.kind, "experiment")
        self.assertEqual(assignment.dependencies, [])
        self.assertEqual(assignment.scope["write_policy"], "exclusive")
        self.assertEqual(assignment.review, {})
        self.assertEqual(assignment.lease, {})

        serialized = assignment.to_dict(status="blocked")
        self.assertEqual(serialized["future_legacy_field"], {"preserve": True})
        self.assertNotIn("scope", serialized)
        self.assertNotIn("dependencies", serialized)
        self.assertNotIn("lease", serialized)

    def test_queued_assignment_preserves_null_owner_and_validates_without_agent(self) -> None:
        assignment = AssignmentRecord.from_dict(
            {
                "assignment_id": "assign_queued",
                "agent_id": None,
                "status": "queued",
                "root_node": "option_x",
                "current_node": "option_x",
                "allowed_subtree": {
                    "root": "option_x",
                    "policy": "descendants_only",
                },
            }
        )
        nodes = {
            "option_x": ResearchNode.from_dict(
                {
                    "id": "option_x",
                    "type": "option",
                    "title": "Option X",
                    "status": "queued",
                }
            )
        }

        errors = validate_assignments(
            {assignment.assignment_id: assignment},
            {},
            nodes,
        )

        self.assertIsNone(assignment.agent_id)
        self.assertIsNone(assignment.to_dict()["agent_id"])
        self.assertFalse(any("agent_id is required" in error for error in errors))

    def test_load_assignment_reads_only_the_requested_file(self) -> None:
        save_yaml(
            self.root / "assignments" / "assign_target.yaml",
            {
                "assignment_id": "assign_target",
                "agent_id": "agent_target",
                "status": "active",
                "root_node": "option_x",
                "current_node": "option_x",
                "allowed_subtree": {
                    "root": "option_x",
                    "policy": "descendants_only",
                },
            },
        )
        unrelated = self.root / "assignments" / "assign_unrelated.yaml"
        unrelated.write_text("not: [valid", encoding="utf-8")

        assignment = load_assignment(self.root, "assign_target")

        self.assertEqual(assignment.assignment_id, "assign_target")

    def test_new_assignment_fields_report_structured_field_errors(self) -> None:
        assignment = AssignmentRecord.from_dict(
            {
                "assignment_id": "assign_invalid",
                "agent_id": "agent_invalid",
                "status": "active",
                "root_node": "option_x",
                "current_node": "option_x",
                "allowed_subtree": {
                    "root": "option_x",
                    "policy": "descendants_only",
                },
                "scope": "not-a-mapping",
                "dependencies": [{"assignment_id": ""}],
                "lease": {
                    "owner_agent_id": "agent_invalid",
                    "lease_id": "lease_x",
                    "lease_epoch": "one",
                    "heartbeat_at": "not-a-time",
                    "expires_at": None,
                },
            }
        )

        errors = assignment_contract_errors(assignment)

        self.assertTrue(any("scope" in error and "mapping" in error for error in errors))
        self.assertTrue(any("dependencies[0].assignment_id" in error for error in errors))
        self.assertTrue(any("lease.lease_epoch" in error for error in errors))
        self.assertTrue(any("lease.heartbeat_at" in error for error in errors))
        self.assertTrue(any("lease.expires_at" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
