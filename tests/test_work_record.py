from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.assignment_leases import AssignmentLeaseError, claim_assignment
from research_cockpit.assignment_records import record_assignment_evidence
from research_cockpit.assignment_runs import start_assignment_run
from research_cockpit.commands.work_record import parse_record_input
from research_cockpit.storage import load_yaml, save_yaml


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class WorkRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"work_record_{uuid.uuid4().hex}"
        save_yaml(
            self.root / "graph" / "nodes" / "option_x.yaml",
            {
                "id": "option_x",
                "type": "option",
                "title": "Option X",
                "status": "active",
                "children": ["experiment_x"],
            },
        )
        save_yaml(
            self.root / "graph" / "nodes" / "experiment_x.yaml",
            {
                "id": "experiment_x",
                "type": "experiment",
                "title": "Experiment X",
                "status": "queued",
                "parent": "option_x",
            },
        )
        save_yaml(self.root / "current_state.yaml", {})
        save_yaml(
            self.root / "agents" / "agent_a.yaml",
            {"agent_id": "agent_a", "status": "idle", "active_assignment_ids": []},
        )
        save_yaml(
            self.root / "assignments" / "assign_x.yaml",
            {
                "assignment_id": "assign_x",
                "agent_id": None,
                "status": "queued",
                "root_node": "option_x",
                "current_node": "experiment_x",
                "allowed_subtree": {"root": "option_x", "policy": "descendants_only"},
                "scope": {
                    "root_node": "option_x",
                    "subtree_policy": "descendants_only",
                    "write_policy": "exclusive",
                },
            },
        )
        claimed = claim_assignment(
            self.root,
            assignment_id="assign_x",
            agent_id="agent_a",
            operation_id="op_claim_record",
            now=NOW,
        )
        self.lease = claimed["packet"]["lease"]
        started = start_assignment_run(
            self.root,
            assignment_id="assign_x",
            agent_id="agent_a",
            lease_id=self.lease["lease_id"],
            lease_epoch=self.lease["lease_epoch"],
            operation_id="op_start_record",
            now=NOW + timedelta(seconds=30),
        )
        self.run_id = started["entities"]["run_id"]
        self.source = self.root.parent / f"record_source_{uuid.uuid4().hex}"
        self.source.mkdir()
        (self.source / "metrics.json").write_text('{"score": 0.9}', encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.source, ignore_errors=True)

    def _record(self, operation_id: str = "op_record") -> dict:
        return record_assignment_evidence(
            self.root,
            assignment_id="assign_x",
            agent_id="agent_a",
            lease_id=self.lease["lease_id"],
            lease_epoch=self.lease["lease_epoch"],
            operation_id=operation_id,
            run_id=self.run_id,
            source_dir=self.source,
            links={"metrics": "metrics.json"},
            now=NOW + timedelta(minutes=1),
        )

    def test_record_is_idempotent_and_persists_payload_once(self) -> None:
        first = self._record()
        replay = self._record()

        self.assertEqual(replay, first)
        record_id = first["entities"]["record_id"]
        records = load_yaml(self.root / "artifact_records" / "experiment_x.yaml")["records"]
        self.assertEqual(list(records), [record_id])
        payload = self.root / records[record_id]["links"]["metrics"]
        self.assertEqual(payload.read_text(encoding="utf-8"), '{"score": 0.9}')

    def test_same_operation_rejects_changed_source_content(self) -> None:
        self._record()
        (self.source / "metrics.json").write_text('{"score": 0.1}', encoding="utf-8")

        with self.assertRaises(AssignmentLeaseError) as caught:
            self._record()

        self.assertEqual(caught.exception.receipt["error"]["code"], "idempotency_conflict")

    def test_retry_recovers_payload_copied_before_transaction_commit(self) -> None:
        with patch(
            "research_cockpit.commands.ingest_artifact.execute_mutation_transaction",
            side_effect=KeyboardInterrupt("simulated process interruption"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._record("op_record_interrupted")

        receipt = self._record("op_record_interrupted")

        record_id = receipt["entities"]["record_id"]
        records = load_yaml(self.root / "artifact_records" / "experiment_x.yaml")["records"]
        self.assertEqual(list(records), [record_id])
        payload = self.root / records[record_id]["links"]["metrics"]
        self.assertEqual(payload.read_text(encoding="utf-8"), '{"score": 0.9}')

    def test_distinct_operations_append_payloads_to_same_run(self) -> None:
        first = self._record("op_record_incremental_1")
        (self.source / "metrics.json").write_text('{"score": 0.95}', encoding="utf-8")
        second = self._record("op_record_incremental_2")

        self.assertNotEqual(first["entities"]["record_id"], second["entities"]["record_id"])
        records = load_yaml(self.root / "artifact_records" / "experiment_x.yaml")["records"]
        self.assertEqual(
            set(records),
            {first["entities"]["record_id"], second["entities"]["record_id"]},
        )
        first_payload = self.root / records[first["entities"]["record_id"]]["links"]["metrics"]
        second_payload = self.root / records[second["entities"]["record_id"]]["links"]["metrics"]
        self.assertEqual(first_payload.read_text(encoding="utf-8"), '{"score": 0.9}')
        self.assertEqual(second_payload.read_text(encoding="utf-8"), '{"score": 0.95}')
        self.assertNotEqual(first_payload, second_payload)

    def test_parser_resolves_relative_source_from_input_file(self) -> None:
        input_path = self.source / "record.yaml"
        parsed = parse_record_input(
            {
                "schema_version": "work_record_v1",
                "agent_id": "agent_a",
                "lease_id": "lease_a",
                "lease_epoch": 1,
                "operation_id": "op_a",
                "run_id": "run_a",
                "source_dir": "output",
            },
            input_path=input_path,
        )
        self.assertEqual(parsed["source_dir"], self.source / "output")


if __name__ == "__main__":
    unittest.main()
