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
REQUIRED_MODULES = {
    "networkx": "networkx",
    "yaml": "PyYAML",
}


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
    if name == "option_workstream_context":
        return {
            "option_id": data.get("option", {}).get("id") if isinstance(data, dict) else None,
            "experiment_count": data.get("evidence_summary", {}).get("experiment_count") if isinstance(data, dict) else None,
        }
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


def missing_modules_for_python(python: str, required: dict[str, str] = REQUIRED_MODULES) -> list[str]:
    missing: list[str] = []
    for module in required:
        result = subprocess.run(
            [python, "-c", f"import {module}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            missing.append(module)
    return missing


def _dependency_failure_check(python: str, missing: list[str]) -> dict[str, Any]:
    packages = ", ".join(REQUIRED_MODULES.get(module, module) for module in missing)
    modules = ", ".join(missing)
    message = (
        f"Missing Python modules for {python}: {modules}. "
        f"From the skill package root, install requirements with `python -m pip install -r requirements.txt` "
        f"or rerun with an interpreter that already has: {packages}."
    )
    return {
        "name": "runtime_dependencies",
        "passed": False,
        "returncode": 1,
        "command": [python, "-c", "import networkx, yaml"],
        "summary": {"missing_modules": missing},
        "stdout": message,
        "stderr": "",
    }


def _current_option_for_root(root: Path) -> str | None:
    import yaml

    path = root / "current_state.yaml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    option_id = data.get("current_option")
    return str(option_id) if option_id else None


def skill_smoke_test_payload(
    root: Path = ROOT,
    *,
    query: str = "demo",
    python_executable: str | None = None,
) -> dict[str, Any]:
    python = python_executable or os.environ.get("RESEARCH_COCKPIT_PYTHON", "").strip() or sys.executable
    missing = missing_modules_for_python(python)
    if missing:
        return {
            "ok": False,
            "skill_root": str(SKILL_ROOT),
            "root": str(root),
            "python": python,
            "checks": [_dependency_failure_check(python, missing)],
        }

    root_arg = str(root)
    option_id = _current_option_for_root(root)
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
    if option_id:
        checks.append(_run_check(
            "option_workstream_context",
            [python, _script_path("option_workstream_context.py"), "--root", root_arg, "--option", option_id, "--json"],
        ))
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
    parser.add_argument("--query", default="demo")
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
