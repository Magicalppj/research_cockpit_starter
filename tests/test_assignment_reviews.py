from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit import assignment_reviews as assignment_reviews_module
from research_cockpit.agent_state import load_agents, load_assignment
from research_cockpit.assignment_leases import AssignmentLeaseError, claim_assignment
from research_cockpit.assignment_reviews import (
    apply_review_result,
    open_review_assignment,
    report_assignment_review,
)
from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.evidence_bundles import build_evidence_bundle, persisted_result
from research_cockpit.interaction_log import iter_interaction_events
from research_cockpit.mutation_lock import MutationError
from research_cockpit.public_contracts import WORKFLOW_BUDGETS
from research_cockpit.storage import save_yaml
from research_cockpit.work_packets import build_work_packet_for_assignment


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class AssignmentReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"assignment_review_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        save_yaml(
            self.root / "graph" / "nodes" / "option_x.yaml",
            {
                "id": "option_x",
                "type": "option",
                "title": "Option X",
                "status": "active",
                "children": ["experiment_x"],
            },
        )
        save_yaml(
            self.root / "graph" / "nodes" / "experiment_x.yaml",
            {
                "id": "experiment_x",
                "type": "experiment",
                "title": "Experiment X",
                "status": "done",
                "parent": "option_x",
            },
        )
        save_yaml(self.root / "current_state.yaml", {})
        save_yaml(
            self.root / "agents" / "reviewer_a.yaml",
            {"agent_id": "reviewer_a", "status": "idle", "active_assignment_ids": []},
        )
        producer_bundle, self.producer_revision = build_evidence_bundle(
            assignment_id="assign_producer",
            operation_id="op_producer_close",
            input_revision="input-v1:producer",
            result_spec={
                "outcome": "negative",
                "summary": "The producer found a bounded regression.",
                "delivery": {
                    "git_commit": "abc123",
                    "changed_files": ["src/producer.py"],
                    "tests": {"status": "passed", "summary": "Producer tests passed."},
                },
                "proposals": [],
            },
            run_ids=["run_producer"],
            finding_ids=["finding_producer"],
            artifact_record_ids=["record_producer"],
            packet_revision="packet-v1:producer",
        )
        save_yaml(
            self.root / "assignments" / "assign_producer.yaml",
            {
                "assignment_id": "assign_producer",
                "agent_id": None,
                "status": "completed",
                "root_node": "option_x",
                "current_node": "experiment_x",
                "allowed_subtree": {"root": "option_x", "policy": "descendants_only"},
                "scope": {
                    "root_node": "option_x",
                    "subtree_policy": "descendants_only",
                    "write_policy": "exclusive",
                },
                "review": {"required": True, "status": "pending", "result_revision": None},
                "result": persisted_result(producer_bundle, self.producer_revision),
            },
        )
        save_yaml(
            self.root / "artifact_records" / "experiment_x.yaml",
            {
                "schema_version": "artifact_records_v1",
                "experiment_id": "experiment_x",
                "records": {
                    "record_producer": {
                        "record_id": "record_producer",
                        "experiment_id": "experiment_x",
                        "run_id": "run_producer",
                        "title": "Producer evidence",
                        "summary": "Bounded metrics.",
                        "links": {"metrics": "artifacts/experiment_x/run_producer/metrics.json"},
                    }
                },
            },
        )
        save_yaml(
            self.root / "assignments" / "assign_review.yaml",
            {
                "assignment_id": "assign_review",
                "agent_id": None,
                "kind": "review",
                "status": "queued",
                "root_node": "option_x",
                "current_node": "experiment_x",
                "allowed_subtree": {"root": "option_x", "policy": "descendants_only"},
                "scope": {
                    "root_node": "option_x",
                    "subtree_policy": "descendants_only",
                    "write_policy": "review_read_only",
                },
                "dependencies": [
                    {"assignment_id": "assign_producer", "required_status": "completed"}
                ],
                "inputs": {
                    "effective_baseline_revision": None,
                    "dependency_revisions": {
                        "assign_producer": self.producer_revision,
                    },
                },
                "input_revision": "input-v1:review",
                "objective": "Review the producer evidence.",
                "review": {"required": False, "status": "not_required", "result_revision": None},
            },
        )
        build_dashboard(self.root)
        claimed = claim_assignment(
            self.root,
            assignment_id="assign_review",
            agent_id="reviewer_a",
            operation_id="op_claim_review",
            now=NOW,
        )
        self.lease = claimed["packet"]["lease"]

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _report_plan(
        self,
        *,
        operation_id: str = "op_report_review",
        producer_revision: str | None = None,
        verdict: str = "approved",
    ) -> dict:
        return {
            "schema_version": "review_report_v1",
            "agent_id": "reviewer_a",
            "lease_id": self.lease["lease_id"],
            "lease_epoch": self.lease["lease_epoch"],
            "operation_id": operation_id,
            "input_revision": "input-v1:review",
            "producer_result_revision": producer_revision or self.producer_revision,
            "verdict": verdict,
            "summary": "The producer result is reproducible within the reviewed scope.",
            "findings": [
                {
                    "severity": "P2",
                    "code": "bounded_gap",
                    "summary": "One non-blocking limitation remains.",
                    "evidence_refs": ["record_producer"],
                }
            ],
            "evidence_inspected": ["record_producer"],
            "validation_performed": ["Targeted producer tests"],
        }

    def test_open_returns_one_bounded_packet_with_producer_evidence(self) -> None:
        producer_path = self.root / "assignments" / "assign_producer.yaml"
        before = producer_path.read_bytes()

        payload = open_review_assignment(
            self.root,
            assignment_id="assign_review",
            now=NOW + timedelta(seconds=30),
        )

        self.assertEqual(payload["schema_version"], "review_open_v1")
        self.assertEqual(payload["assignment"]["allowed_operations"]["items"], ["review"])
        self.assertEqual(payload["producer"]["assignment_id"], "assign_producer")
        self.assertEqual(payload["producer"]["result_revision"], self.producer_revision)
        self.assertEqual(payload["producer"]["result"]["bundle_kind"], "work_result")
        self.assertEqual(payload["evidence_records"]["items"][0]["record_id"], "record_producer")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(encoded), 32 * 1024)
        self.assertEqual(producer_path.read_bytes(), before)

    def test_report_is_idempotent_and_writes_only_reviewer_result(self) -> None:
        producer_path = self.root / "assignments" / "assign_producer.yaml"
        producer_before = producer_path.read_bytes()
        plan = self._report_plan()

        first = report_assignment_review(
            self.root,
            assignment_id="assign_review",
            plan=plan,
            now=NOW + timedelta(minutes=1),
        )
        event_count = len(list(iter_interaction_events(self.root, strict=True)))
        second = report_assignment_review(
            self.root,
            assignment_id="assign_review",
            plan=plan,
            now=NOW + timedelta(minutes=1),
        )

        review = load_assignment(self.root, "assign_review")
        reviewer = load_agents(self.root)["reviewer_a"]
        self.assertEqual(second, first)
        self.assertEqual(len(list(iter_interaction_events(self.root, strict=True))), event_count)
        self.assertNotIn("evidence_bundle", first)
        self.assertLessEqual(
            len(json.dumps(first, separators=(",", ":")).encode("utf-8")),
            WORKFLOW_BUDGETS["mutation_receipt_bytes"],
        )
        self.assertEqual(producer_path.read_bytes(), producer_before)
        self.assertEqual(review.status, "completed")
        self.assertIsNone(review.agent_id)
        self.assertEqual(review.result["bundle_kind"], "review_result")
        self.assertEqual(review.result["review"]["verdict"], "approved")
        self.assertNotIn("assign_review", reviewer.active_assignment_ids)

    def test_report_rejects_stale_producer_revision_without_writes(self) -> None:
        review_path = self.root / "assignments" / "assign_review.yaml"
        before = review_path.read_bytes()
        plan = self._report_plan(producer_revision="result-v1:stale")

        with self.assertRaises(AssignmentLeaseError) as caught:
            report_assignment_review(
                self.root,
                assignment_id="assign_review",
                plan=plan,
                now=NOW + timedelta(minutes=1),
            )

        self.assertEqual(caught.exception.receipt["error"]["code"], "stale_producer_result")
        self.assertEqual(review_path.read_bytes(), before)

    def test_coordinator_applies_verdict_without_rewriting_producer_result(self) -> None:
        report = report_assignment_review(
            self.root,
            assignment_id="assign_review",
            plan=self._report_plan(),
            now=NOW + timedelta(minutes=1),
        )
        producer_before = deepcopy(load_assignment(self.root, "assign_producer").result)
        plan = {
            "schema_version": "coord_review_v1",
            "operation_id": "op_apply_review",
            "review_assignment_id": "assign_review",
            "review_result_revision": report["result_revision"],
            "producer_result_revision": self.producer_revision,
        }

        first = apply_review_result(
            self.root,
            producer_assignment_id="assign_producer",
            plan=plan,
            now=NOW + timedelta(minutes=2),
        )
        second = apply_review_result(
            self.root,
            producer_assignment_id="assign_producer",
            plan=plan,
            now=NOW + timedelta(minutes=2),
        )

        producer = load_assignment(self.root, "assign_producer")
        self.assertEqual(second, first)
        self.assertEqual(producer.review["status"], "approved")
        self.assertEqual(producer.review["result_revision"], report["result_revision"])
        self.assertEqual(producer.result, producer_before)

    def test_inconclusive_review_keeps_producer_pending(self) -> None:
        report = report_assignment_review(
            self.root,
            assignment_id="assign_review",
            plan=self._report_plan(verdict="inconclusive"),
            now=NOW + timedelta(minutes=1),
        )
        apply_review_result(
            self.root,
            producer_assignment_id="assign_producer",
            plan={
                "schema_version": "coord_review_v1",
                "operation_id": "op_apply_inconclusive",
                "review_assignment_id": "assign_review",
                "review_result_revision": report["result_revision"],
                "producer_result_revision": self.producer_revision,
            },
            now=NOW + timedelta(minutes=2),
        )

        producer = load_assignment(self.root, "assign_producer")
        self.assertEqual(producer.review["status"], "pending")
        self.assertIsNone(producer.review["result_revision"])
        packet = build_work_packet_for_assignment(
            self.root,
            producer,
            now=NOW + timedelta(minutes=3),
        )
        self.assertEqual(packet["review"]["status"], "pending")

    def test_review_report_conflict_returns_structured_operation_receipt(self) -> None:
        review_path = self.root / "assignments" / "assign_review.yaml"
        before = review_path.read_bytes()
        conflict_file = "assignments/assign_review.yaml"
        failure = MutationError(
            "concurrent review update",
            {
                "status": "conflict",
                "conflict_files": [conflict_file],
                "rolled_back": True,
                "partial_success": False,
            },
        )

        with patch(
            "research_cockpit.assignment_reviews.execute_mutation_transaction",
            side_effect=failure,
        ):
            with self.assertRaises(AssignmentLeaseError) as caught:
                report_assignment_review(
                    self.root,
                    assignment_id="assign_review",
                    plan=self._report_plan(operation_id="op_report_conflict"),
                    now=NOW + timedelta(minutes=1),
                )

        receipt = caught.exception.receipt
        self.assertEqual(receipt["schema_version"], "work_operation_v1")
        self.assertEqual(receipt["error"]["code"], "conflict")
        self.assertEqual(receipt["error"]["conflict_files"]["items"], [conflict_file])
        self.assertEqual(review_path.read_bytes(), before)

    def test_coord_review_conflict_returns_structured_operation_receipt(self) -> None:
        report = report_assignment_review(
            self.root,
            assignment_id="assign_review",
            plan=self._report_plan(operation_id="op_report_before_coord_conflict"),
            now=NOW + timedelta(minutes=1),
        )
        producer_path = self.root / "assignments" / "assign_producer.yaml"
        before = producer_path.read_bytes()
        conflict_file = "assignments/assign_producer.yaml"
        failure = MutationError(
            "concurrent producer update",
            {
                "status": "conflict",
                "conflict_files": [conflict_file],
                "rolled_back": True,
                "partial_success": False,
            },
        )
        plan = {
            "schema_version": "coord_review_v1",
            "operation_id": "op_coord_conflict",
            "review_assignment_id": "assign_review",
            "review_result_revision": report["result_revision"],
            "producer_result_revision": self.producer_revision,
        }

        with patch(
            "research_cockpit.assignment_reviews.execute_mutation_transaction",
            side_effect=failure,
        ):
            with self.assertRaises(AssignmentLeaseError) as caught:
                apply_review_result(
                    self.root,
                    producer_assignment_id="assign_producer",
                    plan=plan,
                    now=NOW + timedelta(minutes=2),
                )

        receipt = caught.exception.receipt
        self.assertEqual(receipt["schema_version"], "work_operation_v1")
        self.assertEqual(receipt["error"]["code"], "conflict")
        self.assertEqual(receipt["error"]["conflict_files"]["items"], [conflict_file])
        self.assertEqual(producer_path.read_bytes(), before)

    def test_review_lease_expiry_at_commit_rejects_report(self) -> None:
        current = datetime.now(timezone.utc)
        assignment_path = self.root / "assignments" / "assign_review.yaml"
        assignment = load_assignment(self.root, "assign_review").raw
        assignment["lease"]["heartbeat_at"] = current.isoformat().replace("+00:00", "Z")
        assignment["lease"]["expires_at"] = (
            current + timedelta(minutes=10)
        ).isoformat().replace("+00:00", "Z")
        save_yaml(assignment_path, assignment)
        build_dashboard(self.root)

        with patch.object(
            assignment_reviews_module,
            "_review_commit_time",
            return_value=current + timedelta(minutes=20),
        ):
            with self.assertRaises(AssignmentLeaseError) as caught:
                report_assignment_review(
                    self.root,
                    assignment_id="assign_review",
                    plan=self._report_plan(operation_id="op_report_expired_at_commit"),
                    now=None,
                )

        self.assertEqual(
            caught.exception.receipt["error"]["code"],
            "lease_expired",
        )
        self.assertEqual(load_assignment(self.root, "assign_review").status, "active")

if __name__ == "__main__":
    unittest.main()