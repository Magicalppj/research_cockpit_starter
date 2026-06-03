from __future__ import annotations

import argparse

from research_cockpit.assignment_scope import AssignmentScopeError
from research_cockpit.commands._runtime import emit_json, safe_print


def add_assignment_scope_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--assignment")
    parser.add_argument("--coordinator", action="store_true")


def emit_assignment_scope_error(args: argparse.Namespace, exc: AssignmentScopeError) -> None:
    if getattr(args, "json", False):
        emit_json(exc.payload)
    else:
        safe_print(str(exc))
