from __future__ import annotations

from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.assignment_leases import AssignmentLeaseError
from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.commands.start_agent_session import (
    _existing_worktree_matches,
    start_agent_session,
)
from research_cockpit.coordinator_operations import apply_coord_assignment
from research_cockpit.mutation_lock import MutationError
from research_cockpit.operation_receipts import normalized_request_hash
from research_cockpit.storage import find_node_file, load_yaml, save_yaml


class Phase7SessionRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"phase7_session_{uuid.uuid4().hex}"
        shutil.copytree(ROOT_DIR / "examples" / "demo_research_cockpit", self.root)
        build_dashboard(self.root)
        self.worktree = parent / "worktrees" / uuid.uuid4().hex

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.worktree, ignore_errors=True)

    def _plan(self, *, create_worktree: bool = False) -> dict:
        return {
            "schema_version": "coord_assign_v1",
            "operation_id": "op_phase7_session_recovery",
            "action": "session",
            "session": {
                "kind": "experiment",
                "option_id": "option_demo_prompt_refinement",
                "experiment_id": "experiment_demo_prompt_refinement",
                "objective": "Verify recoverable canonical session creation.",
                "branch": "codex/phase7-session-recovery",
                "worktree": str(self.worktree),
                "agent_id": "agent_phase7_recovery",
                "assignment_id": "assign_phase7_recovery",
                "create_worktree": create_worktree,
                "force": True,
            },
        }

    def test_session_preserves_unknown_nested_workstream_fields(self) -> None:
        node_path = find_node_file(self.root, "option_demo_prompt_refinement")
        node = load_yaml(node_path)
        node["agent_workstream"] = {
            "legacy_extension": {
                "downstream_owner": "external",
                "keep": True,
            }
        }
        save_yaml(node_path, node)

        apply_coord_assignment(self.root, self._plan())

        stored = load_yaml(node_path)
        self.assertEqual(
            stored["agent_workstream"]["legacy_extension"],
            {"downstream_owner": "external", "keep": True},
        )

    def test_existing_matching_worktree_recovers_same_canonical_operation(self) -> None:
        def create_worktree(_command: list[str]) -> None:
            self.worktree.mkdir(parents=True)

        failure = MutationError(
            "simulated post-worktree transaction conflict",
            {"recovery_commands": []},
        )
        with (
            patch(
                "research_cockpit.commands.start_agent_session._run_git_worktree_add",
                side_effect=create_worktree,
            ),
            patch(
                "research_cockpit.commands.start_agent_session.finish_mutation",
                side_effect=failure,
            ),
        ):
            with self.assertRaises(MutationError) as captured:
                start_agent_session(
                    self.root,
                    option_id="option_demo_prompt_refinement",
                    objective="Verify recoverable canonical session creation.",
                    branch="codex/phase7-session-recovery",
                    worktree=self.worktree,
                    agent_id="agent_phase7_recovery",
                    assignment_id="assign_phase7_recovery",
                    create_worktree=True,
                    force=True,
                    rebuild_dashboard=False,
                    operation_request={
                        "scope": "coordinator",
                        "operation_id": "op_phase7_session_recovery",
                        "request_hash": normalized_request_hash(self._plan(create_worktree=True)),
                        "operation": "coord assign",
                        "assignment_id": "assign_phase7_recovery",
                    },
                )

        recovery_commands = captured.exception.payload["recovery_commands"]
        self.assertTrue(
            any(command.startswith("research-cockpit coord assign") for command in recovery_commands)
        )
        self.assertFalse(any("start-agent-session" in command for command in recovery_commands))

        with (
            patch(
                "research_cockpit.commands.start_agent_session._existing_worktree_matches",
                return_value=True,
                create=True,
            ),
            patch(
                "research_cockpit.commands.start_agent_session._run_git_worktree_add"
            ) as add_worktree,
        ):
            receipt = apply_coord_assignment(self.root, self._plan(create_worktree=True))

        add_worktree.assert_not_called()
        self.assertTrue(receipt["ok"])
        self.assertTrue(
            (self.root / "assignments" / "assign_phase7_recovery.yaml").is_file()
        )
        pending = self.root / "dashboards" / "pending_operations"
        self.assertEqual(list(pending.glob("*.json")), [])

    def test_worktree_match_returns_false_when_git_cannot_start(self) -> None:
        with patch(
            "research_cockpit.commands.start_agent_session.subprocess.run",
            side_effect=OSError("git unavailable"),
        ):
            self.assertFalse(
                _existing_worktree_matches(
                    self.root.parent,
                    self.worktree,
                    "codex/phase7-session-recovery",
                )
            )

    def test_facade_reports_exact_retry_after_post_worktree_failure(self) -> None:
        def create_worktree(_command: list[str]) -> None:
            self.worktree.mkdir(parents=True)

        with (
            patch(
                "research_cockpit.commands.start_agent_session._run_git_worktree_add",
                side_effect=create_worktree,
            ),
            patch(
                "research_cockpit.commands.start_agent_session.finish_mutation",
                side_effect=MutationError("simulated conflict", {}),
            ),
            self.assertRaises(AssignmentLeaseError) as captured,
        ):
            apply_coord_assignment(self.root, self._plan(create_worktree=True))

        receipt = captured.exception.receipt
        self.assertEqual(receipt["required_action"]["kind"], "manual_recovery")
        self.assertTrue(
            receipt["required_action"]["command"].startswith(
                "research-cockpit coord assign"
            )
        )


if __name__ == "__main__":
    unittest.main()
