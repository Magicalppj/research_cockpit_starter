from __future__ import annotations

import argparse
import json
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
    append_interaction_log,
    build_option_workstream_context,
    load_explicit_edges,
    load_nodes,
    load_yaml,
    save_yaml,
    script_command,
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
    dry_run: bool = False,
) -> Path | dict[str, Any]:
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
    before_workstream = dict(workstream) if workstream else None
    before_report = data.get("workstream_report") if isinstance(data.get("workstream_report"), dict) else None
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
    preview = {
        "dry_run": dry_run,
        "changed": not dry_run,
        "option_id": option_id,
        "agent_id": agent_id,
        "recommendation": recommendation,
        "path": str(option_path),
        "evidence_summary": evidence,
        "before": {
            "agent_workstream": before_workstream,
            "workstream_report": before_report,
        },
        "after": {
            "agent_workstream": data["agent_workstream"],
            "workstream_report": data["workstream_report"],
        },
    }
    if dry_run:
        preview["changed"] = False
        return preview

    save_yaml(option_path, data)
    append_interaction_log(
        root,
        kind="report_option",
        actor=agent_id,
        node_id=option_id,
        command=(
            f"{script_command('report_option_workstream.py')}"
            f" --option {option_id} --agent {agent_id} --recommend {recommendation}"
        ),
        before={
            "agent_workstream": before_workstream,
            "workstream_report": before_report,
        },
        after={
            "agent_workstream": data["agent_workstream"],
            "workstream_report": data["workstream_report"],
        },
        extra={
            "option_id": option_id,
            "agent_id": agent_id,
            "recommendation": recommendation,
        },
    )
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
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
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
            dry_run=args.dry_run,
        )
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        print(str(exc))
        raise SystemExit(1) from exc

    if args.json:
        if isinstance(out, dict):
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({
                "dry_run": False,
                "changed": True,
                "option_id": args.option_id,
                "agent_id": args.agent_id,
                "recommendation": args.recommend,
                "path": str(out),
            }, ensure_ascii=False, indent=2))
        return

    if args.dry_run:
        print(f"Would report option workstream {args.option_id} with recommendation {args.recommend}.")
        return

    print(f"Reported option workstream {args.option_id}: {out}")
    if not args.no_build:
        print(f"Rebuilt dashboards under {args.root / 'dashboards'}")


if __name__ == "__main__":
    main()
