from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.agent_state import load_assignment
from research_cockpit.assignment_leases import AssignmentLeaseError, claim_assignment
from research_cockpit.assignment_runs import start_assignment_run
from research_cockpit.commands.work_start import parse_start_input
from research_cockpit.interaction_log import iter_interaction_events
from research_cockpit.model import load_runs
from research_cockpit.storage import load_yaml, save_yaml


NOW = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)


class WorkStartTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"work_start_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
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
            self.root / "agents" / "agent_b.yaml",
            {"agent_id": "agent_b", "status": "idle", "active_assignment_ids": []},
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
        self.claimed = claim_assignment(
            self.root,
            assignment_id="assign_x",
            agent_id="agent_a",
            operation_id="op_claim",
            now=NOW,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    @property
    def lease(self) -> dict:
        return self.claimed["packet"]["lease"]

    def _start(self, operation_id: str = "op_start") -> dict:
        return start_assignment_run(
            self.root,
            assignment_id="assign_x",
            agent_id="agent_a",
            lease_id=self.lease["lease_id"],
            lease_epoch=self.lease["lease_epoch"],
            operation_id=operation_id,
            slug_hint="trial",
            now=NOW + timedelta(seconds=30),
        )

    def test_start_generates_run_and_piggybacks_lease_renewal(self) -> None:
        receipt = self._start()

        run_id = receipt["entities"]["run_id"]
        run = load_runs(self.root)[run_id]
        assignment = load_assignment(self.root, "assign_x")
        self.assertRegex(run_id, r"^run_assign_x_trial_[a-f0-9]{12}$")
        self.assertEqual(run.status, "running")
        self.assertEqual(run.raw["assignment_id"], "assign_x")
        self.assertEqual(run.raw["operation_id"], "op_start")
        self.assertEqual(load_yaml(self.root / "graph" / "nodes" / "experiment_x.yaml")["status"], "running")
        self.assertGreater(assignment.lease["expires_at"], self.lease["expires_at"])
        kinds = [event["kind"] for event in iter_interaction_events(self.root, strict=True)]
        self.assertEqual(kinds.count("assignment_lease_renewed"), 0)
        self.assertIn("assignment_run_started", kinds)

    def test_start_rejects_assignment_not_allowed_by_work_packet(self) -> None:
        assignment_path = self.root / "assignments" / "assign_x.yaml"
        assignment = load_yaml(assignment_path)
        assignment["inputs"] = {
            "effective_baseline_revision": None,
            "dependency_revisions": {},
        }
        save_yaml(assignment_path, assignment)

        with self.assertRaises(AssignmentLeaseError) as caught:
            self._start(operation_id="op_not_ready")

        self.assertEqual(caught.exception.receipt["error"]["code"], "assignment_not_ready")
        self.assertEqual(load_runs(self.root), {})

    def test_start_rejects_expired_lease(self) -> None:
        assignment_path = self.root / "assignments" / "assign_x.yaml"
        assignment = load_yaml(assignment_path)
        assignment["lease"]["heartbeat_at"] = (NOW - timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
        assignment["lease"]["expires_at"] = (NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        save_yaml(assignment_path, assignment)

        with self.assertRaises(AssignmentLeaseError) as caught:
            self._start(operation_id="op_expired")

        self.assertEqual(caught.exception.receipt["error"]["code"], "lease_expired")
        self.assertEqual(load_runs(self.root), {})

    def test_start_reuses_create_run_domain_for_launcher_metadata(self) -> None:
        receipt = start_assignment_run(
            self.root,
            assignment_id="assign_x",
            agent_id="agent_a",
            lease_id=self.lease["lease_id"],
            lease_epoch=self.lease["lease_epoch"],
            operation_id="op_launcher_start",
            slug_hint="launcher",
            run_fields={
                "launcher": "shell",
                "command": "python train.py",
                "progress_file": "artifacts/{experiment_id}/{run_id}/progress.json",
                "resources": {"gpu": 1},
            },
            now=NOW + timedelta(seconds=30),
        )

        run = load_runs(self.root)[receipt["entities"]["run_id"]]
        self.assertEqual(run.launcher, "shell")
        self.assertEqual(run.command, "python train.py")
        self.assertEqual(
            run.progress_file,
            f"artifacts/experiment_x/{run.run_id}/progress.json",
        )
        self.assertEqual(run.resources, {"gpu": 1})

    def test_start_operation_payload_mismatch_does_not_create_second_run(self) -> None:
        self._start(operation_id="op_start_mismatch")

        with self.assertRaises(AssignmentLeaseError) as caught:
            start_assignment_run(
                self.root,
                assignment_id="assign_x",
                agent_id="agent_a",
                lease_id=self.lease["lease_id"],
                lease_epoch=self.lease["lease_epoch"],
                operation_id="op_start_mismatch",
                run_fields={"command": "python changed.py"},
                now=NOW + timedelta(seconds=30),
            )

        self.assertEqual(caught.exception.receipt["error"]["code"], "idempotency_conflict")
        self.assertEqual(len(load_runs(self.root)), 1)

    def test_start_input_rejects_unknown_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown fields: unexpected"):
            parse_start_input(
                {
                    "schema_version": "work_start_v1",
                    "agent_id": "agent_a",
                    "lease_id": "lease_x",
                    "lease_epoch": 1,
                    "operation_id": "op_x",
                    "unexpected": True,
                }
            )

    def test_start_retry_returns_same_generated_run_without_duplicate(self) -> None:
        first = self._start()
        second = self._start()

        self.assertRegex(first["packet_revision"], r"^packet-v1:[a-f0-9]{64}$")
        self.assertEqual(second, first)
        self.assertEqual(second["packet_revision"], first["packet_revision"])
        self.assertEqual(len(load_runs(self.root)), 1)

    def test_old_owner_cannot_start_after_reassignment(self) -> None:
        assignment_path = self.root / "assignments" / "assign_x.yaml"
        assignment = load_yaml(assignment_path)
        assignment["lease"]["heartbeat_at"] = (NOW - timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
        assignment["lease"]["expires_at"] = (NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        save_yaml(assignment_path, assignment)
        agent_a = load_yaml(self.root / "agents" / "agent_a.yaml")
        agent_a["last_seen_at"] = (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        save_yaml(self.root / "agents" / "agent_a.yaml", agent_a)
        claim_assignment(
            self.root,
            assignment_id="assign_x",
            agent_id="agent_b",
            operation_id="op_reassign",
            now=NOW,
            coordinator=True,
            reassign=True,
        )

        with self.assertRaises(AssignmentLeaseError) as caught:
            self._start(operation_id="op_stale_start")

        self.assertEqual(caught.exception.receipt["error"]["code"], "lease_mismatch")
        self.assertEqual(load_runs(self.root), {})

    def test_work_start_cli_uses_one_mutating_invocation(self) -> None:
        start_file = self.root / "start.yaml"
        save_yaml(
            start_file,
            {
                "schema_version": "work_start_v1",
                "agent_id": "agent_a",
                "lease_id": self.lease["lease_id"],
                "lease_epoch": self.lease["lease_epoch"],
                "operation_id": "op_cli_start",
                "slug": "cli",
                "run": {"launcher": "shell", "command": "python train.py"},
            },
        )
        out = subprocess.run(
            [
                sys.executable,
                "-m",
                "research_cockpit.cli",
                "work",
                "start",
                "--root",
                str(self.root),
                "--assignment",
                "assign_x",
                "--file",
                str(start_file),
                "--json",
                "--compact",
            ],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        self.assertEqual(payload["operation"], "work start")
        self.assertRegex(payload["entities"]["run_id"], r"^run_assign_x_cli_[a-f0-9]{12}$")
        run = load_runs(self.root)[payload["entities"]["run_id"]]
        self.assertEqual(run.launcher, "shell")


if __name__ == "__main__":
    unittest.main()
