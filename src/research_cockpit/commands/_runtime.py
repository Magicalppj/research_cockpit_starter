from __future__ import annotations

from dataclasses import dataclass
import difflib
from pathlib import Path
from typing import Any
import yaml

from research_cockpit.interaction_log import append_interaction_log
from research_cockpit.model import ResearchNode, load_explicit_edges, load_nodes, load_yaml, save_yaml, validate_cockpit


@dataclass(frozen=True)
class CommandState:
    nodes: dict[str, ResearchNode]
    current: dict[str, Any]
    explicit_edges: list[dict[str, Any]]


def load_validated_state(root: Path) -> CommandState:
    nodes = load_nodes(root)
    current = load_yaml(root / "current_state.yaml")
    explicit_edges = load_explicit_edges(root)
    validate_cockpit(root, nodes, current, explicit_edges, raise_on_error=True)
    return CommandState(nodes=nodes, current=current, explicit_edges=explicit_edges)


def finish_mutation(
    root: Path,
    yaml_changes: list[tuple[Path, dict[str, Any]]],
    *,
    interaction: dict[str, Any],
    rebuild_dashboard: bool,
) -> None:
    for path, data in yaml_changes:
        save_yaml(path, data)
    append_interaction_log(root, **interaction)
    if rebuild_dashboard:
        from research_cockpit.commands.build_dashboard import build_dashboard

        build_dashboard(root)


def yaml_preview(data: dict[str, Any] | None) -> str:
    if data is None:
        return ""
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def yaml_change_diff(changes: list[tuple[Path, dict[str, Any] | None, dict[str, Any] | None]]) -> str:
    chunks: list[str] = []
    for path, before, after in changes:
        before_text = yaml_preview(before).splitlines(keepends=True)
        after_text = yaml_preview(after).splitlines(keepends=True)
        chunks.extend(
            difflib.unified_diff(
                before_text,
                after_text,
                fromfile=f"{path}:before",
                tofile=f"{path}:after",
            )
        )
    return "".join(chunks)
