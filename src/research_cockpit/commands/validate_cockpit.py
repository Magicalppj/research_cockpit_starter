from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.lifecycle_guards import terminal_parent_guard_failures
from research_cockpit.model import load_nodes, validate_cockpit


def _lifecycle_error_message(error: dict) -> str:
    blocker_ids = ", ".join(str(item["id"]) for item in error.get("blocking_descendants", []))
    return (
        f"{error['node_id']}: terminal_parent_has_active_descendants for status "
        f"{error['target_status']!r}; active descendants: {blocker_ids}"
    )


def validation_payload(root: Path, *, strict_lifecycle: bool = False) -> dict:
    nodes = load_nodes(root)
    errors = validate_cockpit(root, nodes, include_interaction_log=True)
    lifecycle_errors = terminal_parent_guard_failures(nodes) if strict_lifecycle else []
    if lifecycle_errors:
        errors = [*errors, *[_lifecycle_error_message(error) for error in lifecycle_errors]]
    ok = not errors
    payload = {
        "root": str(root),
        "valid": ok,
        "ok": ok,
        "strict_lifecycle": strict_lifecycle,
        "node_count": len(nodes),
        "errors": errors,
    }
    if strict_lifecycle:
        payload["lifecycle_errors"] = lifecycle_errors
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true", help="Print machine-readable validation output")
    parser.add_argument(
        "--strict-lifecycle",
        action="store_true",
        help="Fail when terminal problem/option nodes still have active downstream work.",
    )
    args = parser.parse_args()

    payload = validation_payload(args.root, strict_lifecycle=args.strict_lifecycle)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif payload["valid"]:
        print(f"OK: {payload['node_count']} nodes validated under {payload['root']}")
    else:
        print(f"FAILED: {len(payload['errors'])} issue(s) under {payload['root']}")
        for error in payload["errors"]:
            print(f"- {error}")

    raise SystemExit(0 if payload["valid"] else 1)


if __name__ == "__main__":
    main()
