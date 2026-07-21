from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
DEV_SCRIPTS = ROOT_DIR / "dev" / "scripts"
sys.path.insert(0, str(DEV_SCRIPTS))

import run_test_profile


class VerificationProfileTests(unittest.TestCase):
    def test_fast_profile_is_a_bounded_subset_of_precommit(self) -> None:
        fast = run_test_profile.profile_test_targets("fast")
        precommit = run_test_profile.profile_test_targets("precommit")

        self.assertEqual(len(fast), len(set(fast)))
        self.assertEqual(len(precommit), len(set(precommit)))
        self.assertLess(set(fast), set(precommit))
        self.assertIn("tests.test_model", fast)
        self.assertIn("tests.test_verification_profiles", fast)
        self.assertIn("tests.test_blind_acceptance_regressions", precommit)
        self.assertIn(
            "tests.test_scripts.ScriptBehaviorTests."
            "test_context_execution_view_is_bounded_and_keeps_execution_invariants",
            precommit,
        )
        self.assertNotIn("tests.test_scripts", precommit)
        self.assertNotIn("tests.test_ui", precommit)
        self.assertNotIn("tests.test_release_check", precommit)

    def test_profile_plan_runs_release_check_once_at_the_right_depth(self) -> None:
        fast = run_test_profile.build_profile_plan("fast", python="python-x")
        precommit = run_test_profile.build_profile_plan("precommit", python="python-x")
        full = run_test_profile.build_profile_plan("full", python="python-x")

        self.assertEqual([stage["name"] for stage in fast], ["tests"])
        self.assertEqual(
            [stage["name"] for stage in precommit],
            ["tests", "release_check_read_only"],
        )
        self.assertEqual(
            [stage["name"] for stage in full],
            ["tests", "release_check_full"],
        )
        self.assertIn("--skip-mutating", precommit[1]["command"])
        self.assertNotIn("--skip-mutating", full[1]["command"])
        self.assertEqual(
            full[0]["env"]["RESEARCH_COCKPIT_EXTERNAL_RELEASE_CHECK"],
            "1",
        )

    def test_extra_tests_are_deduplicated_and_rejected_by_full(self) -> None:
        extra_test = "tests.test_ui.UiAppTests.test_health"
        fast = run_test_profile.build_profile_plan(
            "fast",
            python="python-x",
            extra_tests=(extra_test, extra_test),
        )

        self.assertEqual(fast[0]["command"].count(extra_test), 1)
        with self.assertRaisesRegex(ValueError, "fast and precommit"):
            run_test_profile.build_profile_plan(
                "full",
                python="python-x",
                extra_tests=(extra_test,),
            )

    def test_unittest_summary_parser_keeps_output_bounded(self) -> None:
        summary = run_test_profile.parse_unittest_summary(
            "." * 1000
            + "\n----------------------------------------------------------------------\n"
            + "Ran 259 tests in 34.125s\n\nOK (skipped=3)\n"
        )

        self.assertEqual(summary["tests_run"], 259)
        self.assertEqual(summary["skipped"], 3)
        self.assertEqual(summary["reported_duration_ms"], 34125)

    def test_list_command_is_machine_readable_and_does_not_run_tests(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(DEV_SCRIPTS / "run_test_profile.py"),
                "--list",
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
        self.assertEqual(payload["schema_version"], "test_profiles_v1")
        self.assertEqual([item["name"] for item in payload["profiles"]], [
            "fast",
            "precommit",
            "full",
        ])
        self.assertTrue(all(item["target_seconds"] > 0 for item in payload["profiles"]))

    def test_external_release_stage_skips_duplicate_embedded_full_check(self) -> None:
        env = {
            **os.environ,
            "RESEARCH_COCKPIT_EXTERNAL_RELEASE_CHECK": "1",
        }
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                (
                    "tests.test_release_check.SkillReleaseCheckTests."
                    "test_release_check_mutating_track_only_changes_temp_copy"
                ),
            ],
            cwd=ROOT_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("skipped=1", completed.stderr)

    def test_active_development_docs_route_to_test_profiles(self) -> None:
        active_docs = (
            ROOT_DIR / "AGENTS.md",
            ROOT_DIR / "README.md",
            ROOT_DIR / "dev" / "README.md",
            ROOT_DIR / "docs" / "testing-strategy.md",
        )

        for path in active_docs:
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertIn("run_test_profile.py", text)

        readme = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
        strategy = (ROOT_DIR / "docs" / "testing-strategy.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("\npython -m unittest\n", readme)
        self.assertIn("--extra-test", strategy)
        self.assertIn("不要串行运行", strategy)


if __name__ == "__main__":
    unittest.main()
