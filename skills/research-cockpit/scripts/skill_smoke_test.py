from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
ROOT = SKILL_ROOT / "research_cockpit"


def _script_path(script_name: str) -> str:
    return str(SKILL_ROOT / "scripts" / script_name)


def _summarize_json(name: str, stdout: str) -> dict[str, Any]:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {}

    if name == "agent_bootstrap":
        return {
            "validation_ok": data.get("validation", {}).get("ok"),
            "node_count": data.get("validation", {}).get("node_count"),
            "skill_path": data.get("skill", {}).get("path"),
            "context_paths_present": all(item.get("exists") for item in data.get("context_paths", {}).values()),
        }
    if name == "list_agent_commands":
        return {"command_count": len(data.get("commands", []))}
    if name == "search_knowledge":
        return {"result_count": len(data) if isinstance(data, list) else 0}
    if name == "suggest_next_actions":
        return {"suggestion_count": len(data) if isinstance(data, list) else 0}
    return {}


def _run_check(name: str, args: list[str]) -> dict[str, Any]:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    return {
        "name": name,
        "passed": result.returncode == 0,
        "returncode": result.returncode,
        "command": args,
        "summary": _summarize_json(name, stdout),
        "stdout": stdout[:1000],
        "stderr": stderr[:1000],
    }


def skill_smoke_test_payload(
    root: Path = ROOT,
    *,
    query: str = "t5",
    python_executable: str | None = None,
) -> dict[str, Any]:
    python = python_executable or os.environ.get("RESEARCH_COCKPIT_PYTHON", "").strip() or sys.executable
    root_arg = str(root)
    checks = [
        _run_check("validate_cockpit", [python, _script_path("validate_cockpit.py"), "--root", root_arg]),
        _run_check("agent_bootstrap", [python, _script_path("agent_bootstrap.py"), "--root", root_arg, "--json"]),
        _run_check("list_agent_commands", [python, _script_path("list_agent_commands.py"), "--json"]),
        _run_check(
            "search_knowledge",
            [python, _script_path("search_knowledge.py"), "--root", root_arg, "--query", query, "--json", "--limit", "5"],
        ),
        _run_check("suggest_next_actions", [python, _script_path("suggest_next_actions.py"), "--root", root_arg, "--json"]),
    ]
    return {
        "ok": all(check["passed"] for check in checks),
        "skill_root": str(SKILL_ROOT),
        "root": str(root),
        "python": python,
        "checks": checks,
    }


def _print_text(payload: dict[str, Any]) -> None:
    state = "OK" if payload["ok"] else "FAILED"
    print(f"Skill smoke test: {state}")
    print(f"Skill root: {payload['skill_root']}")
    print(f"Python: {payload['python']}")
    for check in payload["checks"]:
        marker = "OK" if check["passed"] else "FAILED"
        print(f"- {check['name']}: {marker}")
        if check["summary"]:
            print(f"  summary: {check['summary']}")
        if not check["passed"]:
            detail = check["stdout"] or check["stderr"]
            if detail:
                print(f"  detail: {detail}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--query", default="t5")
    parser.add_argument("--python", dest="python_executable", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = skill_smoke_test_payload(args.root, query=args.query, python_executable=args.python_executable)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_text(payload)
    raise SystemExit(0 if payload["ok"] else 1)


if __name__ == "__main__":
    main()
