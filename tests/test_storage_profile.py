from __future__ import annotations

from pathlib import Path
import shutil
import sys
import unittest
import uuid


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR / "dev" / "scripts"))

from multi_agent_baseline import artifact_record_layout_profile
from research_cockpit.storage import save_yaml


class ArtifactRecordLayoutProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"artifact_profile_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_profile_reports_shared_append_only_writer_boundary(self) -> None:
        save_yaml(
            self.root / "artifact_records" / "experiment_x.yaml",
            {
                "schema_version": "artifact_records_v1",
                "experiment_id": "experiment_x",
                "records": {
                    "record_a": {"record_id": "record_a", "run_id": "run_a"},
                    "record_b": {"record_id": "record_b", "run_id": "run_b"},
                },
            },
        )
        for index in range(2):
            save_yaml(
                self.root / "assignments" / f"assign_{index}.yaml",
                {
                    "assignment_id": f"assign_{index}",
                    "agent_id": f"agent_{index}",
                    "status": "active",
                    "root_node": "experiment_x",
                    "current_node": "experiment_x",
                    "scope": {
                        "root_node": "experiment_x",
                        "subtree_policy": "root_only",
                        "write_policy": "append_only",
                    },
                },
            )

        profile = artifact_record_layout_profile(self.root)

        self.assertEqual(profile["schema_version"], "artifact_record_layout_profile_v1")
        self.assertEqual(profile["file_count"], 1)
        self.assertEqual(profile["record_count"], 2)
        self.assertEqual(profile["max_records_per_file"], 2)
        self.assertEqual(profile["append_only_active_assignment_count"], 2)
        self.assertEqual(
            profile["shared_writer_candidates"],
            [
                {
                    "experiment_id": "experiment_x",
                    "assignment_ids": ["assign_0", "assign_1"],
                    "artifact_record_file": "artifact_records/experiment_x.yaml",
                    "shared_file_exists": True,
                }
            ],
        )
        self.assertEqual(
            profile["storage_decision"]["decision"],
            "defer_layout_migration",
        )
        self.assertFalse(profile["storage_decision"]["migration_required"])

    def test_profile_does_not_claim_contention_without_shared_writers(self) -> None:
        profile = artifact_record_layout_profile(self.root)

        self.assertEqual(profile["shared_writer_candidates"], [])
        self.assertEqual(profile["storage_decision"]["decision"], "no_layout_change")
        self.assertFalse(profile["storage_decision"]["migration_required"])


if __name__ == "__main__":
    unittest.main()
