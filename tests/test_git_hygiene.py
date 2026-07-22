from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.maintenance import build_git_hygiene_summary
from research_cockpit.commands.maintenance_audit import maintenance_audit_payload
from research_cockpit.storage import save_yaml


class GitHygieneSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.base = parent / f"git_hygiene_{uuid.uuid4().hex}"
        self.repo = self.base / "repo"
        self.repo.mkdir(parents=True)
        self._git("init")
        self._git("config", "user.name", "Research Cockpit Test")
        self._git("config", "user.email", "test@example.invalid")
        (self.repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        self._git("add", "tracked.txt")
        self._git("commit", "-m", "initial")
        self.nested = self.base / "nested"
        self._git("worktree", "add", "-b", "agent/nested", str(self.nested))

        self.root = self.repo / "state"
        managed_root = self.nested / "managed"
        save_yaml(
            self.root / "storage.yaml",
            {
                "schema_version": "storage_layout_v1",
                "project_id": "git_hygiene_test",
                "artifact_root": str(managed_root),
            },
        )
        (self.root / "artifacts").mkdir(parents=True)
        (self.root / "artifacts" / "legacy.bin").write_bytes(b"legacy")
        managed_root.mkdir(parents=True)
        (managed_root / "ignored.bin").write_bytes(b"managed")
        (self.repo / ".gitignore").write_text(
            "state/\nnested/managed/\n",
            encoding="utf-8",
        )
        self._git("add", ".gitignore")
        self._git("add", "-f", "state/artifacts/legacy.bin")
        self._git("commit", "-m", "track legacy payload")

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def _git(self, *args: str) -> None:
        completed = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_summary_reports_ignored_tracked_and_nested_worktree_risks_bounded(self) -> None:
        payload = build_git_hygiene_summary(
            self.root,
            repo=self.repo,
            max_status_bytes=1,
        )
        roots = {item["kind"]: item for item in payload["storage_roots"]}

        self.assertEqual(payload["schema_version"], "git_hygiene_v1")
        self.assertTrue(payload["status"]["truncated"])
        self.assertTrue(payload["status"]["ignored"]["lower_bound"])
        self.assertTrue(roots["state"]["inside_git_worktree"])
        self.assertTrue(roots["state"]["ignore"]["ignored"])
        self.assertTrue(roots["legacy_artifacts"]["ignore"]["tracked"])
        self.assertIn("tracked_storage_root", roots["legacy_artifacts"]["risks"])
        self.assertTrue(roots["managed_artifacts"]["inside_git_worktree"])
        self.assertEqual(roots["managed_artifacts"]["git_worktree"], "nested")
        self.assertIn("nested", roots["managed_artifacts"]["overlapping_worktrees"])
        self.assertNotIn("modified_gitignore", payload)

        deep = build_git_hygiene_summary(
            self.root,
            repo=self.repo,
            max_status_bytes=1,
            deep=True,
        )
        self.assertFalse(deep["status"]["truncated"])
        self.assertTrue(deep["status"]["ignored"]["exact"])

    def test_summary_reports_untracked_and_ignored_roots_without_writing_ignore_rules(self) -> None:
        root = self.repo / "complete_state"
        shutil.copytree(ROOT_DIR / "examples" / "demo_research_cockpit", root)
        managed_root = self.repo / "managed_payloads"
        save_yaml(
            root / "storage.yaml",
            {
                "schema_version": "storage_layout_v1",
                "project_id": "git_hygiene_complete_test",
                "artifact_root": str(managed_root),
            },
        )
        managed_root.mkdir()
        (managed_root / "payload.bin").write_bytes(b"payload")
        (self.repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text("managed_payloads/\n", encoding="utf-8")
        self._git("add", ".gitignore")
        self._git("commit", "-m", "ignore managed payloads")

        hygiene = build_git_hygiene_summary(root, repo=self.repo)
        audit = maintenance_audit_payload(
            root,
            repo=self.repo,
            base="master",
            min_size_bytes=1,
            limit=1,
        )
        roots = {item["kind"]: item for item in hygiene["storage_roots"]}

        self.assertGreater(hygiene["status"]["untracked"]["count"], 0)
        self.assertGreater(hygiene["status"]["ignored"]["count"], 0)
        self.assertEqual(roots["managed_artifacts"]["ignore"]["coverage"], "ignored")
        self.assertEqual(audit["schema_version"], "maintenance_audit_v2")
        self.assertGreater(audit["summary"]["state"]["file_count"], 0)
        self.assertLess(
            len(json.dumps(audit, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
            16 * 1024,
        )


if __name__ == "__main__":
    unittest.main()
