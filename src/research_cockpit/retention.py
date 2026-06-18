from __future__ import annotations

import json
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
            raise ValueError(f"{field_name} JSON parse error: {exc}") from exc
    else:
        if file_path is None:
            return None
        if not file_path.exists():
            raise FileNotFoundError(file_path)
        data = load_yaml(file_path)
    if validate_retention_class:
        return validate_retention(data, field_name)
    return require_mapping(data, field_name)
