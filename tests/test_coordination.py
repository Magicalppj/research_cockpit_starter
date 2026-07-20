from __future__ import annotations

import json
import shutil
import sys
import unittest
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.agent_state import load_assignments
from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.coordination import build_coordination_snapshot
from research_cockpit.model import ResearchNode
from research_cockpit.public_contracts import parse_public_contract
from research_cockpit.storage import save_text, save_yaml
from research_cockpit.validation_index import (
    build_validation_index,
    validation_index_path,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


class CoordinationSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"coordination_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        save_yaml(self.root / "current_state.yaml", {})
        save_yaml(
            self.root / "graph" / "nodes" / "problem_x.yaml",
            {
                "id": "problem_x",
                "type": "problem",
                "title": "Problem X",
                "status": "active",
                "children": ["option_x"],
            },
        )
        save_yaml(
            self.root / "graph" / "nodes" / "option_x.yaml",
            {
                "id": "option_x",
                "type": "option",
                "title": "Option X",
                "status": "active",
                "parent": "problem_x",
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

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _assignment(
        self,
        assignment_id: str,
        *,
        status: str = "queued",
        agent_id: str | None = None,
        kind: str = "experiment",
        root_node: str = "option_x",
        current_node: str = "experiment_x",
        dependencies: list[dict] | None = None,
        dependency_revisions: dict[str, str] | None = None,
        review_required: bool = False,
        review_status: str | None = None,
        result_revision: str | None = None,
        lease_expires_at: datetime | None = None,
        write_policy: str = "exclusive",
    ) -> None:
        if agent_id:
            save_yaml(
                self.root / "agents" / f"{agent_id}.yaml",
                {
                    "agent_id": agent_id,
                    "status": "active",
                    "active_assignment_ids": [assignment_id],
                },
            )
        lease: dict = {}
        if lease_expires_at is not None:
            lease = {
                "lease_id": f"lease_{assignment_id}",
                "owner_agent_id": agent_id,
                "lease_epoch": 1,
                "heartbeat_at": _timestamp(lease_expires_at - timedelta(minutes=5)),
                "expires_at": _timestamp(lease_expires_at),
            }
        else:
            lease = {
                "lease_id": None,
                "owner_agent_id": None,
                "lease_epoch": 0,
                "heartbeat_at": None,
                "expires_at": None,
            }
        review_state = review_status or ("pending" if review_required else "not_required")
        result = {}
        if result_revision:
            result = {
                "schema_version": "evidence_bundle_v1",
                "revision": result_revision,
                "summary": f"Result for {assignment_id}",
            }
        save_yaml(
            self.root / "assignments" / f"{assignment_id}.yaml",
            {
                "assignment_id": assignment_id,
                "agent_id": agent_id,
                "status": status,
                "kind": kind,
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
                "allow_parallel_assignments": True,
                "dependencies": dependencies or [],
                "inputs": {
                    "effective_baseline_revision": None,
                    "dependency_revisions": dependency_revisions or {},
                },
                "input_revision": f"input-v1:{assignment_id}",
                "lease": lease,
                "review": {
                    "required": review_required,
                    "status": review_state,
                    "result_revision": (
                        result_revision if review_state in {"approved", "changes_requested"} else None
                    ),
                },
                "result": result,
            },
        )

    def test_snapshot_is_revisioned_paginated_filterable_and_bounded(self) -> None:
        for index in range(25):
            self._assignment(f"assign_{index:02d}")
        build_dashboard(self.root)

        first = build_coordination_snapshot(
            self.root,
            limit=7,
            statuses={"queued"},
            now=NOW,
        )
        unchanged = build_coordination_snapshot(
            self.root,
            limit=7,
            statuses={"queued"},
            since_revision=first["revision"],
            now=NOW,
        )
        second = build_coordination_snapshot(
            self.root,
            limit=7,
            statuses={"queued"},
            page=first["next_page"],
            now=NOW,
        )

        parse_public_contract(first)
        self.assertEqual(first["schema_version"], "coordination_snapshot_v1")
        self.assertRegex(first["revision"], r"^coord-v1:[a-f0-9]{64}$")
        self.assertEqual(first["assignments"]["limit"], 7)
        self.assertEqual(first["assignments"]["total"], 25)
        self.assertEqual(len(first["assignments"]["items"]), 7)
        self.assertIsNotNone(first["next_page"])
        self.assertEqual(unchanged["changed"], False)
        self.assertEqual(set(unchanged), {"schema_version", "changed", "revision"})
        first_ids = {item["assignment_id"] for item in first["assignments"]["items"]}
        second_ids = {item["assignment_id"] for item in second["assignments"]["items"]}
        self.assertFalse(first_ids & second_ids)
        self.assertLess(
            len(json.dumps(first, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
            32 * 1024,
        )

    def test_coord_overview_cli_routes_filters_and_compact_json(self) -> None:
        self._assignment("assign_cli")
        build_dashboard(self.root)

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "research_cockpit.cli",
                "coord",
                "overview",
                "--root",
                str(self.root),
                "--status",
                "queued",
                "--limit",
                "1",
                "--json",
                "--compact",
            ],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema_version"], "coordination_snapshot_v1")
        self.assertEqual(payload["assignments"]["total"], 1)
        self.assertEqual(
            payload["assignments"]["items"][0]["assignment_id"],
            "assign_cli",
        )

    def test_snapshot_summarizes_dependencies_leases_and_reviews(self) -> None:
        self._assignment(
            "assign_done",
            status="completed",
            result_revision="result-v1:current",
        )
        self._assignment(
            "assign_incomplete",
            status="cancelled",
            result_revision="result-v1:incomplete",
        )
        self._assignment(
            "assign_wait",
            dependencies=[{"assignment_id": "assign_incomplete", "required_status": "completed"}],
            dependency_revisions={"assign_incomplete": "result-v1:incomplete"},
        )
        self._assignment(
            "assign_stale",
            dependencies=[{"assignment_id": "assign_done", "required_status": "completed"}],
            dependency_revisions={"assign_done": "result-v1:old"},
        )
        self._assignment(
            "assign_expired",
            status="active",
            agent_id="agent_expired",
            lease_expires_at=NOW - timedelta(seconds=1),
        )
        self._assignment(
            "assign_review",
            status="completed",
            review_required=True,
            review_status="pending",
            result_revision="result-v1:review",
        )
        build_dashboard(self.root)

        snapshot = build_coordination_snapshot(self.root, limit=20, now=NOW)
        rows = {item["assignment_id"]: item for item in snapshot["assignments"]["items"]}

        self.assertEqual(rows["assign_wait"]["readiness"], "waiting_dependencies")
        self.assertEqual(rows["assign_stale"]["readiness"], "stale_inputs")
        self.assertEqual(rows["assign_expired"]["lease_state"], "expired")
        self.assertEqual(rows["assign_review"]["review_status"], "pending")
        self.assertEqual(snapshot["counts"]["waiting"], 1)
        self.assertEqual(snapshot["counts"]["stale_inputs"], 1)
        self.assertEqual(snapshot["counts"]["expired_leases"], 1)
        self.assertEqual(snapshot["counts"]["pending_review"], 1)

    def test_nested_active_writer_scopes_are_reported_without_full_graph_load(self) -> None:
        self._assignment(
            "assign_parent",
            status="active",
            agent_id="agent_parent",
            root_node="option_x",
            lease_expires_at=NOW + timedelta(minutes=5),
        )
        self._assignment(
            "assign_child",
            status="active",
            agent_id="agent_child",
            root_node="experiment_x",
            current_node="experiment_x",
            lease_expires_at=NOW + timedelta(minutes=5),
        )
        build_dashboard(self.root)

        with patch("research_cockpit.coordination.load_assignments", side_effect=AssertionError("full assignment scan")):
            snapshot = build_coordination_snapshot(self.root, limit=20, now=NOW)

        warnings = snapshot["overlap_warnings"]["items"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("assign_child", warnings[0])
        self.assertIn("assign_parent", warnings[0])

    def test_stale_assignment_index_falls_back_to_assignment_files(self) -> None:
        self._assignment("assign_a")
        build_dashboard(self.root)
        path = self.root / "assignments" / "assign_a.yaml"
        data = path.read_text(encoding="utf-8")
        path.write_text(data + "objective: changed after build\n", encoding="utf-8")

        with patch("research_cockpit.coordination.load_assignments", wraps=load_assignments) as loader:
            snapshot = build_coordination_snapshot(self.root, limit=20, now=NOW)

        self.assertTrue(loader.called)
        self.assertEqual(snapshot["assignments"]["total"], 1)


class LargeCoordinationFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"coordination_large_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        save_yaml(self.root / "current_state.yaml", {})
        save_yaml(
            self.root / "graph" / "nodes" / "node_0.yaml",
            {
                "id": "node_0",
                "type": "option",
                "title": "Node 0",
                "status": "active",
            },
        )
        for index in range(200):
            save_yaml(
                self.root / "assignments" / f"assign_{index:03d}.yaml",
                {
                    "assignment_id": f"assign_{index:03d}",
                    "agent_id": None,
                    "status": "queued",
                    "kind": "experiment",
                    "root_node": "node_0",
                    "current_node": "node_0",
                    "allowed_subtree": {"root": "node_0", "policy": "descendants_only"},
                    "scope": {
                        "root_node": "node_0",
                        "subtree_policy": "descendants_only",
                        "write_policy": "exclusive",
                    },
                    "allow_parallel_assignments": True,
                    "inputs": {
                        "effective_baseline_revision": None,
                        "dependency_revisions": {},
                    },
                    "input_revision": f"input-v1:{index}",
                    "review": {"required": False, "status": "not_required", "result_revision": None},
                    "lease": {
                        "lease_id": None,
                        "owner_agent_id": None,
                        "lease_epoch": 0,
                        "heartbeat_at": None,
                        "expires_at": None,
                    },
                },
            )
        nodes = {
            f"node_{index}": ResearchNode.from_dict(
                {
                    "id": f"node_{index}",
                    "type": "option",
                    "title": f"Node {index}",
                    "status": "active",
                }
            )
            for index in range(5000)
        }
        index = build_validation_index(
            self.root,
            nodes,
            [],
            assignments=load_assignments(self.root),
        )
        save_text(
            validation_index_path(self.root),
            json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_5k_node_snapshot_uses_assignment_index_and_meets_contract_budget(self) -> None:
        with patch("research_cockpit.coordination.load_assignments", side_effect=AssertionError("full assignment scan")):
            snapshot = build_coordination_snapshot(self.root, limit=20, now=NOW)

        self.assertEqual(snapshot["assignments"]["total"], 200)
        self.assertEqual(len(snapshot["assignments"]["items"]), 20)
        self.assertLess(
            len(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
            32 * 1024,
        )

    def test_runtime_benchmark_measures_coord_overview(self) -> None:
        out = subprocess.run(
            [
                sys.executable,
                str(ROOT_DIR / "dev" / "scripts" / "benchmark_runtime.py"),
                "--root",
                str(self.root),
                "--cold-runs",
                "1",
                "--warm-runs",
                "2",
                "--operation",
                "coord_overview",
                "--json",
            ],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        payload = json.loads(out.stdout)
        result = payload["results"][0]
        self.assertEqual(payload["operations"], ["coord_overview"])
        self.assertLess(result["warm_summary"]["stdout_bytes"]["max"], 32 * 1024)
        samples = [*result["cold_samples"], *result["warm_samples"]]
        self.assertTrue(all(sample["changed_file_count"] == 0 for sample in samples))


if __name__ == "__main__":
    unittest.main()
