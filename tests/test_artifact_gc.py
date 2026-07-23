from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.artifact_gc import (
    execute_managed_artifact_gc,
    plan_managed_artifact_gc,
)
from research_cockpit.artifact_migration import migrate_legacy_artifact
from research_cockpit.milestone_handoffs import root_truth_revision
from research_cockpit.storage import load_yaml, save_yaml


class ManagedArtifactGcTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = Path(tempfile.gettempdir())
        token = uuid.uuid4().hex
        self.root = parent / f"artifact_gc_{token}"
        self.store = parent / f"artifact_gc_store_{token}"
        shutil.copytree(ROOT_DIR / "examples" / "demo_research_cockpit", self.root)
        self.experiment_id = "experiment_demo_prompt_refinement"
        self.run_id = "run_gc"
        self.record_id = "record_gc"
        self.payload = self.root / "artifacts" / self.experiment_id / self.run_id
        self.payload.mkdir(parents=True)
        (self.payload / "metrics.json").write_text('{"score": 0.9}', encoding="utf-8")
        save_yaml(
            self.root / "storage.yaml",
            {
                "schema_version": "storage_layout_v1",
                "project_id": "gc-test",
                "artifact_root": str(self.store),
            },
        )
        save_yaml(
            self.root / "artifact_records" / f"{self.experiment_id}.yaml",
            {
                "schema_version": "artifact_records_v1",
                "experiment_id": self.experiment_id,
                "records": {
                    self.record_id: {
                        "record_id": self.record_id,
                        "experiment_id": self.experiment_id,
                        "run_id": self.run_id,
                        "stable_path": (
                            f"artifacts/{self.experiment_id}/{self.run_id}"
                        ),
                        "links": {},
                        "retention": {"class": "reproducible_output"},
                    }
                },
            },
        )
        with mock.patch(
            "research_cockpit.artifact_migration._same_filesystem",
            return_value=False,
        ):
            migrate_legacy_artifact(
                self.root,
                record_id=self.record_id,
                operation_id="migrate-gc-001",
                execute=True,
            )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.store, ignore_errors=True)

    def _record_path(self) -> Path:
        return self.root / "artifact_records" / f"{self.experiment_id}.yaml"

    def _record(self) -> dict:
        return load_yaml(self._record_path())["records"][self.record_id]

    def _plan(
        self,
        *,
        phase: str = "quarantine",
        operation_id: str = "gc-quarantine-001",
        purge_after_seconds: int = 60,
    ) -> dict:
        return plan_managed_artifact_gc(
            self.root,
            record_id=self.record_id,
            operation_id=operation_id,
            phase=phase,
            purge_after_seconds=purge_after_seconds,
        )

    def _execute(
        self,
        *,
        phase: str,
        operation_id: str,
        expected_revision: str,
        purge_after_seconds: int = 60,
    ) -> dict:
        return execute_managed_artifact_gc(
            self.root,
            record_id=self.record_id,
            operation_id=operation_id,
            phase=phase,
            expected_revision=expected_revision,
            purge_after_seconds=purge_after_seconds,
            execute=True,
        )

    def test_plan_is_dry_run_and_binds_the_current_state_revision(self) -> None:
        before = root_truth_revision(self.root)
        record_before = self._record()

        plan = self._plan()

        self.assertTrue(plan["dry_run"])
        self.assertTrue(plan["eligible"])
        self.assertEqual(plan["state_revision"], before)
        self.assertEqual(plan["record_id"], self.record_id)
        self.assertFalse((self.root / "artifact_gc_manifests").exists())
        self.assertEqual(self._record(), record_before)

    def test_gc_transition_manifests_are_part_of_the_truth_revision(self) -> None:
        before = root_truth_revision(self.root)
        save_yaml(
            self.root / "artifact_gc_manifests" / "probe.yaml",
            {"schema_version": "artifact_gc_transition_v1", "transition": "probe"},
        )

        self.assertNotEqual(before, root_truth_revision(self.root))

    def test_execute_rejects_a_stale_revision_before_moving_payload(self) -> None:
        plan = self._plan()
        current = load_yaml(self.root / "current_state.yaml")
        current["updated_at"] = "2099-01-01"
        save_yaml(self.root / "current_state.yaml", current)

        with self.assertRaisesRegex(ValueError, "plan is stale"):
            self._execute(
                phase="quarantine",
                operation_id="gc-quarantine-001",
                expected_revision=plan["state_revision"],
            )
        self.assertTrue(
            (self.store / self.experiment_id / self.run_id / self.record_id).exists()
        )

    def test_quarantine_is_revision_bound_immutable_and_idempotent(self) -> None:
        plan = self._plan()
        result = self._execute(
            phase="quarantine",
            operation_id="gc-quarantine-001",
            expected_revision=plan["state_revision"],
        )
        record = self._record()
        target = self.store / self.experiment_id / self.run_id / self.record_id
        quarantine = Path(result["quarantine_path"])
        manifest_dir = self.root / "artifact_gc_manifests"

        self.assertFalse(target.exists())
        self.assertTrue((quarantine / "metrics.json").is_file())
        self.assertEqual(record["availability"]["status"], "quarantined")
        self.assertEqual(record["gc"]["operation_id"], "gc-quarantine-001")
        self.assertEqual(len(list(manifest_dir.glob("*.yaml"))), 2)
        self.assertEqual(result["status"], "quarantined")
        self.assertNotEqual(plan["state_revision"], root_truth_revision(self.root))

        replay = self._execute(
            phase="quarantine",
            operation_id="gc-quarantine-001",
            expected_revision="root-v1:stale-value-is-ignored-for-exact-retry",
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(list(manifest_dir.glob("*.yaml"))), 2)

    def test_active_and_weak_integrity_records_cannot_quarantine(self) -> None:
        save_yaml(
            self.root / "assignments" / "assignment_active.yaml",
            {
                "assignment_id": "assignment_active",
                "agent_id": None,
                "status": "queued",
                "root_node": "option_demo_prompt_refinement",
                "current_node": self.experiment_id,
                "allowed_subtree": {
                    "root": "option_demo_prompt_refinement",
                    "policy": "descendants_only",
                },
            },
        )
        plan = self._plan()
        self.assertFalse(plan["eligible"])
        self.assertIn("active_assignment:assignment_active", plan["blockers"])

        record_file = load_yaml(self._record_path())
        record_file["records"][self.record_id]["integrity"] = {
            "level": "inventory",
            "algorithm": "sha256",
            "digest": "inventory-sha256:" + "0" * 64,
        }
        save_yaml(self._record_path(), record_file)
        weak_plan = self._plan(operation_id="gc-weak-001")
        self.assertIn("weak_integrity", weak_plan["blockers"])
        with self.assertRaisesRegex(ValueError, "weak_integrity"):
            self._execute(
                phase="quarantine",
                operation_id="gc-weak-001",
                expected_revision=weak_plan["state_revision"],
            )

    def test_purge_waits_for_delay_then_recovers_after_delete_failure(self) -> None:
        quarantine_plan = self._plan()
        quarantine = self._execute(
            phase="quarantine",
            operation_id="gc-quarantine-001",
            expected_revision=quarantine_plan["state_revision"],
        )
        purge_plan = self._plan(
            phase="purge",
            operation_id="gc-purge-001",
        )
        self.assertFalse(purge_plan["eligible"])
        self.assertIn("purge_delay_not_elapsed", purge_plan["blockers"])

        record_file = load_yaml(self._record_path())
        record_file["records"][self.record_id]["gc"]["purge_not_before"] = (
            "2000-01-01T00:00:00Z"
        )
        save_yaml(self._record_path(), record_file)
        due_plan = self._plan(phase="purge", operation_id="gc-purge-001")
        with mock.patch(
            "research_cockpit.artifact_gc._purge_tree",
            side_effect=OSError("open file"),
        ):
            with self.assertRaisesRegex(OSError, "open file"):
                self._execute(
                    phase="purge",
                    operation_id="gc-purge-001",
                    expected_revision=due_plan["state_revision"],
                )
        self.assertEqual(self._record()["availability"]["status"], "quarantined")
        self.assertTrue(Path(quarantine["quarantine_path"]).exists())

        retry_plan = self._plan(phase="purge", operation_id="gc-purge-001")
        result = self._execute(
            phase="purge",
            operation_id="gc-purge-001",
            expected_revision=retry_plan["state_revision"],
        )
        self.assertEqual(result["status"], "purged")
        self.assertFalse(Path(quarantine["quarantine_path"]).exists())
        self.assertEqual(self._record()["availability"]["status"], "deleted")
        self.assertTrue(
            any("purged" in path.name for path in (self.root / "artifact_gc_manifests").glob("*.yaml"))
        )

    def test_external_and_legacy_payloads_are_not_gc_eligible(self) -> None:
        record_file = load_yaml(self._record_path())
        record = record_file["records"][self.record_id]
        record["storage"] = {
            "mode": "reference",
            "ownership": "external",
            "uri": "s3://bucket/evidence",
            "managed_key": None,
        }
        save_yaml(self._record_path(), record_file)

        plan = self._plan(operation_id="gc-external-001")

        self.assertFalse(plan["eligible"])
        self.assertIn("not_cockpit_managed", plan["blockers"])
        self.assertTrue((self.store / self.experiment_id / self.run_id / self.record_id).exists())

    def test_traversal_and_symlink_payloads_are_not_purged(self) -> None:
        record_file = load_yaml(self._record_path())
        record_file["records"][self.record_id]["storage"]["managed_key"] = "../escape"
        save_yaml(self._record_path(), record_file)
        traversal = self._plan(operation_id="gc-traversal-001")
        self.assertFalse(traversal["eligible"])
        self.assertIn("payload_verification_failed", traversal["blockers"])

        record_file["records"][self.record_id]["storage"]["managed_key"] = (
            f"{self.experiment_id}/{self.run_id}/{self.record_id}"
        )
        save_yaml(self._record_path(), record_file)
        target = self.store / self.experiment_id / self.run_id / self.record_id
        try:
            (target / "unsafe-link").symlink_to(target / "metrics.json")
        except OSError:
            self.skipTest("host does not permit symlink test setup")
        symlink = self._plan(operation_id="gc-symlink-001")
        self.assertFalse(symlink["eligible"])
        self.assertIn("payload_verification_failed", symlink["blockers"])
        self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
