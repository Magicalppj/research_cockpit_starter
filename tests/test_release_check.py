from __future__ import annotations

import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT_DIR
DEV_SCRIPTS = ROOT_DIR / "dev" / "scripts"
sys.path.insert(0, str(DEV_SCRIPTS))

from run_skill_release_check import (
    _run_command,
    instruction_surface_track,
    package_shape_track,
    public_scan_track,
    read_only_startup_track,
    release_check_payload,
    runtime_dependency_track,
    workflow_contract_track,
)
from run_subagent_forward_check import subagent_forward_check_payload
from run_agent_usability_check import agent_usability_check_payload


EXTERNAL_RELEASE_CHECK = (
    os.environ.get("RESEARCH_COCKPIT_EXTERNAL_RELEASE_CHECK") == "1"
)


class SkillReleaseCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_parent = ROOT_DIR / ".test_tmp"
        self.tmp_parent.mkdir(exist_ok=True)
        self.tmp_root = self.tmp_parent / f"rc_{uuid.uuid4().hex[:8]}"
        self.tmp_root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_package_shape_and_public_scan_accept_current_skill(self) -> None:
        shape = package_shape_track(SKILL_ROOT)
        public = public_scan_track(SKILL_ROOT)

        self.assertTrue(shape["passed"], shape)
        self.assertTrue(public["passed"], public)

    def test_workflow_contract_track_checks_compact_and_closeout_contracts(self) -> None:
        track = workflow_contract_track(SKILL_ROOT, sys.executable)

        self.assertTrue(track["passed"], track)
        self.assertEqual(track["summary"]["context_schema_version"], "execution_context_v1")
        self.assertLessEqual(track["summary"]["context_stdout_bytes"], 4 * 1024)
        self.assertLessEqual(track["summary"]["command_summary_stdout_bytes"], 20 * 1024)
        self.assertTrue(track["summary"]["canonical_rows_present"])
        self.assertEqual(track["summary"]["manifest_help_missing"], [])
        self.assertTrue(track["summary"]["canonical_schema_contracts"])
        self.assertTrue(track["summary"]["removed_routes_rejected"])
        self.assertTrue(track["summary"]["structured_closeout_documented"])

    def test_instruction_surface_track_enforces_router_budget(self) -> None:
        track = instruction_surface_track(SKILL_ROOT)

        self.assertTrue(track["passed"], track)
        self.assertLessEqual(track["summary"]["line_count"], 90)
        self.assertLess(track["summary"]["root_bytes"], 6 * 1024)
        self.assertLess(track["summary"]["root_worker_bytes"], 12 * 1024)
        self.assertLessEqual(track["summary"]["command_mentions"], 5)
        self.assertEqual(track["summary"]["missing_playbooks"], [])
        self.assertEqual(track["summary"]["forbidden_role_routes"], [])
        self.assertEqual(track["summary"]["incomplete_lines"], [])

    def test_instruction_surface_track_rejects_truncated_instruction_lines(self) -> None:
        package = self.tmp_root / "truncated-skill"
        package.mkdir()
        shutil.copytree(SKILL_ROOT / "capabilities", package / "capabilities")
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        skill = skill.replace(
            "- Worker: 读取 `capabilities/worker-loop.md`。",
            "-",
        )
        (package / "SKILL.md").write_text(skill, encoding="utf-8")

        track = instruction_surface_track(package)

        self.assertFalse(track["passed"], track)
        self.assertTrue(track["summary"]["incomplete_lines"])
    def test_read_only_startup_uses_only_bounded_discovery(self) -> None:
        track = read_only_startup_track(SKILL_ROOT, sys.executable)

        self.assertTrue(track["passed"], track)
        self.assertEqual(track["summary"]["command_count"], 1)
        commands = [" ".join(check["command"]) for check in track["checks"]]
        self.assertTrue(any("--view execution" in command for command in commands), commands)
        self.assertFalse(any("--summary-only" in command for command in commands), commands)

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

    def test_run_command_decodes_utf8_output_independently_of_platform_locale(self) -> None:
        check = _run_command(
            [
                sys.executable,
                "-c",
                'import sys; sys.stdout.buffer.write("跨平台输出".encode("utf-8"))',
            ]
        )

        self.assertTrue(check["passed"], check)
        self.assertEqual(check["stdout"], "跨平台输出")
        self.assertEqual(check["stderr"], "")
    def test_run_command_reports_untruncated_output_byte_counts(self) -> None:
        expected = "x" * 4096 + "跨平台"
        check = _run_command(
            [
                sys.executable,
                "-c",
                f"import sys; sys.stdout.buffer.write({expected!r}.encode('utf-8'))",
            ]
        )

        self.assertTrue(check["passed"], check)
        self.assertLess(len(check["stdout"]), len(expected))
        self.assertEqual(check["stdout_bytes"], len(expected.encode("utf-8")))
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
        self.assertTrue(by_name["workflow_contract"]["passed"], by_name["workflow_contract"])
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

    @unittest.skipIf(
        EXTERNAL_RELEASE_CHECK,
        "full release check runs as the next verification-profile stage",
    )
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

    def test_subagent_forward_check_skip_mutating_runs_read_only_tracks(self) -> None:
        payload = subagent_forward_check_payload(
            SKILL_ROOT,
            python=sys.executable,
            temp_parent=self.tmp_root,
            skip_mutating=True,
            keep_temp=False,
        )
        by_name = {track["name"]: track for track in payload["tracks"]}

        self.assertTrue(payload["ok"], payload)
        self.assertFalse(payload["original_package_changed"])
        self.assertTrue(
            by_name["track_a_known_node_reader"]["passed"],
            by_name["track_a_known_node_reader"],
        )
        self.assertTrue(
            by_name["track_d_portable_install"]["passed"],
            by_name["track_d_portable_install"],
        )
        self.assertTrue(by_name["track_b_assigned_worker"]["skipped"])
        self.assertTrue(by_name["track_c_reviewer"]["skipped"])
        self.assertGreaterEqual(
            by_name["track_a_known_node_reader"]["metrics"]["context_read_count"],
            1,
        )

    def test_subagent_forward_check_mutating_tracks_only_change_copies(self) -> None:
        payload = subagent_forward_check_payload(
            SKILL_ROOT,
            python=sys.executable,
            temp_parent=self.tmp_root,
            skip_mutating=False,
            keep_temp=False,
        )
        by_name = {track["name"]: track for track in payload["tracks"]}

        self.assertTrue(payload["ok"], payload)
        self.assertFalse(payload["original_package_changed"])
        worker = by_name["track_b_assigned_worker"]
        reviewer = by_name["track_c_reviewer"]
        self.assertTrue(worker["passed"], worker)
        self.assertTrue(reviewer["passed"], reviewer)
        self.assertGreater(len(worker["summary"]["copy_changed_files"]), 0)
        self.assertGreater(len(reviewer["summary"]["copy_changed_files"]), 0)
        self.assertEqual(worker["metrics"]["command_count"], 3)
        self.assertEqual(reviewer["metrics"]["command_count"], 2)
        self.assertEqual(worker["metrics"]["validate_count"], 0)
        self.assertEqual(worker["metrics"]["build_count"], 0)
        self.assertTrue(worker["summary"]["workflow_contract"]["ok"])
        self.assertTrue(reviewer["summary"]["workflow_contract"]["ok"])

    def test_agent_usability_check_exercises_vendored_research_repo(self) -> None:
        payload = agent_usability_check_payload(
            SKILL_ROOT,
            python=sys.executable,
            temp_parent=self.tmp_root,
            keep_temp=False,
        )
        by_case = {case["case"]: case for case in payload["cases"]}

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(
            sorted(by_case),
            [
                "agent_a_cold_start_install",
                "agent_b_known_node_context",
                "agent_c_assigned_worker_round_trip",
                "agent_d_reviewer_round_trip",
                "agent_e_coordinator_overview",
                "agent_f_legacy_data_round_trip",
            ],
        )
        for case in by_case.values():
            self.assertTrue(case["passed"], case)
            self.assertEqual(case["unexpected_writes"], [], case)
            self.assertIn("metrics", case)
            self.assertIn("command_count", case["metrics"])

        worker = by_case["agent_c_assigned_worker_round_trip"]
        self.assertTrue(worker["agent_observations"]["packet_ready"])
        self.assertTrue(worker["agent_observations"]["close_internally_verified"])
        self.assertTrue(worker["agent_observations"]["final_evidence_preserved"])
        self.assertEqual(worker["agent_observations"]["agent_command_count"], 3)
        self.assertTrue(worker["workflow_contract"]["ok"])

        reviewer = by_case["agent_d_reviewer_round_trip"]
        self.assertTrue(reviewer["agent_observations"]["producer_truth_unchanged"])
        self.assertEqual(reviewer["agent_observations"]["agent_command_count"], 2)
        self.assertTrue(reviewer["workflow_contract"]["ok"])

        legacy = by_case["agent_f_legacy_data_round_trip"]
        self.assertTrue(legacy["agent_observations"]["unknown_node_fields_preserved"])
        self.assertTrue(legacy["agent_observations"]["artifact_payload_bytes_preserved"])
        self.assertTrue(
            legacy["agent_observations"][
                "artifact_manifest_unknown_fields_preserved"
            ]
        )


if __name__ == "__main__":
    unittest.main()
