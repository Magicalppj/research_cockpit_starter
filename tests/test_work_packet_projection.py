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
from research_cockpit.public_contracts import parse_public_contract
from research_cockpit.storage import save_text, save_yaml
from research_cockpit.validation_index import build_validation_index, validation_index_path
from research_cockpit.work_packets import assignment_result_revision, build_work_packet


class WorkPacketReadModelTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"work_packet_projection_{uuid.uuid4().hex}"
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

    def _write_dependency(self, *, status: str = "completed", summary: str = "Done") -> str | None:
        save_yaml(
            self.root / "agents" / "agent_dependency.yaml",
            {"agent_id": "agent_dependency", "status": "active"},
        )
        payload = {
            "assignment_id": "assign_dependency",
            "agent_id": "agent_dependency",
            "status": status,
            "root_node": "option_x",
            "current_node": "experiment_x",
            "allowed_subtree": {"root": "option_x", "policy": "descendants_only"},
            "allow_parallel_assignments": True,
        }
        if summary:
            payload["result"] = {"summary": summary}
        save_yaml(self.root / "assignments" / "assign_dependency.yaml", payload)
        return assignment_result_revision(AssignmentRecord.from_dict(payload))

    def _write_target(
        self,
        *,
        dependency_revision: str | None,
        legacy: bool = False,
    ) -> None:
        save_yaml(
            self.root / "agents" / "agent_target.yaml",
            {"agent_id": "agent_target", "status": "active"},
        )
        payload = {
            "assignment_id": "assign_target",
            "agent_id": "agent_target",
            "status": "active",
            "root_node": "option_x",
            "current_node": "experiment_x",
            "allowed_subtree": {"root": "option_x", "policy": "descendants_only"},
            "allow_parallel_assignments": True,
            "objective": "Run the bounded experiment.",
            "next_actions": ["Start the run."],
        }
        if not legacy:
            payload.update(
                {
                    "kind": "experiment",
                    "scope": {
                        "root_node": "option_x",
                        "subtree_policy": "descendants_only",
                        "write_policy": "exclusive",
                    },
                    "dependencies": [
                        {
                            "assignment_id": "assign_dependency",
                            "required_status": "completed",
                        }
                    ],
                    "inputs": {
                        "effective_baseline_revision": None,
                        "dependency_revisions": {
                            "assign_dependency": dependency_revision,
                        },
                    },
                    "success_criteria": ["The target metric improves."],
                    "deliverables": ["run", "artifact_record"],
                    "lease": {
                        "owner_agent_id": "agent_target",
                        "lease_id": "lease_target",
                        "lease_epoch": 1,
                        "heartbeat_at": "2026-07-19T09:55:00Z",
                        "expires_at": "2026-07-19T10:15:00Z",
                    },
                    "review": {
                        "required": False,
                        "status": "not_required",
                        "result_revision": None,
                    },
                }
            )
        save_yaml(self.root / "assignments" / "assign_target.yaml", payload)

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

    def test_fresh_packet_is_bounded_contract_valid_and_uses_index_fast_path(self) -> None:
        dependency_revision = self._write_dependency()
        self.assertIsNotNone(dependency_revision)
        self._write_target(dependency_revision=dependency_revision)
        self._write_index()

        packet = build_work_packet(self.root, "assign_target", now=self.now)

        self.assertEqual(parse_public_contract(packet), packet)
        self.assertTrue(packet["changed"])
        self.assertEqual(packet["revision_status"], "fresh")
        self.assertEqual(packet["readiness"], "ready")
        self.assertEqual(packet["lease"]["state"], "active")
        self.assertEqual(packet["allowed_operations"]["items"], ["start", "record", "close"])
        self.assertTrue(packet["runtime"]["index_fast_path"])
        self.assertFalse(packet["runtime"]["used_full_graph"])
        self.assertLess(len(json.dumps(packet, ensure_ascii=False).encode("utf-8")), 8 * 1024)

    def test_unsatisfied_dependency_with_changed_revision_is_explicitly_stale(self) -> None:
        current_revision = self._write_dependency(status="active", summary="")
        self.assertIsNone(current_revision)
        self._write_target(dependency_revision="result-v1:expected")
        self._write_index()

        packet = build_work_packet(self.root, "assign_target", now=self.now)

        self.assertEqual(packet["readiness"], "stale_inputs")
        self.assertEqual(packet["revision_status"], "stale")
        self.assertFalse(packet["dependencies"]["items"][0]["satisfied"])
        self.assertGreaterEqual(packet["stale_inputs"]["total"], 1)

    def test_completed_dependency_with_changed_result_marks_packet_stale(self) -> None:
        current_revision = self._write_dependency(summary="Current result")
        self.assertIsNotNone(current_revision)
        self._write_target(dependency_revision="result-v1:old")
        self._write_index()

        packet = build_work_packet(self.root, "assign_target", now=self.now)

        self.assertEqual(packet["revision_status"], "stale")
        self.assertEqual(packet["readiness"], "stale_inputs")
        self.assertIn("assign_dependency", " ".join(packet["stale_inputs"]["items"]))

    def test_legacy_packet_remains_usable_without_fabricated_input_or_lease(self) -> None:
        self._write_target(dependency_revision=None, legacy=True)
        self._write_index()

        packet = build_work_packet(self.root, "assign_target", now=self.now)

        self.assertIsNone(packet["input_revision"])
        self.assertEqual(packet["revision_status"], "unknown")
        self.assertEqual(packet["readiness"], "unknown_inputs")
        self.assertEqual(packet["lease"]["state"], "legacy_unknown")
        self.assertEqual(packet["allowed_operations"]["items"], ["start", "record", "close"])
        self.assertGreater(packet["compatibility_warnings"]["total"], 0)

    def test_since_revision_returns_minimal_unchanged_receipt(self) -> None:
        dependency_revision = self._write_dependency()
        self._write_target(dependency_revision=dependency_revision)
        self._write_index()
        first = build_work_packet(self.root, "assign_target", now=self.now)

        unchanged = build_work_packet(
            self.root,
            "assign_target",
            since_revision=first["revision"],
            now=self.now,
        )

        self.assertEqual(
            set(unchanged),
            {"schema_version", "changed", "revision", "assignment_id"},
        )
        self.assertFalse(unchanged["changed"])
        self.assertLess(len(json.dumps(unchanged).encode("utf-8")), 512)

    def test_fresh_index_does_not_parse_an_unrelated_graph_file(self) -> None:
        dependency_revision = self._write_dependency()
        self._write_target(dependency_revision=dependency_revision)
        save_yaml(
            self.root / "graph" / "nodes" / "unrelated.yaml",
            {
                "id": "unrelated",
                "type": "experiment",
                "title": "Unrelated",
                "status": "planned",
            },
        )
        self._write_index()
        (self.root / "graph" / "nodes" / "unrelated.yaml").write_text(
            "not: [valid",
            encoding="utf-8",
        )

        packet = build_work_packet(self.root, "assign_target", now=self.now)

        self.assertTrue(packet["runtime"]["index_fast_path"])
        self.assertEqual(packet["readiness"], "ready")


if __name__ == "__main__":
    unittest.main()
