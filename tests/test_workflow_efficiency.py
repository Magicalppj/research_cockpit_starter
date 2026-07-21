from __future__ import annotations

import ast
from pathlib import Path
import sys
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR / "dev" / "scripts"))

from research_cockpit.public_contracts import WORKFLOW_BUDGETS
from workflow_metrics import evaluate_workflow_contract, workflow_metrics


ROLE_FACADE_MODULES = (
    "work_open.py",
    "work_claim.py",
    "work_renew.py",
    "work_release.py",
    "work_start.py",
    "work_close.py",
    "review_open.py",
    "review_report.py",
    "coord_overview.py",
    "coord_review.py",
    "coord_handoff.py",
)


class WorkflowEfficiencyTests(unittest.TestCase):
    def test_public_budgets_include_output_and_packet_limits(self) -> None:
        self.assertEqual(WORKFLOW_BUDGETS["worker_packet_bytes"], 8 * 1024)
        self.assertEqual(WORKFLOW_BUDGETS["unchanged_packet_bytes"], 512)
        self.assertEqual(WORKFLOW_BUDGETS["worker_stdout_bytes"], 12 * 1024)
        self.assertEqual(WORKFLOW_BUDGETS["worker_estimated_output_tokens"], 4_000)
        self.assertEqual(WORKFLOW_BUDGETS["mutation_receipt_bytes"], 2 * 1024)
        self.assertEqual(
            WORKFLOW_BUDGETS["coordination_snapshot_bytes"],
            32 * 1024,
        )
        self.assertEqual(WORKFLOW_BUDGETS["worker_control_plane_warm_ms"], 10_000)
        self.assertEqual(WORKFLOW_BUDGETS["disjoint_worker_control_plane_warm_ms"], 15_000)

    def test_assigned_worker_fast_path_meets_contract(self) -> None:
        verification = {
            "verification": {
                "status": "internally_verified",
                "additional_verification_required": False,
            }
        }
        checks = [
            {
                "command": ["research-cockpit", "work", "open"],
                "passed": True,
                "stdout_bytes": 7000,
                "stderr_bytes": 0,
                "duration_ms": 20.0,
                "nested_subprocess_count": 0,
            },
            {
                "command": ["research-cockpit", "work", "start"],
                "passed": True,
                "stdout_bytes": 1000,
                "stderr_bytes": 0,
                "duration_ms": 20.0,
                "nested_subprocess_count": 0,
                "json": verification,
            },
            {
                "command": ["research-cockpit", "work", "close"],
                "passed": True,
                "stdout_bytes": 1500,
                "stderr_bytes": 0,
                "duration_ms": 20.0,
                "nested_subprocess_count": 0,
                "json": verification,
            },
        ]

        metrics = workflow_metrics(checks)
        contract = evaluate_workflow_contract(metrics, "assigned_worker")

        self.assertTrue(contract["ok"], contract)
        self.assertEqual(metrics["role_facade_count"], 3)
        self.assertEqual(metrics["packet_open_count"], 1)
        self.assertEqual(metrics["broad_discovery_count"], 0)
        self.assertEqual(metrics["read_after_write_count"], 0)
        self.assertEqual(metrics["extra_verification_after_mutation_count"], 0)

    def test_workflow_contract_rejects_failed_commands(self) -> None:
        checks = [
            {
                "command": ["research-cockpit", "work", "open"],
                "passed": False,
                "stdout_bytes": 0,
                "stderr_bytes": 100,
                "duration_ms": 20.0,
                "nested_subprocess_count": 0,
            }
        ]

        contract = evaluate_workflow_contract(
            workflow_metrics(checks),
            "assigned_worker",
        )

        self.assertFalse(contract["ok"])
        self.assertIn("failed_command_count", contract["violations"])

    def test_unchanged_poll_enforces_single_bounded_packet(self) -> None:
        check = {
            "command": ["research-cockpit", "work", "open", "--since", "rev"],
            "passed": True,
            "stdout_bytes": 400,
            "stderr_bytes": 0,
            "duration_ms": 20.0,
            "nested_subprocess_count": 0,
            "json": {"changed": False, "revision": "rev"},
        }

        passing = evaluate_workflow_contract(
            workflow_metrics([check]),
            "unchanged_poll",
        )
        self.assertTrue(passing["ok"], passing)

        check["stdout_bytes"] = WORKFLOW_BUDGETS["unchanged_packet_bytes"]
        failing = evaluate_workflow_contract(
            workflow_metrics([check]),
            "unchanged_poll",
        )
        self.assertFalse(failing["ok"])
        self.assertIn(
            "unchanged_packet_output_bytes",
            failing["violations"],
        )

    def test_reviewer_and_coordinator_fast_paths_meet_contracts(self) -> None:
        verification = {
            "verification": {
                "status": "internally_verified",
                "additional_verification_required": False,
            }
        }
        reviewer_checks = [
            {
                "command": ["research-cockpit", "review", "open"],
                "passed": True,
                "stdout_bytes": 3000,
                "stderr_bytes": 0,
                "duration_ms": 20.0,
                "nested_subprocess_count": 0,
            },
            {
                "command": ["research-cockpit", "review", "report"],
                "passed": True,
                "stdout_bytes": 1000,
                "stderr_bytes": 0,
                "duration_ms": 20.0,
                "nested_subprocess_count": 0,
                "json": verification,
            },
        ]
        overview_checks = [
            {
                "command": ["research-cockpit", "coord", "overview"],
                "passed": True,
                "stdout_bytes": 5000,
                "stderr_bytes": 0,
                "duration_ms": 20.0,
                "nested_subprocess_count": 0,
            }
        ]
        handoff_checks = [
            {
                "command": ["research-cockpit", "coord", "handoff"],
                "passed": True,
                "stdout_bytes": 1500,
                "stderr_bytes": 0,
                "duration_ms": 20.0,
                "nested_subprocess_count": 0,
                "json": verification,
            }
        ]

        reviewer = evaluate_workflow_contract(
            workflow_metrics(reviewer_checks),
            "reviewer",
        )
        overview = evaluate_workflow_contract(
            workflow_metrics(overview_checks),
            "coordinator_overview",
        )
        handoff = evaluate_workflow_contract(
            workflow_metrics(handoff_checks),
            "milestone_handoff",
        )

        self.assertTrue(reviewer["ok"], reviewer)
        self.assertTrue(overview["ok"], overview)
        self.assertTrue(handoff["ok"], handoff)

    def test_role_facade_modules_do_not_spawn_nested_cli_processes(self) -> None:
        commands_dir = ROOT_DIR / "src" / "research_cockpit" / "commands"
        for filename in ROLE_FACADE_MODULES:
            with self.subTest(module=filename):
                tree = ast.parse(
                    (commands_dir / filename).read_text(encoding="utf-8")
                )
                subprocess_imports = [
                    node
                    for node in ast.walk(tree)
                    if (
                        isinstance(node, ast.Import)
                        and any(alias.name == "subprocess" for alias in node.names)
                    )
                    or (
                        isinstance(node, ast.ImportFrom)
                        and node.module == "subprocess"
                    )
                ]
                self.assertEqual(subprocess_imports, [])


if __name__ == "__main__":
    unittest.main()
