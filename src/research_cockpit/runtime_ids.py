from __future__ import annotations

import re
import secrets


_ID_PART_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _id_part(value: str | None, *, fallback: str) -> str:
    text = _ID_PART_RE.sub("_", str(value or "").strip()).strip("_-")
    return (text or fallback)[:48]


def generate_runtime_id(
    kind: str,
    *,
    scope_hint: str | None = None,
    slug_hint: str | None = None,
) -> str:
    """Generate a collision-resistant, file-safe id with useful local context."""

    parts = [_id_part(kind, fallback="entity")]
    if scope_hint:
        parts.append(_id_part(scope_hint, fallback="scope"))
    if slug_hint:
        parts.append(_id_part(slug_hint, fallback="item"))
    parts.append(secrets.token_hex(6))
    return "_".join(parts)
