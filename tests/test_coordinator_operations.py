from __future__ import annotations

from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.assignment_leases import AssignmentLeaseError
from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.coordinator_operations import (
    _lease_epoch_counter,
    apply_coord_assignment,
    assignment_granularity_warning,
    parse_coord_assign_input,
)
from research_cockpit.interaction_log import iter_interaction_events
from research_cockpit.storage import load_yaml
from research_cockpit.work_packets import build_work_packet


class CoordinatorAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"coord_assign_{uuid.uuid4().hex}"
        shutil.copytree(ROOT_DIR / "examples" / "demo_research_cockpit", self.root)
        build_dashboard(self.root)
        self.worktree = parent / "worktrees" / uuid.uuid4().hex

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.worktree, ignore_errors=True)

    def test_graph_plan_is_idempotent_and_uses_canonical_event(self) -> None:
        plan = {
            "schema_version": "coord_assign_v1",
            "operation_id": "op_coord_graph",
            "action": "graph_plan",
            "graph_plan": {
                "nodes": [
                    {
                        "id": "experiment_coord_cutover",
                        "type": "experiment",
                        "title": "Coordinator cutover experiment",
                        "parent": "option_demo_prompt_refinement",
                        "status": "queued",
                    }
                ],
                "updates": [],
            },
        }

        first = apply_coord_assignment(self.root, plan)
        replay = apply_coord_assignment(self.root, plan)

        self.assertEqual(replay, first)
        node = load_yaml(self.root / "graph" / "nodes" / "experiment_coord_cutover.yaml")
        self.assertEqual(node["parent"], "option_demo_prompt_refinement")
        events = [
            event
            for event in iter_interaction_events(self.root, strict=True)
            if event.get("kind") == "coord_graph_plan_applied"
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["command"], "research-cockpit coord assign")

    def test_same_operation_rejects_different_graph_plan(self) -> None:
        original = {
            "schema_version": "coord_assign_v1",
            "operation_id": "op_coord_conflict",
            "action": "graph_plan",
            "graph_plan": {"nodes": [], "updates": []},
        }
        apply_coord_assignment(self.root, original)
        changed = {
            **original,
            "graph_plan": {
                "nodes": [],
                "updates": [
                    {
                        "id": "option_demo_prompt_refinement",
                        "fields": {"summary": "Changed request"},
                    }
                ],
            },
        }

        with self.assertRaises(AssignmentLeaseError) as caught:
            apply_coord_assignment(self.root, changed)

        self.assertEqual(caught.exception.receipt["error"]["code"], "idempotency_conflict")
    def test_graph_plan_updates_legacy_artifact_metadata(self) -> None:
        plan = {
            "schema_version": "coord_assign_v1",
            "operation_id": "op_coord_artifact_metadata",
            "action": "graph_plan",
            "graph_plan": {
                "nodes": [],
                "updates": [
                    {
                        "id": "artifact_demo_baseline",
                        "fields": {
                            "artifact_kind": "evaluation_bundle",
                            "retention": {"class": "evidence_critical"},
                        },
                    }
                ],
            },
        }

        result = apply_coord_assignment(self.root, plan)

        self.assertTrue(result["changed"])
        artifact = load_yaml(self.root / "graph" / "nodes" / "artifact_demo_baseline.yaml")
        self.assertEqual(artifact["artifact_kind"], "evaluation_bundle")
        self.assertEqual(artifact["retention"], {"class": "evidence_critical"})


    def test_session_action_creates_explicit_assignment_once(self) -> None:
        plan = {
            "schema_version": "coord_assign_v1",
            "operation_id": "op_coord_session",
            "action": "session",
            "session": {
                "kind": "experiment",
                "option_id": "option_demo_prompt_refinement",
                "experiment_id": "experiment_demo_prompt_refinement",
                "objective": "Test the canonical assignment facade.",
                "branch": "codex/canonical-assignment",
                "worktree": str(self.worktree),
                "agent_id": "agent_canonical",
                "assignment_id": "assign_canonical",
                "create_worktree": False,
                "force": True,
                "tracking_reason": "stage_deliverable",
            },
        }

        index_path = self.root / "dashboards" / "validation_index.json"
        index_path.unlink()
        with patch(
            "research_cockpit.coordinator_operations.load_validated_state",
            wraps=__import__(
                "research_cockpit.coordinator_operations",
                fromlist=["load_validated_state"],
            ).load_validated_state,
        ) as state_loader:
            first = apply_coord_assignment(self.root, plan)
        state_loader.assert_called_once_with(self.root)
        self.assertTrue(index_path.is_file())

        index_path.unlink()
        replay = apply_coord_assignment(self.root, plan)

        self.assertEqual(replay, first)
        self.assertTrue(index_path.is_file())
        self.assertEqual(first["tracking_reason"], "stage_deliverable")
        self.assertIsNone(first["granularity_warning"])
        assignment = load_yaml(self.root / "assignments" / "assign_canonical.yaml")
        self.assertEqual(assignment["agent_id"], "agent_canonical")
        self.assertEqual(assignment["current_node"], "experiment_demo_prompt_refinement")
        self.assertEqual(assignment["objective"], "Test the canonical assignment facade.")
        self.assertEqual(assignment["tracking_reason"], "stage_deliverable")
        self.assertEqual(
            assignment["lease"]["owner_agent_id"],
            "agent_canonical",
        )
        self.assertEqual(assignment["lease"]["lease_epoch"], 1)
        self.assertTrue(assignment["input_revision"].startswith("input-v1:"))
        packet = build_work_packet(self.root, "assign_canonical")
        self.assertEqual(packet["lease"]["state"], "active")
        self.assertEqual(packet["readiness"], "ready")
        self.assertIn("start", packet["allowed_operations"]["items"])

    def test_missing_tracking_reason_warns_without_writing_a_default(self) -> None:
        plan = {
            "schema_version": "coord_assign_v1",
            "operation_id": "op_coord_missing_tracking_reason",
            "action": "session",
            "session": {
                "kind": "experiment",
                "option_id": "option_demo_prompt_refinement",
                "experiment_id": "experiment_demo_prompt_refinement",
                "objective": "Keep the existing broad workstream.",
                "branch": "codex/missing-tracking-reason",
                "worktree": str(self.worktree),
                "agent_id": "agent_missing_tracking_reason",
                "assignment_id": "assign_missing_tracking_reason",
                "create_worktree": False,
                "force": True,
            },
        }

        first = apply_coord_assignment(self.root, plan)
        replay = apply_coord_assignment(self.root, plan)

        self.assertEqual(replay, first)
        self.assertIsNone(first["tracking_reason"])
        self.assertEqual(
            first["granularity_warning"],
            {
                "code": "missing_tracking_reason",
                "allowed_tracking_reasons": [
                    "parallel_ownership",
                    "durable_handoff",
                    "independent_review",
                    "stage_deliverable",
                ],
            },
        )
        assignment = load_yaml(
            self.root / "assignments" / "assign_missing_tracking_reason.yaml"
        )
        self.assertNotIn("tracking_reason", assignment)

    def test_tracking_reason_warnings_use_exact_structured_values(self) -> None:
        natural_language = "independent review of a stage deliverable"

        self.assertEqual(
            assignment_granularity_warning(
                {"kind": "experiment", "tracking_reason": natural_language}
            )["code"],
            "unknown_tracking_reason",
        )
        self.assertEqual(
            assignment_granularity_warning(
                {"kind": "review", "tracking_reason": "parallel_ownership"}
            ),
            {
                "code": "review_tracking_reason_mismatch",
                "provided_tracking_reason": "parallel_ownership",
                "expected_tracking_reason": "independent_review",
            },
        )
        self.assertEqual(
            assignment_granularity_warning(
                {"kind": "experiment", "tracking_reason": "independent_review"}
            )["code"],
            "experiment_tracking_reason_mismatch",
        )

    def test_experiment_session_requires_explicit_experiment_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "experiment_id"):
            parse_coord_assign_input(
                {
                    "schema_version": "coord_assign_v1",
                    "operation_id": "op_missing_experiment",
                    "action": "session",
                    "session": {
                        "kind": "experiment",
                        "option_id": "option_demo_prompt_refinement",
                        "objective": "This request has no target.",
                        "branch": "codex/missing-target",
                        "worktree": str(self.worktree),
                        "agent_id": "agent_missing_target",
                        "assignment_id": "assign_missing_target",
                    },
                }
            )



    def test_lease_epoch_counter_ignores_malformed_legacy_values(self) -> None:
        counter = _lease_epoch_counter(
            {
                "lease_epoch_counter": "not-an-integer",
                "lease": {"lease_epoch": 3},
            }
        )

        self.assertEqual(counter, 3)


if __name__ == "__main__":
    unittest.main()
