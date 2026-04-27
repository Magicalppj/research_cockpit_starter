from __future__ import annotations

import argparse
from pathlib import Path
import sys
from datetime import date

ROOT = Path(__file__).resolve().parents[1] / "research_cockpit"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cockpit.model import save_yaml


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True)
    parser.add_argument("--type", required=True, choices=["stage", "problem", "option", "experiment", "decision", "artifact"])
    parser.add_argument("--title", required=True)
    parser.add_argument("--parent", default=None)
    parser.add_argument("--status", default="open")
    parser.add_argument("--summary", default="")
    args = parser.parse_args()

    data = {
        "id": args.id,
        "type": args.type,
        "title": args.title,
        "status": args.status,
        "summary": args.summary,
        "created_at": str(date.today()),
        "updated_at": str(date.today()),
    }
    if args.parent:
        data["parent"] = args.parent

    out = ROOT / "graph" / "nodes" / f"{args.id}.yaml"
    if out.exists():
        raise FileExistsError(out)
    save_yaml(out, data)
    print(f"Created {out}")


if __name__ == "__main__":
    main()
