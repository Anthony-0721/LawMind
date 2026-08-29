"""Small shared helpers for repository record serialization."""
from __future__ import annotations

from datetime import datetime
from typing import Any


class RecordDict(dict):
    """A JSON-serializable dict that also exposes keys as attributes.

    This keeps the repository interface consistent as normalized dictionaries
    while providing legacy `.status`-style access for callers that prefer ORM
    objects.
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def coerce_datetime(value: Any) -> datetime | None:
    """Return a datetime, parse an ISO string, or return None for invalid input."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


__all__ = ["RecordDict", "coerce_datetime"]
