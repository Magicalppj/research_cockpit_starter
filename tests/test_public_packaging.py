from __future__ import annotations

from pathlib import Path
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
FORBIDDEN_STRINGS = (
    "D:\\Tools",
    "C:\\Users\\22339",
    "22339",
    "miniconda3\\envs\\aigc",
)


def packaging_files() -> list[Path]:
    paths: list[Path] = []
    for relative in (
        "README.md",
        "AGENTS.md",
        "dev/docs/development_status.md",
    ):
        path = ROOT_DIR / relative
        if path.exists():
            paths.append(path)

    for directory in (
        ROOT_DIR / "cockpit",
        ROOT_DIR / "ui",
        ROOT_DIR / "scripts",
        ROOT_DIR / "skills",
        ROOT_DIR / "dev",
        ROOT_DIR / "research_cockpit" / "dashboards",
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
        skill_root = ROOT_DIR / "skills" / "research-cockpit"

        self.assertTrue((skill_root / "SKILL.md").exists())
        self.assertTrue((skill_root / "agents" / "openai.yaml").exists())
        self.assertFalse((ROOT_DIR / "SKILL.md").exists())
        self.assertFalse((ROOT_DIR / "agents" / "openai.yaml").exists())

    def test_development_materials_are_separate_from_skill_package(self) -> None:
        self.assertTrue((ROOT_DIR / "dev" / "docs" / "development_status.md").exists())
        self.assertTrue((ROOT_DIR / "dev" / "docs" / "research_cockpit_design.md").exists())
        self.assertTrue((ROOT_DIR / "dev" / "docs" / "requirements_zh.md").exists())
        self.assertTrue((ROOT_DIR / "dev" / "specs" / "research_cockpit_v2_specs").exists())
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
