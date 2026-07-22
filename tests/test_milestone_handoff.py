from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import yaml



ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.commands.build_dashboard import build_dashboard_from_validated_state
from research_cockpit.commands.skill_smoke_test import compact_root_smoke_from_validation
from research_cockpit.commands.validate_cockpit import full_validation_snapshot
from research_cockpit.milestone_handoffs import execute_milestone_handoff, root_truth_revision
from research_cockpit.model import ResearchNode, validate_cockpit
from research_cockpit.research_ledger import build_research_ledger
from research_cockpit.storage import load_yaml, save_yaml


class MilestoneHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"milestone_handoff_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        self._write_node(
            {
                "id": "stage_x",
                "type": "stage",
                "title": "Stage X",
                "status": "active",
                "children": ["problem_x"],
            }
        )
        self._write_node(
            {
                "id": "problem_x",
                "type": "problem",
                "title": "Problem X",
                "status": "active",
                "parent": "stage_x",
                "children": ["option_x"],
            }
        )
        self._write_node(
            {
                "id": "option_x",
                "type": "option",
                "title": "Option X",
                "status": "active",
                "parent": "problem_x",
            }
        )
        save_yaml(
            self.root / "current_state.yaml",
            {
                "current_stage": "stage_x",
                "current_problem": "problem_x",
                "current_option": "option_x",
                "current_focus_path": ["stage_x", "problem_x", "option_x"],
            },
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_node(self, payload: dict) -> None:
        save_yaml(self.root / "graph" / "nodes" / f"{payload['id']}.yaml", payload)

    def _ledger_repo(self) -> Path:
        repo = self.root / "ledger_repo"
        repo.mkdir()
        completed = subprocess.run(
            ["git", "init", str(repo)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return repo

    @staticmethod
    def _plan(operation_id: str = "handoff_test", **overrides: object) -> dict:
        plan = {
            "schema_version": "coord_handoff_v1",
            "operation_id": operation_id,
            "kind": "merge",
            "summary": "Phase handoff",
        }
        plan.update(overrides)
        return plan

    @staticmethod
    def _build_result() -> dict:
        return {
            "ok": True,
            "node_count": 3,
            "written_files": ["dashboards/validation_index.json"],
        }

    @staticmethod
    def _smoke_result() -> dict:
        return {
            "ok": True,
            "mode": "compact",
            "checks": [{"name": "validate_cockpit", "passed": True}],
        }

    def test_truth_revision_tracks_control_state_but_not_artifact_payloads(self) -> None:
        initial = root_truth_revision(self.root)
        payload_path = self.root / "artifacts" / "large" / "result.bin"
        payload_path.parent.mkdir(parents=True)
        payload_path.write_bytes(b"first")

        self.assertEqual(root_truth_revision(self.root), initial)

        payload_path.write_bytes(b"second payload")
        self.assertEqual(root_truth_revision(self.root), initial)

        save_yaml(
            self.root / "artifact_records" / "artifact_x.yaml",
            {
                "id": "artifact_x",
                "schema_version": "artifact_record_v1",
                "uri": "artifacts/large/result.bin",
            },
        )
        self.assertNotEqual(root_truth_revision(self.root), initial)

    def test_truth_revision_never_enumerates_artifact_payload_root(self) -> None:
        artifact_root = self.root / "artifacts"
        artifact_root.mkdir()
        original_rglob = type(self.root).rglob

        def reject_payload_scan(path: Path, pattern: str):
            if path == artifact_root:
                raise AssertionError("artifact payload root was enumerated")
            return original_rglob(path, pattern)

        with patch.object(type(self.root), "rglob", reject_payload_scan):
            root_truth_revision(self.root)

    @staticmethod
    def _coordination_state(rows: list[dict] | None = None) -> dict:
        items = rows or []
        return {
            "rows": items,
            "counts": {
                "waiting": 0,
                "ready": 0,
                "active": sum(item.get("status") == "active" for item in items),
                "blocked": sum(item.get("status") == "blocked" for item in items),
                "stale_inputs": sum(item.get("readiness") == "stale_inputs" for item in items),
                "expired_leases": sum(item.get("lease_state") == "expired" for item in items),
                "pending_review": sum(item.get("review_status") == "pending" for item in items),
            },
            "overlap_warnings": [],
        }

    def test_coord_handoff_cli_exposes_input_schema(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "research_cockpit.cli",
                "coord",
                "handoff",
                "--print-schema",
            ],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        schema = yaml.safe_load(completed.stdout)
        self.assertEqual(schema["schema_version"], "coord_handoff_v1")
        self.assertEqual(schema["kind"], "release")

    def test_handoff_input_rejects_non_string_scalar_fields(self) -> None:
        invalid_values = (
            ("operation_id", 123),
            ("kind", ["merge"]),
            ("summary", {"text": "handoff"}),
        )
        for field, value in invalid_values:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                execute_milestone_handoff(
                    self.root,
                    self._plan(**{field: value}),
                )

    def test_coord_handoff_cli_returns_json_for_invalid_input(self) -> None:
        plan_path = self.root / "invalid_handoff.yaml"
        save_yaml(
            plan_path,
            {
                "schema_version": "coord_handoff_v1",
                "operation_id": "handoff_invalid",
                "kind": "merge",
                "summary": ["not", "text"],
            },
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "research_cockpit.cli",
                "coord",
                "handoff",
                "--root",
                str(self.root),
                "--file",
                str(plan_path),
                "--json",
                "--compact",
            ],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["error"]["code"], "invalid_handoff_input")
        self.assertEqual(completed.stderr, "")

    def test_validation_snapshot_is_reused_by_build_and_compact_smoke(self) -> None:
        with patch(
            "research_cockpit.commands.validate_cockpit.validate_cockpit",
            wraps=validate_cockpit,
        ) as validator:
            validation, state = full_validation_snapshot(self.root)

        self.assertTrue(validation["ok"])
        self.assertEqual(validator.call_count, 1)
        with (
            patch(
                "research_cockpit.commands.build_dashboard.load_nodes",
                side_effect=AssertionError("build reloaded nodes"),
            ),
            patch(
                "research_cockpit.commands.build_dashboard.validate_cockpit",
                side_effect=AssertionError("build revalidated"),
            ),
        ):
            build = build_dashboard_from_validated_state(self.root, state)
        with (
            patch(
                "research_cockpit.model.load_nodes",
                side_effect=AssertionError("smoke reloaded nodes"),
            ),
            patch(
                "research_cockpit.model.validate_cockpit",
                side_effect=AssertionError("smoke revalidated"),
            ),
        ):
            smoke = compact_root_smoke_from_validation(self.root, validation, state)

        self.assertTrue(build["ok"])
        self.assertTrue(smoke["ok"])
        self.assertTrue(smoke["checks"][0]["summary"]["reused_validation"])

    def test_success_runs_each_gate_once_without_holding_canonical_lock(self) -> None:
        canonical_lock = self.root / "graph" / ".mutation.lock"

        def build_once(_root: Path, _state: object) -> dict:
            self.assertFalse(canonical_lock.exists())
            return self._build_result()

        def smoke_once(_root: Path, _validation: dict, _state: object) -> dict:
            self.assertFalse(canonical_lock.exists())
            return self._smoke_result()

        with (
            patch(
                "research_cockpit.milestone_handoffs.build_dashboard_from_validated_state",
                side_effect=build_once,
            ) as build,
            patch(
                "research_cockpit.milestone_handoffs.compact_root_smoke_from_validation",
                side_effect=smoke_once,
            ) as smoke,
            patch(
                "research_cockpit.milestone_handoffs.build_coordination_state",
                return_value=self._coordination_state(),
            ),
        ):
            result = execute_milestone_handoff(self.root, self._plan())

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(build.call_count, 1)
        self.assertEqual(smoke.call_count, 1)
        report = load_yaml(self.root / "handoffs" / "handoff_test.yaml")
        self.assertEqual(report["receipt"], result)
        self.assertEqual(report["target_revision"], result["target_revision"])
        self.assertLess(len(json.dumps(result, separators=(",", ":")).encode("utf-8")), 8 * 1024)

    def test_exact_retry_returns_stored_receipt_without_rerunning_gates(self) -> None:
        with (
            patch(
                "research_cockpit.milestone_handoffs.build_dashboard_from_validated_state",
                return_value=self._build_result(),
            ),
            patch(
                "research_cockpit.milestone_handoffs.compact_root_smoke_from_validation",
                return_value=self._smoke_result(),
            ),
            patch(
                "research_cockpit.milestone_handoffs.build_coordination_state",
                return_value=self._coordination_state(),
            ),
        ):
            first = execute_milestone_handoff(self.root, self._plan("handoff_replay"))

        with (
            patch(
                "research_cockpit.milestone_handoffs.full_validation_snapshot",
                side_effect=AssertionError("validation reran"),
            ),
            patch(
                "research_cockpit.milestone_handoffs.build_dashboard_from_validated_state",
                side_effect=AssertionError("build reran"),
            ),
            patch(
                "research_cockpit.milestone_handoffs.compact_root_smoke_from_validation",
                side_effect=AssertionError("smoke reran"),
            ),
        ):
            replay = execute_milestone_handoff(self.root, self._plan("handoff_replay"))

        self.assertEqual(replay, first)

    def test_exact_retry_rebuilds_missing_portable_ledger_without_rerunning_gates(self) -> None:
        repo = self._ledger_repo()
        plan = self._plan("handoff_ledger")
        with (
            patch(
                "research_cockpit.milestone_handoffs.build_dashboard_from_validated_state",
                return_value=self._build_result(),
            ),
            patch(
                "research_cockpit.milestone_handoffs.compact_root_smoke_from_validation",
                return_value=self._smoke_result(),
            ),
            patch(
                "research_cockpit.milestone_handoffs.build_coordination_state",
                return_value=self._coordination_state(),
            ),
        ):
            first = execute_milestone_handoff(self.root, plan, repo=repo)

        ledger_path = repo / "research-ledger" / "handoff_ledger.yaml"
        self.assertEqual(first["ledger_file"], "research-ledger/handoff_ledger.yaml")
        self.assertTrue(ledger_path.is_file())
        first_bytes = ledger_path.read_bytes()
        ledger = load_yaml(ledger_path)
        self.assertEqual(ledger["schema_version"], "research_ledger_v1")
        self.assertEqual(ledger["milestone"]["state_revision"], first["target_revision"])
        self.assertNotIn(str(self.root), ledger_path.read_text(encoding="utf-8"))
        self.assertNotIn(str(repo), ledger_path.read_text(encoding="utf-8"))

        ledger_path.unlink()
        with (
            patch(
                "research_cockpit.milestone_handoffs.full_validation_snapshot",
                side_effect=AssertionError("validation reran"),
            ),
            patch(
                "research_cockpit.milestone_handoffs.build_dashboard_from_validated_state",
                side_effect=AssertionError("build reran"),
            ),
            patch(
                "research_cockpit.milestone_handoffs.compact_root_smoke_from_validation",
                side_effect=AssertionError("smoke reran"),
            ),
        ):
            replay = execute_milestone_handoff(self.root, plan, repo=repo)

        self.assertEqual(replay, first)
        self.assertEqual(ledger_path.read_bytes(), first_bytes)

    def test_ledger_keeps_portable_artifact_uris_and_omits_local_paths(self) -> None:
        save_yaml(
            self.root / "artifact_records" / "experiment_final.yaml",
            {
                "schema_version": "artifact_records_v1",
                "experiment_id": "experiment_final",
                "records": {
                    "record_local": {
                        "record_id": "record_local",
                        "experiment_id": "experiment_final",
                        "artifact_id": "artifact_local",
                        "storage": {
                            "mode": "reference",
                            "ownership": "external",
                            "uri": f"file://{self.root}/payload.bin",
                            "managed_key": None,
                        },
                    },
                    "record_remote": {
                        "record_id": "record_remote",
                        "experiment_id": "experiment_final",
                        "artifact_id": "artifact_remote",
                        "storage": {
                            "mode": "reference",
                            "ownership": "external",
                            "uri": "https://example.invalid/evidence/final.json",
                            "managed_key": None,
                        },
                    },
                },
            },
        )
        option = ResearchNode.from_dict(
            {"id": "option_final", "type": "option", "title": "Option", "status": "accepted"}
        )
        decision = ResearchNode.from_dict(
            {
                "id": "decision_final",
                "type": "decision",
                "title": "Decision",
                "status": "accepted",
                "parent": "option_final",
                "linked_artifact_records": ["record_local", "record_remote"],
            }
        )
        experiment = ResearchNode.from_dict(
            {
                "id": "experiment_final",
                "type": "experiment",
                "title": "Experiment",
                "status": "done",
                "parent": "option_final",
                "findings": [
                    {
                        "statement": "Remote evidence supports the selected option.",
                        "confidence": "strong",
                        "outcome": "success",
                        "metrics": {"score": 0.91},
                        "linked_artifact_records": ["record_remote"],
                    }
                ],
            }
        )
        state = SimpleNamespace(
            nodes={node.id: node for node in (option, decision, experiment)},
            current={"current_option": "option_final"},
            runs={},
            assignments={},
        )

        ledger = build_research_ledger(
            self.root,
            state,
            operation_id="handoff_portable",
            kind="release",
            target_revision="root-v1:test",
            timestamp="2026-07-22T00:00:00Z",
        )
        text = yaml.safe_dump(ledger, sort_keys=False)

        self.assertEqual(ledger["accepted_decisions"][0]["id"], "decision_final")
        self.assertEqual(ledger["final_findings"][0]["metrics"], {"score": 0.91})
        remote = next(item for item in ledger["reviewed_artifacts"] if item["record_id"] == "record_remote")
        local = next(item for item in ledger["reviewed_artifacts"] if item["record_id"] == "record_local")
        self.assertEqual(remote["uri"], "https://example.invalid/evidence/final.json")
        self.assertNotIn("uri", local)
        self.assertNotIn("file://", text)
        self.assertNotIn(str(self.root), text)

    def test_reused_operation_id_with_changed_request_is_rejected_before_gates(self) -> None:
        with (
            patch(
                "research_cockpit.milestone_handoffs.build_dashboard_from_validated_state",
                return_value=self._build_result(),
            ),
            patch(
                "research_cockpit.milestone_handoffs.compact_root_smoke_from_validation",
                return_value=self._smoke_result(),
            ),
            patch(
                "research_cockpit.milestone_handoffs.build_coordination_state",
                return_value=self._coordination_state(),
            ),
        ):
            execute_milestone_handoff(self.root, self._plan("handoff_conflict"))

        with patch(
            "research_cockpit.milestone_handoffs.full_validation_snapshot",
            side_effect=AssertionError("validation reran"),
        ):
            conflict = execute_milestone_handoff(
                self.root,
                self._plan("handoff_conflict", summary="Changed request"),
            )

        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["status"], "idempotency_conflict")

    def test_truth_change_during_gates_returns_stale_without_receipt(self) -> None:
        def mutate_truth(_root: Path, _state: object) -> dict:
            current = load_yaml(self.root / "current_state.yaml")
            current["note"] = "changed during handoff"
            save_yaml(self.root / "current_state.yaml", current)
            return self._build_result()

        with (
            patch(
                "research_cockpit.milestone_handoffs.build_dashboard_from_validated_state",
                side_effect=mutate_truth,
            ),
            patch(
                "research_cockpit.milestone_handoffs.compact_root_smoke_from_validation",
                return_value=self._smoke_result(),
            ),
            patch(
                "research_cockpit.milestone_handoffs.build_coordination_state",
                return_value=self._coordination_state(),
            ),
            patch("research_cockpit.milestone_handoffs.mark_validation_index_stale") as mark_stale,
        ):
            result = execute_milestone_handoff(self.root, self._plan("handoff_stale"))

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "handoff_stale")
        self.assertFalse((self.root / "handoffs" / "handoff_stale.yaml").exists())
        mark_stale.assert_called_once()

    def test_truth_change_at_commit_returns_stale_receipt(self) -> None:
        def mutate_before_commit(
            _root: Path,
            _changes: list[tuple],
            **kwargs: object,
        ) -> dict:
            current = load_yaml(self.root / "current_state.yaml")
            current["note"] = "changed before receipt commit"
            save_yaml(self.root / "current_state.yaml", current)
            validators = kwargs["commit_validators"]
            assert isinstance(validators, list)
            validators[0]()
            raise AssertionError("stale validator must reject the commit")

        with (
            patch(
                "research_cockpit.milestone_handoffs.build_dashboard_from_validated_state",
                return_value=self._build_result(),
            ),
            patch(
                "research_cockpit.milestone_handoffs.compact_root_smoke_from_validation",
                return_value=self._smoke_result(),
            ),
            patch(
                "research_cockpit.milestone_handoffs.build_coordination_state",
                return_value=self._coordination_state(),
            ),
            patch(
                "research_cockpit.milestone_handoffs.execute_mutation_transaction",
                side_effect=mutate_before_commit,
            ),
            patch("research_cockpit.milestone_handoffs.mark_validation_index_stale") as mark_stale,
        ):
            result = execute_milestone_handoff(
                self.root,
                self._plan("handoff_commit_stale"),
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "handoff_stale")
        self.assertEqual(result["error"]["code"], "handoff_stale")
        self.assertFalse((self.root / "handoffs" / "handoff_commit_stale.yaml").exists())
        mark_stale.assert_called_once()

    def test_gate_failure_stops_pipeline_without_writing_receipt(self) -> None:
        with (
            patch(
                "research_cockpit.milestone_handoffs.build_dashboard_from_validated_state",
                side_effect=RuntimeError("build failed"),
            ),
            patch(
                "research_cockpit.milestone_handoffs.compact_root_smoke_from_validation"
            ) as smoke,
        ):
            result = execute_milestone_handoff(self.root, self._plan("handoff_failed"))

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "build_failed")
        smoke.assert_not_called()
        self.assertFalse((self.root / "handoffs" / "handoff_failed.yaml").exists())

    def test_coordination_failure_is_reported_as_coordination_failure(self) -> None:
        with (
            patch(
                "research_cockpit.milestone_handoffs.build_dashboard_from_validated_state",
                return_value=self._build_result(),
            ),
            patch(
                "research_cockpit.milestone_handoffs.compact_root_smoke_from_validation",
                return_value=self._smoke_result(),
            ),
            patch(
                "research_cockpit.milestone_handoffs.build_coordination_state",
                side_effect=RuntimeError("coordination snapshot failed"),
            ),
        ):
            result = execute_milestone_handoff(
                self.root,
                self._plan("handoff_coordination_failed"),
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "coordination_failed")
        self.assertFalse((self.root / "handoffs" / "handoff_coordination_failed.yaml").exists())

    def test_pending_review_stale_input_and_active_lease_block_handoff(self) -> None:
        rows = [
            {
                "assignment_id": "assign_review",
                "status": "completed",
                "readiness": "ready",
                "review_status": "pending",
                "lease_state": "unclaimed",
            },
            {
                "assignment_id": "assign_stale",
                "status": "active",
                "readiness": "stale_inputs",
                "review_status": "not_required",
                "lease_state": "active",
            },
        ]
        with (
            patch(
                "research_cockpit.milestone_handoffs.build_dashboard_from_validated_state",
                return_value=self._build_result(),
            ),
            patch(
                "research_cockpit.milestone_handoffs.compact_root_smoke_from_validation",
                return_value=self._smoke_result(),
            ),
            patch(
                "research_cockpit.milestone_handoffs.build_coordination_state",
                return_value=self._coordination_state(rows),
            ),
        ):
            result = execute_milestone_handoff(self.root, self._plan("handoff_blocked"))

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blockers"]["pending_reviews"]["total"], 1)
        self.assertEqual(result["blockers"]["stale_inputs"]["total"], 1)
        self.assertEqual(result["blockers"]["active_leases"]["total"], 1)
        self.assertTrue((self.root / "handoffs" / "handoff_blocked.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
