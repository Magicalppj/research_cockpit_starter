#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


MODE_CONFIG = {
    "dry-run": {
        "gate_type": "dry_run",
        "stage": "dry_run",
        "next_allowed_action": "smoke_gate",
        "status": "completed",
    },
    "smoke-gate": {
        "gate_type": "smoke_check",
        "stage": "smoke_gate",
        "next_allowed_action": "full_run",
        "status": "completed",
    },
    "full-run": {
        "gate_type": "full_run",
        "stage": "full_run",
        "next_allowed_action": "artifact_capture",
        "status": "running",
    },
    "artifact-capture": {
        "gate_type": "artifact_capture",
        "stage": "artifact_capture",
        "next_allowed_action": "validate_build",
        "status": "completed",
    },
    "validate-build": {
        "gate_type": "validation_check",
        "stage": "validate_build",
        "next_allowed_action": "next_action_update",
        "status": "completed",
    },
    "next-action-update": {
        "gate_type": "handoff_check",
        "stage": "next_action_update",
        "next_allowed_action": "done",
        "status": "completed",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_links(values: list[str]) -> dict[str, str]:
    links = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--link must be key=relative/path: {value}")
        key, path = value.split("=", 1)
        key = key.strip()
        path = path.strip()
        if not key or not path:
            raise SystemExit(f"--link must be key=relative/path: {value}")
        links[key] = path
    return links


def write_run_record(args: argparse.Namespace, cfg: dict, status: str, started_at: str, finished_at: str) -> None:
    monitor_command = args.monitor_command
    fields = [
        ("schema_version", "launcher_run_record_v1"),
        ("run_id", args.run_id),
        ("experiment_id", args.experiment_id),
        ("status", status),
        ("launcher", args.launcher),
        ("mode", args.mode),
        ("command", args.command),
        ("started_at", started_at),
        ("finished_at", finished_at),
        ("pid", args.pid),
        ("tmux_session", args.tmux_session),
        ("log_root", "logs"),
        ("output_root", "outputs"),
        ("monitor_command", monitor_command),
        ("stop_command", args.stop_command),
        ("progress_file", "progress.json"),
        ("gate_result_file", "gate_result.json"),
        ("artifact_manifest_file", "artifact_manifest.json"),
        ("config_file", args.config_file),
        ("next_allowed_action", args.next_allowed_action or cfg["next_allowed_action"]),
        ("notes", args.notes),
    ]
    text = "\n".join(f"{key}: {value}" for key, value in fields) + "\n"
    (args.run_dir / "run_record.txt").write_text(text, encoding="utf-8")


def write_progress(args: argparse.Namespace, cfg: dict, status: str, timestamp: str) -> None:
    completed_steps = 1 if status in {"completed", "failed", "cancelled"} else 0
    payload = {
        "status": status,
        "completed_steps": completed_steps,
        "total_steps": 1,
        "last_update": timestamp,
        "current_stage": cfg["stage"],
        "latest_artifact": "artifact_manifest.json",
        "warnings": [],
    }
    write_json(args.run_dir / "progress.json", payload)


def write_gate_result(args: argparse.Namespace, cfg: dict, status: str) -> None:
    passed = args.gate_passed
    if passed is None:
        passed = status not in {"failed", "cancelled"}
    fatal_failures = {}
    if not passed:
        fatal_failures = {"reason": args.failure_reason or "gate failed"}
    payload = {
        "gate_type": args.gate_type or cfg["gate_type"],
        "passed": passed,
        "expected": {},
        "observed": {"mode": args.mode, "status": status},
        "fatal_failures": fatal_failures,
        "warnings": [],
        "next_allowed_action": args.next_allowed_action or cfg["next_allowed_action"],
        "experiment_id": args.experiment_id,
        "run_id": args.run_id,
    }
    write_json(args.run_dir / "gate_result.json", payload)


def write_artifact_manifest(args: argparse.Namespace, extra_links: dict[str, str]) -> None:
    links = {
        "run_record": "run_record.txt",
        "progress": "progress.json",
        "gate_result": "gate_result.json",
        "log": "logs/run.log",
    }
    links.update(extra_links)
    payload = {
        "schema_version": "artifact_manifest_v1",
        "artifact_id": args.artifact_id or f"artifact_{args.experiment_id}_{args.run_id}",
        "title": args.title or f"{args.run_id} output bundle",
        "experiment_id": args.experiment_id,
        "run_id": args.run_id,
        "summary": args.summary or f"{args.mode} launcher output.",
        "path": ".",
        "links": links,
        "warnings": [],
    }
    write_json(args.run_dir / "artifact_manifest.json", payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write standard Research Cockpit launcher output files.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", choices=sorted(MODE_CONFIG), required=True)
    parser.add_argument("--command", default="")
    parser.add_argument("--launcher", default="python")
    parser.add_argument("--status", choices=["queued", "running", "completed", "failed", "cancelled"])
    parser.add_argument("--gate-type")
    parser.add_argument("--next-allowed-action")
    parser.add_argument("--gate-passed", action="store_true", default=None)
    parser.add_argument("--gate-failed", action="store_false", dest="gate_passed")
    parser.add_argument("--failure-reason", default="")
    parser.add_argument("--monitor-command", default="")
    parser.add_argument("--stop-command", default="")
    parser.add_argument("--pid", default="")
    parser.add_argument("--tmux-session", default="")
    parser.add_argument("--config-file", default="")
    parser.add_argument("--artifact-id", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--summary", default="")
    parser.add_argument("--notes", default="")
    parser.add_argument("--link", action="append", default=[])
    args = parser.parse_args()

    cfg = MODE_CONFIG[args.mode]
    status = args.status or cfg["status"]
    timestamp = utc_now()
    finished_at = timestamp if status in {"completed", "failed", "cancelled"} else ""
    args.run_dir.mkdir(parents=True, exist_ok=True)
    (args.run_dir / "logs").mkdir(exist_ok=True)
    (args.run_dir / "outputs").mkdir(exist_ok=True)
    (args.run_dir / "logs" / "run.log").write_text(
        f"{timestamp} mode={args.mode} status={status} command={args.command}\n",
        encoding="utf-8",
    )

    write_run_record(args, cfg, status, timestamp, finished_at)
    write_progress(args, cfg, status, timestamp)
    write_gate_result(args, cfg, status)
    write_artifact_manifest(args, parse_links(args.link))

    print(json.dumps({"run_dir": str(args.run_dir), "mode": args.mode, "status": status}, sort_keys=True))


if __name__ == "__main__":
    main()
