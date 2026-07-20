from __future__ import annotations

import re
import secrets


_ID_PART_RE = re.compile(r"[^A-Za-z0-9_-]+")
_MAX_ID_PART_LENGTH = 48
_MAX_KIND_LENGTH = 16
_MAX_RUNTIME_ID_LENGTH = 63


def _id_part(value: str | None, *, fallback: str, limit: int = _MAX_ID_PART_LENGTH) -> str:
    text = _ID_PART_RE.sub("_", str(value or "").strip()).strip("_-")
    return (text or fallback)[:limit]


def generate_runtime_id(
    kind: str,
    *,
    scope_hint: str | None = None,
    slug_hint: str | None = None,
) -> str:
    """Generate a collision-resistant, file-safe id with useful local context."""

    kind_part = _id_part(kind, fallback="entity", limit=_MAX_KIND_LENGTH)
    hints: list[str] = []
    if scope_hint:
        hints.append(_id_part(scope_hint, fallback="scope"))
    if slug_hint:
        hints.append(_id_part(slug_hint, fallback="item"))
    token = secrets.token_hex(6)
    candidate = "_".join([kind_part, *hints, token])
    if len(candidate) <= _MAX_RUNTIME_ID_LENGTH or not hints:
        return candidate

    separators = len(hints) + 1
    hint_budget = (
        _MAX_RUNTIME_ID_LENGTH - len(kind_part) - len(token) - separators
    )
    quota, remainder = divmod(hint_budget, len(hints))
    bounded_hints = [
        hint[: quota + (1 if index < remainder else 0)]
        for index, hint in enumerate(hints)
    ]
    return "_".join([kind_part, *bounded_hints, token])
