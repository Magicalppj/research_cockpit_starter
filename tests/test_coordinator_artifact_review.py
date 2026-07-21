from __future__ import annotations

from pathlib import Path
import shutil
import sys
import unittest
import uuid


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.artifact_records import build_artifact_record
from research_cockpit.assignment_leases import AssignmentLeaseError
from research_cockpit.coordinator_reviews import apply_coord_review
from research_cockpit.storage import find_node_file, load_yaml, save_yaml


class CoordinatorArtifactReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"coord_artifact_review_{uuid.uuid4().hex}"
        shutil.copytree(ROOT_DIR / "examples" / "demo_research_cockpit", self.root)
        self.experiment_id = "experiment_demo_prompt_refinement"
        self.record_id = "record_coord_promotion"
        stable_path = f"artifacts/{self.experiment_id}/run_coord_promotion"
        payload_dir = self.root / stable_path
        payload_dir.mkdir(parents=True)
        (payload_dir / "metrics.json").write_text('{"score": 0.93}', encoding="utf-8")
        record = build_artifact_record(
            record_id=self.record_id,
            experiment_id=self.experiment_id,
            run_id="run_coord_promotion",
            artifact_id=self.record_id,
            title="Coordinator promotion evidence",
            summary="Evidence selected by the coordinator.",
            stable_path=stable_path,
            manifest_path=f"{stable_path}/_research_cockpit_ingest.json",
            source_file_count=1,
            links={"metrics": f"{stable_path}/metrics.json"},
            agent_id="agent_reviewed",
        )
        save_yaml(
            self.root / "artifact_records" / f"{self.experiment_id}.yaml",
            {
                "schema_version": "artifact_records_v1",
                "experiment_id": self.experiment_id,
                "records": {self.record_id: record},
            },
        )
        node_path = find_node_file(self.root, self.experiment_id)
        node = load_yaml(node_path)
        node["linked_artifact_records"] = [self.record_id]
        save_yaml(node_path, node)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _plan(self, *, reason: str = "Required for durable cross-branch comparison.") -> dict:
        return {
            "schema_version": "coord_review_v1",
            "operation_id": "op_coord_promote_record",
            "action": "promote_artifact",
            "record_id": self.record_id,
            "artifact_id": "artifact_coord_promotion",
            "link_to": [self.experiment_id],
            "promotion_reason": reason,
        }

    def test_promote_artifact_action_is_idempotent_and_preserves_provenance(self) -> None:
        first = apply_coord_review(self.root, plan=self._plan())
        replay = apply_coord_review(self.root, plan=self._plan())

        self.assertEqual(replay, first)
        artifact = load_yaml(
            self.root / "graph" / "nodes" / "artifact_coord_promotion.yaml"
        )
        self.assertEqual(artifact["source_artifact_record"], self.record_id)
        self.assertEqual(
            artifact["promotion"]["reason"],
            "Required for durable cross-branch comparison.",
        )
        record_file = load_yaml(
            self.root / "artifact_records" / f"{self.experiment_id}.yaml"
        )
        promoted = record_file["records"][self.record_id]
        self.assertEqual(promoted["promoted_artifact_id"], "artifact_coord_promotion")
        self.assertEqual(first["entities"]["record_id"], self.record_id)
        self.assertEqual(first["entities"]["artifact_id"], "artifact_coord_promotion")

    def test_changed_promotion_retry_conflicts(self) -> None:
        apply_coord_review(self.root, plan=self._plan())

        with self.assertRaises(AssignmentLeaseError) as captured:
            apply_coord_review(self.root, plan=self._plan(reason="Different retry payload."))

        self.assertEqual(captured.exception.receipt["error"]["code"], "idempotency_conflict")


if __name__ == "__main__":
    unittest.main()
