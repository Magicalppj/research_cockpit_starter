from __future__ import annotations

from pathlib import Path
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT_DIR
FORBIDDEN_STRINGS = (
    "D:" + "\\Tools",
    "C:" + "\\Users" + "\\" + "22" + "339",
    "22" + "339",
    "miniconda3" + "\\envs" + "\\aigc",
)


def packaging_files() -> list[Path]:
    paths: list[Path] = []
    for relative in (
        "README.md",
        "SKILL.md",
        "AGENTS.md",
        "pyproject.toml",
        "requirements.txt",
    ):
        path = ROOT_DIR / relative
        if path.exists():
            paths.append(path)

    for directory in (
        ROOT_DIR / "agents",
        ROOT_DIR / "capabilities",
        ROOT_DIR / "docs",
        ROOT_DIR / "examples",
        ROOT_DIR / "schemas",
        ROOT_DIR / "src",
        ROOT_DIR / "templates",
    ):
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if "__pycache__" in path.parts or not path.is_file():
                continue
            if path.suffix.lower() in {".py", ".md", ".json", ".yaml", ".yml", ".toml"}:
                paths.append(path)
    return paths


class PublicPackagingTests(unittest.TestCase):
    def test_skill_package_uses_dedicated_folder_shape(self) -> None:
        self.assertTrue((SKILL_ROOT / "SKILL.md").exists())
        self.assertTrue((SKILL_ROOT / "AGENTS.md").exists())
        self.assertTrue((SKILL_ROOT / "agents" / "openai.yaml").exists())
        self.assertTrue((SKILL_ROOT / "src" / "research_cockpit" / "model.py").exists())
        self.assertTrue((SKILL_ROOT / "src" / "research_cockpit" / "cli.py").exists())
        self.assertTrue((SKILL_ROOT / "src" / "research_cockpit" / "command_registry.py").exists())
        self.assertTrue((SKILL_ROOT / "src" / "research_cockpit" / "commands" / "agent_bootstrap.py").exists())
        self.assertTrue((ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").exists())
        self.assertTrue((SKILL_ROOT / "examples" / "demo_research_cockpit" / "current_state.yaml").exists())
        self.assertTrue((SKILL_ROOT / "templates" / "minimal_research_cockpit" / "current_state.yaml").exists())
        self.assertTrue((SKILL_ROOT / "capabilities" / "graph-state.md").exists())
        self.assertTrue((SKILL_ROOT / "requirements.txt").exists())
        self.assertTrue((SKILL_ROOT / "pyproject.toml").exists())
        self.assertFalse((ROOT_DIR / "skills" / "research-cockpit").exists())
        self.assertFalse((ROOT_DIR / "cockpit").exists())
        self.assertFalse((ROOT_DIR / "ui").exists())
        self.assertFalse((ROOT_DIR / "scripts").exists())
        self.assertFalse((ROOT_DIR / "research_cockpit").exists())

    def test_development_materials_are_separate_from_skill_package(self) -> None:
        self.assertTrue((ROOT_DIR / "dev" / "docs" / "development_status.md").exists())
        self.assertTrue((ROOT_DIR / "dev" / "docs" / "research_cockpit_design.md").exists())
        self.assertTrue((ROOT_DIR / "dev" / "docs" / "requirements_zh.md").exists())
        self.assertTrue((ROOT_DIR / "dev" / "specs" / "research_cockpit_v2_specs").exists())
        self.assertTrue((ROOT_DIR / "dev" / "scripts" / "run_skill_release_check.py").exists())
        self.assertTrue((ROOT_DIR / "tests").exists())
        self.assertFalse((ROOT_DIR / "dev" / "tests").exists())
        self.assertFalse((ROOT_DIR / "docs_development_status.md").exists())
        self.assertFalse((ROOT_DIR / "research_cockpit_v2_specs").exists())

    def test_public_packaging_files_do_not_contain_private_paths(self) -> None:
        offenders: list[str] = []
        for path in packaging_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for forbidden in FORBIDDEN_STRINGS:
                if forbidden in text:
                    offenders.append(f"{path.relative_to(ROOT_DIR)} contains {forbidden}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
