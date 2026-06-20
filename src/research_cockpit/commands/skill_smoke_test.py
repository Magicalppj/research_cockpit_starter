from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from research_cockpit.paths import default_data_root, plugin_root

PLUGIN_ROOT = plugin_root()
ROOT = default_data_root()
REQUIRED_MODULES = {
    "networkx": "networkx",
    "yaml": "PyYAML",
}


def _cli_args(python: str, command: str, *args: str) -> list[str]:
    return [python, "-m", "research_cockpit.cli", command, *args]


def _duration_ms(started_at: float) -> float:
    return round(max(0.0, time.perf_counter() - started_at) * 1000, 3)


def _same_python(left: str, right: str) -> bool:
    try:
        return Path(left).resolve(strict=False) == Path(right).resolve(strict=False)
    except (OSError, RuntimeError):
        return left == right


def _emit_progress(enabled: bool, message: str) -> None:
    if enabled:
        print(message, file=sys.stderr, flush=True)


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
    if name == "node_context":
        return {
            "node_id": data.get("node", {}).get("id") if isinstance(data, dict) else None,
            "recommended_next_step_count": len(data.get("recommended_next_steps", [])) if isinstance(data, dict) else None,
        }
    if name == "option_workstream_context":
        return {
            "option_id": data.get("option", {}).get("id") if isinstance(data, dict) else None,
            "experiment_count": data.get("evidence_summary", {}).get("experiment_count") if isinstance(data, dict) else None,
        }
    return {}


def _check_payload(
    name: str,
    *,
    passed: bool,
    returncode: int,
    command: list[str],
    summary: dict[str, Any] | None = None,
    stdout: str = "",
    stderr: str = "",
    elapsed_ms: float | None = None,
) -> dict[str, Any]:
    payload = {
        "name": name,
        "passed": passed,
        "returncode": returncode,
        "command": command,
        "summary": summary or {},
        "stdout": stdout[:1000],
        "stderr": stderr[:1000],
    }
    if elapsed_ms is not None:
        payload["elapsed_ms"] = elapsed_ms
    return payload


def _run_check(name: str, args: list[str], *, progress: bool = False) -> dict[str, Any]:
    _emit_progress(progress, f"smoke: starting {name}")
    started_at = time.perf_counter()
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    elapsed_ms = _duration_ms(started_at)
    _emit_progress(progress, f"smoke: finished {name} in {elapsed_ms} ms")
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    return _check_payload(
        name,
        passed=result.returncode == 0,
        returncode=result.returncode,
        command=args,
        summary=_summarize_json(name, stdout),
        stdout=stdout,
        stderr=stderr,
        elapsed_ms=elapsed_ms,
    )


def _run_direct_check(name: str, command: list[str], callback: Any, *, progress: bool = False) -> dict[str, Any]:
    _emit_progress(progress, f"smoke: starting {name}")
    started_at = time.perf_counter()
    try:
        summary = callback()
    except Exception as exc:
        elapsed_ms = _duration_ms(started_at)
        _emit_progress(progress, f"smoke: failed {name} in {elapsed_ms} ms")
        return _check_payload(
            name,
            passed=False,
            returncode=1,
            command=command,
            summary={},
            stdout=str(exc),
            elapsed_ms=elapsed_ms,
        )
    elapsed_ms = _duration_ms(started_at)
    _emit_progress(progress, f"smoke: finished {name} in {elapsed_ms} ms")
    return _check_payload(
        name,
        passed=True,
        returncode=0,
        command=command,
        summary=summary,
        elapsed_ms=elapsed_ms,
    )


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
        f"From the Research Cockpit plugin root, install the package with `python -m pip install -e .` "
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


def _compact_smoke_checks(root: Path, *, query: str, progress: bool = False) -> list[dict[str, Any]]:
    from research_cockpit.commands.agent_bootstrap import _context_paths
    from research_cockpit.commands.list_agent_commands import agent_command_manifest
    from research_cockpit.commands.option_workstream_context import compact_option_workstream_context
    from research_cockpit.graph_core import GraphTopology, focus_node_id_from_current
    from research_cockpit.model import (
        build_search_index,
        load_explicit_edges,
        load_nodes,
        load_yaml,
        search_knowledge,
        validate_cockpit,
    )
    from research_cockpit.option_workstreams import build_option_workstream_context
    from research_cockpit.resources import build_link_rows
    from research_cockpit.node_onboarding import build_node_onboarding_context
    from research_cockpit.suggestions import build_action_suggestions

    root_arg = str(root)
    state: dict[str, Any] = {}

    def ensure_core() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        return state["nodes"], state["current"], state["explicit_edges"]

    def ensure_topology() -> GraphTopology:
        if "topology" not in state:
            state["topology"] = GraphTopology.from_nodes(state["nodes"])
        return state["topology"]

    def ensure_link_rows() -> list[dict[str, Any]]:
        if "link_rows" not in state:
            state["link_rows"] = build_link_rows(root, state["nodes"])
        return state["link_rows"]

    def ensure_suggestions() -> list[dict[str, Any]]:
        if "suggestions" not in state:
            state["suggestions"] = build_action_suggestions(root, state["nodes"], state["current"], ensure_link_rows())
        return state["suggestions"]

    def validate_check() -> dict[str, Any]:
        nodes = load_nodes(root)
        current = load_yaml(root / "current_state.yaml")
        explicit_edges = load_explicit_edges(root)
        validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
        state["nodes"] = nodes
        state["current"] = current
        state["explicit_edges"] = explicit_edges
        return {
            "node_count": len(nodes),
            "edge_count": len(explicit_edges),
        }

    def bootstrap_contract_check() -> dict[str, Any]:
        nodes, current, _explicit_edges = ensure_core()
        context_paths = _context_paths(root)
        return {
            "validation_ok": True,
            "node_count": len(nodes),
            "skill_path": str(PLUGIN_ROOT),
            "context_paths_present": all(item.get("exists") for item in context_paths.values()),
            "current_focus_node": focus_node_id_from_current(current, nodes),
            "smoke_scope": "compact_bootstrap_contract",
        }

    def commands_check() -> dict[str, Any]:
        commands = agent_command_manifest(compact=True)
        return {"command_count": len(commands)}

    def search_check() -> dict[str, Any]:
        nodes, current, _explicit_edges = ensure_core()
        index = build_search_index(
            root,
            nodes,
            current,
            link_rows=ensure_link_rows(),
            topology=ensure_topology(),
            include_resource_text=False,
        )
        results = search_knowledge(index, query, limit=5)
        return {
            "result_count": len(results),
            "search_index_entry_count": len(index),
            "resource_text_enabled": False,
        }

    def suggestions_check() -> dict[str, Any]:
        suggestions = ensure_suggestions()
        active_count = len([
            item for item in suggestions
            if item.get("lifecycle_state", "active") == "active"
        ])
        return {
            "suggestion_count": len(suggestions),
            "active_suggestion_count": active_count,
            "sampled_count": min(len(suggestions), 5),
        }

    def node_context_check() -> dict[str, Any]:
        nodes, current, _explicit_edges = ensure_core()
        node_id = focus_node_id_from_current(current, nodes) or current.get("current_option")
        if not node_id or str(node_id) not in nodes:
            return {"node_id": None, "schema_version": None, "recommended_next_step_count": 0}
        node_id = str(node_id)
        context = build_node_onboarding_context(
            root,
            nodes,
            current,
            node_id,
            compact=True,
            link_rows=ensure_link_rows(),
            suggestions=ensure_suggestions(),
        )
        return {
            "node_id": context.get("node", {}).get("id"),
            "node_type": context.get("node", {}).get("type"),
            "schema_version": context.get("schema_version"),
            "parent_chain_count": len(context.get("parent_path", []) or []),
            "recommended_next_step_count": len(context.get("recommended_next_steps", []) or []),
            "smoke_scope": "compact_node_context",
        }

    def option_context_check() -> dict[str, Any]:
        nodes, current, _explicit_edges = ensure_core()
        option_id = current.get("current_option")
        if not option_id:
            return {"option_id": None, "experiment_count": None}
        payload = build_option_workstream_context(root, nodes, current, str(option_id))
        compact = compact_option_workstream_context(payload, nodes)
        return {
            "option_id": compact.get("option", {}).get("id"),
            "experiment_count": compact.get("evidence_summary", {}).get("experiment_count"),
        }

    checks = [
        _run_direct_check("validate_cockpit", ["in-process", "validate", "--root", root_arg], validate_check, progress=progress)
    ]
    if not checks[-1]["passed"]:
        return checks

    checks.extend([
        _run_direct_check(
            "agent_bootstrap",
            ["in-process", "bootstrap", "--root", root_arg, "--coordinator", "--compact-contract"],
            bootstrap_contract_check,
            progress=progress,
        ),
        _run_direct_check("list_agent_commands", ["in-process", "commands", "--compact"], commands_check, progress=progress),
        _run_direct_check(
            "search_knowledge",
            ["in-process", "search", "--root", root_arg, "--query", query, "--limit", "5", "--skip-resource-text"],
            search_check,
            progress=progress,
        ),
        _run_direct_check(
            "suggest_next_actions",
            ["in-process", "suggest-next-actions", "--root", root_arg, "--summary-only"],
            suggestions_check,
            progress=progress,
        ),
        _run_direct_check(
            "node_context",
            ["in-process", "node-context", "--root", root_arg, "--id", "<current_focus_node>", "--compact"],
            node_context_check,
            progress=progress,
        ),
    ])
    if state.get("current", {}).get("current_option"):
        checks.append(_run_direct_check(
            "option_workstream_context",
            ["in-process", "option-workstream-context", "--root", root_arg, "--compact"],
            option_context_check,
            progress=progress,
        ))
    return checks


def _full_smoke_checks(python: str, root: Path, *, query: str, progress: bool = False) -> list[dict[str, Any]]:
    root_arg = str(root)
    option_id = _current_option_for_root(root)
    checks = [
        _run_check("validate_cockpit", _cli_args(python, "validate", "--root", root_arg), progress=progress),
        _run_check("agent_bootstrap", _cli_args(python, "bootstrap", "--root", root_arg, "--coordinator", "--json"), progress=progress),
        _run_check("list_agent_commands", _cli_args(python, "commands", "--json"), progress=progress),
        _run_check(
            "search_knowledge",
            _cli_args(python, "search", "--root", root_arg, "--query", query, "--json", "--limit", "5"),
            progress=progress,
        ),
        _run_check("suggest_next_actions", _cli_args(python, "suggest-next-actions", "--root", root_arg, "--json"), progress=progress),
    ]
    if option_id:
        checks.append(_run_check(
            "node_context",
            _cli_args(python, "node-context", "--root", root_arg, "--id", option_id, "--json"),
            progress=progress,
        ))
        checks.append(_run_check(
            "option_workstream_context",
            _cli_args(python, "option-workstream-context", "--root", root_arg, "--option", option_id, "--json"),
            progress=progress,
        ))
    return checks


def skill_smoke_test_payload(
    root: Path = ROOT,
    *,
    query: str = "demo",
    python_executable: str | None = None,
    full: bool = False,
    progress: bool = False,
) -> dict[str, Any]:
    python = python_executable or os.environ.get("RESEARCH_COCKPIT_PYTHON", "").strip() or sys.executable
    missing = missing_modules_for_python(python)
    if missing:
        return {
            "ok": False,
            "skill_root": str(PLUGIN_ROOT),
            "plugin_root": str(PLUGIN_ROOT),
            "root": str(root),
            "python": python,
            "checks": [_dependency_failure_check(python, missing)],
        }

    mode = "full" if full else "compact"
    if not full and not _same_python(python, sys.executable):
        mode = "full_external_python"

    if mode == "compact":
        checks = _compact_smoke_checks(root, query=query, progress=progress)
    else:
        checks = _full_smoke_checks(python, root, query=query, progress=progress)

    failed = next((check for check in checks if not check["passed"]), None)
    if failed:
        _emit_progress(progress, f"smoke: failed at {failed['name']}")
    else:
        _emit_progress(progress, "smoke: all checks passed")

    return {
        "ok": all(check["passed"] for check in checks),
        "mode": mode,
        "skill_root": str(PLUGIN_ROOT),
        "plugin_root": str(PLUGIN_ROOT),
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
    parser.add_argument("--full", action="store_true", help="Run the legacy full subprocess smoke workflow.")
    parser.add_argument("--progress", action="store_true", help="Print per-check progress to stderr.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = skill_smoke_test_payload(
        args.root,
        query=args.query,
        python_executable=args.python_executable,
        full=args.full,
        progress=args.progress,
    )
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_text(payload)
    raise SystemExit(0 if payload["ok"] else 1)


if __name__ == "__main__":
    main()
