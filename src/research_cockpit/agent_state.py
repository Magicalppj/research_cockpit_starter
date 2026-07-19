from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import yaml

from research_cockpit.storage import load_yaml
from research_cockpit.types import ValidationError


@dataclass
class AgentRecord:
    agent_id: str
    status: str
    label: str | None = None
    display_name: str | None = None
    created_at: str | None = None
    last_seen_at: str | None = None
    active_assignment_ids: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentRecord":
        active_assignment_ids = data.get("active_assignment_ids", []) or []
        if not isinstance(active_assignment_ids, list):
            active_assignment_ids = []
        return cls(
            agent_id=str(data["agent_id"]),
            status=str(data.get("status", "")),
            label=None if data.get("label") is None else str(data.get("label")),
            display_name=None if data.get("display_name") is None else str(data.get("display_name")),
            created_at=None if data.get("created_at") is None else str(data.get("created_at")),
            last_seen_at=None if data.get("last_seen_at") is None else str(data.get("last_seen_at")),
            active_assignment_ids=[str(item) for item in active_assignment_ids],
            raw=data,
        )


@dataclass
class AssignmentRecord:
    assignment_id: str
    agent_id: str
    status: str
    root_node: str
    current_node: str
    allowed_subtree: dict[str, Any] = field(default_factory=dict)
    objective: str | None = None
    next_actions: list[str] = field(default_factory=list)
    worktree: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AssignmentRecord":
        allowed_subtree = data.get("allowed_subtree") if isinstance(data.get("allowed_subtree"), dict) else {}
        worktree = data.get("worktree") if isinstance(data.get("worktree"), dict) else {}
        next_actions = data.get("next_actions", []) or []
        if not isinstance(next_actions, list):
            next_actions = []
        return cls(
            assignment_id=str(data["assignment_id"]),
            agent_id=str(data.get("agent_id", "")),
            status=str(data.get("status", "")),
            root_node=str(data.get("root_node", "")),
            current_node=str(data.get("current_node", "")),
            allowed_subtree=dict(allowed_subtree),
            objective=None if data.get("objective") is None else str(data.get("objective")),
            next_actions=[str(item) for item in next_actions],
            worktree=dict(worktree),
            created_at=None if data.get("created_at") is None else str(data.get("created_at")),
            updated_at=None if data.get("updated_at") is None else str(data.get("updated_at")),
            raw=data,
        )

    def to_dict(self, **updates: Any) -> dict[str, Any]:
        data = deepcopy(self.raw)
        data.update(
            {
                "assignment_id": self.assignment_id,
                "agent_id": self.agent_id,
                "status": self.status,
                "root_node": self.root_node,
                "current_node": self.current_node,
                "allowed_subtree": deepcopy(self.allowed_subtree),
            }
        )
        optional_fields = {
            "objective": self.objective,
            "next_actions": list(self.next_actions),
            "worktree": deepcopy(self.worktree),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        for key, value in optional_fields.items():
            if key in data or value not in (None, [], {}):
                data[key] = value
        data.update(deepcopy(updates))
        return data


@dataclass
class CoordinatorState:
    selected_node: str | None = None
    selected_assignment: str | None = None
    global_next_actions: list[str] = field(default_factory=list)
    dashboard_filters: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CoordinatorState":
        filters = data.get("dashboard_filters") if isinstance(data.get("dashboard_filters"), dict) else {}
        global_next_actions = data.get("global_next_actions", []) or []
        if not isinstance(global_next_actions, list):
            global_next_actions = []
        return cls(
            selected_node=None if data.get("selected_node") is None else str(data.get("selected_node")),
            selected_assignment=(
                None if data.get("selected_assignment") is None else str(data.get("selected_assignment"))
            ),
            global_next_actions=[str(item) for item in global_next_actions],
            dashboard_filters=dict(filters),
            raw=data,
        )


def load_agents(root: Path) -> dict[str, AgentRecord]:
    agent_dir = root / "agents"
    agents: dict[str, AgentRecord] = {}
    if not agent_dir.exists():
        return agents
    for path in sorted(agent_dir.glob("*.yaml")):
        rel_path = f"agents/{path.name}"
        try:
            data = load_yaml(path)
        except (OSError, yaml.YAMLError) as exc:
            raise ValidationError([f"{rel_path}: YAML parse error: {exc}"]) from exc
        if not data:
            continue
        if not isinstance(data, dict):
            raise ValidationError([f"{rel_path}: agent record must be a mapping"])
        if data.get("agent_id") in (None, ""):
            raise ValidationError([f"{rel_path}: missing required field 'agent_id'"])
        try:
            agent = AgentRecord.from_dict(data)
        except KeyError as exc:
            raise ValidationError([f"{rel_path}: missing required field {exc.args[0]!r}"]) from exc
        if agent.agent_id in agents:
            raise ValidationError([f"{rel_path}: duplicate agent id {agent.agent_id!r}"])
        agents[agent.agent_id] = agent
    return agents


def load_assignments(root: Path) -> dict[str, AssignmentRecord]:
    assignment_dir = root / "assignments"
    assignments: dict[str, AssignmentRecord] = {}
    if not assignment_dir.exists():
        return assignments
    for path in sorted(assignment_dir.glob("*.yaml")):
        rel_path = f"assignments/{path.name}"
        try:
            data = load_yaml(path)
        except (OSError, yaml.YAMLError) as exc:
            raise ValidationError([f"{rel_path}: YAML parse error: {exc}"]) from exc
        if not data:
            continue
        if not isinstance(data, dict):
            raise ValidationError([f"{rel_path}: assignment record must be a mapping"])
        if data.get("assignment_id") in (None, ""):
            raise ValidationError([f"{rel_path}: missing required field 'assignment_id'"])
        try:
            assignment = AssignmentRecord.from_dict(data)
        except KeyError as exc:
            raise ValidationError([f"{rel_path}: missing required field {exc.args[0]!r}"]) from exc
        if assignment.assignment_id in assignments:
            raise ValidationError([f"{rel_path}: duplicate assignment id {assignment.assignment_id!r}"])
        assignments[assignment.assignment_id] = assignment
    return assignments


def load_coordinator_state(root: Path) -> CoordinatorState:
    path = root / "coordinator_state.yaml"
    if not path.exists():
        return CoordinatorState()
    try:
        data = load_yaml(path)
    except (OSError, yaml.YAMLError) as exc:
        raise ValidationError([f"coordinator_state.yaml: YAML parse error: {exc}"]) from exc
    if not data:
        return CoordinatorState()
    if not isinstance(data, dict):
        raise ValidationError(["coordinator_state.yaml must be a mapping"])
    return CoordinatorState.from_dict(data)
