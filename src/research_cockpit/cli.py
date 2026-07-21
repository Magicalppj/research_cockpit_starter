from __future__ import annotations

import argparse
from importlib import import_module
import os
from pathlib import Path
import shutil
import subprocess
import sys

from research_cockpit.command_registry import (
    COMMAND_MODULES,
    GROUPED_COMMAND_ALIASES,
    ROLE_COMMAND_MODULES,
)
from research_cockpit.cli_progress import progress_session
from research_cockpit.commands._runtime import configure_utf8_stdio, emit_json, safe_print
from research_cockpit.mutation_lock import MutationError
from research_cockpit.paths import default_data_root, plugin_root

COMMAND_CHOICES = [
    *COMMAND_MODULES.keys(),
    "init",
    "ui",
    *GROUPED_COMMAND_ALIASES.keys(),
    *ROLE_COMMAND_MODULES.keys(),
]
LOCAL_PROGRESS_MODULES = {
    "build_dashboard",
    "complete_run",
    "context",
    "coord_handoff",
    "ingest_artifact",
    "ingest_gate_result",
    "node_context",
    "record_finding",
    "skill_smoke_test",
    "update_node_fields",
    "update_run",
    "validate_cockpit",
}


def _run_module_name(module_name: str, argv: list[str], *, display_command_name: str) -> None:
    module = import_module(f"research_cockpit.commands.{module_name}")
    progress_requested = "--progress" in argv
    module_argv = (
        argv
        if module_name in LOCAL_PROGRESS_MODULES
        else [item for item in argv if item != "--progress"]
    )
    old_argv = sys.argv
    sys.argv = [f"research-cockpit {display_command_name}", *module_argv]
    try:
        with progress_session(
            display_command_name,
            explicit=progress_requested,
        ):
            module.main()
    except MutationError as exc:
        if "--json" in argv:
            emit_json(exc.payload or {"ok": False, "error": str(exc)})
        else:
            safe_print(str(exc))
        raise SystemExit(1) from None
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        if "--json" in argv:
            emit_json({"ok": False, "error": str(exc)}, compact="--compact" in argv)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from None
    finally:
        sys.argv = old_argv


def _run_module(command_name: str, argv: list[str], *, display_command_name: str | None = None) -> None:
    _run_module_name(
        COMMAND_MODULES[command_name],
        argv,
        display_command_name=display_command_name or command_name,
    )


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
    parser.add_argument("--build", action="store_true", help="Build dashboards/context packs after initialization.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable init result.")
    args = parser.parse_args(argv)

    source = plugin_root() / "templates" / "minimal_research_cockpit"
    if args.template == "demo":
        source = plugin_root() / "examples" / "demo_research_cockpit"
    if args.root.exists() and any(args.root.iterdir()) and not args.force:
        raise SystemExit(f"Target already exists and is not empty: {args.root}")
    _copytree_contents(source, args.root)
    if args.build:
        from research_cockpit.commands.build_dashboard import build_dashboard

        build_dashboard(args.root)
    payload = {
        "root": str(args.root),
        "template": args.template,
        "built": bool(args.build),
        "dashboard_path": str(args.root / "dashboards") if args.build else None,
    }
    if args.json:
        emit_json(payload)
        return
    safe_print(f"Initialized Research Cockpit state at {args.root}")
    if args.build:
        safe_print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


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


def _top_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-cockpit")
    parser.add_argument("command", nargs="?", choices=COMMAND_CHOICES)
    return parser


def _print_group_help(group_name: str) -> None:
    actions = GROUPED_COMMAND_ALIASES[group_name]
    print(f"usage: research-cockpit {group_name} <action> [args]")
    print()
    print("actions:")
    for action_name, command_name in sorted(actions.items()):
        print(f"  {action_name:<12} alias for research-cockpit {command_name}")


def _print_role_help(role_name: str) -> None:
    print(f"usage: research-cockpit {role_name} <action> [args]")
    print()
    print("actions:")
    for action_name in sorted(ROLE_COMMAND_MODULES[role_name]):
        print(f"  {action_name}")


def main(argv: list[str] | None = None) -> None:
    configure_utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _top_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return

    command = argv[0]
    rest = argv[1:]
    if command not in COMMAND_CHOICES:
        parser.error(
            f"argument command: invalid choice: {command!r} "
            f"(choose from {', '.join(repr(item) for item in COMMAND_CHOICES)})"
        )

    if command == "init":
        init_command(rest)
        return
    if command == "ui":
        ui_command(rest)
        return
    if command in ROLE_COMMAND_MODULES:
        if not rest or rest[0] in ("-h", "--help"):
            _print_role_help(command)
            return
        action = rest[0]
        actions = ROLE_COMMAND_MODULES[command]
        if action not in actions:
            parser.error(
                f"argument action: invalid choice: {action!r} "
                f"(choose from {', '.join(repr(item) for item in sorted(actions))})"
            )
        _run_module_name(
            actions[action],
            rest[1:],
            display_command_name=f"{command} {action}",
        )
        return
    if command in GROUPED_COMMAND_ALIASES:
        if not rest or rest[0] in ("-h", "--help"):
            _print_group_help(command)
            return
        action = rest[0]
        actions = GROUPED_COMMAND_ALIASES[command]
        if action not in actions:
            parser.error(
                f"argument action: invalid choice: {action!r} "
                f"(choose from {', '.join(repr(item) for item in sorted(actions))})"
            )
        _run_module(actions[action], rest[1:], display_command_name=f"{command} {action}")
        return
    _run_module(command, rest)


if __name__ == "__main__":
    main()
