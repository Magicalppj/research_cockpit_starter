from __future__ import annotations

from pathlib import Path
import shutil
import sys
import unittest
import uuid
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.maintenance_actions import apply_maintenance_action
from research_cockpit.storage import load_yaml, save_yaml


class MaintenanceActionTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"maintenance_action_{uuid.uuid4().hex}"
        shutil.copytree(ROOT_DIR / "examples" / "demo_research_cockpit", self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_repair_defaults_to_a_non_mutating_plan(self) -> None:
        log_path = self.root / "graph" / "interaction_log.yaml"
        log = load_yaml(log_path)
        log["events"].append("invalid legacy event")
        save_yaml(log_path, log)
        before = log_path.read_bytes()

        payload = apply_maintenance_action(
            self.root,
            command="repair",
            plan={
                "schema_version": "maintenance_action_v1",
                "action": "interaction_log",
                "execute": False,
                "parameters": {"show_diff": True},
            },
        )

        self.assertEqual(payload["schema_version"], "maintenance_result_v1")
        self.assertFalse(payload["executed"])
        self.assertTrue(payload["result"]["would_change"])
        self.assertEqual(log_path.read_bytes(), before)

    def test_migrate_interaction_log_dry_run_does_not_activate_segments(self) -> None:
        payload = apply_maintenance_action(
            self.root,
            command="migrate",
            plan={
                "schema_version": "maintenance_action_v1",
                "action": "interaction_log",
                "execute": False,
                "parameters": {},
            },
        )

        self.assertFalse(payload["executed"])
        self.assertGreaterEqual(payload["result"]["event_count"], 1)
        self.assertFalse(
            (self.root / "graph" / "interaction_events" / "manifest.json").exists()
        )

    def test_migrate_artifact_storage_routes_one_record_and_operation_id(self) -> None:
        expected = {"schema_version": "artifact_storage_migration_plan_v1"}
        with mock.patch(
            "research_cockpit.maintenance_actions.migrate_legacy_artifact",
            return_value=expected,
        ) as migrated:
            payload = apply_maintenance_action(
                self.root,
                command="migrate",
                plan={
                    "schema_version": "maintenance_action_v1",
                    "action": "artifact_storage",
                    "execute": False,
                    "parameters": {
                        "record_id": "record_legacy",
                        "operation_id": "migrate-legacy-001",
                    },
                },
            )

        self.assertFalse(payload["executed"])
        self.assertEqual(payload["result"], expected)
        migrated.assert_called_once_with(
            self.root,
            record_id="record_legacy",
            operation_id="migrate-legacy-001",
            execute=False,
        )

    def test_compact_dry_run_requires_no_artifact_selection(self) -> None:
        payload = apply_maintenance_action(
            self.root,
            command="compact",
            plan={
                "schema_version": "maintenance_action_v1",
                "action": "artifact",
                "execute": False,
                "parameters": {},
            },
        )

        self.assertFalse(payload["executed"])
        self.assertEqual(payload["result"]["schema_version"], "artifact_compaction_plan_v1")

    def test_command_rejects_an_action_owned_by_another_maintenance_route(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported by maintenance repair"):
            apply_maintenance_action(
                self.root,
                command="repair",
                plan={
                    "schema_version": "maintenance_action_v1",
                    "action": "artifact",
                    "execute": False,
                    "parameters": {},
                },
            )


if __name__ == "__main__":
    unittest.main()
