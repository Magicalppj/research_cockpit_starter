from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.commands.list_agent_commands import agent_command_manifest


ROLES = {"worker", "reviewer", "coordinator", "maintainer"}
SURFACES = {"core", "advanced", "maintenance"}
INTENTS = {"open", "claim", "renew", "release", "start", "record", "close", "review", "decide", "assign", "handoff", "discover", "maintain"}
IDEMPOTENCY = {"unsupported", "optional", "required"}
VERIFICATION = {"internal", "changed-scope", "milestone", "conditional"}


class RoleDiscoveryTests(unittest.TestCase):
    def test_full_manifest_exposes_role_contract_for_every_public_route(self) -> None:
        manifest = agent_command_manifest()
        by_name = {str(row["name"]): row for row in manifest}

        self.assertEqual(len(manifest), 75)
        self.assertIn("work open", by_name)
        for name, row in by_name.items():
            with self.subTest(command=name):
                self.assertTrue(set(row["audiences"]).issubset(ROLES))
                self.assertTrue(row["audiences"])
                self.assertTrue(set(row["core_roles"]).issubset(set(row["audiences"])))
                self.assertIn(row["surface"], SURFACES)
                self.assertIn(row["intent"], INTENTS)
                self.assertIn(row["idempotency"], IDEMPOTENCY)
                self.assertIn(row["verification_policy"], VERIFICATION)
                self.assertIn(
                    row["scope_policy"],
                    {"read_only", "assignment", "coordinator", "root"},
                )
                self.assertTrue(row["input_schema_version"])
                self.assertTrue(row["output_schema_version"])

        work_open = by_name["work open"]
        self.assertEqual(work_open["audiences"], ["worker", "reviewer", "coordinator"])
        self.assertEqual(work_open["surface"], "core")
        self.assertEqual(work_open["intent"], "open")
        self.assertEqual(work_open["scope_policy"], "assignment")
        self.assertEqual(work_open["output_schema_version"], "work_packet_v1")
        self.assertEqual(work_open["command"], "research-cockpit work open --assignment <assignment_id>")
        self.assertEqual(work_open["workflow_tags"], ["read", "evidence"])
        self.assertEqual(by_name["work claim"]["idempotency"], "required")
        self.assertIn("--return-packet", by_name["work claim"]["required_flags"])
        self.assertEqual(by_name["work start"]["output_schema_version"], "work_operation_v1")
        self.assertEqual(by_name["work start"]["input_schema_version"], "work_start_v1")
        self.assertEqual(by_name["work start"]["required_flags"], ["--assignment", "<assignment_id>", "--file", "<path>"])
        self.assertEqual(by_name["work start"]["surface"], "core")
        self.assertEqual(by_name["create-run"]["surface"], "advanced")
        self.assertEqual(by_name["add-node"]["intent"], "assign")
        self.assertEqual(by_name["build"]["audiences"], ["coordinator", "maintainer"])

    def test_default_worker_discovery_is_core_bounded_and_compact(self) -> None:
        manifest = agent_command_manifest(
            role="worker",
            compact=True,
            summary_only=True,
        )
        encoded = json.dumps(
            {"commands": manifest},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        self.assertLessEqual(len(manifest), 12)
        self.assertLess(len(encoded), 8 * 1024)
        self.assertIn("work open", {str(row["name"]) for row in manifest})
        self.assertTrue(
            all("worker" in row["audiences"] and row["surface"] == "core" for row in manifest)
        )
        self.assertNotIn("bootstrap", {str(row["name"]) for row in manifest})
        self.assertNotIn("maintenance-audit", {str(row["name"]) for row in manifest})
        self.assertNotIn("context", {str(row["name"]) for row in manifest})
        self.assertNotIn("option-workstream-context", {str(row["name"]) for row in manifest})

    def test_name_lookup_can_reveal_an_advanced_role_command(self) -> None:
        rows = agent_command_manifest(
            role="worker",
            name="update-run",
            compact=True,
        )

        self.assertEqual([row["name"] for row in rows], ["update-run"])
        self.assertEqual(rows[0]["surface"], "advanced")

        context_rows = agent_command_manifest(role="worker", name="context", compact=True)
        self.assertEqual(context_rows[0]["surface"], "advanced")

    def test_commands_cli_role_filter_matches_manifest_budget(self) -> None:
        out = subprocess.run(
            [
                sys.executable,
                "-m",
                "research_cockpit.cli",
                "commands",
                "--role",
                "worker",
                "--json",
                "--compact",
                "--summary-only",
            ],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        self.assertLess(len(out.stdout.encode("utf-8")), 8 * 1024)
        payload = json.loads(out.stdout)
        self.assertEqual(
            payload["commands"],
            agent_command_manifest(role="worker", compact=True, summary_only=True),
        )


if __name__ == "__main__":
    unittest.main()
