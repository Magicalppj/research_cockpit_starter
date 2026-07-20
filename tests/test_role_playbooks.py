from __future__ import annotations

from pathlib import Path
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]


class RolePlaybookTests(unittest.TestCase):
    def test_root_router_and_worker_playbook_fit_combined_budget(self) -> None:
        root_router = (ROOT_DIR / "SKILL.md").read_text(encoding="utf-8")
        worker = (ROOT_DIR / "capabilities" / "worker-loop.md").read_text(encoding="utf-8")

        self.assertLess(len((root_router + worker).encode("utf-8")), 12 * 1024)
        self.assertIn("capabilities/worker-loop.md", root_router)
        self.assertIn("capabilities/reviewer-loop.md", root_router)
        self.assertIn("capabilities/coordinator-loop.md", root_router)
        self.assertIn("capabilities/maintainer-loop.md", root_router)

    def test_worker_default_path_uses_one_packet_and_no_broad_discovery(self) -> None:
        worker = (ROOT_DIR / "capabilities" / "worker-loop.md").read_text(encoding="utf-8")

        self.assertIn("research-cockpit work open", worker)
        self.assertIn("--since <revision>", worker)
        self.assertNotIn("commands --json --compact --summary-only", worker)
        self.assertNotIn("research-cockpit bootstrap", worker)
        self.assertNotIn("research-cockpit build", worker)
        self.assertNotIn("research-cockpit maintenance", worker)
        self.assertIn("additional_verification_required", worker)
        self.assertIn("do not run another", worker.lower())

    def test_role_playbooks_do_not_leak_other_roles_default_routes(self) -> None:
        reviewer = (ROOT_DIR / "capabilities" / "reviewer-loop.md").read_text(encoding="utf-8")
        coordinator = (ROOT_DIR / "capabilities" / "coordinator-loop.md").read_text(encoding="utf-8")

        self.assertIn("review", reviewer.lower())
        self.assertNotIn("research-cockpit create-run", reviewer)
        self.assertNotIn("research-cockpit maintenance", reviewer)
        self.assertIn("coordinator", coordinator.lower())
        self.assertIn("milestone_handoff", coordinator)
        self.assertNotIn("research-cockpit commands --json --compact --summary-only", coordinator)

    def test_agent_default_prompt_routes_to_work_packet(self) -> None:
        metadata = (ROOT_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")

        self.assertIn("research-cockpit work open", metadata)
        self.assertIn("capabilities/worker-loop.md", metadata)
        self.assertNotIn("agent-session-context", metadata)


if __name__ == "__main__":
    unittest.main()
