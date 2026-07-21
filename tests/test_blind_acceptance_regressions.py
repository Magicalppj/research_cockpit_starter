from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid

import yaml


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.agent_state import load_assignment
from research_cockpit.assignment_runs import start_assignment_run
from research_cockpit.commands.context import context_payload
from research_cockpit.commands.list_agent_commands import agent_command_manifest
from research_cockpit.coordinator_operations import apply_coord_assignment
from research_cockpit.assignment_reviews import open_review_assignment
from research_cockpit.evidence_bundles import build_evidence_bundle, persisted_result
from research_cockpit.milestone_handoffs import _collect_blockers
from research_cockpit.storage import load_yaml, save_yaml
from research_cockpit.work_packets import build_work_packet


class BlindAcceptanceRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"blind_acceptance_{uuid.uuid4().hex}"
        shutil.copytree(ROOT_DIR / "examples" / "demo_research_cockpit", self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "research_cockpit.cli", *args],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_session_and_work_start_schemas_expose_target_and_revision(self) -> None:
        session = self._run("coord", "assign", "--print-schema", "--action", "session")
        review_session = self._run(
            "coord", "assign", "--print-schema", "--action", "review_session"
        )
        start = self._run("work", "start", "--print-schema", "--json", "--compact")

        self.assertEqual(session.returncode, 0, session.stderr or session.stdout)
        self.assertEqual(
            review_session.returncode, 0, review_session.stderr or review_session.stdout
        )
        self.assertEqual(start.returncode, 0, start.stderr or start.stdout)
        session_schema = json.loads(session.stdout)
        review_schema = json.loads(review_session.stdout)
        start_schema = json.loads(start.stdout)
        self.assertEqual(session_schema["session"]["experiment_id"], "experiment_x")
        self.assertEqual(review_schema["session"]["kind"], "review")
        self.assertIn("producer_assignment_id", review_schema["session"])
        self.assertEqual(start_schema["experiment_id"], "experiment_x")
        self.assertEqual(start_schema["input_revision"], "input-v1:<from-work-packet>")

    def test_work_close_schema_exposes_review_and_finding_contract(self) -> None:
        completed = self._run(
            "work", "close", "--print-schema", "--json", "--compact"
        )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertEqual(completed.stderr, "")
        schema = json.loads(completed.stdout)
        self.assertEqual(schema["schema_version"], "work_close_v1")
        self.assertIs(schema["review_required"], False)
        self.assertEqual(schema["finding"]["confidence"], "medium")

        template = (
            ROOT_DIR / "templates" / "launcher" / "work_close.example.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("review_required:", template)
        self.assertIn("weak, medium, strong", template)

    def test_session_targets_experiment_and_start_binds_packet_revision(self) -> None:
        plan = {
            "schema_version": "coord_assign_v1",
            "operation_id": "op_blind_session",
            "action": "session",
            "session": {
                "kind": "experiment",
                "option_id": "option_demo_prompt_refinement",
                "experiment_id": "experiment_demo_prompt_refinement",
                "objective": "Run the explicit experiment target.",
                "branch": "codex/blind-explicit-target",
                "worktree": str(self.root.parent / "blind-worktree"),
                "agent_id": "agent_blind",
                "assignment_id": "assign_blind",
                "create_worktree": False,
                "force": True,
            },
        }
        apply_coord_assignment(self.root, plan)
        assignment = load_assignment(self.root, "assign_blind")
        packet = build_work_packet(self.root, "assign_blind")

        self.assertEqual(assignment.current_node, "experiment_demo_prompt_refinement")
        receipt = start_assignment_run(
            self.root,
            assignment_id="assign_blind",
            agent_id="agent_blind",
            lease_id=packet["lease"]["lease_id"],
            lease_epoch=packet["lease"]["lease_epoch"],
            operation_id="op_blind_start",
            input_revision=packet["input_revision"],
        )
        self.assertEqual(
            receipt["entities"]["experiment_id"],
            "experiment_demo_prompt_refinement",
        )

    def test_work_start_json_validation_error_is_structured(self) -> None:
        start_file = self.root / "invalid-start.yaml"
        save_yaml(
            start_file,
            {
                "schema_version": "work_start_v1",
                "agent_id": "agent_x",
                "lease_id": "lease_x",
                "lease_epoch": 1,
                "operation_id": "op_invalid_start",
                "input_revision": "input-v1:stale",
                "unexpected": True,
            },
        )

        completed = self._run(
            "work",
            "start",
            "--root",
            str(self.root),
            "--assignment",
            "assign_missing",
            "--file",
            str(start_file),
            "--json",
            "--compact",
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stderr, "")
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema_version"], "work_operation_v1")
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertEqual(payload["operation_id"], "op_invalid_start")
        self.assertEqual(payload["assignment_id"], "assign_missing")

    def test_coord_assign_missing_file_is_structured_json(self) -> None:
        completed = self._run(
            "coord",
            "assign",
            "--root",
            str(self.root),
            "--json",
            "--compact",
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stderr, "")
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema_version"], "work_operation_v1")
        self.assertEqual(payload["operation"], "coord assign")
        self.assertEqual(payload["error"]["code"], "invalid_request")

    def test_maintenance_invalid_file_is_structured_json(self) -> None:
        plan_path = self.root / "invalid-maintenance.yaml"
        save_yaml(
            plan_path,
            {
                "schema_version": "maintenance_action_v1",
                "action": "interaction_log",
                "execute": False,
                "unexpected": True,
            },
        )

        completed = self._run(
            "maintenance",
            "repair",
            "--root",
            str(self.root),
            "--file",
            str(plan_path),
            "--json",
            "--compact",
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stderr, "")
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema_version"], "work_operation_v1")
        self.assertEqual(payload["operation"], "maintenance repair")
        self.assertEqual(payload["error"]["code"], "invalid_request")


    def test_handoff_success_progress_ends_completed(self) -> None:
        plan_path = self.root / "handoff-blind.yaml"
        save_yaml(
            plan_path,
            {
                "schema_version": "coord_handoff_v1",
                "operation_id": "handoff_blind_progress",
                "kind": "merge",
                "summary": "Verify progress status.",
                "strict_lifecycle": False,
                "allow": {
                    "pending_reviews": True,
                    "stale_inputs": True,
                    "active_leases": True,
                    "unresolved_blockers": True,
                },
            },
        )

        completed = self._run(
            "coord",
            "handoff",
            "--root",
            str(self.root),
            "--file",
            str(plan_path),
            "--json",
            "--compact",
            "--progress",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        progress = [
            json.loads(line.removeprefix("[research-cockpit-progress] "))
            for line in completed.stderr.splitlines()
            if line.startswith("[research-cockpit-progress] ")
        ]
        self.assertTrue(progress)
        self.assertEqual(progress[-1]["phase"], "command")
        self.assertEqual(progress[-1]["status"], "completed")

    def test_unfinished_assignments_are_handoff_blockers(self) -> None:
        blockers = _collect_blockers(
            {
                "rows": [
                    {
                        "assignment_id": "assign_ready",
                        "status": "queued",
                        "readiness": "ready",
                        "review_status": "not_required",
                        "lease_state": "unclaimed",
                    }
                ],
                "overlap_warnings": [],
            },
            {
                "pending_reviews": False,
                "stale_inputs": False,
                "active_leases": False,
                "unresolved_blockers": False,
            },
        )

        self.assertEqual(blockers["unresolved"]["items"], ["assign_ready"])
        self.assertIn("unresolved_blockers", blockers["blocking_categories"])

    def test_compact_discovery_is_bounded_and_accepts_role_leaf_name(self) -> None:
        rows = agent_command_manifest(role="worker", compact=True)
        encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()

        self.assertLess(encoded.__len__(), 8 * 1024)
        self.assertEqual(
            [row["name"] for row in agent_command_manifest(
                role="coordinator", name="assign", compact=True
            )],
            ["coord assign"],
        )
        start_rows = agent_command_manifest(
            role="worker", name="start", compact=True
        )
        self.assertEqual(len(start_rows), 1)
        self.assertIn("--print-schema", start_rows[0]["supported_flags"])
        self.assertTrue(start_rows[0]["schema_command"])

    def test_compact_context_does_not_repeat_focus_or_node_payloads(self) -> None:
        payload = context_payload(
            self.root,
            node_id="decision_demo_prompt_refinement",
            compact=True,
        )
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

        self.assertNotIn("current_global_focus", payload)
        self.assertNotIn("node", payload["node_context"])
        self.assertNotIn("effective_baseline", payload["node_context"])
        self.assertLess(len(encoded), 12 * 1024)

    def test_coordinator_creates_discoverable_review_session(self) -> None:
        bundle, result_revision = build_evidence_bundle(
            assignment_id="assign_producer",
            operation_id="op_producer_close",
            input_revision="input-v1:producer",
            result_spec={
                "outcome": "positive",
                "summary": "Producer result ready for review.",
                "delivery": {
                    "git_commit": "abc123",
                    "changed_files": ["src/example.py"],
                    "tests": {"status": "passed", "summary": "Focused tests passed."},
                },
                "proposals": [],
            },
            run_ids=[],
            finding_ids=[],
            artifact_record_ids=[],
            packet_revision="packet-v1:producer",
        )
        save_yaml(
            self.root / "assignments" / "assign_producer.yaml",
            {
                "assignment_id": "assign_producer",
                "agent_id": None,
                "kind": "experiment",
                "status": "completed",
                "root_node": "option_demo_prompt_refinement",
                "current_node": "experiment_demo_prompt_refinement",
                "allowed_subtree": {
                    "root": "option_demo_prompt_refinement",
                    "policy": "descendants_only",
                },
                "scope": {
                    "root_node": "option_demo_prompt_refinement",
                    "subtree_policy": "descendants_only",
                    "write_policy": "exclusive",
                },
                "review": {"required": True, "status": "pending", "result_revision": None},
                "result": persisted_result(bundle, result_revision),
            },
        )
        option_path = self.root / "graph" / "nodes" / "option_demo_prompt_refinement.yaml"
        option_before = option_path.read_bytes()
        plan = {
            "schema_version": "coord_assign_v1",
            "operation_id": "op_create_review_session",
            "action": "session",
            "session": {
                "kind": "review",
                "option_id": "option_demo_prompt_refinement",
                "producer_assignment_id": "assign_producer",
                "objective": "Review producer evidence independently.",
                "branch": "codex/review-producer",
                "worktree": str(self.root.parent / "review-worktree"),
                "agent_id": "reviewer_blind",
                "assignment_id": "assign_review_blind",
                "create_worktree": False,
                "force": False,
            },
        }

        apply_coord_assignment(self.root, plan)
        review = load_assignment(self.root, "assign_review_blind")
        packet = open_review_assignment(self.root, assignment_id="assign_review_blind")

        self.assertEqual(review.kind, "review")
        self.assertEqual(review.current_node, "experiment_demo_prompt_refinement")
        self.assertEqual(review.scope["write_policy"], "review_read_only")
        self.assertEqual(review.dependencies[0]["assignment_id"], "assign_producer")
        self.assertEqual(packet["producer"]["result_revision"], result_revision)
        self.assertEqual(option_path.read_bytes(), option_before)

    def test_coord_decide_domain_rejection_is_structured_json(self) -> None:
        plan_path = self.root / "invalid-decision.yaml"
        save_yaml(
            plan_path,
            {
                "schema_version": "coord_decide_v1",
                "operation_id": "op_reject_unready_decision",
                "action": "accept",
                "parameters": {
                    "decision_id": "decision_demo_prompt_refinement",
                    "force_accept": False,
                },
            },
        )

        completed = self._run(
            "coord",
            "decide",
            "--root",
            str(self.root),
            "--file",
            str(plan_path),
            "--json",
            "--compact",
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "")
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema_version"], "work_operation_v1")
        self.assertEqual(payload["operation_id"], "op_reject_unready_decision")
        self.assertTrue(payload["error"]["code"])
        self.assertTrue(payload["error"]["retry_action"]["command"])


if __name__ == "__main__":
    unittest.main()
