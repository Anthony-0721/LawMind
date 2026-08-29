"""Stable session identity helpers shared by API and database migration code.

``LAWMIND_SESSION_SECRET`` should be configured in production. When unset, this
module uses one process-random secret so no predictable shared fallback exists.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from typing import Optional

logger = logging.getLogger(__name__)

_process_secret: Optional[str] = None
_warned = False


def get_session_secret() -> str:
    """Return the configured secret or a single process-random fallback."""
    global _process_secret, _warned
    value = os.getenv("LAWMIND_SESSION_SECRET", "")
    if value.strip():
        return value.strip()
    if _process_secret is None:
        _process_secret = secrets.token_urlsafe(32)
        if not _warned:
            _warned = True
            logger.warning(
                "LAWMIND_SESSION_SECRET is not set; using a process-random "
                "session secret. Set it for stable identities across restarts."
            )
    return _process_secret


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
