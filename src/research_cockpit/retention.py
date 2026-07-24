from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from research_cockpit.storage import load_yaml


RETENTION_CLASSES = {
    "evidence_critical",
    "portable_review_bundle",
    "final_checkpoint",
    "resume_state",
    "reproducible_output",
    "disposable_cache",
    "deprecated_payload",
}


def require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping")
    return dict(value)


def validate_retention(value: Any, field_name: str = "retention") -> dict[str, Any]:
    data = require_mapping(value, field_name)
    retention_class = data.get("class")
    if retention_class not in (None, "") and str(retention_class) not in RETENTION_CLASSES:
        allowed = ", ".join(sorted(RETENTION_CLASSES))
        raise ValueError(f"Invalid {field_name}.class {retention_class!r}; allowed: {allowed}")
    expires_at = data.get("expires_at")
    if expires_at not in (None, ""):
        if not isinstance(expires_at, str):
            raise ValueError(f"{field_name}.expires_at must be an ISO-8601 timestamp or null")
        try:
            parsed_expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"{field_name}.expires_at must be an ISO-8601 timestamp or null"
            ) from exc
        if parsed_expiry.tzinfo is None:
            raise ValueError(f"{field_name}.expires_at must include a timezone")
    keep_until_decision = data.get("keep_until_decision")
    if keep_until_decision is not None and (
        not isinstance(keep_until_decision, str) or not keep_until_decision.strip()
    ):
        raise ValueError(
            f"{field_name}.keep_until_decision must be a decision id or null"
        )
    return data


def load_mapping_argument(
    *,
    json_text: str | None = None,
    file_path: Path | None = None,
    field_name: str,
    validate_retention_class: bool = False,
) -> dict[str, Any] | None:
    if json_text and file_path:
        raise ValueError(f"Use only one of --{field_name.replace('_', '-')}-json or --{field_name.replace('_', '-')}-file")
    if json_text is None and file_path is None:
        return None
    if json_text is not None:
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as exc:
            file_flag = f"--{field_name.replace('_', '-')}-file"
            raise ValueError(
                f"{field_name} JSON parse error: {exc}. "
                f"For shell-safe structured input, write the mapping to a file and pass {file_flag} <path>."
            ) from exc
    else:
        if file_path is None:
            return None
        if not file_path.exists():
            raise FileNotFoundError(file_path)
        data = load_yaml(file_path)
    if validate_retention_class:
        return validate_retention(data, field_name)
    return require_mapping(data, field_name)
