from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.agent_state import load_agents, load_assignment
import research_cockpit.assignment_leases as assignment_leases_module
from research_cockpit.assignment_leases import (
    AssignmentLeaseError,
    claim_assignment,
    heartbeat_assignment_lease,
    release_assignment,
    renew_assignment,
)
from research_cockpit.interaction_log import iter_interaction_events
from research_cockpit.public_contracts import parse_public_contract
from research_cockpit.storage import load_yaml, save_yaml


NOW = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)


class AssignmentLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"assignment_leases_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        self._save_node("option_x", node_type="option")
        self._save_node("experiment_x", parent="option_x")
        self._save_agent("agent_a")
        self._save_agent("agent_b")
        self._save_assignment()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _save_node(
        self,
        node_id: str,
        *,
        node_type: str = "experiment",
        parent: str | None = None,
    ) -> None:
        data = {
            "id": node_id,
            "type": node_type,
            "title": node_id,
            "status": "planned" if node_type == "experiment" else "open",
        }
        if parent:
            data["parent"] = parent
        save_yaml(self.root / "graph" / "nodes" / f"{node_id}.yaml", data)

    def _save_agent(self, agent_id: str) -> None:
        save_yaml(
            self.root / "agents" / f"{agent_id}.yaml",
            {
                "agent_id": agent_id,
                "status": "idle",
                "active_assignment_ids": [],
            },
        )

    def _save_assignment(
        self,
        assignment_id: str = "assign_x",
        *,
        status: str = "queued",
        agent_id: str | None = None,
        root_node: str = "option_x",
        current_node: str = "experiment_x",
        write_policy: str = "exclusive",
        lease: dict | None = None,
        lease_epoch_counter: int | None = None,
    ) -> None:
        data = {
            "assignment_id": assignment_id,
            "agent_id": agent_id,
            "status": status,
            "root_node": root_node,
            "current_node": current_node,
            "allowed_subtree": {
                "root": root_node,
                "policy": "descendants_only",
            },
            "scope": {
                "root_node": root_node,
                "subtree_policy": "descendants_only",
                "write_policy": write_policy,
            },
            "objective": f"Execute {assignment_id}",
        }
        if lease is not None:
            data["lease"] = lease
        if lease_epoch_counter is not None:
            data["lease_epoch_counter"] = lease_epoch_counter
        save_yaml(self.root / "assignments" / f"{assignment_id}.yaml", data)

    def _claim(
        self,
        *,
        assignment_id: str = "assign_x",
        agent_id: str = "agent_a",
        operation_id: str = "op_claim_x",
        now: datetime = NOW,
        coordinator: bool = False,
        reassign: bool = False,
    ) -> dict:
        return claim_assignment(
            self.root,
            assignment_id=assignment_id,
            agent_id=agent_id,
            operation_id=operation_id,
            now=now,
            coordinator=coordinator,
            reassign=reassign,
        )

    def test_claim_atomically_updates_assignment_agent_and_returns_packet(self) -> None:
        receipt = self._claim()

        parse_public_contract(receipt)
        assignment = load_assignment(self.root, "assign_x")
        agent = load_agents(self.root)["agent_a"]
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["operation"], "work claim")
        self.assertEqual(receipt["packet"]["revision"], receipt["packet_revision"])
        self.assertEqual(assignment.status, "active")
        self.assertEqual(assignment.agent_id, "agent_a")
        self.assertEqual(assignment.lease["owner_agent_id"], "agent_a")
        self.assertEqual(assignment.lease["lease_epoch"], 1)
        self.assertIn("assign_x", agent.active_assignment_ids)

    def test_exact_operation_retry_returns_same_receipt_without_new_event(self) -> None:
        first = self._claim()
        event_count = len(list(iter_interaction_events(self.root, strict=True)))

        assignment = load_yaml(self.root / "assignments" / "assign_x.yaml")
        assignment["next_actions"] = ["This later change must not alter the stored receipt."]
        save_yaml(self.root / "assignments" / "assign_x.yaml", assignment)
        second = self._claim()

        self.assertEqual(second, first)
        self.assertEqual(
            len(list(iter_interaction_events(self.root, strict=True))),
            event_count,
        )

    def test_missing_operation_index_is_rebuilt_on_exact_retry(self) -> None:
        first = self._claim()
        index_path = self.root / "dashboards" / "operation_index.json"
        index_path.unlink()

        second = self._claim()

        self.assertEqual(second, first)
        self.assertTrue(index_path.exists())

    def test_operation_id_payload_mismatch_is_rejected_without_write(self) -> None:
        self._claim()
        assignment_before = load_yaml(self.root / "assignments" / "assign_x.yaml")
        events_before = list(iter_interaction_events(self.root, strict=True))

        with self.assertRaises(AssignmentLeaseError) as caught:
            self._claim(agent_id="agent_b")

        self.assertEqual(caught.exception.receipt["error"]["code"], "idempotency_conflict")
        self.assertEqual(
            load_yaml(self.root / "assignments" / "assign_x.yaml"),
            assignment_before,
        )
        self.assertEqual(
            list(iter_interaction_events(self.root, strict=True)),
            events_before,
        )

    def test_serial_mutation_patches_operation_index_without_event_rescan(self) -> None:
        claimed = self._claim()
        lease = claimed["packet"]["lease"]

        with mock.patch(
            "research_cockpit.operation_receipts.iter_interaction_events",
            side_effect=AssertionError("fresh incremental index must avoid an event scan"),
        ):
            renewed = renew_assignment(
                self.root,
                assignment_id="assign_x",
                agent_id="agent_a",
                lease_id=lease["lease_id"],
                lease_epoch=lease["lease_epoch"],
                operation_id="op_renew_indexed",
                now=NOW + timedelta(seconds=30),
            )

        self.assertTrue(renewed["ok"])
        index = json.loads(
            (self.root / "dashboards" / "operation_index.json").read_text(encoding="utf-8")
        )
        self.assertIn("op_renew_indexed", index["operations"]["assignment:assign_x"])

    def test_concurrent_claim_has_one_winner(self) -> None:
        def attempt(agent_id: str, operation_id: str) -> tuple[str, bool]:
            try:
                self._claim(agent_id=agent_id, operation_id=operation_id)
            except AssignmentLeaseError:
                return agent_id, False
            return agent_id, True

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(
                pool.map(
                    lambda args: attempt(*args),
                    [("agent_a", "op_claim_a"), ("agent_b", "op_claim_b")],
                )
            )

        winners = [agent_id for agent_id, ok in outcomes if ok]
        assignment = load_assignment(self.root, "assign_x")
        self.assertEqual(len(winners), 1)
        self.assertEqual(assignment.agent_id, winners[0])

    def test_concurrent_claim_of_overlapping_assignments_has_one_winner(self) -> None:
        self._save_assignment(
            "assign_inner",
            root_node="experiment_x",
            current_node="experiment_x",
        )
        barrier = threading.Barrier(2)
        original_transaction = assignment_leases_module.execute_mutation_transaction

        def synchronized_transaction(*args: object, **kwargs: object) -> dict:
            barrier.wait(timeout=5)
            return original_transaction(*args, **kwargs)

        def attempt(
            assignment_id: str,
            agent_id: str,
            operation_id: str,
        ) -> tuple[str, bool, str | None]:
            try:
                self._claim(
                    assignment_id=assignment_id,
                    agent_id=agent_id,
                    operation_id=operation_id,
                )
            except AssignmentLeaseError as exc:
                return assignment_id, False, exc.receipt["error"]["code"]
            return assignment_id, True, None

        with mock.patch(
            "research_cockpit.assignment_leases.execute_mutation_transaction",
            side_effect=synchronized_transaction,
        ), ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(
                pool.map(
                    lambda args: attempt(*args),
                    [
                        ("assign_x", "agent_a", "op_outer"),
                        ("assign_inner", "agent_b", "op_inner"),
                    ],
                )
            )

        self.assertEqual(sum(1 for _assignment, ok, _code in outcomes if ok), 1)
        self.assertEqual(
            [code for _assignment, ok, code in outcomes if not ok],
            ["assignment_scope_conflict"],
        )

    def test_scope_overlap_includes_declared_children(self) -> None:
        option_path = self.root / "graph" / "nodes" / "option_x.yaml"
        option = load_yaml(option_path)
        option["children"] = ["experiment_x", "declared_child"]
        save_yaml(option_path, option)
        self._save_node("declared_child")
        self._save_assignment(
            "assign_other",
            status="active",
            agent_id="agent_b",
            root_node="declared_child",
            current_node="declared_child",
        )

        with self.assertRaises(AssignmentLeaseError) as caught:
            self._claim(operation_id="op_overlap")

        self.assertEqual(
            caught.exception.receipt["error"]["code"],
            "assignment_scope_conflict",
        )
        self.assertEqual(load_assignment(self.root, "assign_x").status, "queued")

    def test_invalid_epoch_counter_is_rejected_before_claim(self) -> None:
        self._save_assignment(lease_epoch_counter=-1)

        with self.assertRaisesRegex(
            ValueError,
            "lease_epoch_counter must be an integer >= 0",
        ):
            self._claim()

        assignment = load_assignment(self.root, "assign_x")
        self.assertEqual(assignment.status, "queued")

    def test_expired_lease_with_active_run_cannot_be_reassigned(self) -> None:
        self._claim()
        assignment_path = self.root / "assignments" / "assign_x.yaml"
        assignment = load_yaml(assignment_path)
        assignment["lease"]["heartbeat_at"] = (NOW - timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
        assignment["lease"]["expires_at"] = (NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        save_yaml(assignment_path, assignment)
        save_yaml(
            self.root / "runs" / "run_active.yaml",
            {
                "run_id": "run_active",
                "assignment_id": "assign_x",
                "experiment_id": "experiment_x",
                "status": "running",
                "started_at": (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            },
        )

        with self.assertRaises(AssignmentLeaseError) as caught:
            self._claim(
                agent_id="agent_b",
                operation_id="op_reassign",
                coordinator=True,
                reassign=True,
            )

        self.assertEqual(caught.exception.receipt["error"]["code"], "active_run_blocks_reassignment")
        self.assertEqual(load_assignment(self.root, "assign_x").agent_id, "agent_a")

    def test_reassignment_increments_epoch_and_rejects_old_owner(self) -> None:
        first = self._claim()
        old_lease = first["packet"]["lease"]
        assignment_path = self.root / "assignments" / "assign_x.yaml"
        assignment = load_yaml(assignment_path)
        assignment["lease"]["heartbeat_at"] = (NOW - timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
        assignment["lease"]["expires_at"] = (NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        save_yaml(assignment_path, assignment)
        stale_agent = load_yaml(self.root / "agents" / "agent_a.yaml")
        stale_agent["last_seen_at"] = (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        save_yaml(self.root / "agents" / "agent_a.yaml", stale_agent)

        second = self._claim(
            agent_id="agent_b",
            operation_id="op_reassign",
            coordinator=True,
            reassign=True,
        )

        self.assertEqual(second["packet"]["lease"]["lease_epoch"], 2)
        with self.assertRaises(AssignmentLeaseError) as caught:
            renew_assignment(
                self.root,
                assignment_id="assign_x",
                agent_id="agent_a",
                lease_id=old_lease["lease_id"],
                lease_epoch=old_lease["lease_epoch"],
                operation_id="op_old_renew",
                now=NOW,
            )
        self.assertEqual(caught.exception.receipt["error"]["code"], "lease_mismatch")

    def test_release_and_claim_preserve_monotonic_epoch(self) -> None:
        first = self._claim()
        lease = first["packet"]["lease"]
        release_assignment(
            self.root,
            assignment_id="assign_x",
            agent_id="agent_a",
            lease_id=lease["lease_id"],
            lease_epoch=lease["lease_epoch"],
            operation_id="op_release",
            now=NOW + timedelta(seconds=1),
        )

        released = load_assignment(self.root, "assign_x")
        self.assertEqual(released.status, "queued")
        self.assertEqual(released.lease["lease_epoch"], 0)
        self.assertEqual(released.raw["lease_epoch_counter"], 1)
        reclaimed = self._claim(
            agent_id="agent_b",
            operation_id="op_reclaim",
            now=NOW + timedelta(seconds=2),
        )
        self.assertEqual(reclaimed["packet"]["lease"]["lease_epoch"], 2)

    def test_runtime_heartbeat_renews_without_model_visible_output(self) -> None:
        claimed = self._claim()
        lease = claimed["packet"]["lease"]
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            receipt = heartbeat_assignment_lease(
                self.root,
                assignment_id="assign_x",
                agent_id="agent_a",
                lease_id=lease["lease_id"],
                lease_epoch=lease["lease_epoch"],
                now=NOW + timedelta(minutes=5),
            )

        self.assertEqual(stdout.getvalue(), "")
        self.assertTrue(receipt["ok"])
        self.assertGreater(
            load_assignment(self.root, "assign_x").lease["expires_at"],
            lease["expires_at"],
        )


class AssignmentLeaseCliTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"assignment_lease_cli_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
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
                "root_node": "experiment_x",
                "current_node": "experiment_x",
                "allowed_subtree": {"root": "experiment_x", "policy": "descendants_only"},
            },
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "research_cockpit.cli", *args],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_work_claim_route_returns_compact_operation_and_packet(self) -> None:
        out = self._run(
            "work",
            "claim",
            "--root",
            str(self.root),
            "--assignment",
            "assign_x",
            "--agent",
            "agent_a",
            "--operation-id",
            "op_cli_claim",
            "--return-packet",
            "--json",
            "--compact",
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        self.assertEqual(payload["schema_version"], "work_operation_v1")
        self.assertEqual(payload["operation"], "work claim")
        self.assertEqual(payload["packet"]["assignment_id"], "assign_x")
        self.assertLess(len(out.stdout.encode("utf-8")), 10 * 1024)

        retry = self._run(
            "work",
            "claim",
            "--root",
            str(self.root),
            "--assignment",
            "assign_x",
            "--agent",
            "agent_a",
            "--operation-id",
            "op_cli_claim",
            "--return-packet",
            "--json",
            "--compact",
        )
        self.assertEqual(retry.returncode, 0, retry.stderr or retry.stdout)
        self.assertEqual(json.loads(retry.stdout), payload)

    def test_work_claim_subprocess_concurrency_has_one_winner(self) -> None:
        base = [
            sys.executable,
            "-m",
            "research_cockpit.cli",
            "work",
            "claim",
            "--root",
            str(self.root),
            "--assignment",
            "assign_x",
        ]
        processes = [
            subprocess.Popen(
                [
                    *base,
                    "--agent",
                    agent_id,
                    "--operation-id",
                    operation_id,
                    "--return-packet",
                    "--json",
                    "--compact",
                ],
                cwd=ROOT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for agent_id, operation_id in (
                ("agent_a", "op_process_a"),
                ("agent_b", "op_process_b"),
            )
        ]
        results = [(*process.communicate(timeout=20), process.returncode) for process in processes]

        self.assertEqual(sorted(code for _stdout, _stderr, code in results), [0, 1])
        payloads = [json.loads(stdout) for stdout, _stderr, _code in results]
        self.assertEqual(sum(1 for payload in payloads if payload["ok"]), 1)
        self.assertEqual(load_assignment(self.root, "assign_x").status, "active")


if __name__ == "__main__":
    unittest.main()
