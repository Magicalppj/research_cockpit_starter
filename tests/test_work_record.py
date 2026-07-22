from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import tempfile
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

    def _record(self, operation_id: str = "op_record", *, mode: str = "reference") -> dict:
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
            mode=mode,
            now=NOW + timedelta(minutes=1),
        )

    def test_record_is_idempotent_and_defaults_to_reference(self) -> None:
        first = self._record()
        replay = self._record()

        self.assertEqual(replay, first)
        record_id = first["entities"]["record_id"]
        records = load_yaml(self.root / "artifact_records" / "experiment_x.yaml")["records"]
        self.assertEqual(list(records), [record_id])
        record = records[record_id]
        self.assertEqual(record["storage"]["mode"], "reference")
        self.assertEqual(record["storage"]["uri"], self.source.resolve().as_uri())
        self.assertEqual(
            record["links"]["metrics"],
            (self.source / "metrics.json").resolve().as_uri(),
        )
        self.assertEqual(record["integrity"]["level"], "inventory")
        self.assertFalse((self.root / "artifacts").exists())

    def test_exact_retry_does_not_reinspect_changed_or_missing_source(self) -> None:
        first = self._record()
        shutil.rmtree(self.source)

        replay = self._record()

        self.assertEqual(replay, first)

    def test_same_operation_rejects_changed_source_locator(self) -> None:
        self._record()
        alternate = self.root.parent / f"record_alternate_{uuid.uuid4().hex}"
        alternate.mkdir()
        (alternate / "metrics.json").write_text('{"score": 0.1}', encoding="utf-8")
        try:
            with self.assertRaises(AssignmentLeaseError) as caught:
                record_assignment_evidence(
                    self.root,
                    assignment_id="assign_x",
                    agent_id="agent_a",
                    lease_id=self.lease["lease_id"],
                    lease_epoch=self.lease["lease_epoch"],
                    operation_id="op_record",
                    run_id=self.run_id,
                    source_dir=alternate,
                    links={"metrics": "metrics.json"},
                    now=NOW + timedelta(minutes=1),
                )
        finally:
            shutil.rmtree(alternate, ignore_errors=True)

        self.assertEqual(
            caught.exception.receipt["error"]["code"],
            "idempotency_conflict",
        )

    def test_retry_after_interrupted_reference_transaction_does_not_copy_payload(self) -> None:
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
        record = records[record_id]
        self.assertEqual(record["storage"]["mode"], "reference")
        self.assertEqual(
            record["links"]["metrics"],
            (self.source / "metrics.json").resolve().as_uri(),
        )
        self.assertFalse((self.root / "artifacts").exists())

    def test_distinct_operations_append_reference_snapshots_to_same_run(self) -> None:
        first = self._record("op_record_incremental_1")
        (self.source / "metrics.json").write_text('{"score": 0.951}', encoding="utf-8")
        second = self._record("op_record_incremental_2")

        self.assertNotEqual(first["entities"]["record_id"], second["entities"]["record_id"])
        records = load_yaml(self.root / "artifact_records" / "experiment_x.yaml")["records"]
        self.assertEqual(
            set(records),
            {first["entities"]["record_id"], second["entities"]["record_id"]},
        )
        first_record = records[first["entities"]["record_id"]]
        second_record = records[second["entities"]["record_id"]]
        self.assertEqual(first_record["storage"]["mode"], "reference")
        self.assertEqual(second_record["storage"]["mode"], "reference")
        self.assertEqual(first_record["storage"]["uri"], second_record["storage"]["uri"])
        self.assertNotEqual(
            first_record["integrity"]["digest"],
            second_record["integrity"]["digest"],
        )
        self.assertFalse((self.root / "artifacts").exists())

    def test_explicit_managed_record_uses_external_store(self) -> None:
        managed_root = Path(tempfile.mkdtemp(prefix="research-cockpit-record-"))
        try:
            save_yaml(
                self.root / "storage.yaml",
                {
                    "schema_version": "storage_layout_v1",
                    "project_id": "project_record",
                    "artifact_root": str(managed_root),
                },
            )

            receipt = self._record("op_record_managed", mode="managed")

            record_id = receipt["entities"]["record_id"]
            record = load_yaml(
                self.root / "artifact_records" / "experiment_x.yaml"
            )["records"][record_id]
            target = managed_root / record["storage"]["managed_key"]
            self.assertEqual(record["storage"]["mode"], "managed")
            self.assertTrue((target / "metrics.json").is_file())
            self.assertEqual(
                (target / "metrics.json").read_text(encoding="utf-8"),
                '{"score": 0.9}',
            )
            self.assertFalse((self.root / "artifacts").exists())
        finally:
            shutil.rmtree(managed_root, ignore_errors=True)

    def test_explicit_managed_record_exact_retry_reuses_external_payload(self) -> None:
        managed_root = Path(tempfile.mkdtemp(prefix="research-cockpit-retry-"))
        try:
            save_yaml(
                self.root / "storage.yaml",
                {
                    "schema_version": "storage_layout_v1",
                    "project_id": "project_record",
                    "artifact_root": str(managed_root),
                },
            )

            first = self._record("op_record_managed_retry", mode="managed")
            shutil.rmtree(self.source)
            with patch(
                "research_cockpit.evidence_staging._copy_source_tree_hashed",
                side_effect=AssertionError("exact retry copied payload"),
            ):
                replay = self._record("op_record_managed_retry", mode="managed")

            self.assertEqual(replay, first)
            record_id = first["entities"]["record_id"]
            record = load_yaml(
                self.root / "artifact_records" / "experiment_x.yaml"
            )["records"][record_id]
            target = managed_root / record["storage"]["managed_key"]
            self.assertEqual(
                (target / "metrics.json").read_text(encoding="utf-8"),
                '{"score": 0.9}',
            )
            self.assertEqual(
                len(
                    [
                        path
                        for path in managed_root.rglob("metrics.json")
                        if path.is_file()
                    ]
                ),
                1,
            )
        finally:
            shutil.rmtree(managed_root, ignore_errors=True)

    def test_invalid_lease_is_rejected_before_managed_payload_copy(self) -> None:
        managed_root = Path(tempfile.mkdtemp(prefix="research-cockpit-preflight-"))
        try:
            save_yaml(
                self.root / "storage.yaml",
                {
                    "schema_version": "storage_layout_v1",
                    "project_id": "project_record",
                    "artifact_root": str(managed_root),
                },
            )
            with patch(
                "research_cockpit.evidence_staging._copy_source_tree_hashed"
            ) as copy_payload:
                with self.assertRaises(AssignmentLeaseError):
                    record_assignment_evidence(
                        self.root,
                        assignment_id="assign_x",
                        agent_id="agent_a",
                        lease_id="wrong-lease",
                        lease_epoch=self.lease["lease_epoch"],
                        operation_id="op_record_invalid_lease",
                        run_id=self.run_id,
                        source_dir=self.source,
                        links={"metrics": "metrics.json"},
                        mode="managed",
                        now=NOW + timedelta(minutes=1),
                    )

            copy_payload.assert_not_called()
            self.assertFalse((managed_root / ".staging").exists())
        finally:
            shutil.rmtree(managed_root, ignore_errors=True)

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
        self.assertEqual(parsed["mode"], "reference")


if __name__ == "__main__":
    unittest.main()
