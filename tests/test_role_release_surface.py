from __future__ import annotations

from pathlib import Path
import shutil
import sys
import unittest
import uuid


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "dev" / "scripts"))

from run_skill_release_check import instruction_surface_track


class RoleReleaseSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"role_release_{uuid.uuid4().hex}"

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _copy_instruction_surface(self) -> Path:
        self.root.mkdir(parents=True)
        shutil.copy2(ROOT_DIR / "SKILL.md", self.root / "SKILL.md")
        destination = self.root / "capabilities"
        destination.mkdir()
        for name in (
            "worker-loop.md",
            "reviewer-loop.md",
            "coordinator-loop.md",
            "maintainer-loop.md",
        ):
            shutil.copy2(ROOT_DIR / "capabilities" / name, destination / name)
        return self.root

    def test_current_role_router_and_playbooks_pass_independent_budgets(self) -> None:
        track = instruction_surface_track(ROOT_DIR)

        self.assertTrue(track["passed"], track)
        self.assertLess(track["summary"]["root_bytes"], 6 * 1024)
        self.assertLess(track["summary"]["root_worker_bytes"], 12 * 1024)
        self.assertTrue(
            all(value < 12 * 1024 for value in track["summary"]["root_role_bytes"].values())
        )
        self.assertLess(track["summary"]["root_worker_estimated_tokens"], 3 * 1024)
        self.assertEqual(track["summary"]["missing_playbooks"], [])
        self.assertEqual(track["summary"]["forbidden_role_routes"], [])

    def test_worker_coordinator_route_leak_fails_release_surface(self) -> None:
        package = self._copy_instruction_surface()
        worker = package / "capabilities" / "worker-loop.md"
        worker.write_text(
            worker.read_text(encoding="utf-8")
            + "\nresearch-cockpit build --root <data-root>\n",
            encoding="utf-8",
        )

        track = instruction_surface_track(package)

        self.assertFalse(track["passed"], track)
        self.assertTrue(track["summary"]["forbidden_role_routes"])


if __name__ == "__main__":
    unittest.main()
