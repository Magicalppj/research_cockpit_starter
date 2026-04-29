from __future__ import annotations

import argparse
from importlib import import_module
import os
from pathlib import Path
import shutil
import subprocess
import sys

from research_cockpit.paths import default_data_root, plugin_root


COMMAND_MAP = {
    "bootstrap": "agent_bootstrap",
    "validate": "validate_cockpit",
    "build": "build_dashboard",
    "commands": "list_agent_commands",
    "smoke": "skill_smoke_test",
}


def _run_module(module_name: str, argv: list[str]) -> None:
    module = import_module(f"research_cockpit.commands.{module_name}")
    old_argv = sys.argv
    sys.argv = [f"{module_name}.py", *argv]
    try:
        module.main()
    finally:
        sys.argv = old_argv


def _copytree_contents(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)


def init_command(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit init")
    parser.add_argument("--root", type=Path, default=Path.cwd() / "research_cockpit")
    parser.add_argument(
        "--template",
        choices=["minimal", "demo"],
        default="minimal",
        help="State template to copy into the research repo.",
    )
    parser.add_argument("--force", action="store_true", help="Copy over an existing target directory.")
    args = parser.parse_args(argv)

    source = plugin_root() / "templates" / "minimal_research_cockpit"
    if args.template == "demo":
        source = plugin_root() / "examples" / "demo_research_cockpit"
    if args.root.exists() and any(args.root.iterdir()) and not args.force:
        raise SystemExit(f"Target already exists and is not empty: {args.root}")
    _copytree_contents(source, args.root)
    print(f"Initialized Research Cockpit state at {args.root}")


def ui_command(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit ui")
    parser.add_argument("--root", type=Path, default=default_data_root())
    parser.add_argument("--server.address", dest="address", default="0.0.0.0")
    parser.add_argument("--server.port", dest="port", default="8501")
    args, rest = parser.parse_known_args(argv)

    env = os.environ.copy()
    env["RESEARCH_COCKPIT_ROOT"] = str(args.root)
    app_path = Path(__file__).resolve().parent / "ui" / "app.py"
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.address",
        args.address,
        "--server.port",
        str(args.port),
        *rest,
    ]
    raise SystemExit(subprocess.call(command, env=env))


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-cockpit")
    parser.add_argument("command", choices=[*COMMAND_MAP.keys(), "init", "ui"])
    args, rest = parser.parse_known_args()

    if args.command == "init":
        init_command(rest)
        return
    if args.command == "ui":
        ui_command(rest)
        return
    _run_module(COMMAND_MAP[args.command], rest)


if __name__ == "__main__":
    main()
