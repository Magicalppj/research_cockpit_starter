from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


VALID_COMMAND_STYLES = {"console", "python"}
VALID_NODE_TYPES = {"stage", "problem", "option", "experiment", "decision", "artifact"}

VALID_STATUSES_BY_TYPE = {
    "stage": {"planned", "active", "blocked", "done"},
    "problem": {"open", "active", "blocked", "resolved", "parked"},
    "option": {"open", "active", "promising", "rejected", "accepted", "paused", "parked"},
    "experiment": {"planned", "queued", "running", "done", "failed", "cancelled"},
    "decision": {"proposed", "accepted", "superseded", "rejected"},
    "artifact": {"draft", "planned", "active", "done", "superseded", "deprecated", "archived"},
}

DEFAULT_STATUS_BY_TYPE = {
    "stage": "planned",
    "problem": "open",
    "option": "open",
    "experiment": "planned",
    "decision": "proposed",
    "artifact": "active",
}

ALL_KNOWN_STATUSES = {status for statuses in VALID_STATUSES_BY_TYPE.values() for status in statuses}
VALID_FINDING_CONFIDENCES = {"weak", "medium", "strong"}
VALID_FINDING_OUTCOMES = {"positive", "negative", "mixed", "inconclusive"}
VALID_SUGGESTION_LIFECYCLE_STATES = {"active", "dismissed", "completed"}
VALID_WORKSTREAM_STATUSES = {"claimed", "in_progress", "blocked", "reported", "released"}
ACTIVE_WORKSTREAM_STATUSES = {"claimed", "in_progress", "blocked"}
VALID_WORKSTREAM_RECOMMENDATIONS = {"accept", "reject", "continue"}
CONTEXT_SCHEMA_VERSION = "agent_context_v1"

SEARCH_NODE_TEXT_FIELDS = (
    "question",
    "hypothesis",
    "result_summary",
    "evidence_summary",
    "findings",
    "next_actions",
    "blockers",
    "agent_workstream",
    "workstream_report",
    "pros",
    "cons",
    "rejection_reason",
    "alternatives_considered",
    "consequences",
    "next_required_actions",
    "current_conclusion",
)

RESOURCE_SEARCH_ALLOWED_SUFFIXES = {".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".csv", ".tsv"}
RESOURCE_SEARCH_MAX_BYTES = 128 * 1024

DEFAULT_FOCUS_MODE = {
    "default_depth": 2,
    "hide_statuses": ["rejected", "parked", "archived"],
    "show_resolved": False,
    "show_rejected": False,
    "show_parked": False,
}

VALID_GRAPH_VIEW_SCOPES = {
    "focus_depth_2",
    "focus_depth_1",
    "current_branch",
    "option_workstream",
    "global",
}

GRAPH_VIEW_FILTER_LIST_KEYS = (
    "node_types",
    "statuses",
    "stages",
    "focus_roles",
    "workstreams",
    "collapsed_branch_roots",
    "revealed_child_roots",
)

GRAPH_VIEW_FILTER_BOOL_KEYS = (
    "only_blocking",
    "only_next_actions",
    "only_missing_evidence",
    "show_baseline_lens",
)

STATUS_COLORS = {
    "draft": "#D9E8FF",
    "planned": "#D9E8FF",
    "open": "#D9E8FF",
    "queued": "#D9E8FF",
    "active": "#FFE9A8",
    "running": "#FFE9A8",
    "promising": "#E8F5D8",
    "accepted": "#CFEAD6",
    "done": "#CFEAD6",
    "resolved": "#CFEAD6",
    "rejected": "#F3D0D0",
    "failed": "#F3D0D0",
    "blocked": "#F6C6C6",
    "parked": "#E5E5E5",
    "paused": "#E5E5E5",
    "cancelled": "#E5E5E5",
    "proposed": "#E7D8F6",
    "superseded": "#E5E5E5",
    "archived": "#E5E5E5",
    "deprecated": "#E5E5E5",
}

TYPE_SHAPES = {
    "stage": "box",
    "problem": "diamond",
    "option": "box",
    "experiment": "ellipse",
    "decision": "hexagon",
    "artifact": "database",
}


class ValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass
class ResearchNode:
    id: str
    type: str
    title: str
    status: str = "open"
    summary: str = ""
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    priority: str | None = None
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchNode":
        return cls(
            id=str(data["id"]),
            type=str(data.get("type", "node")),
            title=str(data.get("title", data["id"])),
            status=str(data.get("status", "open")),
            summary=str(data.get("summary", "")),
            parent=data.get("parent"),
            children=[str(x) for x in data.get("children", []) or []],
            priority=data.get("priority"),
            tags=[str(x) for x in data.get("tags", []) or []],
            raw=data,
        )
