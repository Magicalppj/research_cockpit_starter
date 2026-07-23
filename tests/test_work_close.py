from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit import evidence_bundles as evidence_bundles_module
from research_cockpit import evidence_staging as evidence_staging_module
from research_cockpit import mutation_runtime as mutation_runtime_module
from research_cockpit import run_closeout as run_closeout_module
from research_cockpit.agent_state import load_agents, load_assignment
from research_cockpit.assignment_leases import AssignmentLeaseError, claim_assignment
from research_cockpit.assignment_results import close_assignment_work
from research_cockpit.assignment_runs import start_assignment_run
from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.evidence_staging import stage_final_evidence
from research_cockpit.interaction_log import iter_interaction_events
from research_cockpit.model import load_runs
from research_cockpit.mutation_lock import MutationError
from research_cockpit.public_contracts import WORKFLOW_BUDGETS
from research_cockpit.storage import load_yaml, save_yaml
from research_cockpit.work_packets import build_work_packet


NOW = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)


class WorkCloseTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"work_close_{uuid.uuid4().hex}"
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
                "status": "queued",
                "parent": "option_x",
            },
        )
        save_yaml(self.root / "current_state.yaml", {})
        save_yaml(
            self.root / "agents" / "agent_a.yaml",
            {"agent_id": "agent_a", "status": "idle", "active_assignment_ids": []},
        )
        save_yaml(
            self.root / "assignments" / "assign_x.yaml",
            {
                "assignment_id": "assign_x",
                "agent_id": None,
                "status": "queued",
                "root_node": "option_x",
                "current_node": "experiment_x",
                "allowed_subtree": {"root": "option_x", "policy": "descendants_only"},
                "scope": {
                    "root_node": "option_x",
                    "subtree_policy": "descendants_only",
                    "write_policy": "exclusive",
                },
                "inputs": {
                    "effective_baseline_revision": None,
                    "dependency_revisions": {},
                },
                "input_revision": "input-v1:seed",
                "objective": "Complete experiment X",
                "review": {"required": True, "status": "pending", "result_revision": None},
            },
        )
        build_dashboard(self.root)
        claimed = claim_assignment(
            self.root,
            assignment_id="assign_x",
            agent_id="agent_a",
            operation_id="op_claim_close",
            now=NOW,
        )
        self.lease = claimed["packet"]["lease"]
        started = start_assignment_run(
            self.root,
            assignment_id="assign_x",
            agent_id="agent_a",
            lease_id=self.lease["lease_id"],
            lease_epoch=self.lease["lease_epoch"],
            operation_id="op_start_close",
            input_revision="input-v1:seed",
            slug_hint="close",
            now=NOW + timedelta(seconds=30),
        )
        self.run_id = started["entities"]["run_id"]

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _configure_managed_root(self) -> Path:
        managed_root = Path(tempfile.mkdtemp(prefix="research-cockpit-close-"))
        self.addCleanup(shutil.rmtree, managed_root, True)
        save_yaml(
            self.root / "storage.yaml",
            {
                "schema_version": "storage_layout_v1",
                "project_id": "project_close",
                "artifact_root": str(managed_root),
            },
        )
        return managed_root

    def _plan(self, *, operation_id: str = "op_close", outcome: str = "negative") -> dict:
        return {
            "schema_version": "work_close_v1",
            "agent_id": "agent_a",
            "lease_id": self.lease["lease_id"],
            "lease_epoch": self.lease["lease_epoch"],
            "operation_id": operation_id,
            "input_revision": "input-v1:seed",
            "run": {"id": self.run_id, "status": "completed"},
            "experiment": {
                "status": "done",
                "result_summary": "The bounded run completed.",
            },
            "finding": {
                "statement": "The tested approach did not meet the target.",
                "confidence": "strong",
                "outcome": outcome,
            },
            "assignment_result": {
                "outcome": outcome,
                "summary": "The approach was measured and rejected.",
                "delivery": {
                    "git_commit": None,
                    "changed_files": [],
                    "tests": {"status": "passed", "summary": "Targeted tests passed."},
                },
                "proposals": [
                    {
                        "kind": "new_branch",
                        "title": "Test a separate strategy",
                        "rationale": "The current strategy missed the target.",
                        "parent_candidate": "option_x",
                        "dependencies": ["assign_x"],
                        "success_criteria": ["Meet the target."],
                        "expected_deliverables": ["run", "finding"],
                    }
                ],
            },
            "review_required": True,
        }

    def test_close_writes_bounded_result_and_completes_assignment_atomically(self) -> None:
        plan = self._plan()
        first = close_assignment_work(
            self.root,
            assignment_id="assign_x",
            plan=plan,
            now=NOW + timedelta(minutes=2),
        )
        event_count = len(list(iter_interaction_events(self.root, strict=True)))
        second = close_assignment_work(
            self.root,
            assignment_id="assign_x",
            plan=plan,
            now=NOW + timedelta(minutes=2),
        )

        assignment = load_assignment(self.root, "assign_x")
        bundle = assignment.result
        run = load_runs(self.root)[self.run_id]
        experiment = load_yaml(self.root / "graph" / "nodes" / "experiment_x.yaml")
        agent = load_agents(self.root)["agent_a"]

        self.assertEqual(second, first)
        self.assertEqual(len(list(iter_interaction_events(self.root, strict=True))), event_count)
        self.assertRegex(first["result_revision"], r"^result-v1:[a-f0-9]{64}$")
        self.assertNotIn("evidence_bundle", first)
        self.assertLessEqual(
            len(json.dumps(first, separators=(",", ":")).encode("utf-8")),
            WORKFLOW_BUDGETS["mutation_receipt_bytes"],
        )
        self.assertEqual(bundle["schema_version"], "evidence_bundle_v1")
        self.assertEqual(bundle["revision"], first["result_revision"])
        self.assertEqual(bundle["bundle_kind"], "work_result")
        self.assertEqual(bundle["outcome"], "negative")
        self.assertEqual(bundle["runs"]["items"], [self.run_id])
        self.assertEqual(len(bundle["findings"]["items"]), 1)
        self.assertEqual(bundle["proposals"]["items"][0]["kind"], "new_branch")
        self.assertEqual(run.status, "completed")
        self.assertEqual(experiment["status"], "done")
        self.assertEqual(assignment.status, "completed")
        self.assertIsNone(assignment.agent_id)
        self.assertIsNone(assignment.lease["lease_id"])
        self.assertEqual(assignment.review["status"], "pending")
        self.assertNotIn("assign_x", agent.active_assignment_ids)
        self.assertEqual(len(list((self.root / "assignments").glob("*.yaml"))), 1)

    def test_closeout_aggregates_external_attempts_without_graph_growth(self) -> None:
        plan = self._plan(operation_id="op_close_aggregated_attempts")
        plan["assignment_result"]["attempts"] = [
            {
                "attempt_id": "preflight_seed_1",
                "status": "failed",
                "outcome": "negative",
                "summary": "Seed 1 exhausted the memory budget before evaluation.",
                "evidence_refs": ["s3://research-output/seed_1/stderr.log"],
            },
            {
                "attempt_id": "retry_seed_2",
                "status": "completed",
                "outcome": "negative",
                "summary": "Seed 2 completed but missed the quality threshold.",
                "evidence_refs": ["s3://research-output/seed_2/metrics.json"],
            },
            {
                "attempt_id": "selected_seed_3",
                "status": "completed",
                "outcome": "negative",
                "summary": "Seed 3 confirmed the same negative conclusion.",
                "evidence_refs": ["s3://research-output/seed_3/metrics.json"],
            },
        ]

        receipt = close_assignment_work(
            self.root,
            assignment_id="assign_x",
            plan=plan,
            now=NOW + timedelta(minutes=2),
        )

        assignment = load_assignment(self.root, "assign_x")
        bundle = assignment.result
        packet = build_work_packet(
            self.root,
            "assign_x",
            now=NOW + timedelta(minutes=3),
        )

        self.assertEqual(
            [item["attempt_id"] for item in bundle["attempts"]["items"]],
            ["preflight_seed_1", "retry_seed_2", "selected_seed_3"],
        )
        self.assertEqual(bundle["attempts"]["total"], 3)
        self.assertEqual(bundle["attempts"]["omitted"], 0)
        self.assertEqual(bundle["attempts"]["items"][0]["status"], "failed")
        self.assertEqual(bundle["attempts"]["items"][0]["outcome"], "negative")
        self.assertEqual(
            bundle["attempts"]["items"][0]["evidence_refs"]["items"],
            ["s3://research-output/seed_1/stderr.log"],
        )
        self.assertEqual(packet["result"]["revision"], receipt["result_revision"])
        self.assertEqual(packet["result"]["attempts"]["total"], 3)
        self.assertEqual(
            [item["attempt_id"] for item in packet["result"]["attempts"]["items"]],
            ["preflight_seed_1", "retry_seed_2", "selected_seed_3"],
        )
        self.assertEqual(len(list((self.root / "assignments").glob("*.yaml"))), 1)
        self.assertEqual(len(list((self.root / "graph" / "nodes").glob("*.yaml"))), 2)
        self.assertEqual(len(load_runs(self.root)), 1)
        self.assertLessEqual(
            len(json.dumps(bundle, separators=(",", ":")).encode("utf-8")),
            16 * 1024,
        )

    def test_closeout_bounds_selected_attempts(self) -> None:
        plan = self._plan(operation_id="op_close_bounded_attempts")
        plan["assignment_result"]["attempts"] = [
            {
                "attempt_id": f"seed_{index}",
                "status": "completed",
                "outcome": "negative",
                "summary": f"Seed {index} missed the accepted threshold.",
                "evidence_refs": [f"s3://research-output/seed_{index}/metrics.json"],
            }
            for index in range(21)
        ]

        close_assignment_work(
            self.root,
            assignment_id="assign_x",
            plan=plan,
            now=NOW + timedelta(minutes=2),
        )

        attempts = load_assignment(self.root, "assign_x").result["attempts"]
        self.assertEqual(attempts["limit"], 20)
        self.assertEqual(attempts["total"], 21)
        self.assertEqual(attempts["omitted"], 1)
        self.assertEqual(len(attempts["items"]), 20)

    def test_invalid_finding_confidence_lists_allowed_values(self) -> None:
        plan = self._plan(operation_id="op_close_invalid_confidence")
        plan["finding"]["confidence"] = "high"

        with self.assertRaisesRegex(
            ValueError,
            "allowed: medium, strong, weak",
        ):
            close_assignment_work(
                self.root,
                assignment_id="assign_x",
                plan=plan,
                now=NOW + timedelta(minutes=2),
            )

    def test_close_payload_mismatch_rejects_without_second_write(self) -> None:
        self.close = close_assignment_work(
            self.root,
            assignment_id="assign_x",
            plan=self._plan(operation_id="op_close_mismatch"),
            now=NOW + timedelta(minutes=2),
        )
        assignment_before = load_yaml(self.root / "assignments" / "assign_x.yaml")
        changed = self._plan(operation_id="op_close_mismatch")
        changed["assignment_result"]["summary"] = "Changed retry payload."

        with self.assertRaises(AssignmentLeaseError) as caught:
            close_assignment_work(
                self.root,
                assignment_id="assign_x",
                plan=changed,
                now=NOW + timedelta(minutes=2),
            )

        self.assertEqual(caught.exception.receipt["error"]["code"], "idempotency_conflict")
        self.assertEqual(load_yaml(self.root / "assignments" / "assign_x.yaml"), assignment_before)

    def test_local_followup_moves_cursor_without_completing_assignment(self) -> None:
        plan = self._plan(operation_id="op_close_followup", outcome="inconclusive")
        plan["next_experiment"] = {
            "id": "experiment_followup",
            "title": "Resolve the inconclusive result",
            "success_criteria": ["Resolve the remaining uncertainty."],
            "next_action": "Run the focused follow-up.",
        }
        plan["assignment_result"]["proposals"] = []

        receipt = close_assignment_work(
            self.root,
            assignment_id="assign_x",
            plan=plan,
            now=NOW + timedelta(minutes=2),
        )

        assignment = load_assignment(self.root, "assign_x")
        followup = load_yaml(self.root / "graph" / "nodes" / "experiment_followup.yaml")
        self.assertEqual(receipt["entities"]["next_experiment_id"], "experiment_followup")
        self.assertEqual(assignment.status, "active")
        self.assertEqual(assignment.current_node, "experiment_followup")
        self.assertEqual(assignment.agent_id, "agent_a")
        self.assertEqual(assignment.result["outcome"], "inconclusive")
        self.assertEqual(followup["derived_from"], ["experiment_x"])
        self.assertEqual(len(list((self.root / "assignments").glob("*.yaml"))), 1)

    def test_final_evidence_defaults_to_reference_without_copy(self) -> None:
        source = self.root.parent / f"evidence_{uuid.uuid4().hex}"
        source.mkdir()
        try:
            (source / "metrics.json").write_text('{"score": 0.73}', encoding="utf-8")
            plan = self._plan(operation_id="op_close_evidence")
            plan["evidence_inputs"] = {
                "source": str(source),
                "title": "Final run evidence",
                "summary": "Metrics referenced at close.",
                "links": {"metrics": "metrics.json"},
            }

            receipt = close_assignment_work(
                self.root,
                assignment_id="assign_x",
                plan=plan,
                now=NOW + timedelta(minutes=2),
            )

            record_id = receipt["entities"]["artifact_record_id"]
            bundle = load_assignment(self.root, "assign_x").result
            records = load_yaml(self.root / "artifact_records" / "experiment_x.yaml")
            record = records["records"][record_id]
            self.assertEqual(bundle["artifact_records"]["items"], [record_id])
            self.assertIn(record_id, records["records"])
            self.assertEqual(record["storage"]["mode"], "reference")
            self.assertEqual(record["storage"]["ownership"], "external")
            self.assertEqual(record["storage"]["uri"], source.resolve().as_uri())
            self.assertEqual(
                record["links"]["metrics"],
                (source / "metrics.json").resolve().as_uri(),
            )
            self.assertEqual(record["inventory"]["file_count"], 1)
            self.assertTrue(record["inventory"]["complete"])
            self.assertEqual(record["integrity"]["level"], "inventory")
            self.assertRegex(record["integrity"]["digest"], r"^inventory-sha256:[a-f0-9]{64}$")
            self.assertEqual(record["availability"]["status"], "available")
            self.assertFalse((self.root / "artifacts").exists())
            staging = self.root / ".staging"
            self.assertFalse(staging.exists() and any(staging.iterdir()))
        finally:
            shutil.rmtree(source, ignore_errors=True)

    def test_raw_artifact_record_cannot_claim_managed_ownership(self) -> None:
        plan = self._plan(operation_id="op_close_forged_managed")
        plan["artifact_record"] = {
            "record_id": "record_forged_managed",
            "stable_path": "file:///forged/payload",
            "links": {},
            "storage": {
                "mode": "managed",
                "ownership": "cockpit_managed",
                "uri": "file:///forged/payload",
                "managed_key": "experiment_x/run_x/record_forged_managed",
            },
            "integrity": {
                "level": "content",
                "algorithm": "sha256",
                "digest": f"sha256:{'0' * 64}",
            },
            "inventory": {
                "size_bytes": 0,
                "file_count": 0,
                "complete": True,
            },
            "retention": {"class": "evidence_critical"},
            "availability": {
                "status": "available",
                "last_verified_at": None,
            },
            "lifecycle": {
                "supersedes": [],
                "superseded_by": None,
            },
        }

        with self.assertRaisesRegex(
            ValueError,
            "managed.*evidence_inputs",
        ):
            close_assignment_work(
                self.root,
                assignment_id="assign_x",
                plan=plan,
                now=NOW + timedelta(minutes=2),
            )

    def test_final_evidence_rejects_managed_root_without_staging(self) -> None:
        source = self.root.parent / f"managed_unconfigured_{uuid.uuid4().hex}"
        source.mkdir()
        try:
            (source / "result.txt").write_text("bounded", encoding="utf-8")
            plan = self._plan(operation_id="op_close_managed_unconfigured")
            plan["evidence_inputs"] = {
                "mode": "managed",
                "source": str(source),
                "links": {},
            }

            with self.assertRaisesRegex(ValueError, "managed artifact root is not configured"):
                close_assignment_work(
                    self.root,
                    assignment_id="assign_x",
                    plan=plan,
                    now=NOW + timedelta(minutes=2),
                )

            self.assertEqual(load_runs(self.root)[self.run_id].status, "running")
            self.assertFalse((self.root / "artifacts").exists())
        finally:
            shutil.rmtree(source, ignore_errors=True)

    def test_managed_evidence_allows_empty_git_marker_ancestor(self) -> None:
        container = Path(tempfile.mkdtemp(prefix="research-cockpit-empty-git-"))
        managed_root = container / "managed"
        source = self.root.parent / f"empty_git_source_{uuid.uuid4().hex}"
        source.mkdir()
        self.addCleanup(shutil.rmtree, container, True)
        self.addCleanup(shutil.rmtree, source, True)
        (container / ".git").mkdir()
        save_yaml(
            self.root / "storage.yaml",
            {
                "schema_version": "storage_layout_v1",
                "project_id": "project_test",
                "artifact_root": str(managed_root),
            },
        )
        (source / "result.txt").write_text("bounded", encoding="utf-8")

        staged = stage_final_evidence(
            self.root,
            assignment_id="assign_x",
            experiment_id="experiment_x",
            run_id=self.run_id,
            agent_id="agent_a",
            record_id="record_empty_git_marker",
            spec={"mode": "managed", "source": str(source), "links": {}},
        )
        self.addCleanup(staged.cleanup)

        self.assertEqual(
            staged.target_dir,
            managed_root / "experiment_x" / self.run_id / "record_empty_git_marker",
        )
        self.assertTrue(staged.staging_dir and staged.staging_dir.is_dir())

    def test_final_evidence_explicit_managed_mode_uses_external_store(self) -> None:
        managed_root = Path(tempfile.mkdtemp(prefix="research-cockpit-managed-"))
        source = self.root.parent / f"managed_source_{uuid.uuid4().hex}"
        source.mkdir()
        try:
            save_yaml(
                self.root / "storage.yaml",
                {
                    "schema_version": "storage_layout_v1",
                    "project_id": "project_test",
                    "artifact_root": str(managed_root),
                },
            )
            (source / "metrics.json").write_text('{"score": 0.81}', encoding="utf-8")
            plan = self._plan(operation_id="op_close_managed_external")
            plan["evidence_inputs"] = {
                "mode": "managed",
                "source": str(source),
                "links": {"metrics": "metrics.json"},
                "retention": {"class": "portable_review_bundle"},
            }

            with patch.object(
                evidence_staging_module,
                "_copy_source_tree_hashed",
                wraps=evidence_staging_module._copy_source_tree_hashed,
            ) as copy_hashed:
                receipt = close_assignment_work(
                    self.root,
                    assignment_id="assign_x",
                    plan=plan,
                    now=NOW + timedelta(minutes=2),
                )
                shutil.rmtree(source)
                replay = close_assignment_work(
                    self.root,
                    assignment_id="assign_x",
                    plan=plan,
                    now=NOW + timedelta(minutes=2),
                )
            self.assertEqual(replay, receipt)
            self.assertEqual(copy_hashed.call_count, 1)

            record_id = receipt["entities"]["artifact_record_id"]
            record = load_yaml(
                self.root / "artifact_records" / "experiment_x.yaml"
            )["records"][record_id]
            target = managed_root / record["storage"]["managed_key"]
            self.assertEqual(record["storage"]["mode"], "managed")
            self.assertEqual(record["storage"]["ownership"], "cockpit_managed")
            self.assertEqual(record["storage"]["uri"], target.resolve().as_uri())
            self.assertTrue((target / "metrics.json").is_file())
            self.assertEqual(record["integrity"]["level"], "content")
            self.assertRegex(record["integrity"]["digest"], r"^sha256:[a-f0-9]{64}$")
            self.assertTrue(record["inventory"]["complete"])
            self.assertFalse((self.root / "artifacts").exists())
            staging = managed_root / ".staging"
            self.assertFalse(staging.exists() and any(staging.iterdir()))
        finally:
            shutil.rmtree(source, ignore_errors=True)
            shutil.rmtree(managed_root, ignore_errors=True)

    def test_invalid_close_lease_is_rejected_before_managed_copy(self) -> None:
        managed_root = self._configure_managed_root()
        source = self.root.parent / f"invalid_close_lease_{uuid.uuid4().hex}"
        source.mkdir()
        try:
            (source / "result.txt").write_text("bounded", encoding="utf-8")
            plan = self._plan(operation_id="op_close_invalid_lease")
            plan["lease_id"] = "wrong-lease"
            plan["evidence_inputs"] = {
                "mode": "managed",
                "source": str(source),
                "links": {},
            }

            with patch.object(
                evidence_staging_module,
                "_copy_source_tree_hashed",
            ) as copy_payload:
                with self.assertRaises(AssignmentLeaseError):
                    close_assignment_work(
                        self.root,
                        assignment_id="assign_x",
                        plan=plan,
                        now=NOW + timedelta(minutes=2),
                    )

            copy_payload.assert_not_called()
            self.assertFalse((managed_root / ".staging").exists())
        finally:
            shutil.rmtree(source, ignore_errors=True)

    def test_truncated_reference_inventory_requires_explicit_retention(self) -> None:
        source = self.root.parent / f"bounded_reference_{uuid.uuid4().hex}"
        source.mkdir()
        try:
            (source / "first.txt").write_text("first", encoding="utf-8")
            (source / "second.txt").write_text("second", encoding="utf-8")
            plan = self._plan(operation_id="op_close_reference_truncated")
            plan["evidence_inputs"] = {
                "source": str(source),
                "links": {},
            }

            with patch.object(evidence_staging_module, "MAX_INVENTORY_FILES", 1):
                with self.assertRaisesRegex(ValueError, "retention"):
                    close_assignment_work(
                        self.root,
                        assignment_id="assign_x",
                        plan=plan,
                        now=NOW + timedelta(minutes=2),
                    )
                plan["evidence_inputs"]["retention"] = {}
                with self.assertRaisesRegex(ValueError, "retention.*class"):
                    close_assignment_work(
                        self.root,
                        assignment_id="assign_x",
                        plan=plan,
                        now=NOW + timedelta(minutes=2),
                    )


                plan["evidence_inputs"]["retention"] = {
                    "class": "reproducible_output"
                }
                receipt = close_assignment_work(
                    self.root,
                    assignment_id="assign_x",
                    plan=plan,
                    now=NOW + timedelta(minutes=2),
                )

            record_id = receipt["entities"]["artifact_record_id"]
            record = load_yaml(
                self.root / "artifact_records" / "experiment_x.yaml"
            )["records"][record_id]
            self.assertFalse(record["inventory"]["complete"])
            self.assertGreater(record["inventory"]["file_count"], 1)
        finally:
            shutil.rmtree(source, ignore_errors=True)

    def test_managed_evidence_above_limit_requires_explicit_retention(self) -> None:
        managed_root = self._configure_managed_root()
        source = self.root.parent / f"managed_limit_{uuid.uuid4().hex}"
        source.mkdir()
        try:
            (source / "result.txt").write_text("too large", encoding="utf-8")
            plan = self._plan(operation_id="op_close_managed_limit")
            plan["evidence_inputs"] = {
                "mode": "managed",
                "source": str(source),
                "links": {},
            }

            with patch.object(evidence_staging_module, "MAX_INVENTORY_BYTES", 1):
                with self.assertRaisesRegex(ValueError, "retention"):
                    close_assignment_work(
                        self.root,
                        assignment_id="assign_x",
                        plan=plan,
                        now=NOW + timedelta(minutes=2),
                    )

            self.assertEqual(load_runs(self.root)[self.run_id].status, "running")
            self.assertFalse(any(managed_root.rglob("result.txt")))
            staging = managed_root / ".staging"
            self.assertFalse(staging.exists() and any(staging.iterdir()))
        finally:
            shutil.rmtree(source, ignore_errors=True)

    def test_managed_evidence_enforces_hard_directory_entry_limit(self) -> None:
        managed_root = self._configure_managed_root()
        source = self.root.parent / f"managed_entries_{uuid.uuid4().hex}"
        source.mkdir()
        try:
            (source / "empty_a").mkdir()
            (source / "empty_b").mkdir()
            plan = self._plan(operation_id="op_close_managed_entries")
            plan["evidence_inputs"] = {
                "mode": "managed",
                "source": str(source),
                "links": {},
                "retention": {"class": "reproducible_output"},
            }

            with patch.object(
                evidence_staging_module,
                "MAX_MANAGED_TREE_ENTRIES",
                1,
            ):
                with self.assertRaisesRegex(ValueError, "entry limit"):
                    close_assignment_work(
                        self.root,
                        assignment_id="assign_x",
                        plan=plan,
                        now=NOW + timedelta(minutes=2),
                    )

            self.assertFalse(any(managed_root.rglob("empty_a")))
        finally:
            shutil.rmtree(source, ignore_errors=True)

    def test_final_evidence_rejects_more_than_twenty_links(self) -> None:
        source = self.root.parent / f"evidence_links_{uuid.uuid4().hex}"
        source.mkdir()
        try:
            (source / "result.txt").write_text("bounded", encoding="utf-8")
            plan = self._plan(operation_id="op_close_unbounded_links")
            plan["evidence_inputs"] = {
                "source": str(source),
                "links": {f"link_{index}": "result.txt" for index in range(21)},
            }

            with self.assertRaisesRegex(ValueError, "at most 20"):
                close_assignment_work(
                    self.root,
                    assignment_id="assign_x",
                    plan=plan,
                    now=NOW + timedelta(minutes=2),
                )

            self.assertEqual(load_runs(self.root)[self.run_id].status, "running")
        finally:
            shutil.rmtree(source, ignore_errors=True)

    def test_final_evidence_target_cannot_escape_artifact_store(self) -> None:
        self._configure_managed_root()
        source = self.root.parent / f"evidence_escape_{uuid.uuid4().hex}"
        source.mkdir()
        try:
            (source / "result.txt").write_text("bounded", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inside the artifact store"):
                stage_final_evidence(
                    self.root,
                    assignment_id="assign_x",
                    experiment_id="../outside",
                    run_id=self.run_id,
                    agent_id="agent_a",
                    spec={"source": str(source), "links": {}},
                )
        finally:
            shutil.rmtree(source, ignore_errors=True)

    def test_final_evidence_rejects_a_source_directory_symlink(self) -> None:
        source = self.root.parent / f"evidence_source_{uuid.uuid4().hex}"
        source.mkdir()
        link = self.root.parent / f"evidence_link_{uuid.uuid4().hex}"
        try:
            (source / "result.txt").write_text("bounded", encoding="utf-8")
            try:
                link.symlink_to(source, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")
            plan = self._plan(operation_id="op_close_symlink_source")
            plan["evidence_inputs"] = {"source": str(link), "links": {}}

            with self.assertRaisesRegex(ValueError, "source must not be a symlink"):
                close_assignment_work(
                    self.root,
                    assignment_id="assign_x",
                    plan=plan,
                    now=NOW + timedelta(minutes=2),
                )
        finally:
            link.unlink(missing_ok=True)
            shutil.rmtree(source, ignore_errors=True)

    def test_evidence_bundle_summary_and_total_payload_are_bounded(self) -> None:
        plan = self._plan(operation_id="op_close_bounded_bundle")
        plan["assignment_result"]["summary"] = "x" * 10000

        close_assignment_work(
            self.root,
            assignment_id="assign_x",
            plan=plan,
            now=NOW + timedelta(minutes=2),
        )

        bundle = load_assignment(self.root, "assign_x").result
        self.assertEqual(len(bundle["summary"]), 1000)
        encoded = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(encoded), 16 * 1024)

    def test_worker_cannot_disable_required_review(self) -> None:
        plan = self._plan(operation_id="op_close_preserve_review")
        plan["review_required"] = False

        close_assignment_work(
            self.root,
            assignment_id="assign_x",
            plan=plan,
            now=NOW + timedelta(minutes=2),
        )

        review = load_assignment(self.root, "assign_x").review
        self.assertTrue(review["required"])
        self.assertEqual(review["status"], "pending")

    def test_review_assignment_cannot_use_worker_close(self) -> None:
        assignment_path = self.root / "assignments" / "assign_x.yaml"
        assignment = load_yaml(assignment_path)
        assignment["kind"] = "review"
        assignment["scope"]["write_policy"] = "review_read_only"
        save_yaml(assignment_path, assignment)

        with self.assertRaises(AssignmentLeaseError) as caught:
            close_assignment_work(
                self.root,
                assignment_id="assign_x",
                plan=self._plan(operation_id="op_review_wrong_close"),
                now=NOW + timedelta(minutes=2),
            )

        self.assertEqual(caught.exception.receipt["error"]["code"], "review_scope_read_only")

    def test_transaction_conflict_returns_structured_operation_receipt(self) -> None:
        conflict_file = "assignments/assign_x.yaml"
        failure = MutationError(
            "concurrent assignment update",
            {
                "status": "conflict",
                "conflict_files": [conflict_file],
                "rolled_back": True,
                "partial_success": False,
            },
        )

        with patch(
            "research_cockpit.assignment_results.complete_run_closeout",
            side_effect=failure,
        ):
            with self.assertRaises(AssignmentLeaseError) as caught:
                close_assignment_work(
                    self.root,
                    assignment_id="assign_x",
                    plan=self._plan(operation_id="op_close_conflict"),
                    now=NOW + timedelta(minutes=2),
                )

        receipt = caught.exception.receipt
        self.assertEqual(receipt["schema_version"], "work_operation_v1")
        self.assertEqual(receipt["error"]["code"], "conflict")
        self.assertEqual(receipt["error"]["conflict_files"]["items"], [conflict_file])
        self.assertTrue(receipt["rolled_back"])
        self.assertFalse(receipt["partial_success"])

    def test_dependency_change_before_commit_rejects_close_without_writes(self) -> None:
        producer_revision = "result-v1:producer"
        producer_path = self.root / "assignments" / "assign_producer.yaml"
        save_yaml(
            producer_path,
            {
                "assignment_id": "assign_producer",
                "agent_id": None,
                "status": "completed",
                "root_node": "option_x",
                "current_node": "experiment_x",
                "allowed_subtree": {
                    "root": "option_x",
                    "policy": "descendants_only",
                },
                "scope": {
                    "root_node": "option_x",
                    "subtree_policy": "descendants_only",
                    "write_policy": "exclusive",
                },
                "review": {
                    "required": False,
                    "status": "not_required",
                    "result_revision": None,
                },
                "result": {"revision": producer_revision, "summary": "Producer result."},
            },
        )
        assignment_path = self.root / "assignments" / "assign_x.yaml"
        assignment = load_yaml(assignment_path)
        assignment["dependencies"] = [
            {"assignment_id": "assign_producer", "required_status": "completed"}
        ]
        assignment["inputs"]["dependency_revisions"] = {
            "assign_producer": producer_revision
        }
        save_yaml(assignment_path, assignment)
        build_dashboard(self.root)
        before = {
            "assignment": assignment_path.read_bytes(),
            "run": (self.root / "runs" / f"{self.run_id}.yaml").read_bytes(),
            "experiment": (
                self.root / "graph" / "nodes" / "experiment_x.yaml"
            ).read_bytes(),
        }
        original_transaction = run_closeout_module.execute_mutation_transaction

        def change_dependency(*args: object, **kwargs: object) -> dict:
            producer = load_yaml(producer_path)
            producer["result"]["revision"] = "result-v1:changed"
            save_yaml(producer_path, producer)
            return original_transaction(*args, **kwargs)

        with patch(
            "research_cockpit.run_closeout.execute_mutation_transaction",
            side_effect=change_dependency,
        ):
            with self.assertRaises(AssignmentLeaseError) as caught:
                close_assignment_work(
                    self.root,
                    assignment_id="assign_x",
                    plan=self._plan(operation_id="op_close_stale_dependency"),
                    now=NOW + timedelta(minutes=2),
                )

        self.assertIn(
            caught.exception.receipt["error"]["code"],
            {"conflict", "stale_inputs"},
            caught.exception.receipt,
        )
        self.assertEqual(assignment_path.read_bytes(), before["assignment"])
        self.assertEqual(
            (self.root / "runs" / f"{self.run_id}.yaml").read_bytes(),
            before["run"],
        )
        self.assertEqual(
            (self.root / "graph" / "nodes" / "experiment_x.yaml").read_bytes(),
            before["experiment"],
        )

    def test_baseline_source_change_before_commit_rejects_close(self) -> None:
        option_path = self.root / "graph" / "nodes" / "option_x.yaml"
        assignment_path = self.root / "assignments" / "assign_x.yaml"
        assignment_before = assignment_path.read_bytes()
        original_transaction = run_closeout_module.execute_mutation_transaction

        def change_baseline_source(*args: object, **kwargs: object) -> dict:
            option = load_yaml(option_path)
            option["title"] = "Option X changed during commit planning"
            save_yaml(option_path, option)
            return original_transaction(*args, **kwargs)

        with patch(
            "research_cockpit.run_closeout.execute_mutation_transaction",
            side_effect=change_baseline_source,
        ):
            with self.assertRaises(AssignmentLeaseError) as caught:
                close_assignment_work(
                    self.root,
                    assignment_id="assign_x",
                    plan=self._plan(operation_id="op_close_stale_baseline"),
                    now=NOW + timedelta(minutes=2),
                )

        self.assertEqual(caught.exception.receipt["error"]["code"], "stale_inputs")
        self.assertEqual(assignment_path.read_bytes(), assignment_before)
        self.assertEqual(load_runs(self.root)[self.run_id].status, "running")

    def test_lease_expiry_at_commit_rejects_close(self) -> None:
        current = datetime.now(timezone.utc)
        assignment_path = self.root / "assignments" / "assign_x.yaml"
        assignment = load_yaml(assignment_path)
        assignment["lease"]["heartbeat_at"] = current.isoformat().replace("+00:00", "Z")
        assignment["lease"]["expires_at"] = (
            current + timedelta(minutes=10)
        ).isoformat().replace("+00:00", "Z")
        save_yaml(assignment_path, assignment)
        build_dashboard(self.root)

        with patch.object(
            evidence_bundles_module,
            "_commit_time",
            return_value=current + timedelta(minutes=20),
        ):
            with self.assertRaises(AssignmentLeaseError) as caught:
                close_assignment_work(
                    self.root,
                    assignment_id="assign_x",
                    plan=self._plan(operation_id="op_close_expired_at_commit"),
                    now=None,
                )

        self.assertEqual(
            caught.exception.receipt["error"]["code"],
            "lease_expired",
            caught.exception.receipt,
        )
        self.assertEqual(load_runs(self.root)[self.run_id].status, "running")

    def test_exact_retry_does_not_reinspect_changed_evidence_bytes(self) -> None:
        source = self.root.parent / f"evidence_retry_{uuid.uuid4().hex}"
        source.mkdir()
        try:
            payload = source / "metrics.json"
            payload.write_text('{"score": 0.73}', encoding="utf-8")
            plan = self._plan(operation_id="op_close_evidence_retry")
            plan["evidence_inputs"] = {
                "source": str(source),
                "links": {"metrics": "metrics.json"},
            }
            first = close_assignment_work(
                self.root,
                assignment_id="assign_x",
                plan=plan,
                now=NOW + timedelta(minutes=2),
            )
            payload.write_text('{"score": 0.99}', encoding="utf-8")

            with patch.object(
                evidence_staging_module,
                "_reference_inventory",
                side_effect=AssertionError("exact retry scanned evidence"),
            ):
                replay = close_assignment_work(
                    self.root,
                    assignment_id="assign_x",
                    plan=plan,
                    now=NOW + timedelta(minutes=2),
                )

            self.assertEqual(replay, first)
        finally:
            shutil.rmtree(source, ignore_errors=True)

    def test_link_removed_from_staged_snapshot_is_rejected(self) -> None:
        self._configure_managed_root()
        source = self.root.parent / f"evidence_link_race_{uuid.uuid4().hex}"
        source.mkdir()
        try:
            (source / "metrics.json").write_text("{}", encoding="utf-8")
            original_copy = evidence_staging_module._copy_source_tree

            def copy_then_remove(source_path: Path, target_path: Path) -> None:
                original_copy(source_path, target_path)
                (target_path / "metrics.json").unlink()

            with patch.object(
                evidence_staging_module,
                "_copy_source_tree",
                side_effect=copy_then_remove,
            ):
                with self.assertRaises(FileNotFoundError):
                    stage_final_evidence(
                        self.root,
                        assignment_id="assign_x",
                        experiment_id="experiment_x",
                        run_id=self.run_id,
                        agent_id="agent_a",
                        spec={
                            "source": str(source),
                            "links": {"metrics": "metrics.json"},
                        },
                    )
        finally:
            shutil.rmtree(source, ignore_errors=True)

    def test_file_swap_to_symlink_during_snapshot_is_rejected(self) -> None:
        self._configure_managed_root()
        source = self.root.parent / f"evidence_swap_{uuid.uuid4().hex}"
        source.mkdir()
        outside = self.root.parent / f"evidence_outside_{uuid.uuid4().hex}.txt"
        outside.write_text("outside", encoding="utf-8")
        source_file = source / "result.txt"
        source_file.write_text("inside", encoding="utf-8")
        real_open = os.open
        swapped = False

        def swap_before_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
            nonlocal swapped
            if not swapped and Path(path) == source_file:
                source_file.unlink()
                try:
                    source_file.symlink_to(outside)
                except OSError as exc:
                    raise unittest.SkipTest(f"file symlinks are unavailable: {exc}")
                swapped = True
            return real_open(path, flags, *args, **kwargs)

        try:
            with patch.object(evidence_staging_module.os, "open", side_effect=swap_before_open):
                with self.assertRaisesRegex(ValueError, "changed or became a symlink"):
                    stage_final_evidence(
                        self.root,
                        assignment_id="assign_x",
                        experiment_id="experiment_x",
                        run_id=self.run_id,
                        agent_id="agent_a",
                        spec={"source": str(source), "links": {}},
                    )
        finally:
            source_file.unlink(missing_ok=True)
            shutil.rmtree(source, ignore_errors=True)
            outside.unlink(missing_ok=True)
    def test_managed_orphan_recovery_rehashes_target_payload(self) -> None:
        managed_root = self._configure_managed_root()
        source = self.root.parent / f"evidence_orphan_{uuid.uuid4().hex}"
        source.mkdir()
        (source / "result.txt").write_text("trusted", encoding="utf-8")
        plan = self._plan(operation_id="op_close_orphan_recovery")
        plan["evidence_inputs"] = {
            "mode": "managed",
            "source": str(source),
            "links": {},
        }

        def move_then_interrupt(*args: object, **kwargs: object) -> dict:
            staged_moves = kwargs.get("staged_moves") or []
            staging_dir, target_dir = staged_moves[0]
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            staging_dir.replace(target_dir)
            raise KeyboardInterrupt("simulated process interruption")

        try:
            with patch(
                "research_cockpit.assignment_results.complete_run_closeout",
                side_effect=move_then_interrupt,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    close_assignment_work(
                        self.root,
                        assignment_id="assign_x",
                        plan=plan,
                        now=NOW + timedelta(minutes=2),
                    )

            manifest = next(
                managed_root.rglob(evidence_staging_module.MANIFEST_NAME)
            )
            (manifest.parent / "result.txt").write_text(
                "tampered",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(FileExistsError, "content"):
                close_assignment_work(
                    self.root,
                    assignment_id="assign_x",
                    plan=plan,
                    now=NOW + timedelta(minutes=2),
                )
        finally:
            shutil.rmtree(source, ignore_errors=True)

    def test_target_parent_symlink_before_commit_is_rejected(self) -> None:
        managed_root = self._configure_managed_root()
        source = self.root.parent / f"evidence_target_race_{uuid.uuid4().hex}"
        outside = self.root.parent / f"evidence_target_outside_{uuid.uuid4().hex}"
        source.mkdir()
        outside.mkdir()
        (source / "result.txt").write_text("bounded", encoding="utf-8")
        target_parent = managed_root / "experiment_x"
        original_transaction = run_closeout_module.execute_mutation_transaction
        plan = self._plan(operation_id="op_close_target_symlink")
        plan["evidence_inputs"] = {
            "mode": "managed",
            "source": str(source),
            "links": {},
        }

        def inject_target_symlink(*args: object, **kwargs: object) -> dict:
            target_parent.parent.mkdir(parents=True, exist_ok=True)
            try:
                target_parent.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                raise unittest.SkipTest(f"directory symlinks are unavailable: {exc}")
            return original_transaction(*args, **kwargs)

        try:
            with patch(
                "research_cockpit.run_closeout.execute_mutation_transaction",
                side_effect=inject_target_symlink,
            ):
                with self.assertRaises(AssignmentLeaseError) as caught:
                    close_assignment_work(
                        self.root,
                        assignment_id="assign_x",
                        plan=plan,
                        now=NOW + timedelta(minutes=2),
                    )
            self.assertEqual(caught.exception.receipt["error"]["code"], "conflict")
            self.assertFalse((outside / self.run_id).exists())
            self.assertEqual(load_runs(self.root)[self.run_id].status, "running")
        finally:
            if target_parent.is_symlink():
                target_parent.unlink()
            shutil.rmtree(source, ignore_errors=True)
            shutil.rmtree(outside, ignore_errors=True)

    def test_work_close_rolls_back_when_staged_move_fails_and_can_retry(self) -> None:
        managed_root = self._configure_managed_root()
        source = self.root.parent / f"evidence_move_failure_{uuid.uuid4().hex}"
        source.mkdir()
        (source / "result.txt").write_text("bounded", encoding="utf-8")
        plan = self._plan(operation_id="op_close_move_rollback")
        plan["evidence_inputs"] = {
            "mode": "managed",
            "source": str(source),
            "links": {},
        }
        tracked_paths = [
            self.root / "runs" / f"{self.run_id}.yaml",
            self.root / "graph" / "nodes" / "experiment_x.yaml",
            self.root / "assignments" / "assign_x.yaml",
            self.root / "agents" / "agent_a.yaml",
            self.root / "artifact_records" / "experiment_x.yaml",
        ]
        before = {
            path: path.read_bytes() if path.exists() else None for path in tracked_paths
        }
        events_before = list(iter_interaction_events(self.root, strict=True))
        findings_before = sorted(
            path.name for path in (self.root / "graph" / "nodes").glob("finding_*.yaml")
        )
        try:
            with patch.object(
                mutation_runtime_module,
                "_commit_staged_move",
                side_effect=OSError("injected staged move failure"),
            ):
                with self.assertRaises(AssignmentLeaseError) as caught:
                    close_assignment_work(
                        self.root,
                        assignment_id="assign_x",
                        plan=plan,
                        now=NOW + timedelta(minutes=2),
                    )

            self.assertEqual(caught.exception.receipt["error"]["code"], "mutation_failed")
            self.assertTrue(caught.exception.receipt["rolled_back"])
            for path, expected in before.items():
                self.assertEqual(path.read_bytes() if path.exists() else None, expected)
            self.assertEqual(
                list(iter_interaction_events(self.root, strict=True)),
                events_before,
            )
            self.assertEqual(
                sorted(
                    path.name
                    for path in (self.root / "graph" / "nodes").glob("finding_*.yaml")
                ),
                findings_before,
            )
            self.assertFalse(
                any(managed_root.rglob("result.txt"))
            )

            retry = close_assignment_work(
                self.root,
                assignment_id="assign_x",
                plan=plan,
                now=NOW + timedelta(minutes=2),
            )
            self.assertTrue(retry["ok"])
            record_id = retry["entities"]["artifact_record_id"]
            record = load_yaml(
                self.root / "artifact_records" / "experiment_x.yaml"
            )["records"][record_id]
            target = managed_root / record["storage"]["managed_key"]
            self.assertTrue(target.is_dir())
            self.assertTrue((target / "result.txt").is_file())
        finally:
            shutil.rmtree(source, ignore_errors=True)

    def test_work_close_rolls_back_appended_event_and_can_retry(self) -> None:
        managed_root = self._configure_managed_root()
        source = self.root.parent / f"evidence_event_failure_{uuid.uuid4().hex}"
        source.mkdir()
        (source / "result.txt").write_text("bounded", encoding="utf-8")
        plan = self._plan(operation_id="op_close_event_rollback")
        plan["evidence_inputs"] = {
            "mode": "managed",
            "source": str(source),
            "links": {},
        }
        tracked_paths = [
            self.root / "runs" / f"{self.run_id}.yaml",
            self.root / "graph" / "nodes" / "experiment_x.yaml",
            self.root / "assignments" / "assign_x.yaml",
            self.root / "agents" / "agent_a.yaml",
            self.root / "artifact_records" / "experiment_x.yaml",
        ]
        before = {
            path: path.read_bytes() if path.exists() else None for path in tracked_paths
        }
        events_before = list(iter_interaction_events(self.root, strict=True))
        real_append = mutation_runtime_module._append_interaction_log_unlocked

        def append_then_fail(*args: object, **kwargs: object) -> dict:
            real_append(*args, **kwargs)
            raise OSError("injected event append failure")

        try:
            with patch.object(
                mutation_runtime_module,
                "_append_interaction_log_unlocked",
                side_effect=append_then_fail,
            ):
                with self.assertRaises(AssignmentLeaseError) as caught:
                    close_assignment_work(
                        self.root,
                        assignment_id="assign_x",
                        plan=plan,
                        now=NOW + timedelta(minutes=2),
                    )

            self.assertEqual(caught.exception.receipt["error"]["code"], "mutation_failed")
            self.assertTrue(caught.exception.receipt["rolled_back"])
            for path, expected in before.items():
                self.assertEqual(path.read_bytes() if path.exists() else None, expected)
            self.assertEqual(
                list(iter_interaction_events(self.root, strict=True)),
                events_before,
            )
            self.assertFalse(any(managed_root.rglob("result.txt")))

            retry = close_assignment_work(
                self.root,
                assignment_id="assign_x",
                plan=plan,
                now=NOW + timedelta(minutes=2),
            )
            self.assertTrue(retry["ok"])
            record_id = retry["entities"]["artifact_record_id"]
            record = load_yaml(
                self.root / "artifact_records" / "experiment_x.yaml"
            )["records"][record_id]
            target = managed_root / record["storage"]["managed_key"]
            self.assertTrue(target.is_dir())
            self.assertTrue((target / "result.txt").is_file())
        finally:
            shutil.rmtree(source, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
