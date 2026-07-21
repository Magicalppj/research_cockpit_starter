from __future__ import annotations

from pathlib import Path
import shutil
import sys
import unittest
import uuid


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.assignment_leases import AssignmentLeaseError
from research_cockpit.coordinator_decisions import apply_coord_decision
from research_cockpit.interaction_log import recent_interactions
from research_cockpit.storage import find_node_file, load_yaml, save_yaml


class CoordinatorDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"coord_decide_{uuid.uuid4().hex}"
        shutil.copytree(ROOT_DIR / "examples" / "demo_research_cockpit", self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _plan(self, *, reason: str = "Updated through the coordinator facade.") -> dict:
        return {
            "schema_version": "coord_decide_v1",
            "operation_id": "op_coord_decide_baseline",
            "action": "set_baseline",
            "parameters": {
                "node_id": "problem_demo_quality_gap",
                "option_id": "option_demo_prompt_refinement",
                "decision_id": "decision_demo_prompt_refinement",
                "artifacts": ["artifact_demo_baseline"],
                "reason": reason,
            },
        }

    def test_set_baseline_is_idempotent_and_preserves_unknown_fields(self) -> None:
        node_path = find_node_file(self.root, "problem_demo_quality_gap")
        node = load_yaml(node_path)
        node["legacy_node_extension"] = {"owner": "downstream", "keep": True}
        save_yaml(node_path, node)

        first = apply_coord_decision(self.root, self._plan())
        replay = apply_coord_decision(self.root, self._plan())

        self.assertEqual(replay, first)
        stored = load_yaml(node_path)
        self.assertEqual(
            stored["legacy_node_extension"],
            {"owner": "downstream", "keep": True},
        )
        self.assertEqual(
            stored["baseline"]["reason"],
            "Updated through the coordinator facade.",
        )
        matching_events = [
            event
            for event in recent_interactions(self.root, limit=20)
            if event.get("command") == "research-cockpit coord decide"
        ]
        self.assertEqual(len(matching_events), 1)

    def test_changed_request_with_same_operation_id_conflicts(self) -> None:
        apply_coord_decision(self.root, self._plan())

        with self.assertRaises(AssignmentLeaseError) as captured:
            apply_coord_decision(self.root, self._plan(reason="A conflicting retry."))

        self.assertEqual(captured.exception.receipt["error"]["code"], "idempotency_conflict")


if __name__ == "__main__":
    unittest.main()
