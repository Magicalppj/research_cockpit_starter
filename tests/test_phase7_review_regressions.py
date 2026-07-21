from __future__ import annotations

from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.commands.coord_assign import (
    COORD_ASSIGN_SCHEMA,
    COORD_ASSIGN_SCHEMAS,
)
from research_cockpit.commands.coord_decide import (
    COORD_DECIDE_SCHEMA,
    COORD_DECIDE_SCHEMAS,
)
from research_cockpit.commands.coord_review import COORD_REVIEW_SCHEMAS
from research_cockpit.coordinator_decisions import (
    apply_coord_decision,
    parse_coord_decide_input,
)
from research_cockpit.coordinator_operations import parse_coord_assign_input
from research_cockpit.coordinator_reviews import apply_coord_review
from research_cockpit.storage import find_node_file, load_yaml


class Phase7InputContractTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"phase7_contract_{uuid.uuid4().hex}"
        shutil.copytree(ROOT_DIR / "examples" / "demo_research_cockpit", self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_quoted_clear_is_rejected_without_writing(self) -> None:
        node_path = find_node_file(self.root, "problem_demo_quality_gap")
        before = load_yaml(node_path)
        plan = {
            "schema_version": "coord_decide_v1",
            "operation_id": "op_coord_decide_invalid_clear",
            "action": "set_baseline",
            "parameters": {
                "node_id": "problem_demo_quality_gap",
                "clear": "false",
            },
        }

        with self.assertRaisesRegex(ValueError, "clear must be boolean"):
            apply_coord_decision(self.root, plan)

        self.assertEqual(load_yaml(node_path), before)

    def test_optional_parameter_types_are_validated(self) -> None:
        invalid_cases = (
            (
                "accept",
                {"decision_id": "decision_demo_prompt_refinement", "force_accept": "false"},
                "force_accept must be boolean",
            ),
            (
                "promote",
                {
                    "decision_id": "decision_new",
                    "option_id": "option_demo_prompt_refinement",
                    "title": "New decision",
                    "summary": "Summary",
                    "supporting_experiments": "experiment_demo_prompt_v1",
                },
                "supporting_experiments must be a list of strings",
            ),
            (
                "refresh_evidence",
                {"decision_id": "decision_demo_prompt_refinement", "locale": 7},
                "locale must be a string or null",
            ),
            (
                "update_checklist",
                {
                    "decision_id": "decision_demo_prompt_refinement",
                    "evidence_summary": ["invalid"],
                },
                "evidence_summary must be a string or null",
            ),
            (
                "set_baseline",
                {"node_id": "problem_demo_quality_gap", "artifacts": {"id": "x"}},
                "artifacts must be a list of strings",
            ),
        )

        for index, (action, parameters, message) in enumerate(invalid_cases):
            with self.subTest(action=action):
                with self.assertRaisesRegex(ValueError, message):
                    parse_coord_decide_input(
                        {
                            "schema_version": "coord_decide_v1",
                            "operation_id": f"op_invalid_type_{index}",
                            "action": action,
                            "parameters": parameters,
                        }
                    )

    def test_coord_assign_print_schema_example_is_parser_valid(self) -> None:
        self.assertEqual(COORD_ASSIGN_SCHEMA, COORD_ASSIGN_SCHEMAS["graph_plan"])
        for action, schema in COORD_ASSIGN_SCHEMAS.items():
            with self.subTest(action=action):
                self.assertEqual(parse_coord_assign_input(schema), schema)

    def test_coord_decide_print_schema_example_is_parser_valid(self) -> None:
        self.assertEqual(COORD_DECIDE_SCHEMA, COORD_DECIDE_SCHEMAS["set_baseline"])
        for action, schema in COORD_DECIDE_SCHEMAS.items():
            with self.subTest(action=action):
                self.assertEqual(parse_coord_decide_input(schema), schema)

    def test_coord_review_print_schema_examples_are_dispatcher_valid(self) -> None:
        assignment = COORD_REVIEW_SCHEMAS["assignment_result"]
        with patch(
            "research_cockpit.coordinator_reviews.apply_review_result",
            return_value={"ok": True},
        ) as apply_assignment:
            apply_coord_review(
                self.root,
                plan=assignment,
                producer_assignment_id="assignment_example",
            )
        apply_assignment.assert_called_once()

        promotion = COORD_REVIEW_SCHEMAS["promote_artifact"]
        with patch(
            "research_cockpit.coordinator_reviews._promote_artifact",
            return_value={"ok": True},
        ) as apply_promotion:
            apply_coord_review(self.root, plan=promotion)
        apply_promotion.assert_called_once_with(self.root, promotion)


if __name__ == "__main__":
    unittest.main()
