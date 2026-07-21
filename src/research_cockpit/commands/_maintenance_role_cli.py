from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research_cockpit.commands._runtime import emit_json, safe_print
from research_cockpit.commands._work_lease_cli import handle_role_cli_input_error
from research_cockpit.maintenance_actions import apply_maintenance_action
from research_cockpit.mutation_lock import MutationError
from research_cockpit.paths import default_data_root
from research_cockpit.storage import load_yaml
from research_cockpit.types import ValidationError


def run_maintenance_role(
    *,
    command: str,
    schema: dict[str, Any],
) -> None:
    parser = argparse.ArgumentParser(
        prog=f"research-cockpit maintenance {command}",
        allow_abbrev=False,
    )
    parser.add_argument("--root", type=Path, default=default_data_root())
    parser.add_argument("--file", type=Path)
    parser.add_argument("--print-schema", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    if args.print_schema:
        emit_json(schema, compact=args.compact)
        return
    raw_plan = {}
    if args.file is None:
        handle_role_cli_input_error(
            args,
            parser,
            ValueError("--file is required unless --print-schema is used"),
            operation=f"maintenance {command}",
            retry_command=f"research-cockpit maintenance {command} --print-schema",
        )
    try:
        if not args.file.is_file():
            raise FileNotFoundError(args.file)
        raw_plan = load_yaml(args.file)
        payload = apply_maintenance_action(
            args.root,
            command=command,
            plan=raw_plan,
            input_path=args.file.resolve(),
        )
    except MutationError as exc:
        if args.json:
            emit_json(exc.payload or {"ok": False, "error": str(exc)}, compact=args.compact)
        else:
            safe_print(str(exc))
        raise SystemExit(1) from None
    except (ValidationError, ValueError, FileNotFoundError, OSError) as exc:
        handle_role_cli_input_error(
            args,
            parser,
            exc,
            operation=f"maintenance {command}",
            retry_command=f"research-cockpit maintenance {command} --print-schema",
        )

    if args.json:
        emit_json(payload, compact=args.compact)
        return
    mode = "executed" if payload["executed"] else "planned"
    safe_print(f"maintenance {command} {payload['action']}: {mode}")
