from __future__ import annotations

import json
from pathlib import Path
import shutil
from datetime import datetime, timedelta, timezone
import sys
import unittest
import uuid


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.agent_state import load_assignment, load_assignments
from research_cockpit.assignment_leases import claim_assignment
from research_cockpit.assignment_results import close_assignment_work
from research_cockpit.assignment_reviews import apply_review_result, report_assignment_review
from research_cockpit.assignment_runs import start_assignment_run
from research_cockpit.artifact_records import build_artifact_record, upsert_artifact_record
from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.commands.create_run import create_run
from research_cockpit.commands.ingest_artifact import ingest_artifact
from research_cockpit.commands.record_gate_result import record_gate_result
from research_cockpit.commands.start_agent_session import start_agent_session
from research_cockpit.commands.update_node_fields import update_node_fields
from research_cockpit.commands.update_run import update_run
from research_cockpit.storage import find_node_file, load_yaml, save_yaml
from research_cockpit.interaction_log import recent_interactions
from research_cockpit.mutation_lock import MutationError


class LegacyDataCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"legacy_compat_{uuid.uuid4().hex}"
        shutil.copytree(
            ROOT_DIR / "examples" / "demo_research_cockpit",
            self.root,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_legacy_assignment_unknown_fields_survive_read_modify_write(self) -> None:
        path = self.root / "assignments" / "assign_legacy.yaml"
        save_yaml(
            path,
            {
                "assignment_id": "assign_legacy",
                "agent_id": "agent_legacy",
                "status": "active",
                "root_node": "option_legacy",
                "current_node": "experiment_legacy",
                "allowed_subtree": {
                    "root": "option_legacy",
                    "policy": "descendants_only",
                    "legacy_scope_hint": "keep",
                },
                "objective": "Continue an older assignment.",
                "legacy_extension": {
                    "custom_owner": "downstream",
                    "custom_flags": ["a", "b"],
                },
            },
        )

        assignment = load_assignments(self.root)["assign_legacy"]
        updated = assignment.to_dict(status="completed", next_actions=["Review result."])
        save_yaml(path, updated)
        stored = load_yaml(path)

        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["next_actions"], ["Review result."])
        self.assertEqual(
            stored["legacy_extension"],
            {"custom_owner": "downstream", "custom_flags": ["a", "b"]},
        )
        self.assertEqual(stored["allowed_subtree"]["legacy_scope_hint"], "keep")
        self.assertNotIn("schema_version", stored)

    def test_legacy_artifact_record_file_metadata_and_payload_survive_upsert(self) -> None:
        record_path = self.root / "artifact_records" / "experiment_legacy.yaml"
        save_yaml(
            record_path,
            {
                "experiment_id": "experiment_legacy",
                "legacy_file_metadata": {"owner": "downstream", "keep": True},
                "records": {
                    "record_old": {
                        "record_id": "record_old",
                        "run_id": "run_old",
                        "status": "recorded",
                        "legacy_record_field": "keep",
                    }
                },
            },
        )
        payload = self.root / "artifacts" / "experiment_legacy" / "run_old" / "result.bin"
        payload.parent.mkdir(parents=True)
        original_bytes = b"legacy-artifact-payload\x00\x01"
        payload.write_bytes(original_bytes)

        _, _, after = upsert_artifact_record(
            self.root,
            "experiment_legacy",
            {
                "record_id": "record_new",
                "experiment_id": "experiment_legacy",
                "run_id": "run_new",
                "status": "recorded",
                "custom_record_field": {"preserve": True},
            },
        )
        save_yaml(record_path, after)
        stored = load_yaml(record_path)

        self.assertEqual(stored["schema_version"], "artifact_records_v1")
        self.assertEqual(
            stored["legacy_file_metadata"],
            {"owner": "downstream", "keep": True},
        )
        self.assertEqual(
            stored["records"]["record_old"]["legacy_record_field"],
            "keep",
        )
        self.assertEqual(
            stored["records"]["record_new"]["custom_record_field"],
            {"preserve": True},
        )
        self.assertEqual(payload.read_bytes(), original_bytes)

    def test_start_session_writer_preserves_nested_legacy_assignment_fields(self) -> None:
        assignment_id = "assign_legacy_writer"
        agent_id = "agent_legacy_writer"
        worktree = self.root.parent / "worktrees" / uuid.uuid4().hex

        start_agent_session(
            self.root,
            option_id="option_demo_prompt_refinement",
            objective="Create the legacy assignment fixture.",
            branch="codex/legacy-writer",
            worktree=worktree,
            agent_id=agent_id,
            assignment_id=assignment_id,
            force=True,
            rebuild_dashboard=False,
        )
        path = self.root / "assignments" / f"{assignment_id}.yaml"
        legacy = load_yaml(path)
        legacy["legacy_extension"] = {"owner": "downstream"}
        legacy["allowed_subtree"]["legacy_scope_hint"] = "keep"
        legacy["worktree"]["legacy_runtime_hint"] = "keep"
        save_yaml(path, legacy)
        agent_path = self.root / "agents" / f"{agent_id}.yaml"
        legacy_agent = load_yaml(agent_path)
        legacy_agent["legacy_agent_extension"] = {"owner": "downstream"}
        save_yaml(agent_path, legacy_agent)

        start_agent_session(
            self.root,
            option_id="option_demo_prompt_refinement",
            objective="Update through the real session writer.",
            branch="codex/legacy-writer",
            worktree=worktree,
            agent_id=agent_id,
            assignment_id=assignment_id,
            force=True,
            rebuild_dashboard=False,
        )
        stored = load_yaml(path)

        self.assertEqual(stored["legacy_extension"], {"owner": "downstream"})
        self.assertEqual(stored["allowed_subtree"]["legacy_scope_hint"], "keep")
        self.assertEqual(stored["worktree"]["legacy_runtime_hint"], "keep")
        self.assertEqual(
            load_yaml(agent_path)["legacy_agent_extension"],
            {"owner": "downstream"},
        )

    def test_ingest_writer_preserves_legacy_record_provenance_and_payload(self) -> None:
        experiment_id = "experiment_demo_prompt_refinement"
        record_id = "artifact_legacy_payload"
        stable_path = f"artifacts/{experiment_id}/run_legacy"
        payload_dir = self.root / stable_path
        payload_dir.mkdir(parents=True)
        payload_path = payload_dir / "result.bin"
        manifest_path = payload_dir / "manifest.json"
        original_bytes = b"legacy-linked-payload\x00\x01"
        original_manifest = json.dumps(
            {
                "schema_version": "artifact_manifest_v1",
                "artifact_id": record_id,
                "stable_path": stable_path,
                "legacy_manifest_field": "keep",
            },
            sort_keys=True,
        ).encode("utf-8")
        payload_path.write_bytes(original_bytes)
        manifest_path.write_bytes(original_manifest)

        record = build_artifact_record(
            record_id=record_id,
            experiment_id=experiment_id,
            run_id="run_legacy",
            artifact_id=record_id,
            title="Legacy linked payload",
            summary="Preserve this 0.2.x record.",
            stable_path=stable_path,
            manifest_path=f"{stable_path}/manifest.json",
            source_file_count=1,
            links={"result": f"{stable_path}/result.bin"},
        )
        record["legacy_record_field"] = {"keep": True}
        record_path = self.root / "artifact_records" / f"{experiment_id}.yaml"
        save_yaml(
            record_path,
            {
                "experiment_id": experiment_id,
                "legacy_file_metadata": {"keep": True},
                "records": {record_id: record},
            },
        )
        node_path = find_node_file(self.root, experiment_id)
        node = load_yaml(node_path)
        node["linked_artifact_records"] = [record_id]
        save_yaml(node_path, node)

        source = self.root / ".agent_runs" / "run_new"
        source.mkdir(parents=True)
        (source / "metrics.json").write_text('{"score": 0.9}', encoding="utf-8")
        try:
            ingest_artifact(
                self.root,
                node_id=experiment_id,
                source_dir=source,
                run_id="run_new",
                links={"metrics": "metrics.json"},
                rebuild_dashboard=False,
                coordinator=True,
            )
        except MutationError as exc:
            self.fail(exc.payload)
        stored = load_yaml(record_path)

        self.assertEqual(stored["legacy_file_metadata"], {"keep": True})
        self.assertEqual(
            stored["records"][record_id]["legacy_record_field"],
            {"keep": True},
        )
        self.assertEqual(
            stored["records"][record_id]["links"],
            {"result": f"{stable_path}/result.bin"},
        )
        self.assertEqual(payload_path.read_bytes(), original_bytes)
        self.assertEqual(manifest_path.read_bytes(), original_manifest)
        new_records = [
            candidate
            for candidate in stored["records"].values()
            if candidate.get("run_id") == "run_new"
        ]
        self.assertEqual(len(new_records), 1)
        self.assertEqual(new_records[0]["experiment_id"], experiment_id)




    def test_node_writer_preserves_unknown_node_and_interaction_fields(self) -> None:
        node_id = "experiment_demo_prompt_refinement"
        node_path = find_node_file(self.root, node_id)
        node = load_yaml(node_path)
        node["legacy_node_extension"] = {"owner": "downstream"}
        save_yaml(node_path, node)

        log_path = self.root / "graph" / "interaction_log.yaml"
        interaction_log = load_yaml(log_path)
        original_event_count = len(interaction_log["events"])
        interaction_log["events"][0]["legacy_event_extension"] = {
            "owner": "downstream"
        }
        save_yaml(log_path, interaction_log)

        update_node_fields(
            self.root,
            node_id=node_id,
            scalar_updates={"summary": "Updated through the real node writer."},
            rebuild_dashboard=False,
            coordinator=True,
        )

        stored_node = load_yaml(node_path)
        stored_log = load_yaml(log_path)
        self.assertEqual(
            stored_node["legacy_node_extension"],
            {"owner": "downstream"},
        )
        self.assertEqual(
            stored_log["events"][0]["legacy_event_extension"],
            {"owner": "downstream"},
        )
        self.assertEqual(len(stored_log["events"]), original_event_count)
        self.assertEqual(
            recent_interactions(self.root, limit=1)[0]["kind"],
            "update_node_fields",
        )

    def test_run_writer_preserves_unknown_legacy_fields(self) -> None:
        run_id = "run_legacy_roundtrip"
        create_run(
            self.root,
            run_id=run_id,
            experiment_id="experiment_demo_prompt_refinement",
            status="queued",
            rebuild_dashboard=False,
            coordinator=True,
        )
        path = self.root / "runs" / f"{run_id}.yaml"
        legacy = load_yaml(path)
        legacy["legacy_run_extension"] = {"owner": "downstream"}
        legacy["resources"] = {"legacy_resource_hint": "keep"}
        save_yaml(path, legacy)

        update_run(
            self.root,
            run_id=run_id,
            status="running",
            rebuild_dashboard=False,
            coordinator=True,
        )
        stored = load_yaml(path)

        self.assertEqual(stored["status"], "running")
        self.assertEqual(
            stored["legacy_run_extension"],
            {"owner": "downstream"},
        )
        self.assertEqual(
            stored["resources"]["legacy_resource_hint"],
            "keep",
        )

    def test_gate_writer_leaves_existing_legacy_gate_and_payload_untouched(self) -> None:
        first = record_gate_result(
            self.root,
            gate_id="gate_legacy_roundtrip",
            experiment_id="experiment_demo_prompt_refinement",
            gate_type="quality",
            passed=True,
            expected={"minimum": 0.8},
            observed={"score": 0.9},
            rebuild_dashboard=False,
            coordinator=True,
        )
        record_path = Path(first["path"])
        payload_path = self.root / first["gate_result_file"]
        legacy_record = load_yaml(record_path)
        legacy_record["legacy_gate_extension"] = {"owner": "downstream"}
        save_yaml(record_path, legacy_record)
        legacy_payload = json.loads(payload_path.read_text(encoding="utf-8"))
        legacy_payload["legacy_payload_extension"] = {"owner": "downstream"}
        payload_path.write_text(
            json.dumps(legacy_payload, indent=2) + "\n",
            encoding="utf-8",
        )
        expected_record = record_path.read_bytes()
        expected_payload = payload_path.read_bytes()

        record_gate_result(
            self.root,
            gate_id="gate_new_roundtrip",
            experiment_id="experiment_demo_prompt_refinement",
            gate_type="quality",
            passed=True,
            expected={"minimum": 0.8},
            observed={"score": 0.95},
            rebuild_dashboard=False,
            coordinator=True,
        )

        self.assertEqual(record_path.read_bytes(), expected_record)
        self.assertEqual(payload_path.read_bytes(), expected_payload)

    def test_legacy_unknown_fields_survive_work_and_review_facades(self) -> None:
        now = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
        option_id = "option_legacy_facade"
        experiment_id = "experiment_legacy_facade"
        assignment_id = "assign_legacy_facade"
        agent_id = "agent_legacy_facade"
        record_id = "record_legacy_facade"
        save_yaml(self.root / "current_state.yaml", {})
        save_yaml(
            self.root / "graph" / "nodes" / f"{option_id}.yaml",
            {
                "id": option_id,
                "type": "option",
                "title": "Legacy facade option",
                "status": "active",
                "children": [experiment_id],
            },
        )
        save_yaml(
            self.root / "graph" / "nodes" / f"{experiment_id}.yaml",
            {
                "id": experiment_id,
                "type": "experiment",
                "title": "Legacy facade experiment",
                "status": "queued",
                "parent": option_id,
                "linked_artifact_records": [record_id],
            },
        )
        save_yaml(
            self.root / "agents" / f"{agent_id}.yaml",
            {
                "agent_id": agent_id,
                "status": "idle",
                "active_assignment_ids": [],
                "legacy_agent_extension": {"keep": True},
            },
        )
        save_yaml(
            self.root / "assignments" / f"{assignment_id}.yaml",
            {
                "assignment_id": assignment_id,
                "agent_id": None,
                "status": "queued",
                "root_node": option_id,
                "current_node": experiment_id,
                "allowed_subtree": {
                    "root": option_id,
                    "policy": "descendants_only",
                    "legacy_scope_hint": "keep",
                },
                "scope": {
                    "root_node": option_id,
                    "subtree_policy": "descendants_only",
                    "write_policy": "exclusive",
                },
                "inputs": {
                    "effective_baseline_revision": None,
                    "dependency_revisions": {},
                },
                "input_revision": "input-v1:legacy-facade",
                "objective": "Round-trip legacy fields through new facades.",
                "review": {"required": True, "status": "pending", "result_revision": None},
                "legacy_assignment_extension": {"owner": "downstream", "keep": True},
            },
        )
        stable_path = f"artifacts/{experiment_id}/legacy_payload"
        payload_path = self.root / stable_path / "result.bin"
        manifest_path = self.root / stable_path / "_research_cockpit_ingest.json"
        payload_path.parent.mkdir(parents=True)
        payload_bytes = b"legacy-facade-payload\x00\x01"
        manifest_bytes = (
            json.dumps(
                {
                    "schema_version": "evidence_ingest_v1",
                    "record_id": record_id,
                    "content_sha256": "a" * 64,
                    "legacy_manifest_extension": {"keep": True},
                },
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        payload_path.write_bytes(payload_bytes)
        manifest_path.write_bytes(manifest_bytes)
        record_path = self.root / "artifact_records" / f"{experiment_id}.yaml"
        save_yaml(
            record_path,
            {
                "schema_version": "artifact_records_v1",
                "experiment_id": experiment_id,
                "legacy_file_extension": {"keep": True},
                "records": {
                    record_id: {
                        "record_id": record_id,
                        "experiment_id": experiment_id,
                        "run_id": "legacy_payload",
                        "status": "recorded",
                        "stable_path": stable_path,
                        "manifest_path": f"{stable_path}/_research_cockpit_ingest.json",
                        "content_sha256": "a" * 64,
                        "links": {"result": f"{stable_path}/result.bin"},
                        "legacy_record_extension": {"keep": True},
                    }
                },
            },
        )
        build_dashboard(self.root)

        claimed = claim_assignment(
            self.root,
            assignment_id=assignment_id,
            agent_id=agent_id,
            operation_id="op_claim_legacy_facade",
            now=now,
        )
        lease = claimed["packet"]["lease"]
        started = start_assignment_run(
            self.root,
            assignment_id=assignment_id,
            agent_id=agent_id,
            lease_id=lease["lease_id"],
            lease_epoch=lease["lease_epoch"],
            operation_id="op_start_legacy_facade",
            input_revision=claimed["packet"]["input_revision"],
            slug_hint="legacy",
            now=now + timedelta(seconds=30),
        )
        run_id = started["entities"]["run_id"]
        run_path = self.root / "runs" / f"{run_id}.yaml"
        run = load_yaml(run_path)
        run["legacy_run_extension"] = {"owner": "downstream", "keep": True}
        run["resources"] = {"legacy_resource_hint": "keep"}
        save_yaml(run_path, run)
        record_file = load_yaml(record_path)
        record_file["records"][record_id]["run_id"] = run_id
        save_yaml(record_path, record_file)
        build_dashboard(self.root)
        record_bytes = record_path.read_bytes()

        close_receipt = close_assignment_work(
            self.root,
            assignment_id=assignment_id,
            plan={
                "schema_version": "work_close_v1",
                "agent_id": agent_id,
                "lease_id": lease["lease_id"],
                "lease_epoch": lease["lease_epoch"],
                "operation_id": "op_close_legacy_facade",
                "input_revision": "input-v1:legacy-facade",
                "run": {"id": run_id, "status": "completed"},
                "experiment": {
                    "status": "done",
                    "result_summary": "Legacy facade round-trip completed.",
                },
                "finding": {
                    "statement": "Legacy state remained readable and writable.",
                    "confidence": "strong",
                    "outcome": "positive",
                },
                "artifact_record": {"existing_record_id": record_id},
                "assignment_result": {
                    "outcome": "positive",
                    "summary": "The compatibility closeout completed.",
                    "delivery": {
                        "git_commit": None,
                        "changed_files": [],
                        "tests": {"status": "passed", "summary": "Compatibility passed."},
                    },
                    "proposals": [],
                },
                "review_required": True,
            },
            now=now + timedelta(minutes=2),
        )
        producer = load_assignment(self.root, assignment_id)
        producer_result_bytes = json.dumps(
            producer.result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(
            producer.raw["legacy_assignment_extension"],
            {"owner": "downstream", "keep": True},
        )
        self.assertEqual(
            load_yaml(run_path)["legacy_run_extension"],
            {"owner": "downstream", "keep": True},
        )
        self.assertEqual(record_path.read_bytes(), record_bytes)
        self.assertEqual(payload_path.read_bytes(), payload_bytes)
        self.assertEqual(manifest_path.read_bytes(), manifest_bytes)

        review_id = "assign_review_legacy_facade"
        reviewer_id = "reviewer_legacy_facade"
        save_yaml(
            self.root / "agents" / f"{reviewer_id}.yaml",
            {"agent_id": reviewer_id, "status": "idle", "active_assignment_ids": []},
        )
        save_yaml(
            self.root / "assignments" / f"{review_id}.yaml",
            {
                "assignment_id": review_id,
                "agent_id": None,
                "kind": "review",
                "status": "queued",
                "root_node": option_id,
                "current_node": experiment_id,
                "allowed_subtree": {"root": option_id, "policy": "descendants_only"},
                "scope": {
                    "root_node": option_id,
                    "subtree_policy": "descendants_only",
                    "write_policy": "review_read_only",
                },
                "dependencies": [
                    {"assignment_id": assignment_id, "required_status": "completed"}
                ],
                "inputs": {
                    "effective_baseline_revision": None,
                    "dependency_revisions": {
                        assignment_id: close_receipt["result_revision"]
                    },
                },
                "input_revision": "input-v1:legacy-review",
                "objective": "Review the compatibility result.",
                "review": {
                    "required": False,
                    "status": "not_required",
                    "result_revision": None,
                },
                "legacy_review_extension": {"keep": True},
            },
        )
        build_dashboard(self.root)
        review_claim = claim_assignment(
            self.root,
            assignment_id=review_id,
            agent_id=reviewer_id,
            operation_id="op_claim_legacy_review",
            now=now + timedelta(minutes=3),
        )
        review_lease = review_claim["packet"]["lease"]
        producer_path = self.root / "assignments" / f"{assignment_id}.yaml"
        producer_before_report = producer_path.read_bytes()
        report = report_assignment_review(
            self.root,
            assignment_id=review_id,
            plan={
                "schema_version": "review_report_v1",
                "agent_id": reviewer_id,
                "lease_id": review_lease["lease_id"],
                "lease_epoch": review_lease["lease_epoch"],
                "operation_id": "op_report_legacy_review",
                "input_revision": "input-v1:legacy-review",
                "producer_result_revision": close_receipt["result_revision"],
                "verdict": "approved",
                "summary": "The compatibility evidence is intact.",
                "findings": [],
                "evidence_inspected": [record_id],
                "validation_performed": ["Legacy round-trip assertions"],
            },
            now=now + timedelta(minutes=4),
        )
        self.assertEqual(producer_path.read_bytes(), producer_before_report)
        self.assertEqual(
            load_assignment(self.root, review_id).raw["legacy_review_extension"],
            {"keep": True},
        )

        apply_review_result(
            self.root,
            producer_assignment_id=assignment_id,
            plan={
                "schema_version": "coord_review_v1",
                "operation_id": "op_apply_legacy_review",
                "review_assignment_id": review_id,
                "review_result_revision": report["result_revision"],
                "producer_result_revision": close_receipt["result_revision"],
            },
            now=now + timedelta(minutes=5),
        )
        producer_after_review = load_assignment(self.root, assignment_id)
        self.assertEqual(producer_after_review.result["revision"], close_receipt["result_revision"])
        self.assertEqual(
            json.dumps(
                producer_after_review.result,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            producer_result_bytes,
        )
        self.assertEqual(
            producer_after_review.raw["legacy_assignment_extension"],
            {"owner": "downstream", "keep": True},
        )
        self.assertEqual(record_path.read_bytes(), record_bytes)
        self.assertEqual(payload_path.read_bytes(), payload_bytes)
        self.assertEqual(manifest_path.read_bytes(), manifest_bytes)

if __name__ == "__main__":
    unittest.main()
