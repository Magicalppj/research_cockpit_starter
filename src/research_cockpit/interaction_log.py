from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import re

from research_cockpit.storage import load_yaml, save_yaml


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_interaction_log(root: Path) -> dict[str, Any]:
    data = load_yaml(root / "graph" / "interaction_log.yaml")
    events = data.get("events", [])
    if not isinstance(events, list):
        events = []
    return {"events": events}


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

    log = load_interaction_log(root)
    log["events"].append(event)
    save_yaml(root / "graph" / "interaction_log.yaml", log)
    return event


def recent_interactions(root: Path, limit: int = 5) -> list[dict[str, Any]]:
    events = load_interaction_log(root).get("events", [])
    return list(reversed(events[-limit:]))
