from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))
existing_pythonpath = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = str(SRC_DIR) if not existing_pythonpath else str(SRC_DIR) + os.pathsep + existing_pythonpath

from research_cockpit.commands.list_agent_commands import agent_command_manifest
from research_cockpit.commands.node_context import node_context_payload
from research_cockpit.model import save_yaml


def write_node(root: Path, data: dict) -> None:
    save_yaml(root / "graph" / "nodes" / f"{data['id']}.yaml", data)


def cli_command(command: str, *args: str) -> list[str]:
    return [sys.executable, "-m", "research_cockpit.cli", command, *args]


class NodeOnboardingTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_parent = ROOT_DIR / ".test_tmp"
        temp_parent.mkdir(exist_ok=True)
        self.tmp_root = temp_parent / f"onboarding_{uuid.uuid4().hex}"
        self.root = self.tmp_root / "research_cockpit"
        self.root.mkdir(parents=True)
        write_node(
            self.root,
            {
                "id": "stage_text",
                "type": "stage",
                "title": "Text",
                "status": "active",
                "children": ["problem_text"],
            },
        )
        write_node(
            self.root,
            {
                "id": "problem_text",
                "type": "problem",
                "title": "Weak text",
                "status": "active",
                "parent": "stage_text",
                "children": ["option_t5"],
                "question": "How can the agent improve repeated-answer consistency?",
                "next_actions": ["Run the T5 ablation."],
                "blockers": ["Need one structured finding before accepting the decision."],
            },
        )
        write_node(
            self.root,
            {
                "id": "option_t5",
                "type": "option",
                "title": "T5",
                "status": "active",
                "parent": "problem_text",
                "children": ["exp_t5"],
                "summary": "Prompt refinement branch.",
            },
        )
        write_node(
            self.root,
            {
                "id": "exp_t5",
                "type": "experiment",
                "title": "T5 ablation",
                "status": "planned",
                "parent": "option_t5",
            },
        )
        save_yaml(
            self.root / "current_state.yaml",
            {
                "current_stage": "stage_text",
                "current_problem": "problem_text",
                "current_option": "option_t5",
                "current_focus_path": ["stage_text", "problem_text", "option_t5"],
                "next_actions": ["Record one finding."],
            },
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_node_context_compact_payload_omits_repeated_context(self) -> None:
        payload = node_context_payload(self.root, node_id="option_t5", compact=True)

        self.assertEqual(payload["schema_version"], "node_context_compact_v1")
        self.assertEqual(payload["node"]["id"], "option_t5")
        self.assertEqual(payload["core_problem"]["id"], "problem_text")
        self.assertIn("next_actions", payload)
        self.assertIn("evidence_summary", payload)
        self.assertIn("claim_option", payload["command_drafts"])
        self.assertIn("context_freshness", payload)
        self.assertNotIn("relations", payload)
        self.assertNotIn("recent_interactions", payload)
        self.assertNotIn("subtree_nodes", json.dumps(payload))

    def test_node_context_python_command_style_uses_module_entrypoint(self) -> None:
        payload = node_context_payload(
            self.root,
            node_id="option_t5",
            compact=True,
            command_style="python",
        )

        command = payload["command_drafts"]["claim_option"]
        self.assertNotIn("research-cockpit", command)
        self.assertIn("-m research_cockpit.cli claim-option", command)
        self.assertIn("--root", command)
        self.assertEqual(payload["recommended_next_steps"][0]["command"], command)

    def test_node_context_cli_outputs_compact_json(self) -> None:
        result = subprocess.run(
            [*cli_command("node-context"), "--root", str(self.root), "--id", "option_t5", "--json", "--compact"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema_version"], "node_context_compact_v1")
        self.assertEqual(payload["node"]["id"], "option_t5")
        self.assertNotIn("relations", payload)

    def test_node_context_cli_python_command_style_outputs_module_commands(self) -> None:
        result = subprocess.run(
            [
                *cli_command("node-context"),
                "--root",
                str(self.root),
                "--id",
                "option_t5",
                "--json",
                "--compact",
                "--command-style",
                "python",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        command = payload["command_drafts"]["claim_option"]
        self.assertNotIn("research-cockpit", command)
        self.assertIn("-m research_cockpit.cli claim-option", command)

    def test_commands_manifest_includes_python_module_command(self) -> None:
        manifest = agent_command_manifest()
        by_name = {item["name"]: item for item in manifest}

        self.assertTrue(by_name["build"]["mutating"])
        self.assertTrue(by_name["build"]["writes_generated_files"])
        self.assertTrue(all(item["command"].startswith("research-cockpit ") for item in manifest))
        self.assertTrue(all(item["python_module_command"].startswith("python -m research_cockpit.cli ") for item in manifest))


if __name__ == "__main__":
    unittest.main()
