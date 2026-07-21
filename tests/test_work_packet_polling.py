from __future__ import annotations

from datetime import datetime, timezone
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.agent_state import load_assignments
from research_cockpit.model import load_nodes
from research_cockpit.storage import save_text, save_yaml
from research_cockpit.validation_index import build_validation_index, validation_index_path
from research_cockpit.work_packets import build_work_packet


class WorkPacketPollingTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"work_packet_polling_{uuid.uuid4().hex}"
        save_yaml(
            self.root / "graph" / "nodes" / "option_poll.yaml",
            {
                "id": "option_poll",
                "type": "option",
                "title": "Polling option",
                "status": "active",
                "children": ["experiment_poll"],
            },
        )
        save_yaml(
            self.root / "graph" / "nodes" / "experiment_poll.yaml",
            {
                "id": "experiment_poll",
                "type": "experiment",
                "title": "Polling experiment",
                "status": "planned",
                "parent": "option_poll",
            },
        )
        save_yaml(self.root / "current_state.yaml", {})
        save_yaml(
            self.root / "agents" / "agent_poll.yaml",
            {"agent_id": "agent_poll", "status": "active"},
        )
        save_yaml(
            self.root / "assignments" / "assign_poll.yaml",
            {
                "assignment_id": "assign_poll",
                "agent_id": "agent_poll",
                "kind": "experiment",
                "status": "active",
                "root_node": "option_poll",
                "current_node": "experiment_poll",
                "allowed_subtree": {
                    "root": "option_poll",
                    "policy": "descendants_only",
                },
                "scope": {
                    "root_node": "option_poll",
                    "subtree_policy": "descendants_only",
                    "write_policy": "exclusive",
                },
                "inputs": {
                    "effective_baseline_revision": None,
                    "dependency_revisions": {},
                },
                "lease": {
                    "owner_agent_id": "agent_poll",
                    "lease_id": "lease_poll",
                    "lease_epoch": 1,
                    "heartbeat_at": "2026-07-19T09:55:00Z",
                    "expires_at": "2026-07-19T10:15:00Z",
                },
            },
        )
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

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_unchanged_poll_skips_indexed_graph_projection(self) -> None:
        now = datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc)
        packet = build_work_packet(self.root, "assign_poll", now=now)

        with patch(
            "research_cockpit.work_packets.load_indexed_root_snapshot",
            side_effect=AssertionError("unchanged polling projected graph YAML"),
        ):
            receipt = build_work_packet(
                self.root,
                "assign_poll",
                since_revision=packet["revision"],
                now=now,
            )

        self.assertEqual(
            receipt,
            {
                "schema_version": "work_packet_v1",
                "changed": False,
                "revision": packet["revision"],
                "assignment_id": "assign_poll",
            },
        )

    def test_lease_expiry_invalidates_unchanged_revision(self) -> None:
        active = build_work_packet(
            self.root,
            "assign_poll",
            now=datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc),
        )

        expired = build_work_packet(
            self.root,
            "assign_poll",
            since_revision=active["revision"],
            now=datetime(2026, 7, 19, 10, 16, tzinfo=timezone.utc),
        )

        self.assertTrue(expired["changed"])
        self.assertNotEqual(expired["revision"], active["revision"])
        self.assertEqual(expired["lease"]["state"], "expired")


if __name__ == "__main__":
    unittest.main()
