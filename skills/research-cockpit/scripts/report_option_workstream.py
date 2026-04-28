from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "research_cockpit"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cockpit.model import (
    ResearchNode,
    VALID_WORKSTREAM_RECOMMENDATIONS,
    ValidationError,
    build_option_workstream_context,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    save_yaml,
    validate_cockpit,
)
from scripts.build_dashboard import build_dashboard
from scripts.record_finding import find_node_file


def report_option_workstream(
    root: Path,
    *,
    option_id: str,
    agent_id: str,
    recommendation: str,
    summary: str,
    rebuild_dashboard: bool = True,
) -> Path:
    if recommendation not in VALID_WORKSTREAM_RECOMMENDATIONS:
        allowed = ", ".join(sorted(VALID_WORKSTREAM_RECOMMENDATIONS))
        raise ValueError(f"Invalid recommendation {recommendation!r}; allowed: {allowed}")

    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
    if option_id not in nodes:
        raise ValueError(f"Option node does not exist: {option_id}")
    if nodes[option_id].type != "option":
        raise ValueError(f"Node {option_id} must be option, got {nodes[option_id].type}")

    option_path = find_node_file(root, option_id)
    data = load_yaml(option_path)
    workstream = data.get("agent_workstream") if isinstance(data.get("agent_workstream"), dict) else {}
    existing_owner = str(workstream.get("owner") or "")
    if existing_owner and existing_owner != agent_id:
        raise ValueError(f"{option_id} is owned by {existing_owner}; reporting agent was {agent_id}")

    context = build_option_workstream_context(root, nodes, current, option_id)
    evidence = context["evidence_summary"]
    today = str(date.today())
    report: dict[str, Any] = {
        "reporting_agent": agent_id,
        "recommendation": recommendation,
        "summary": summary,
        "evidence_summary": evidence.get("evidence_summary"),
        "experiment_count": evidence.get("experiment_count", 0),
        "finding_count": evidence.get("findings_count", 0),
        "reported_at": today,
    }
    data["workstream_report"] = report
    data["agent_workstream"] = {
        **workstream,
        "owner": agent_id,
        "status": "reported",
        "report_to_problem": (context.get("upstream_problem") or {}).get("id"),
        "started_at": workstream.get("started_at") or today,
        "updated_at": today,
    }
    data["updated_at"] = today

    candidate = dict(nodes)
    candidate[option_id] = ResearchNode.from_dict(data)
    validate_cockpit(root, candidate, current, explicit_edges, raise_on_error=True)
    save_yaml(option_path, data)
    if rebuild_dashboard:
        build_dashboard(root)
    return option_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--option", required=True, dest="option_id")
    parser.add_argument("--agent", required=True, dest="agent_id")
    parser.add_argument("--recommend", required=True, choices=sorted(VALID_WORKSTREAM_RECOMMENDATIONS))
    parser.add_argument("--summary", required=True)
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()

    try:
        out = report_option_workstream(
            args.root,
            option_id=args.option_id,
            agent_id=args.agent_id,
            recommendation=args.recommend,
            summary=args.summary,
            rebuild_dashboard=not args.no_build,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    print(f"Reported option workstream {args.option_id}: {out}")
    if not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
