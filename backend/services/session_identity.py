"""Stable session identity helpers shared by API and database migration code.

``LAWMIND_SESSION_SECRET`` is required at runtime. No random or shared
fallback is used; deployments must configure a stable secret.
"""
from __future__ import annotations

import hashlib
import os


def get_session_secret() -> str:
    """Return the required stable session secret.

    Raises RuntimeError when ``LAWMIND_SESSION_SECRET`` is not configured.
    """
    value = os.getenv("LAWMIND_SESSION_SECRET", "")
    if not value.strip():
        raise RuntimeError("LAWMIND_SESSION_SECRET is required")
    return value.strip()


def derive_user_id(conversation_id: str) -> str:
    """Derive a stable internal user id from the server secret + conversation."""
    return hashlib.sha256(
        (get_session_secret() + str(conversation_id or "")).encode("utf-8")
    ).hexdigest()[:16]


def make_session_token(conversation_id: str) -> str:
    """Return the deterministic server-issued token for a conversation."""
    return hashlib.sha256(
        (get_session_secret() + ":session:" + str(conversation_id or "")).encode("utf-8")
    ).hexdigest()[:32]


def hash_session_token(session_token: str) -> str:
    """Hash a session token before persistence."""
    return hashlib.sha256(
        (get_session_secret() + ":hash:" + str(session_token or "")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "derive_user_id",
    "get_session_secret",
    "hash_session_token",
    "make_session_token",
]
