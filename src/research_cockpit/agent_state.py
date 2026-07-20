from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import re
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
    agent_id: str | None
    status: str
    root_node: str
    current_node: str
    allowed_subtree: dict[str, Any] = field(default_factory=dict)
    kind: str = "experiment"
    scope: dict[str, Any] = field(default_factory=dict)
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    input_revision: str | None = None
    success_criteria: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    lease: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    objective: str | None = None
    next_actions: list[str] = field(default_factory=list)
    worktree: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AssignmentRecord":
        allowed_subtree = data.get("allowed_subtree") if isinstance(data.get("allowed_subtree"), dict) else {}
        root_node = str(data.get("root_node", ""))
        raw_scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
        scope = {
            "root_node": str(raw_scope.get("root_node") or root_node),
            "subtree_policy": str(
                raw_scope.get("subtree_policy")
                or allowed_subtree.get("policy")
                or "descendants_only"
            ),
            "write_policy": str(raw_scope.get("write_policy") or "exclusive"),
        }
        raw_dependencies = data.get("dependencies") if isinstance(data.get("dependencies"), list) else []
        dependencies = [deepcopy(item) for item in raw_dependencies if isinstance(item, dict)]
        inputs = data.get("inputs") if isinstance(data.get("inputs"), dict) else {}
        lease = data.get("lease") if isinstance(data.get("lease"), dict) else {}
        review = data.get("review") if isinstance(data.get("review"), dict) else {}
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        worktree = data.get("worktree") if isinstance(data.get("worktree"), dict) else {}
        next_actions = data.get("next_actions", []) or []
        if not isinstance(next_actions, list):
            next_actions = []
        success_criteria = data.get("success_criteria", []) or []
        if not isinstance(success_criteria, list):
            success_criteria = []
        deliverables = data.get("deliverables", []) or []
        if not isinstance(deliverables, list):
            deliverables = []
        return cls(
            assignment_id=str(data["assignment_id"]),
            agent_id=None if data.get("agent_id") is None else str(data.get("agent_id")),
            status=str(data.get("status", "")),
            root_node=root_node,
            current_node=str(data.get("current_node", "")),
            allowed_subtree=dict(allowed_subtree),
            kind=str(data.get("kind") or "experiment"),
            scope=scope,
            dependencies=dependencies,
            inputs=deepcopy(inputs),
            input_revision=None if data.get("input_revision") is None else str(data.get("input_revision")),
            success_criteria=[str(item) for item in success_criteria],
            deliverables=[str(item) for item in deliverables],
            lease=deepcopy(lease),
            review=deepcopy(review),
            result=deepcopy(result),
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
            "kind": self.kind,
            "dependencies": deepcopy(self.dependencies),
            "inputs": deepcopy(self.inputs),
            "input_revision": self.input_revision,
            "success_criteria": list(self.success_criteria),
            "deliverables": list(self.deliverables),
            "lease": deepcopy(self.lease),
            "review": deepcopy(self.review),
            "result": deepcopy(self.result),
            "objective": self.objective,
            "next_actions": list(self.next_actions),
            "worktree": deepcopy(self.worktree),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        for key, value in optional_fields.items():
            default_value = "experiment" if key == "kind" else None
            if key in data or value not in (default_value, None, [], {}):
                data[key] = value
        legacy_scope = {
            "root_node": self.root_node,
            "subtree_policy": str(self.allowed_subtree.get("policy") or "descendants_only"),
            "write_policy": "exclusive",
        }
        if "scope" in data or self.scope != legacy_scope:
            data["scope"] = deepcopy(self.scope)
        data.update(deepcopy(updates))
        return data


_IDENTITY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_WRITE_POLICIES = {"exclusive", "append_only", "review_read_only", "coordinator"}
_REVIEW_STATUSES = {"not_required", "pending", "approved", "changes_requested"}


def _utc_datetime(value: Any, field_name: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field_name} must be a non-empty UTC ISO-8601 string")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field_name} must be a valid UTC ISO-8601 timestamp")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        errors.append(f"{field_name} must use UTC")
        return None
    return parsed


def assignment_contract_errors(assignment: AssignmentRecord) -> list[str]:
    """Validate additive assignment fields without rejecting unknown legacy data."""

    prefix = assignment.assignment_id or "assignment"
    raw = assignment.raw
    errors: list[str] = []

    if "kind" in raw and (not isinstance(raw.get("kind"), str) or not raw["kind"].strip()):
        errors.append(f"{prefix}: kind must be a non-empty string")

    if "scope" in raw:
        scope = raw.get("scope")
        if not isinstance(scope, dict):
            errors.append(f"{prefix}: scope must be a mapping")
        else:
            root_node = scope.get("root_node")
            if not isinstance(root_node, str) or not root_node.strip():
                errors.append(f"{prefix}: scope.root_node must be a non-empty string")
            elif root_node != assignment.root_node:
                errors.append(
                    f"{prefix}: scope.root_node {root_node!r} must match root_node {assignment.root_node!r}"
                )
            if scope.get("subtree_policy") != "descendants_only":
                errors.append(f"{prefix}: scope.subtree_policy must be 'descendants_only'")
            if scope.get("write_policy") not in _WRITE_POLICIES:
                allowed = ", ".join(sorted(_WRITE_POLICIES))
                errors.append(f"{prefix}: scope.write_policy must be one of: {allowed}")

    if "dependencies" in raw:
        dependencies = raw.get("dependencies")
        if not isinstance(dependencies, list):
            errors.append(f"{prefix}: dependencies must be a list")
        else:
            seen: set[str] = set()
            for index, dependency in enumerate(dependencies):
                field_name = f"{prefix}: dependencies[{index}]"
                if not isinstance(dependency, dict):
                    errors.append(f"{field_name} must be a mapping")
                    continue
                dependency_id = dependency.get("assignment_id")
                if not isinstance(dependency_id, str) or not dependency_id.strip():
                    errors.append(f"{field_name}.assignment_id must be a non-empty string")
                elif dependency_id == assignment.assignment_id:
                    errors.append(f"{field_name}.assignment_id must not reference itself")
                elif dependency_id in seen:
                    errors.append(f"{field_name}.assignment_id is duplicated")
                else:
                    seen.add(dependency_id)
                if "required_status" in dependency and (
                    not isinstance(dependency.get("required_status"), str)
                    or not dependency["required_status"].strip()
                ):
                    errors.append(f"{field_name}.required_status must be a non-empty string")
                if (
                    "required_review_status" in dependency
                    and dependency.get("required_review_status") not in {"approved", "changes_requested"}
                ):
                    errors.append(
                        f"{field_name}.required_review_status must be 'approved' or 'changes_requested'"
                    )

    if "inputs" in raw:
        inputs = raw.get("inputs")
        if not isinstance(inputs, dict):
            errors.append(f"{prefix}: inputs must be a mapping")
        else:
            baseline_revision = inputs.get("effective_baseline_revision")
            if baseline_revision is not None and (
                not isinstance(baseline_revision, str) or not baseline_revision.strip()
            ):
                errors.append(
                    f"{prefix}: inputs.effective_baseline_revision must be null or a non-empty string"
                )
            dependency_revisions = inputs.get("dependency_revisions", {})
            if not isinstance(dependency_revisions, dict):
                errors.append(f"{prefix}: inputs.dependency_revisions must be a mapping")
            else:
                for dependency_id, revision in dependency_revisions.items():
                    if not isinstance(dependency_id, str) or not dependency_id:
                        errors.append(f"{prefix}: inputs.dependency_revisions keys must be non-empty strings")
                    if not isinstance(revision, str) or not revision:
                        errors.append(
                            f"{prefix}: inputs.dependency_revisions.{dependency_id} must be a non-empty string"
                        )

    if "input_revision" in raw and raw.get("input_revision") is not None and (
        not isinstance(raw.get("input_revision"), str) or not raw["input_revision"].strip()
    ):
        errors.append(f"{prefix}: input_revision must be null or a non-empty string")

    for key in ("success_criteria", "deliverables"):
        if key not in raw:
            continue
        values = raw.get(key)
        if not isinstance(values, list):
            errors.append(f"{prefix}: {key} must be a list")
            continue
        for index, value in enumerate(values):
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{prefix}: {key}[{index}] must be a non-empty string")

    if "lease_epoch_counter" in raw:
        counter = raw.get("lease_epoch_counter")
        if isinstance(counter, bool) or not isinstance(counter, int) or counter < 0:
            errors.append(
                f"{prefix}: lease_epoch_counter must be an integer >= 0"
            )

    if "lease" in raw:
        lease = raw.get("lease")
        if not isinstance(lease, dict):
            errors.append(f"{prefix}: lease must be a mapping")
        else:
            lease_id = lease.get("lease_id")
            owner = lease.get("owner_agent_id")
            epoch = lease.get("lease_epoch")
            heartbeat = lease.get("heartbeat_at")
            expires = lease.get("expires_at")
            if lease_id is None:
                if epoch != 0 or any(value is not None for value in (owner, heartbeat, expires)):
                    errors.append(
                        f"{prefix}: lease without lease_id requires epoch 0 and null owner/timestamps"
                    )
            else:
                if not isinstance(lease_id, str) or not lease_id.strip():
                    errors.append(f"{prefix}: lease.lease_id must be a non-empty string")
                if not isinstance(owner, str) or not owner.strip():
                    errors.append(f"{prefix}: lease.owner_agent_id must be a non-empty string")
                elif assignment.agent_id is not None and owner != assignment.agent_id:
                    errors.append(
                        f"{prefix}: lease.owner_agent_id must match agent_id {assignment.agent_id!r}"
                    )
                if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
                    errors.append(f"{prefix}: lease.lease_epoch must be an integer >= 1")
                heartbeat_at = _utc_datetime(heartbeat, f"{prefix}: lease.heartbeat_at", errors)
                expires_at = _utc_datetime(expires, f"{prefix}: lease.expires_at", errors)
                if heartbeat_at is not None and expires_at is not None and expires_at <= heartbeat_at:
                    errors.append(f"{prefix}: lease.expires_at must be after lease.heartbeat_at")

    if "review" in raw:
        review = raw.get("review")
        if not isinstance(review, dict):
            errors.append(f"{prefix}: review must be a mapping")
        else:
            required = review.get("required", False)
            if not isinstance(required, bool):
                errors.append(f"{prefix}: review.required must be a boolean")
            status = review.get("status", "pending" if required is True else "not_required")
            if status not in _REVIEW_STATUSES:
                allowed = ", ".join(sorted(_REVIEW_STATUSES))
                errors.append(f"{prefix}: review.status must be one of: {allowed}")
            result_revision = review.get("result_revision")
            if result_revision is not None and (
                not isinstance(result_revision, str) or not result_revision.strip()
            ):
                errors.append(f"{prefix}: review.result_revision must be null or a non-empty string")
            if required is False and status != "not_required":
                errors.append(f"{prefix}: review.required=false requires status 'not_required'")
            if status in {"approved", "changes_requested"} and result_revision is None:
                errors.append(f"{prefix}: review status {status!r} requires result_revision")

    if "result" in raw and not isinstance(raw.get("result"), dict):
        errors.append(f"{prefix}: result must be a mapping")
    return errors

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


def _load_assignment_path(path: Path, rel_path: str) -> AssignmentRecord | None:
    try:
        data = load_yaml(path)
    except (OSError, yaml.YAMLError) as exc:
        raise ValidationError([f"{rel_path}: YAML parse error: {exc}"]) from exc
    if not data:
        return None
    if not isinstance(data, dict):
        raise ValidationError([f"{rel_path}: assignment record must be a mapping"])
    if data.get("assignment_id") in (None, ""):
        raise ValidationError([f"{rel_path}: missing required field 'assignment_id'"])
    try:
        return AssignmentRecord.from_dict(data)
    except KeyError as exc:
        raise ValidationError([f"{rel_path}: missing required field {exc.args[0]!r}"]) from exc


def load_assignment(root: Path, assignment_id: str) -> AssignmentRecord:
    if not _IDENTITY_ID_RE.fullmatch(str(assignment_id)):
        raise ValidationError([
            "assignment_id must contain only letters, numbers, underscores, or hyphens"
        ])
    path = root / "assignments" / f"{assignment_id}.yaml"
    rel_path = f"assignments/{path.name}"
    if not path.exists():
        raise FileNotFoundError(f"Assignment does not exist: {assignment_id}")
    assignment = _load_assignment_path(path, rel_path)
    if assignment is None:
        raise ValidationError([f"{rel_path}: assignment record must not be empty"])
    if assignment.assignment_id != assignment_id:
        raise ValidationError([
            f"{rel_path}: assignment_id {assignment.assignment_id!r} must match filename {assignment_id!r}"
        ])
    return assignment


def load_assignments(root: Path) -> dict[str, AssignmentRecord]:
    assignment_dir = root / "assignments"
    assignments: dict[str, AssignmentRecord] = {}
    if not assignment_dir.exists():
        return assignments
    for path in sorted(assignment_dir.glob("*.yaml")):
        rel_path = f"assignments/{path.name}"
        assignment = _load_assignment_path(path, rel_path)
        if assignment is None:
            continue
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
