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
        "docs_development_status.md",
        "AGENTS.md",
        "SKILL.md",
        "agents/openai.yaml",
    ):
        path = ROOT_DIR / relative
        if path.exists():
            paths.append(path)

    for directory in (
        ROOT_DIR / "cockpit",
        ROOT_DIR / "ui",
        ROOT_DIR / "scripts",
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
