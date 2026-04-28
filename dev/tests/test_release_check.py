from __future__ import annotations

import shutil
import sys
import unittest
import uuid
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT_DIR / "skills" / "research-cockpit"
DEV_SCRIPTS = ROOT_DIR / "dev" / "scripts"
sys.path.insert(0, str(DEV_SCRIPTS))

from run_skill_release_check import (
    package_shape_track,
    public_scan_track,
    release_check_payload,
    runtime_dependency_track,
)


class SkillReleaseCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_parent = ROOT_DIR / ".test_tmp"
        self.tmp_parent.mkdir(exist_ok=True)
        self.tmp_root = self.tmp_parent / f"release_check_tests_{uuid.uuid4().hex}"
        self.tmp_root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_package_shape_and_public_scan_accept_current_skill(self) -> None:
        shape = package_shape_track(SKILL_ROOT)
        public = public_scan_track(SKILL_ROOT)

        self.assertTrue(shape["passed"], shape)
        self.assertTrue(public["passed"], public)

    def test_public_scan_reports_private_path_like_content(self) -> None:
        package = self.tmp_root / "research-cockpit"
        package.mkdir()
        (package / "README.md").write_text("Use " + "D:" + "\\Tools" + " here\n", encoding="utf-8")

        public = public_scan_track(package)

        self.assertFalse(public["passed"])
        self.assertIn("README.md", public["summary"]["offenders"][0])

    def test_runtime_dependency_failure_is_structured(self) -> None:
        track = runtime_dependency_track(
            sys.executable,
            {"sys": "stdlib", "definitely_missing_module_for_release_check": "example-package"},
        )

        self.assertFalse(track["passed"])
        self.assertIn("definitely_missing_module_for_release_check", track["summary"]["missing_modules"])
        self.assertNotIn("Traceback", track["stdout"])
        self.assertNotIn("Traceback", track["stderr"])

    def test_release_check_skip_mutating_runs_read_only_tracks(self) -> None:
        payload = release_check_payload(
            SKILL_ROOT,
            python=sys.executable,
            temp_parent=self.tmp_root,
            skip_mutating=True,
            keep_temp=False,
        )
        by_name = {track["name"]: track for track in payload["tracks"]}

        self.assertTrue(payload["ok"], payload)
        self.assertTrue(by_name["read_only_startup"]["passed"], by_name["read_only_startup"])
        self.assertTrue(by_name["portable_copy"]["passed"], by_name["portable_copy"])
        self.assertEqual(by_name["isolated_mutation"]["skipped"], True)

    def test_release_check_missing_package_path_fails_without_traceback(self) -> None:
        payload = release_check_payload(
            self.tmp_root / "missing-skill",
            python=sys.executable,
            temp_parent=self.tmp_root,
            skip_mutating=True,
            keep_temp=False,
        )
        by_name = {track["name"]: track for track in payload["tracks"]}

        self.assertFalse(payload["ok"])
        self.assertFalse(by_name["package_shape"]["passed"])
        self.assertEqual(by_name["portable_copy"]["skipped"], True)
        self.assertNotIn("Traceback", str(payload))

    def test_release_check_mutating_track_only_changes_temp_copy(self) -> None:
        payload = release_check_payload(
            SKILL_ROOT,
            python=sys.executable,
            temp_parent=self.tmp_root,
            skip_mutating=False,
            keep_temp=False,
        )
        by_name = {track["name"]: track for track in payload["tracks"]}

        self.assertTrue(payload["ok"], payload)
        self.assertTrue(by_name["isolated_mutation"]["passed"], by_name["isolated_mutation"])
        self.assertEqual(by_name["isolated_mutation"]["summary"]["source_changed"], False)
        self.assertGreaterEqual(len(by_name["isolated_mutation"]["summary"]["copy_changed_files"]), 1)


if __name__ == "__main__":
    unittest.main()
