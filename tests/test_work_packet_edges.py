from __future__ import annotations

from datetime import datetime, timezone
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.agent_state import AssignmentRecord, load_assignments
from research_cockpit.model import load_nodes
from research_cockpit.storage import save_text, save_yaml
from research_cockpit.types import ValidationError
from research_cockpit.validation_index import build_validation_index, validation_index_path
from research_cockpit.work_packets import assignment_result_revision, build_work_packet


class WorkPacketEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"work_packet_edges_{uuid.uuid4().hex}"
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
                "status": "planned",
                "parent": "option_x",
            },
        )
        save_yaml(self.root / "current_state.yaml", {})
        self.now = datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_agent(self, agent_id: str) -> None:
        save_yaml(
            self.root / "agents" / f"{agent_id}.yaml",
            {"agent_id": agent_id, "status": "active"},
        )

    def _write_index(self) -> None:
        index = build_validation_index(
            self.root,
            load_nodes(self.root),
            [],
            assignments=load_assignments(self.root),
        )
        save_text(
            validation_index_path(self.root),
            json.dumps(index, ensure_ascii=False, indent=2),
        )

    def test_unchanged_dependency_result_waits_for_required_status(self) -> None:
        self._write_agent("agent_dependency")
        self._write_agent("agent_target")
        dependency = {
            "assignment_id": "assign_dependency",
            "agent_id": "agent_dependency",
            "status": "active",
            "root_node": "option_x",
            "current_node": "experiment_x",
            "allowed_subtree": {"root": "option_x", "policy": "descendants_only"},
            "allow_parallel_assignments": True,
            "result": {"summary": "Provisional result"},
        }
        dependency_revision = assignment_result_revision(AssignmentRecord.from_dict(dependency))
        save_yaml(self.root / "assignments" / "assign_dependency.yaml", dependency)
        save_yaml(
            self.root / "assignments" / "assign_target.yaml",
            {
                "assignment_id": "assign_target",
                "agent_id": "agent_target",
                "status": "active",
                "root_node": "option_x",
                "current_node": "experiment_x",
                "allowed_subtree": {"root": "option_x", "policy": "descendants_only"},
                "allow_parallel_assignments": True,
                "scope": {
                    "root_node": "option_x",
                    "subtree_policy": "descendants_only",
                    "write_policy": "exclusive",
                },
                "dependencies": [
                    {"assignment_id": "assign_dependency", "required_status": "completed"}
                ],
                "inputs": {
                    "effective_baseline_revision": None,
                    "dependency_revisions": {"assign_dependency": dependency_revision},
                },
                "lease": {
                    "owner_agent_id": "agent_target",
                    "lease_id": "lease_target",
                    "lease_epoch": 1,
                    "heartbeat_at": "2026-07-19T09:55:00Z",
                    "expires_at": "2026-07-19T10:15:00Z",
                },
            },
        )
        self._write_index()

        packet = build_work_packet(self.root, "assign_target", now=self.now)

        self.assertEqual(packet["revision_status"], "fresh")
        self.assertEqual(packet["readiness"], "waiting_dependencies")
        self.assertEqual(packet["stale_inputs"]["total"], 0)

    def test_dependency_cycle_is_a_structured_validation_error(self) -> None:
        self._write_agent("agent_dependency")
        self._write_agent("agent_target")
        save_yaml(
            self.root / "assignments" / "assign_dependency.yaml",
            {
                "assignment_id": "assign_dependency",
                "agent_id": "agent_dependency",
                "status": "completed",
                "root_node": "option_x",
                "current_node": "experiment_x",
                "allowed_subtree": {"root": "option_x", "policy": "descendants_only"},
                "dependencies": [{"assignment_id": "assign_target"}],
                "result": {"summary": "Done"},
            },
        )
        save_yaml(
            self.root / "assignments" / "assign_target.yaml",
            {
                "assignment_id": "assign_target",
                "agent_id": "agent_target",
                "status": "active",
                "root_node": "option_x",
                "current_node": "experiment_x",
                "allowed_subtree": {"root": "option_x", "policy": "descendants_only"},
                "dependencies": [{"assignment_id": "assign_dependency"}],
            },
        )

        with self.assertRaisesRegex(ValidationError, "dependency cycle"):
            build_work_packet(self.root, "assign_target", now=self.now)

    def test_active_assignment_without_owner_is_rejected(self) -> None:
        save_yaml(
            self.root / "assignments" / "assign_unowned.yaml",
            {
                "assignment_id": "assign_unowned",
                "agent_id": None,
                "status": "active",
                "root_node": "option_x",
                "current_node": "experiment_x",
                "allowed_subtree": {"root": "option_x", "policy": "descendants_only"},
            },
        )

        with self.assertRaisesRegex(ValidationError, "agent_id is required"):
            build_work_packet(self.root, "assign_unowned", now=self.now)

    def test_missing_index_uses_bounded_truth_fallback_without_graph_parse(self) -> None:
        self._write_agent("agent_legacy")
        save_yaml(
            self.root / "assignments" / "assign_legacy.yaml",
            {
                "assignment_id": "assign_legacy",
                "agent_id": "agent_legacy",
                "status": "active",
                "root_node": "option_x",
                "current_node": "experiment_x",
                "allowed_subtree": {"root": "option_x", "policy": "descendants_only"},
            },
        )
        (self.root / "graph" / "nodes" / "experiment_x.yaml").write_text(
            "not: [valid",
            encoding="utf-8",
        )

        packet = build_work_packet(self.root, "assign_legacy", now=self.now)

        self.assertFalse(packet["runtime"]["index_fast_path"])
        self.assertFalse(packet["runtime"]["used_full_graph"])
        self.assertEqual(packet["readiness"], "unknown_inputs")


if __name__ == "__main__":
    unittest.main()
