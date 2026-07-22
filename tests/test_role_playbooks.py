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
        self.assertIn("不追加查询", worker)
        self.assertIn("cursor.current_node", worker)
        self.assertIn("experiment_id", worker)

    def test_role_playbooks_do_not_leak_other_roles_default_routes(self) -> None:
        reviewer = (ROOT_DIR / "capabilities" / "reviewer-loop.md").read_text(encoding="utf-8")
        coordinator = (ROOT_DIR / "capabilities" / "coordinator-loop.md").read_text(encoding="utf-8")

        self.assertIn("review", reviewer.lower())
        self.assertNotIn("research-cockpit create-run", reviewer)
        self.assertNotIn("research-cockpit maintenance", reviewer)
        self.assertIn("coordinator", coordinator.lower())
        self.assertIn("coord handoff", coordinator)
    def test_lightweight_tracking_rules_are_explicit_and_add_no_commands(self) -> None:
        root_router = (ROOT_DIR / "SKILL.md").read_text(encoding="utf-8")
        worker = (ROOT_DIR / "capabilities" / "worker-loop.md").read_text(
            encoding="utf-8"
        )
        coordinator = (ROOT_DIR / "capabilities" / "coordinator-loop.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("stage workstream", root_router)
        self.assertIn("同一 research contract", worker)
        self.assertIn("不创建新的 assignment 或 graph node", worker)
        self.assertIn("parallel_ownership", coordinator)
        self.assertIn("documentation-only", coordinator.lower())
        self.assertEqual(worker.count("research-cockpit work open --root"), 2)
        self.assertEqual(worker.count("research-cockpit work start --root"), 1)
        self.assertEqual(worker.count("research-cockpit work close --root"), 1)

    def test_review_assignment_creation_and_graph_contracts_are_discoverable(self) -> None:
        coordinator = (ROOT_DIR / "capabilities" / "coordinator-loop.md").read_text(
            encoding="utf-8"
        )
        reviewer = (ROOT_DIR / "capabilities" / "reviewer-loop.md").read_text(
            encoding="utf-8"
        )
        graph = (ROOT_DIR / "capabilities" / "graph-state.md").read_text(encoding="utf-8")

        self.assertIn("--action review_session", coordinator)
        self.assertIn("producer_assignment_id", reviewer)
        self.assertIn("queued", graph)
        self.assertIn("cancelled", graph)

        self.assertNotIn("research-cockpit commands --json --compact --summary-only", coordinator)

    def test_agent_default_prompt_routes_to_work_packet(self) -> None:
        metadata = (ROOT_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")

        self.assertIn("research-cockpit work open", metadata)
        self.assertIn("capabilities/worker-loop.md", metadata)
        self.assertNotIn("agent-session-context", metadata)


if __name__ == "__main__":
    unittest.main()
