from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR / "dev" / "scripts"))

from multi_agent_baseline import (
    WORKFLOW_BASELINE_EVIDENCE,
    WORKFLOW_BASELINES,
    benchmark_concurrency,
    concurrency_round_passed,
    legacy_command_inventory,
    parse_progress_events,
    summarize_concurrency_samples,
)
from run_skill_release_check import _run_command
from research_cockpit.cli_progress import PROGRESS_PREFIX
from research_cockpit.commands.list_agent_commands import agent_command_manifest
from workflow_metrics import workflow_metrics


class MultiAgentBaselineTests(unittest.TestCase):
    def test_legacy_inventory_covers_the_current_manifest_once(self) -> None:
        manifest = agent_command_manifest()
        inventory = legacy_command_inventory(manifest)

        self.assertEqual(len(manifest), 71)
        self.assertEqual(len(inventory), 70)
        self.assertEqual(
            {row["name"] for row in inventory},
            {row["name"] for row in manifest} - {"work open"},
        )
        for row in inventory:
            with self.subTest(command=row["name"]):
                self.assertTrue(row["audiences"])
                self.assertIn(row["surface"], {"core", "advanced", "maintenance"})
                self.assertTrue(row["intent"])
                self.assertTrue(row["canonical_replacement"])
                self.assertIn(
                    row["removal_disposition"],
                    {"retain_unique", "move_to_role_group", "remove_after_facade"},
                )

        by_name = {row["name"]: row for row in inventory}
        self.assertIn("reviewer", by_name["option-workstream-context"]["audiences"])
        self.assertIn("reviewer", by_name["check-decision-acceptance"]["audiences"])

    def test_workflow_baselines_cover_all_required_phase_zero_traces(self) -> None:
        self.assertEqual(
            set(WORKFLOW_BASELINES),
            {
                "assigned_worker_no_payload",
                "assigned_worker_final_payload",
                "assigned_worker_incremental_evidence",
                "unclaimed_worker",
                "reviewer",
                "milestone_handoff",
            },
        )
        self.assertEqual(
            WORKFLOW_BASELINES["assigned_worker_no_payload"]["cli_invocations"],
            3,
        )
        self.assertEqual(
            WORKFLOW_BASELINES["assigned_worker_final_payload"]["cli_invocations"],
            4,
        )
        self.assertGreater(
            WORKFLOW_BASELINES["assigned_worker_incremental_evidence"][
                "cli_invocations"
            ],
            WORKFLOW_BASELINES["assigned_worker_final_payload"]["cli_invocations"],
        )
        self.assertIn("known_gap", WORKFLOW_BASELINES["reviewer"])
        self.assertEqual(
            WORKFLOW_BASELINE_EVIDENCE["measurement_status"],
            "declared_command_shapes",
        )
        self.assertEqual(
            set(WORKFLOW_BASELINE_EVIDENCE["actual_trace_sources"]),
            {"run_agent_usability_check", "run_skill_release_check"},
        )
        for name, trace in WORKFLOW_BASELINES.items():
            with self.subTest(workflow=name):
                self.assertEqual(trace["cli_invocations"], len(trace["commands"]))
                self.assertIn("state_load_lower_bound", trace)
                self.assertIn("nested_subprocesses", trace)
                self.assertIn("measurement_fields", trace)


    def test_progress_parser_and_concurrency_summary_keep_stage_boundaries(self) -> None:
        stderr = "\n".join(
            [
                f'{PROGRESS_PREFIX}{{"event":"phase_end","phase":"targeted_preflight","duration_ms":4.0}}',
                f'{PROGRESS_PREFIX}{{"event":"phase_end","phase":"lock_wait","duration_ms":2.0}}',
                f'{PROGRESS_PREFIX}{{"event":"phase_end","phase":"commit","duration_ms":3.0}}',
                f'{PROGRESS_PREFIX}{{"event":"phase_end","phase":"lock_hold","duration_ms":5.0}}',
                f'{PROGRESS_PREFIX}{{"event":"phase_end","phase":"index_update","duration_ms":7.0}}',
                "unrelated stderr",
            ]
        )
        phases = parse_progress_events(stderr)
        self.assertEqual(phases["prepare_ms"], 4.0)
        self.assertEqual(phases["lock_wait_ms"], 2.0)
        self.assertEqual(phases["commit_ms"], 3.0)
        self.assertEqual(phases["lock_hold_ms"], 5.0)
        self.assertEqual(phases["index_patch_ms"], 7.0)
        self.assertIsNone(phases["transaction_ms"])

        summary = summarize_concurrency_samples(
            [
                {
                    **phases,
                    "wall_time_ms": 20.0,
                    "returncode": 0,
                    "success": True,
                    "conflict": False,
                    "valid_outcome": True,
                },
                {
                    **phases,
                    "commit_ms": None,
                    "index_patch_ms": None,
                    "wall_time_ms": 30.0,
                    "returncode": 2,
                    "success": False,
                    "conflict": True,
                    "valid_outcome": True,
                },
            ]
        )
        json.dumps(summary)
        self.assertEqual(summary["sample_count"], 2)
        self.assertEqual(summary["success_count"], 1)
        self.assertEqual(summary["conflict_count"], 1)
        self.assertEqual(summary["valid_outcome_count"], 2)
        self.assertEqual(summary["invalid_outcome_count"], 0)
        self.assertEqual(summary["conflict_rate"], 0.5)
        self.assertEqual(summary["stages"]["commit_ms"]["median"], 3.0)
        self.assertEqual(summary["stages"]["commit_ms"]["executed_count"], 1)

        percentile_samples = [
            {
                "wall_time_ms": float(index),
                "returncode": 0,
                "success": True,
                "conflict": False,
                "valid_outcome": True,
            }
            for index in range(1, 17)
        ]
        percentile_summary = summarize_concurrency_samples(percentile_samples)
        self.assertEqual(percentile_summary["stages"]["wall_time_ms"]["p95"], 16.0)

    def test_progress_parser_keeps_missing_duration_unknown(self) -> None:
        phases = parse_progress_events(
            f'{PROGRESS_PREFIX}{{"event":"phase_end","phase":"commit"}}'
        )

        self.assertIsNone(phases["commit_ms"])

    def test_concurrency_round_policy_rejects_disjoint_conflicts(self) -> None:
        one_success_one_conflict = {
            "sample_count": 2,
            "success_count": 1,
            "failure_count": 1,
            "conflict_count": 1,
            "valid_outcome_count": 2,
        }
        two_successes = {
            "sample_count": 2,
            "success_count": 2,
            "failure_count": 0,
            "conflict_count": 0,
            "valid_outcome_count": 2,
        }
        all_conflicts = {
            "sample_count": 2,
            "success_count": 0,
            "failure_count": 2,
            "conflict_count": 2,
            "valid_outcome_count": 2,
        }

        self.assertFalse(
            concurrency_round_passed("disjoint", one_success_one_conflict)
        )
        self.assertTrue(
            concurrency_round_passed("same_target", one_success_one_conflict)
        )
        self.assertFalse(concurrency_round_passed("same_target", two_successes))
        self.assertFalse(concurrency_round_passed("same_target", all_conflicts))

    def test_concurrency_benchmark_only_mutates_a_temporary_copy(self) -> None:
        source = ROOT_DIR / "examples" / "demo_research_cockpit"
        temp_parent = ROOT_DIR / ".test_tmp"
        temp_parent.mkdir(exist_ok=True)

        def fingerprint() -> dict[str, bytes]:
            return {
                path.relative_to(source).as_posix(): path.read_bytes()
                for path in source.rglob("*")
                if path.is_file()
            }

        before = fingerprint()
        payload = benchmark_concurrency(
            source,
            agent_counts=(1,),
            scenarios=("disjoint",),
            temp_parent=temp_parent,
        )
        after = fingerprint()

        self.assertTrue(payload["ok"], payload)
        self.assertFalse(payload["source_root_mutated"])
        self.assertEqual(before, after)
        self.assertEqual(
            payload["synchronization"],
            "cross_process_post_preflight_file_barrier",
        )
        self.assertEqual(payload["rounds"][0]["summary"]["sample_count"], 1)
        self.assertEqual(payload["rounds"][0]["summary"]["success_count"], 1)

    def test_same_target_benchmark_requires_one_winner_and_structured_conflict(self) -> None:
        source = ROOT_DIR / "examples" / "demo_research_cockpit"
        temp_parent = ROOT_DIR / ".test_tmp"
        temp_parent.mkdir(exist_ok=True)

        payload = benchmark_concurrency(
            source,
            agent_counts=(4,),
            scenarios=("same_target",),
            temp_parent=temp_parent,
        )

        self.assertTrue(payload["ok"], payload)
        summary = payload["rounds"][0]["summary"]
        self.assertEqual(summary["success_count"], 1)
        self.assertEqual(summary["conflict_count"], 3)
        self.assertEqual(summary["valid_outcome_count"], 4)
        self.assertEqual(summary["invalid_outcome_count"], 0)

    def test_concurrency_benchmark_rejects_a_temp_directory_inside_source(self) -> None:
        source = ROOT_DIR / "examples" / "demo_research_cockpit"

        with self.assertRaisesRegex(ValueError, "outside the source root"):
            benchmark_concurrency(
                source,
                agent_counts=(1,),
                scenarios=("disjoint",),
                temp_parent=source / ".test_tmp",
            )

    def test_workflow_metrics_reports_round_trip_and_model_visible_costs(self) -> None:
        checks = [
            {
                "command": ["research-cockpit", "context"],
                "passed": True,
                "stdout_bytes": 1200,
                "stderr_bytes": 100,
                "duration_ms": 100.0,
                "state_load_count": 1,
                "nested_subprocess_count": 0,
            },
            {
                "command": ["research-cockpit", "create-run"],
                "passed": True,
                "stdout_bytes": 800,
                "stderr_bytes": 50,
                "duration_ms": 200.0,
                "state_load_count": 2,
                "nested_subprocess_count": 0,
                "json": {
                    "verified": True,
                    "additional_verification_required": False,
                    "verification_stage": "internal_verify",
                },
            },
            {
                "command": ["research-cockpit", "validate"],
                "passed": True,
                "stdout_bytes": 400,
                "stderr_bytes": 10,
                "duration_ms": 300.0,
                "state_load_count": 1,
                "nested_subprocess_count": 1,
            },
        ]

        metrics = workflow_metrics(checks, documentation_bytes=2048)

        self.assertEqual(metrics["model_visible_output_bytes"], 2560)
        self.assertEqual(metrics["documentation_input_bytes"], 2048)
        self.assertEqual(metrics["estimated_visible_tokens"], 1152)
        self.assertEqual(metrics["control_plane_wall_time_ms"], 600.0)
        self.assertEqual(metrics["state_load_count"], 4)
        self.assertEqual(metrics["nested_subprocess_count"], 1)
        self.assertEqual(metrics["extra_verification_after_mutation_count"], 1)
        self.assertEqual(metrics["token_estimation"]["method"], "utf8_bytes_div_4")
        self.assertIsNone(metrics["token_estimation"]["tokenizer"])

    def test_workflow_metrics_preserves_unknown_measurements(self) -> None:
        metrics = workflow_metrics(
            [
                {
                    "command": ["research-cockpit", "agent-session-context"],
                    "passed": True,
                    "stdout_bytes": 100,
                    "stderr_bytes": 0,
                },
                {
                    "command": ["research-cockpit", "create-run"],
                    "passed": True,
                    "stdout_bytes": 100,
                    "stderr_bytes": 0,
                },
                {
                    "command": ["research-cockpit", "validate"],
                    "passed": True,
                    "stdout_bytes": 100,
                    "stderr_bytes": 0,
                },
                {
                    "command": ["research-cockpit", "unmeasured-command"],
                    "passed": True,
                },
            ]
        )

        self.assertEqual(metrics["context_read_count"], 1)
        self.assertIsNone(metrics["model_visible_output_bytes"])
        self.assertIsNone(metrics["estimated_output_tokens"])
        self.assertFalse(metrics["measurements"]["model_visible_output_bytes"])
        self.assertIsNone(metrics["documentation_input_bytes"])
        self.assertIsNone(metrics["estimated_visible_tokens"])
        self.assertIsNone(metrics["control_plane_wall_time_ms"])
        self.assertIsNone(metrics["state_load_count"])
        self.assertIsNone(metrics["nested_subprocess_count"])
        self.assertEqual(metrics["extra_verification_after_mutation_count"], 0)
        self.assertFalse(metrics["measurements"]["state_load_count"])
        self.assertFalse(metrics["measurements"]["nested_subprocess_count"])

    def test_workflow_metrics_treats_explicit_null_numeric_fields_as_unknown(self) -> None:
        metrics = workflow_metrics(
            [
                {
                    "command": ["research-cockpit", "context"],
                    "passed": True,
                    "stdout_bytes": 100,
                    "stderr_bytes": 0,
                    "duration_ms": None,
                    "state_load_count": None,
                    "nested_subprocess_count": None,
                }
            ]
        )

        self.assertIsNone(metrics["control_plane_wall_time_ms"])
        self.assertIsNone(metrics["state_load_count"])
        self.assertIsNone(metrics["nested_subprocess_count"])
        self.assertFalse(metrics["measurements"]["control_plane_wall_time_ms"])
        self.assertFalse(metrics["measurements"]["state_load_count"])
        self.assertFalse(metrics["measurements"]["nested_subprocess_count"])

    def test_release_check_command_records_wall_time_for_workflow_traces(self) -> None:
        result = _run_command(
            [sys.executable, "-c", "print('trace')"],
            cwd=ROOT_DIR,
        )

        self.assertTrue(result["passed"], result)
        self.assertGreaterEqual(result["duration_ms"], 0.0)
        self.assertGreater(result["stdout_bytes"], 0)

if __name__ == "__main__":
    unittest.main()
