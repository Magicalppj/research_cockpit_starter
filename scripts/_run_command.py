from __future__ import annotations

from importlib import import_module
from pathlib import Path
import sys


def run(module_name: str) -> None:
    plugin_root = Path(__file__).resolve().parents[1]
    src_root = plugin_root / "src"
    sys.path.insert(0, str(src_root))
    module = import_module(f"research_cockpit.commands.{module_name}")
    module.main()
