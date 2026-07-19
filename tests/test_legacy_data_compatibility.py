from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.agent_state import load_assignments
from research_cockpit.artifact_records import build_artifact_record, upsert_artifact_record
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
        self.assertIn(
            f"artifact_{experiment_id}_run_new",
            stored["records"],
        )




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


if __name__ == "__main__":
    unittest.main()
