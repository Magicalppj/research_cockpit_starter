from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.artifact_inventory import (
    ensure_artifact_inventory,
    load_artifact_inventory,
    mark_artifact_inventory_stale,
    patch_artifact_inventory,
)
from research_cockpit.evidence_staging import MANIFEST_NAME
from research_cockpit.storage import save_yaml


class ArtifactInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"artifact_inventory_{uuid.uuid4().hex}"
        self.managed_root = parent / f"managed_artifacts_{uuid.uuid4().hex}"
        self.root.mkdir()
        save_yaml(
            self.root / "storage.yaml",
            {
                "schema_version": "storage_layout_v1",
                "project_id": "inventory_test",
                "artifact_root": str(self.managed_root),
            },
        )
        self.record_path = self.root / "artifact_records" / "experiment_a.yaml"
        save_yaml(
            self.record_path,
            {
                "schema_version": "artifact_records_v1",
                "experiment_id": "experiment_a",
                "records": {
                    "record_reference": self._record(
                        "record_reference",
                        mode="reference",
                        ownership="external",
                        uri="file:///external/reference",
                        managed_key=None,
                        size_bytes=11,
                        file_count=2,
                    ),
                    "record_managed": self._record(
                        "record_managed",
                        mode="managed",
                        ownership="cockpit_managed",
                        uri=(self.managed_root / "experiment_a" / "run_a" / "record_managed").as_uri(),
                        managed_key="experiment_a/run_a/record_managed",
                        size_bytes=7,
                        file_count=1,
                    ),
                },
            },
        )
        save_yaml(
            self.root / "graph" / "nodes" / "artifact_a.yaml",
            {
                "id": "artifact_a",
                "type": "artifact",
                "title": "Artifact A",
                "status": "done",
                "path": "artifacts/legacy-a",
                "retention": {"class": "reproducible_output"},
            },
        )
        self._write_managed_manifest(
            "experiment_a/run_a/record_managed",
            record_id="record_managed",
            size_bytes=7,
            file_count=1,
        )
        self._write_managed_manifest(
            "experiment_a/run_a/record_orphan",
            record_id="record_orphan",
            size_bytes=13,
            file_count=3,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.managed_root, ignore_errors=True)

    def _record(
        self,
        record_id: str,
        *,
        mode: str,
        ownership: str,
        uri: str,
        managed_key: str | None,
        size_bytes: int,
        file_count: int,
    ) -> dict[str, object]:
        return {
            "record_id": record_id,
            "experiment_id": "experiment_a",
            "run_id": "run_a",
            "title": record_id,
            "links": {},
            "storage": {
                "mode": mode,
                "ownership": ownership,
                "uri": uri,
                "managed_key": managed_key,
            },
            "integrity": {
                "level": "manifest",
                "algorithm": "sha256",
                "digest": "manifest-sha256:" + "a" * 64,
            },
            "inventory": {
                "size_bytes": size_bytes,
                "file_count": file_count,
                "complete": True,
            },
            "retention": {"class": "reproducible_output"},
            "availability": {"status": "available", "last_verified_at": None},
            "lifecycle": {"supersedes": [], "superseded_by": None},
        }

    def _write_managed_manifest(
        self,
        managed_key: str,
        *,
        record_id: str,
        size_bytes: int,
        file_count: int,
    ) -> Path:
        target = self.managed_root.joinpath(*managed_key.split("/"))
        target.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": "evidence_ingest_v1",
            "record_id": record_id,
            "storage": {
                "mode": "managed",
                "ownership": "cockpit_managed",
                "uri": target.as_uri(),
                "managed_key": managed_key,
            },
            "integrity": {
                "level": "content",
                "algorithm": "sha256",
                "digest": "sha256:" + "b" * 64,
            },
            "inventory": {
                "size_bytes": size_bytes,
                "file_count": file_count,
                "complete": True,
            },
        }
        (target / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        return target

    def test_inventory_indexes_records_graph_artifacts_and_managed_orphans(self) -> None:
        result = ensure_artifact_inventory(self.root, max_managed_entries=20)
        inventory = result["inventory"]

        self.assertEqual(result["status"], "rebuilt")
        self.assertEqual(inventory["schema_version"], "artifact_inventory_v1")
        self.assertEqual(inventory["aggregates"]["records"]["count"], 2)
        self.assertEqual(
            inventory["aggregates"]["records"]["by_storage_mode"],
            {"managed": 1, "reference": 1},
        )
        self.assertEqual(inventory["aggregates"]["graph_artifacts"]["count"], 1)
        self.assertEqual(inventory["aggregates"]["managed_payloads"]["count"], 2)
        self.assertEqual(inventory["aggregates"]["managed_orphans"]["count"], 1)
        self.assertEqual(
            inventory["managed_orphans"]["experiment_a/run_a/record_orphan"]["record_id"],
            "record_orphan",
        )
        self.assertTrue(inventory["scan"]["managed_store"]["complete"])
        self.assertTrue((self.root / "dashboards" / "artifact_inventory.json").exists())

    def test_incremental_patch_updates_only_changed_artifact_record_file(self) -> None:
        ensure_artifact_inventory(self.root)
        data = {
            "schema_version": "artifact_records_v1",
            "experiment_id": "experiment_a",
            "records": {
                "record_reference": self._record(
                    "record_reference",
                    mode="reference",
                    ownership="external",
                    uri="file:///external/reference",
                    managed_key=None,
                    size_bytes=23,
                    file_count=4,
                )
            },
        }
        save_yaml(self.record_path, data)

        result = patch_artifact_inventory(self.root, [self.record_path])
        inventory = load_artifact_inventory(self.root)

        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["changed_files"], ["artifact_records/experiment_a.yaml"])
        self.assertEqual(inventory["aggregates"]["records"]["count"], 1)
        self.assertEqual(inventory["records"]["record_reference"]["inventory"]["size_bytes"], 23)
        self.assertNotIn("record_managed", inventory["records"])
        self.assertEqual(inventory["aggregates"]["managed_orphans"]["count"], 2)

    def test_managed_manifest_symlink_is_reported_without_following_it(self) -> None:
        manifest_path = (
            self.managed_root
            / "experiment_a"
            / "run_a"
            / "record_managed"
            / MANIFEST_NAME
        )
        external_manifest = self.root / "external_manifest.json"
        external_manifest.write_text("{}", encoding="utf-8")
        manifest_path.unlink()
        try:
            manifest_path.symlink_to(external_manifest)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")

        inventory = ensure_artifact_inventory(self.root)["inventory"]

        self.assertEqual(
            inventory["managed_payloads"]["experiment_a/run_a/record_managed"]["manifest_status"],
            "unsafe_link",
        )
        self.assertIn(
            "experiment_a/run_a/record_managed",
            inventory["managed_orphans"],
        )

    def test_manual_metadata_change_triggers_stale_index_recovery(self) -> None:
        ensure_artifact_inventory(self.root)
        data = {
            "schema_version": "artifact_records_v1",
            "experiment_id": "experiment_a",
            "records": {
                "record_reference": self._record(
                    "record_reference",
                    mode="reference",
                    ownership="external",
                    uri="file:///external/reference",
                    managed_key=None,
                    size_bytes=29,
                    file_count=5,
                )
            },
        }
        save_yaml(self.record_path, data)

        result = ensure_artifact_inventory(self.root)

        self.assertEqual(result["status"], "rebuilt")
        self.assertEqual(
            result["inventory"]["records"]["record_reference"]["inventory"]["size_bytes"],
            29,
        )

    def test_stale_recovery_rebuilds_with_bounded_managed_store_scan(self) -> None:
        ensure_artifact_inventory(self.root)
        mark_artifact_inventory_stale(self.root, reason="test")

        result = ensure_artifact_inventory(self.root, max_managed_entries=3)
        inventory = result["inventory"]

        self.assertEqual(result["status"], "rebuilt")
        self.assertFalse(inventory["scan"]["managed_store"]["complete"])
        self.assertTrue(inventory["scan"]["managed_store"]["truncated"])
        self.assertEqual(inventory["aggregates"]["managed_payloads"]["count"], 1)
        self.assertTrue(inventory["aggregates"]["managed_payloads"]["count_lower_bound"])
        self.assertTrue(inventory["aggregates"]["managed_payloads"]["statistics"]["lower_bound"])


if __name__ == "__main__":
    unittest.main()
