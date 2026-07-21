from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import tomllib
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit import cli
from research_cockpit.command_registry import (
    COMMAND_MODULES,
    GROUPED_COMMAND_ALIASES,
    LEGACY_COMMAND_MODULES,
    LEGACY_COMMAND_REPLACEMENTS,
    ROLE_COMMAND_MODULES,
)
from research_cockpit.commands.list_agent_commands import agent_command_manifest


class CliCutoverContractTests(unittest.TestCase):
    def test_public_surface_contains_only_canonical_routes(self) -> None:
        expected_groups = {
            "work": {"claim", "open", "release", "renew", "start", "record", "close"},
            "review": {"open", "report"},
            "coord": {"overview", "assign", "review", "decide", "handoff"},
            "maintenance": {"audit", "repair", "migrate", "compact"},
        }
        self.assertEqual(
            {group: set(actions) for group, actions in ROLE_COMMAND_MODULES.items()},
            expected_groups,
        )
        self.assertEqual(GROUPED_COMMAND_ALIASES, {})
        self.assertEqual(
            set(cli.COMMAND_CHOICES),
            {*COMMAND_MODULES, "init", "ui", *expected_groups},
        )

        manifest_names = {row["name"] for row in agent_command_manifest()}
        expected_manifest = {
            *COMMAND_MODULES,
            "init",
            "ui",
            *(
                f"{group} {action}"
                for group, actions in expected_groups.items()
                for action in actions
            ),
        }
        self.assertEqual(manifest_names, expected_manifest)
        self.assertTrue(
            all(row["route_kind"] != "legacy" for row in agent_command_manifest())
        )

    def test_every_removed_route_is_invalid_and_has_a_live_replacement(self) -> None:
        removed = set(LEGACY_COMMAND_MODULES) - set(COMMAND_MODULES)
        self.assertEqual(set(LEGACY_COMMAND_REPLACEMENTS), removed)
        manifest_names = {row["name"] for row in agent_command_manifest()}
        self.assertTrue(removed.isdisjoint(cli.COMMAND_CHOICES))
        self.assertTrue(removed.isdisjoint(manifest_names))
        self.assertTrue(set(LEGACY_COMMAND_REPLACEMENTS.values()) <= manifest_names)

        for command in sorted(removed):
            with self.subTest(command=command):
                stderr = StringIO()
                with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                    cli.main([command])
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("invalid choice", stderr.getvalue())

    def test_every_canonical_role_route_loads_and_exposes_help(self) -> None:
        for group, actions in ROLE_COMMAND_MODULES.items():
            for action in actions:
                with self.subTest(route=f"{group} {action}"):
                    stdout = StringIO()
                    with redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
                        cli.main([group, action, "--help"])
                    self.assertEqual(raised.exception.code, 0)
                    self.assertIn(
                        f"research-cockpit {group} {action}", stdout.getvalue()
                    )

    def test_removed_ui_and_acceptance_routes_have_semantic_replacements(self) -> None:
        self.assertEqual(LEGACY_COMMAND_REPLACEMENTS["check-decision-acceptance"], "context")
        self.assertEqual(LEGACY_COMMAND_REPLACEMENTS["set-focus"], "ui")
        self.assertEqual(LEGACY_COMMAND_REPLACEMENTS["apply-suggestion"], "ui")
        self.assertEqual(
            LEGACY_COMMAND_REPLACEMENTS["update-suggestion-state"], "ui"
        )

    def test_cutover_package_version_is_0_3_0(self) -> None:
        with (ROOT_DIR / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]
        self.assertEqual(project["version"], "0.3.0")


if __name__ == "__main__":
    unittest.main()
