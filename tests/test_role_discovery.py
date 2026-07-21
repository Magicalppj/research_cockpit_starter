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
INTENTS = {"open", "claim", "renew", "release", "start", "record", "close", "review", "decide", "assign", "handoff", "discover", "maintain", "validate", "build", "smoke", "search", "context"}
IDEMPOTENCY = {"unsupported", "optional", "required"}
VERIFICATION = {"internal", "changed-scope", "milestone", "conditional"}


class RoleDiscoveryTests(unittest.TestCase):
    def test_full_manifest_exposes_role_contract_for_every_public_route(self) -> None:
        manifest = agent_command_manifest()
        by_name = {str(row["name"]): row for row in manifest}

        self.assertEqual(len(manifest), 26)
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
        self.assertEqual(by_name["work close"]["input_schema_version"], "work_close_v1")
        self.assertEqual(by_name["work close"]["surface"], "core")
        self.assertEqual(by_name["review open"]["audiences"], ["reviewer", "coordinator"])
        self.assertEqual(by_name["review open"]["scope_policy"], "read_only")
        self.assertEqual(by_name["review open"]["work_packet_kinds"], ["review"])
        self.assertEqual(by_name["review report"]["input_schema_version"], "review_report_v1")
        self.assertEqual(by_name["review report"]["idempotency"], "required")
        self.assertEqual(by_name["coord review"]["audiences"], ["coordinator"])
        self.assertEqual(by_name["coord review"]["scope_policy"], "coordinator")
        self.assertEqual(by_name["coord overview"]["audiences"], ["coordinator"])
        self.assertEqual(by_name["coord overview"]["surface"], "core")
        self.assertEqual(by_name["coord overview"]["input_schema_version"], "coord_overview_v1")
        self.assertEqual(by_name["coord overview"]["output_schema_version"], "coordination_snapshot_v1")
        self.assertIn("--limit", by_name["coord overview"]["supported_flags"])
        self.assertEqual(by_name["coord handoff"]["audiences"], ["coordinator"])
        self.assertEqual(by_name["coord handoff"]["surface"], "core")
        self.assertEqual(by_name["coord handoff"]["intent"], "handoff")
        self.assertEqual(by_name["coord handoff"]["verification_policy"], "milestone")
        self.assertEqual(by_name["coord handoff"]["input_schema_version"], "coord_handoff_v1")
        self.assertEqual(by_name["coord handoff"]["output_schema_version"], "milestone_handoff_v1")
        self.assertIn("--progress", by_name["coord handoff"]["supported_flags"])
        self.assertEqual(by_name["work record"]["surface"], "core")
        self.assertEqual(by_name["coord assign"]["intent"], "assign")
        self.assertEqual(by_name["maintenance compact"]["surface"], "core")
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
        self.assertIn("work close", {str(row["name"]) for row in manifest})
        self.assertNotIn("complete-run", {str(row["name"]) for row in manifest})
        self.assertNotIn("ingest-artifact", {str(row["name"]) for row in manifest})
        self.assertTrue(
            all("worker" in row["audiences"] and row["surface"] == "core" for row in manifest)
        )
        self.assertNotIn("bootstrap", {str(row["name"]) for row in manifest})
        self.assertNotIn("maintenance-audit", {str(row["name"]) for row in manifest})
        self.assertNotIn("context", {str(row["name"]) for row in manifest})
        self.assertNotIn("option-workstream-context", {str(row["name"]) for row in manifest})

    def test_coordinator_core_uses_one_handoff_facade_for_milestone_gates(self) -> None:
        manifest = agent_command_manifest(
            role="coordinator",
            compact=True,
            summary_only=True,
        )
        names = {str(row["name"]) for row in manifest}

        self.assertIn("coord overview", names)
        self.assertIn("coord handoff", names)
        self.assertNotIn("validate", names)
        self.assertNotIn("build", names)
        self.assertNotIn("smoke", names)
        self.assertTrue(
            all("coordinator" in row["audiences"] and row["surface"] == "core" for row in manifest)
        )


    def test_name_lookup_reveals_only_exact_canonical_routes(self) -> None:
        context_rows = agent_command_manifest(
            role="worker", name="context", compact=True
        )
        self.assertEqual([row["name"] for row in context_rows], ["context"])
        self.assertEqual(context_rows[0]["surface"], "advanced")

        maintenance_rows = agent_command_manifest(
            role="maintainer", name="maintenance compact", compact=True
        )
        self.assertEqual([row["name"] for row in maintenance_rows], ["maintenance compact"])

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
