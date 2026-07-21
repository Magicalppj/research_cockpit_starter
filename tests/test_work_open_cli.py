from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.cli import COMMAND_CHOICES
from research_cockpit.storage import save_yaml


class WorkOpenCliTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"work_open_cli_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)

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

    def test_work_open_is_a_role_route_without_top_level_internal_alias(self) -> None:
        save_yaml(
            self.root / "assignments" / "assign_legacy.yaml",
            {
                "assignment_id": "assign_legacy",
                "agent_id": "agent_legacy",
                "status": "active",
                "root_node": "option_x",
                "current_node": "experiment_x",
                "allowed_subtree": {
                    "root": "option_x",
                    "policy": "descendants_only",
                },
                "objective": "Continue the legacy experiment.",
            },
        )

        out = self._run(
            "work",
            "open",
            "--root",
            str(self.root),
            "--assignment",
            "assign_legacy",
            "--json",
            "--compact",
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        self.assertIn("work", COMMAND_CHOICES)
        self.assertNotIn("work-open", COMMAND_CHOICES)
        self.assertEqual(payload["assignment_id"], "assign_legacy")
        self.assertEqual(payload["revision_status"], "unknown")
        self.assertFalse(payload["runtime"]["used_full_graph"])

    def test_work_open_since_returns_minimal_payload(self) -> None:
        save_yaml(
            self.root / "assignments" / "assign_legacy.yaml",
            {
                "assignment_id": "assign_legacy",
                "agent_id": "agent_legacy",
                "status": "active",
                "root_node": "option_x",
                "current_node": "experiment_x",
                "allowed_subtree": {
                    "root": "option_x",
                    "policy": "descendants_only",
                },
                "objective": "Continue the legacy experiment.",
            },
        )
        first = self._run(
            "work",
            "open",
            "--root",
            str(self.root),
            "--assignment",
            "assign_legacy",
            "--json",
            "--compact",
        )
        revision = json.loads(first.stdout)["revision"]

        unchanged = self._run(
            "work",
            "open",
            "--root",
            str(self.root),
            "--assignment",
            "assign_legacy",
            "--since",
            revision,
            "--json",
            "--compact",
        )
        payload = json.loads(unchanged.stdout)

        self.assertEqual(unchanged.returncode, 0, unchanged.stderr or unchanged.stdout)
        self.assertFalse(payload["changed"])
        self.assertLess(len(unchanged.stdout.encode("utf-8")), 512)

    def test_work_open_reports_structured_assignment_validation_errors(self) -> None:
        save_yaml(
            self.root / "assignments" / "assign_invalid.yaml",
            {
                "assignment_id": "assign_invalid",
                "agent_id": "agent_invalid",
                "status": "active",
                "root_node": "option_x",
                "current_node": "option_x",
                "allowed_subtree": {
                    "root": "option_x",
                    "policy": "descendants_only",
                },
                "scope": "invalid",
                "dependencies": [{"assignment_id": ""}],
                "lease": {"lease_id": "lease_x", "lease_epoch": "one"},
            },
        )

        out = self._run(
            "work",
            "open",
            "--root",
            str(self.root),
            "--assignment",
            "assign_invalid",
            "--json",
            "--compact",
        )
        payload = json.loads(out.stdout)

        self.assertEqual(out.returncode, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "assignment_validation_error")
        messages = payload["error"]["messages"]
        self.assertTrue(any("scope" in message for message in messages))
        self.assertTrue(any("dependencies[0].assignment_id" in message for message in messages))
        self.assertTrue(any("lease.lease_epoch" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
