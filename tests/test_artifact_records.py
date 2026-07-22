from __future__ import annotations

from pathlib import Path
import shutil
import sys
import unittest
import uuid


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.artifact_records import (
    load_artifact_record_file,
    normalize_artifact_record,
    upsert_artifact_record,
)
from research_cockpit.model import ResearchNode, validate_artifact_records
from research_cockpit.resources import _target_resolution
from research_cockpit.storage import load_yaml, save_yaml


class ArtifactRecordCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"artifact_records_{uuid.uuid4().hex}"
        self.payload = self.root / "artifacts" / "experiment_x" / "run_legacy"
        self.payload.mkdir(parents=True)
        (self.payload / "result.txt").write_text("legacy bytes", encoding="utf-8")
        save_yaml(
            self.root / "artifact_records" / "experiment_x.yaml",
            {
                "schema_version": "artifact_records_v1",
                "experiment_id": "experiment_x",
                "records": {
                    "record_legacy": {
                        "record_id": "record_legacy",
                        "experiment_id": "experiment_x",
                        "run_id": "run_legacy",
                        "stable_path": "artifacts/experiment_x/run_legacy",
                        "links": {
                            "result": "artifacts/experiment_x/run_legacy/result.txt"
                        },
                        "unknown_legacy_field": {"keep": True},
                    }
                },
            },
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_legacy_record_normalizes_in_memory_without_moving_payload(self) -> None:
        before = (self.payload / "result.txt").read_bytes()

        data = load_artifact_record_file(self.root, "experiment_x")
        record = data["records"]["record_legacy"]

        self.assertEqual(record["storage"]["mode"], "legacy")
        self.assertEqual(record["storage"]["ownership"], "historical")
        self.assertEqual(record["storage"]["uri"], "artifacts/experiment_x/run_legacy")
        self.assertEqual(record["availability"]["status"], "unknown")
        self.assertEqual(record["unknown_legacy_field"], {"keep": True})
        self.assertEqual((self.payload / "result.txt").read_bytes(), before)

    def test_touched_legacy_file_preserves_unknown_fields_and_payload_bytes(self) -> None:
        before = (self.payload / "result.txt").read_bytes()
        new_record = {
            "record_id": "record_reference",
            "experiment_id": "experiment_x",
            "run_id": "run_reference",
            "storage": {
                "mode": "reference",
                "ownership": "external",
                "uri": "file:///external/result",
                "managed_key": None,
            },
            "integrity": {
                "level": "unverified",
                "algorithm": None,
                "digest": None,
            },
            "inventory": {"size_bytes": 0, "file_count": 0, "complete": True},
            "retention": {"class": "reproducible_output"},
            "availability": {"status": "available", "last_verified_at": None},
            "lifecycle": {"supersedes": [], "superseded_by": None},
            "links": {},
        }

        path, _before, after = upsert_artifact_record(
            self.root,
            "experiment_x",
            new_record,
        )
        save_yaml(path, after)
        persisted = load_yaml(path)["records"]["record_legacy"]

        self.assertEqual(persisted["storage"]["mode"], "legacy")
        self.assertEqual(persisted["storage"]["ownership"], "historical")
        self.assertEqual(persisted["unknown_legacy_field"], {"keep": True})
        self.assertEqual((self.payload / "result.txt").read_bytes(), before)


    def test_normalized_legacy_record_passes_artifact_contract_validation(self) -> None:
        record = load_artifact_record_file(
            self.root,
            "experiment_x",
        )["records"]["record_legacy"]
        node = ResearchNode.from_dict(
            {
                "id": "experiment_x",
                "type": "experiment",
                "title": "Experiment X",
                "status": "done",
                "parent": "option_x",
            }
        )

        errors = validate_artifact_records(
            self.root,
            {"experiment_x": node},
            [record],
        )

        self.assertEqual(errors, [])

    def test_only_portable_artifacts_prefix_is_inferred_as_legacy_owned(self) -> None:
        for stable_path in (
            "/artifacts/external",
            "../artifacts/escape",
            r"C:\artifacts\external",
            "file:///artifacts/external",
        ):
            with self.subTest(stable_path=stable_path):
                record = normalize_artifact_record(
                    {
                        "record_id": "record_external",
                        "stable_path": stable_path,
                        "links": {},
                    }
                )
                self.assertEqual(record["storage"]["mode"], "reference")
                self.assertEqual(record["storage"]["ownership"], "external")

    def test_file_uri_artifact_resource_resolves_to_local_path(self) -> None:
        payload = self.root / "external evidence" / "result value.txt"
        payload.parent.mkdir(parents=True)
        payload.write_text("evidence", encoding="utf-8")

        resolution = _target_resolution(
            self.root,
            "artifact_record_path",
            payload.resolve().as_uri(),
            {},
        )

        self.assertTrue(resolution["exists"])
        self.assertEqual(resolution["resolution_base"], "file_uri")

    def test_invalid_extended_metadata_is_reported(self) -> None:
        node = ResearchNode.from_dict(
            {
                "id": "experiment_x",
                "type": "experiment",
                "title": "Experiment X",
                "status": "done",
                "parent": "option_x",
            }
        )
        record = {
            "record_id": "record_invalid",
            "experiment_id": "experiment_x",
            "links": {},
            "storage": {
                "mode": "managed",
                "ownership": "external",
                "uri": "",
                "managed_key": "../escape",
            },
            "integrity": {
                "level": "content",
                "algorithm": None,
                "digest": "inventory-sha256:not-content",
            },
            "inventory": {"size_bytes": -1, "file_count": "many", "complete": "yes"},
            "retention": {"class": "reproducible_output"},
            "availability": {"status": "online", "last_verified_at": None},
            "lifecycle": {"supersedes": "record_old", "superseded_by": 7},
        }

        errors = validate_artifact_records(
            self.root,
            {"experiment_x": node},
            [record],
        )
        joined = "\n".join(errors)

        for field_name in (
            "storage.ownership",
            "storage.uri",
            "storage.managed_key",
            "integrity.algorithm",
            "integrity.digest",
            "inventory.size_bytes",
            "inventory.file_count",
            "inventory.complete",
            "availability.status",
            "lifecycle.supersedes",
            "lifecycle.superseded_by",
        ):
            self.assertIn(field_name, joined)


if __name__ == "__main__":
    unittest.main()
