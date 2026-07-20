from __future__ import annotations

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

    def test_workflow_contract_track_checks_compact_and_closeout_contracts(self) -> None:
        track = workflow_contract_track(SKILL_ROOT, sys.executable)

        self.assertTrue(track["passed"], track)
        self.assertEqual(track["summary"]["context_schema_version"], "execution_context_v1")
        self.assertLessEqual(track["summary"]["context_stdout_bytes"], 4 * 1024)
        self.assertLessEqual(track["summary"]["command_summary_stdout_bytes"], 20 * 1024)
        self.assertEqual(track["summary"]["artifact_default_mode"], "record")
        self.assertEqual(track["summary"]["ingest_verification_mode"], "internal_non_dry_run")
        self.assertEqual(track["summary"]["ingest_worker_verify_commands"], [])
        self.assertTrue(track["summary"]["manifest_rows_present"])
        self.assertEqual(track["summary"]["manifest_help_missing"], [])
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
            "- Assigned worker: read `capabilities/worker-loop.md`.",
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
        self.assertTrue(by_name["track_a_read_only_agent"]["passed"], by_name["track_a_read_only_agent"])
        self.assertTrue(by_name["track_e_portable_skill_agent"]["passed"], by_name["track_e_portable_skill_agent"])
        self.assertEqual(by_name["track_b_prompt_refinement_workstream"]["skipped"], True)
        self.assertIn("metrics", by_name["track_a_read_only_agent"])
        self.assertGreaterEqual(by_name["track_a_read_only_agent"]["metrics"]["context_read_count"], 1)

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
        self.assertTrue(by_name["track_b_prompt_refinement_workstream"]["passed"], by_name["track_b_prompt_refinement_workstream"])
        self.assertTrue(by_name["track_c_retrieval_branch_agent"]["passed"], by_name["track_c_retrieval_branch_agent"])
        self.assertTrue(by_name["track_d_decision_gate_agent"]["passed"], by_name["track_d_decision_gate_agent"])
        self.assertGreater(by_name["track_d_decision_gate_agent"]["summary"]["copy_changed_count"], 0)
        self.assertGreater(by_name["track_d_decision_gate_agent"]["metrics"]["mutating_count"], 0)
        self.assertGreaterEqual(by_name["track_d_decision_gate_agent"]["metrics"]["validate_count"], 1)

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
                "agent_b_read_only_context",
                "agent_c_safe_option_workstream",
                "agent_d_decision_suggestion_dry_run",
                "agent_e_ui_collaboration_docs",
                "agent_f_worker_closeout",
            ],
        )
        for case in by_case.values():
            self.assertTrue(case["passed"], case)
            self.assertEqual(case["unexpected_writes"], [], case)
            self.assertIn("metrics", case)
            self.assertIn("command_count", case["metrics"])
        self.assertTrue(by_case["agent_c_safe_option_workstream"]["agent_observations"]["dry_run_preserved_files"])
        self.assertIn("claim_option", by_case["agent_c_safe_option_workstream"]["agent_observations"]["interaction_kinds"])
        self.assertGreater(by_case["agent_c_safe_option_workstream"]["metrics"]["dry_run_count"], 0)
        self.assertTrue(by_case["agent_f_worker_closeout"]["agent_observations"]["default_ingest_created_record"])
        self.assertTrue(by_case["agent_f_worker_closeout"]["agent_observations"]["structured_closeout_linked_record"])


if __name__ == "__main__":
    unittest.main()
