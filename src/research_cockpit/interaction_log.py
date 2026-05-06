from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import re
import yaml

from research_cockpit.storage import load_yaml, save_yaml


class InteractionLogError(ValueError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _warning(message: str) -> str:
    return f"graph/interaction_log.yaml: {message}"


def load_interaction_log(root: Path, *, strict: bool = False) -> dict[str, Any]:
    path = root / "graph" / "interaction_log.yaml"
    warnings: list[str] = []
    if not path.exists():
        return {"events": [], "warnings": warnings}
    try:
        data = load_yaml(path)
    except yaml.YAMLError as exc:
        message = _warning(f"YAML parse error: {exc}")
        if strict:
            raise InteractionLogError(message) from exc
        return {"events": [], "warnings": [message]}
    if not isinstance(data, dict):
        message = _warning("top-level document must be a mapping")
        if strict:
            raise InteractionLogError(message)
        return {"events": [], "warnings": [message]}

    events = data.get("events", [])
    if events is None:
        events = []
    if not isinstance(events, list):
        message = _warning("events must be a list")
        if strict:
            raise InteractionLogError(message)
        return {"events": [], "warnings": [message]}

    valid_events: list[dict[str, Any]] = []
    for index, event in enumerate(events, start=1):
        if isinstance(event, dict):
            valid_events.append(event)
            continue
        message = _warning(f"events[{index}] must be a mapping; got {type(event).__name__}")
        if strict:
            raise InteractionLogError(message)
        warnings.append(message)
    return {"events": valid_events, "warnings": warnings}


def validate_interaction_log(root: Path) -> list[str]:
    try:
        load_interaction_log(root, strict=True)
    except InteractionLogError as exc:
        return [str(exc)]
    return []


def interaction_log_warnings(root: Path) -> list[str]:
    return list(load_interaction_log(root).get("warnings", []))


def append_interaction_log(
    root: Path,
    *,
    kind: str,
    actor: str = "researcher",
    node_id: str | None = None,
    command: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    created_at = utc_timestamp()
    raw_id = "_".join(str(part) for part in (created_at, kind, node_id or "event") if part)
    event_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_id)
    event: dict[str, Any] = {
        "id": event_id,
        "kind": str(kind),
        "actor": str(actor),
        "created_at": created_at,
    }
    if node_id:
        event["node_id"] = str(node_id)
    if command:
        event["command"] = str(command)
    if before:
        event["before"] = before
    if after:
        event["after"] = after
    if extra:
        event.update(extra)

    log = load_interaction_log(root, strict=True)
    events = list(log.get("events", []))
    events.append(event)
    save_yaml(root / "graph" / "interaction_log.yaml", {"events": events})
    return event


def recent_interactions(root: Path, limit: int = 5) -> list[dict[str, Any]]:
    events = load_interaction_log(root).get("events", [])
    return list(reversed(events[-limit:]))
