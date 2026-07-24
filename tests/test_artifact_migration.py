from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit import artifact_migration
from research_cockpit.artifact_migration import (
    migrate_legacy_artifact,
    plan_legacy_artifact_migration,
)
from research_cockpit.storage import load_yaml, save_yaml


class LegacyArtifactMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = Path(tempfile.gettempdir())
        token = uuid.uuid4().hex
        self.root = parent / f"artifact_migration_{token}"
        self.store = Path(tempfile.gettempdir()) / f"artifact_migration_store_{token}"
        shutil.copytree(ROOT_DIR / "examples" / "demo_research_cockpit", self.root)
        save_yaml(
            self.root / "storage.yaml",
            {
                "schema_version": "storage_layout_v1",
                "project_id": "migration-test",
                "artifact_root": str(self.store),
            },
        )
        self.experiment_id = "experiment_demo_prompt_refinement"
        self.run_id = "run_legacy"
        self.record_id = "record_legacy"
        self.payload = self.root / "artifacts" / self.experiment_id / self.run_id
        self.payload.mkdir(parents=True)
        (self.payload / "metrics.json").write_text('{"score": 0.9}', encoding="utf-8")
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
                        "links": {
                            "metrics": (
                                f"artifacts/{self.experiment_id}/{self.run_id}/metrics.json"
                            ),
                        },
                        "retention": {"class": "reproducible_output"},
                        "unknown_legacy_field": {"preserve": True},
                    }
                },
            },
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.store, ignore_errors=True)

    def _record(self) -> dict:
        return load_yaml(
            self.root / "artifact_records" / f"{self.experiment_id}.yaml"
        )["records"][self.record_id]

    def _plan(self, *, operation_id: str = "migrate-legacy-001") -> dict:
        return plan_legacy_artifact_migration(
            self.root,
            record_id=self.record_id,
            operation_id=operation_id,
        )

    def _execute(self, *, operation_id: str = "migrate-legacy-001") -> dict:
        return migrate_legacy_artifact(
            self.root,
            record_id=self.record_id,
            operation_id=operation_id,
            execute=True,
        )

    def test_dry_run_is_zero_write_and_binds_one_legacy_record(self) -> None:
        before_record = deepcopy(self._record())
        before_payload = (self.payload / "metrics.json").read_bytes()

        payload = self._plan()

        self.assertTrue(payload["dry_run"])
        self.assertTrue(payload["eligible"])
        self.assertEqual(payload["record_id"], self.record_id)
        self.assertEqual(payload["source_relative_path"], str(self.payload.relative_to(self.root)).replace("\\", "/"))
        self.assertFalse(self.store.exists())
        self.assertEqual(self._record(), before_record)
        self.assertEqual((self.payload / "metrics.json").read_bytes(), before_payload)
        self.assertFalse((self.root / "artifact_migrations").exists())

    def test_active_assignment_blocks_migration_without_mutating_source(self) -> None:
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

        payload = self._plan()

        self.assertFalse(payload["eligible"])
        self.assertIn("active_assignment:assignment_active", payload["blockers"])
        with self.assertRaisesRegex(ValueError, "active_assignment"):
            self._execute()
        self.assertTrue(self.payload.exists())
        self.assertEqual(self._record().get("storage"), None)

    def test_active_assignment_created_after_staging_blocks_publish_and_restores_source(self) -> None:
        original_stage = artifact_migration._stage_payload

        def stage_then_add_active_assignment(context: object) -> tuple[Path, dict]:
            staging, verification = original_stage(context)
            save_yaml(
                self.root / "assignments" / "assignment_migration_race.yaml",
                {
                    "assignment_id": "assignment_migration_race",
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
            return staging, verification

        with mock.patch(
            "research_cockpit.artifact_migration._same_filesystem",
            return_value=True,
        ), mock.patch(
            "research_cockpit.artifact_migration._stage_payload",
            side_effect=stage_then_add_active_assignment,
        ):
            with self.assertRaisesRegex(ValueError, "active_assignment:assignment_migration_race"):
                self._execute()

        self.assertTrue(self.payload.exists())
        self.assertEqual(self._record().get("storage"), None)

    def test_active_resource_path_blocks_migration(self) -> None:
        save_yaml(
            self.root / "runs" / "run_migration_resource.yaml",
            {
                "run_id": "run_migration_resource",
                "experiment_id": self.experiment_id,
                "status": "running",
                "started_at": "2026-07-23T00:00:00Z",
                "output_root": str(self.payload),
            },
        )

        payload = self._plan(operation_id="migrate-resource-001")

        self.assertFalse(payload["eligible"])
        self.assertIn(
            "active_resource:run_migration_resource:output_root",
            payload["blockers"],
        )

    def test_cross_filesystem_copy_preserves_source_and_relinks_record(self) -> None:
        with mock.patch(
            "research_cockpit.artifact_migration._same_filesystem",
            return_value=False,
        ):
            payload = self._execute()

        target = self.store / self.experiment_id / self.run_id / self.record_id
        record = self._record()
        journal = load_yaml(Path(payload["migration_report_path"]))

        self.assertEqual(payload["transfer_method"], "copy_and_verify")
        self.assertTrue(self.payload.exists())
        self.assertEqual((target / "metrics.json").read_text(encoding="utf-8"), '{"score": 0.9}')
        self.assertEqual(record["storage"]["mode"], "managed")
        self.assertEqual(record["storage"]["managed_key"], f"{self.experiment_id}/{self.run_id}/{self.record_id}")
        self.assertEqual(record["availability"]["status"], "available")
        self.assertTrue(record["links"]["metrics"].startswith("file:"))
        self.assertEqual(record["unknown_legacy_field"], {"preserve": True})
        self.assertEqual(journal["phase"], "published")
        self.assertEqual(journal["source_disposition"], "retained")

    def test_same_filesystem_rename_relinks_only_after_verified_publish(self) -> None:
        with mock.patch(
            "research_cockpit.artifact_migration._same_filesystem",
            return_value=True,
        ):
            payload = self._execute()

        target = self.store / self.experiment_id / self.run_id / self.record_id
        self.assertEqual(payload["transfer_method"], "same_filesystem_rename")
        self.assertFalse(self.payload.exists())
        self.assertTrue((target / "metrics.json").is_file())
        self.assertEqual(self._record()["storage"]["mode"], "managed")

    def test_copy_failure_keeps_legacy_source_and_exact_retry_resumes(self) -> None:
        with mock.patch(
            "research_cockpit.artifact_migration._copy_source_tree_hashed",
            side_effect=OSError("copy interrupted"),
        ), mock.patch(
            "research_cockpit.artifact_migration._same_filesystem",
            return_value=False,
        ):
            with self.assertRaisesRegex(OSError, "copy interrupted"):
                self._execute()

        self.assertTrue(self.payload.exists())
        self.assertEqual(self._record().get("storage"), None)
        with mock.patch(
            "research_cockpit.artifact_migration._same_filesystem",
            return_value=False,
        ):
            payload = self._execute()
        self.assertFalse(payload["replayed"])
        self.assertEqual(self._record()["storage"]["mode"], "managed")

    def test_hash_failure_after_same_filesystem_rename_restores_legacy_source(self) -> None:
        with mock.patch(
            "research_cockpit.artifact_migration._same_filesystem",
            return_value=True,
        ), mock.patch(
            "research_cockpit.artifact_migration._content_digest",
            side_effect=OSError("hash interrupted"),
        ):
            with self.assertRaisesRegex(OSError, "hash interrupted"):
                self._execute()

        self.assertTrue(self.payload.exists())
        staging_root = self.store / ".staging"
        self.assertEqual(
            list(staging_root.iterdir()) if staging_root.exists() else [],
            [],
        )
        self.assertEqual(self._record().get("storage"), None)
        with mock.patch(
            "research_cockpit.artifact_migration._same_filesystem",
            return_value=True,
        ):
            payload = self._execute()
        self.assertFalse(payload["replayed"])
        self.assertEqual(self._record()["storage"]["mode"], "managed")

    def test_publish_failure_keeps_source_and_exact_retry_reuses_verified_stage(self) -> None:
        with mock.patch(
            "research_cockpit.artifact_migration._same_filesystem",
            return_value=False,
        ), mock.patch(
            "research_cockpit.artifact_migration._publish_migration",
            side_effect=OSError("publish interrupted"),
        ):
            with self.assertRaisesRegex(OSError, "publish interrupted"):
                self._execute()

        self.assertTrue(self.payload.exists())
        self.assertEqual(self._record().get("storage"), None)
        with mock.patch(
            "research_cockpit.artifact_migration._same_filesystem",
            return_value=False,
        ):
            payload = self._execute()
        self.assertFalse(payload["replayed"])
        self.assertEqual(self._record()["storage"]["mode"], "managed")

    def test_exact_retry_recovers_same_filesystem_stage_after_process_crash(self) -> None:
        with mock.patch(
            "research_cockpit.artifact_migration._same_filesystem",
            return_value=True,
        ), mock.patch(
            "research_cockpit.artifact_migration._publish_migration",
            side_effect=OSError("process terminated before publish"),
        ), mock.patch(
            "research_cockpit.artifact_migration._restore_renamed_source",
        ):
            with self.assertRaisesRegex(OSError, "process terminated before publish"):
                self._execute()

        self.assertFalse(self.payload.exists())
        payload = self._execute()

        self.assertEqual(payload["transfer_method"], "same_filesystem_rename")
        self.assertFalse(payload["replayed"])
        self.assertEqual(self._record()["storage"]["mode"], "managed")

    def test_completed_operation_replays_without_recopying_or_moving_source(self) -> None:
        with mock.patch(
            "research_cockpit.artifact_migration._same_filesystem",
            return_value=False,
        ):
            self._execute()
            replay = self._execute()

        self.assertTrue(replay["replayed"])
        self.assertTrue(self.payload.exists())
        self.assertEqual(replay["status"], "replayed")

    def test_retry_recovers_when_record_publish_precedes_journal_publish(self) -> None:
        with mock.patch(
            "research_cockpit.artifact_migration._same_filesystem",
            return_value=False,
        ):
            result = self._execute()
        journal_path = Path(result["migration_report_path"])
        journal = load_yaml(journal_path)
        journal["phase"] = "staged"
        journal.pop("source_disposition", None)
        save_yaml(journal_path, journal)

        replay = self._execute()

        self.assertTrue(replay["replayed"])
        self.assertEqual(load_yaml(journal_path)["phase"], "published")


if __name__ == "__main__":
    unittest.main()
