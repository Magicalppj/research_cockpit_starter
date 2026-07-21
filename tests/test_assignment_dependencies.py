from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.agent_state import AgentRecord, AssignmentRecord
from research_cockpit.model import validate_assignments
from research_cockpit.types import ResearchNode


class AssignmentDependencyValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nodes = {
            "option_x": ResearchNode.from_dict(
                {
                    "id": "option_x",
                    "type": "option",
                    "title": "Option X",
                    "status": "active",
                }
            )
        }
        self.agents = {
            "agent_a": AgentRecord.from_dict({"agent_id": "agent_a", "status": "active"}),
            "agent_b": AgentRecord.from_dict({"agent_id": "agent_b", "status": "active"}),
        }

    def _assignment(self, assignment_id: str, agent_id: str, dependency_id: str) -> AssignmentRecord:
        return AssignmentRecord.from_dict(
            {
                "assignment_id": assignment_id,
                "agent_id": agent_id,
                "status": "completed",
                "root_node": "option_x",
                "current_node": "option_x",
                "allowed_subtree": {
                    "root": "option_x",
                    "policy": "descendants_only",
                },
                "dependencies": [{"assignment_id": dependency_id}],
            }
        )

    def test_validate_assignments_reports_missing_dependency(self) -> None:
        assignment = self._assignment("assign_a", "agent_a", "assign_missing")

        errors = validate_assignments(
            {assignment.assignment_id: assignment},
            self.agents,
            self.nodes,
        )

        self.assertTrue(any("references missing assignment 'assign_missing'" in error for error in errors))

    def test_validate_assignments_reports_dependency_cycle_once(self) -> None:
        assignment_a = self._assignment("assign_a", "agent_a", "assign_b")
        assignment_b = self._assignment("assign_b", "agent_b", "assign_a")

        errors = validate_assignments(
            {
                assignment_a.assignment_id: assignment_a,
                assignment_b.assignment_id: assignment_b,
            },
            self.agents,
            self.nodes,
        )
        cycle_errors = [error for error in errors if "dependency cycle" in error]

        self.assertEqual(len(cycle_errors), 1)
        self.assertIn("assign_a", cycle_errors[0])
        self.assertIn("assign_b", cycle_errors[0])


if __name__ == "__main__":
    unittest.main()
