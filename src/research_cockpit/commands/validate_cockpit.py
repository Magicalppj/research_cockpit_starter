from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_cockpit.paths import default_data_root

ROOT = default_data_root()

from research_cockpit.model import load_nodes, validate_cockpit


def validation_payload(root: Path) -> dict:
    nodes = load_nodes(root)
    errors = validate_cockpit(root, nodes, include_interaction_log=True)
    ok = not errors
    return {
        "root": str(root),
        "valid": ok,
        "ok": ok,
        "node_count": len(nodes),
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true", help="Print machine-readable validation output")
    args = parser.parse_args()

    payload = validation_payload(args.root)
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
