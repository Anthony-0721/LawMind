"""Database engine/session bootstrap for LawMind.

Local development and tests default to SQLite; production deployments set
DATABASE_URL to a PostgreSQL DSN.
"""
import logging
import os
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from db.models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./lawmind.db")

_connect_args = (
    {"check_same_thread": False}
    if DATABASE_URL.startswith("sqlite")
    else {}
)

engine = create_engine(
    DATABASE_URL,
    future=True,
    pool_pre_ping=True,
    connect_args=_connect_args,
)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def _ensure_consultation_columns() -> None:
    """Add modern consultation ownership columns when missing."""
    try:
        inspector = inspect(engine)
        if "consultations" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("consultations")}
        with engine.begin() as connection:
            if "conversation_id" not in columns:
                connection.execute(text(
                    "ALTER TABLE consultations "
                    "ADD COLUMN conversation_id VARCHAR(128) NOT NULL DEFAULT ''"
                ))
            if "session_token_hash" not in columns:
                connection.execute(text(
                    "ALTER TABLE consultations "
                    "ADD COLUMN session_token_hash VARCHAR(64) NOT NULL DEFAULT ''"
                ))
            if "session_token_version" not in columns:
                connection.execute(text(
                    "ALTER TABLE consultations "
                    "ADD COLUMN session_token_version INTEGER NOT NULL DEFAULT 1"
                ))
    except Exception:
        # Legacy databases without the columns should not block startup; the
        # ORM create_all path will handle fresh databases.
        return


def backfill_session_token_hashes(target_engine: Any = None) -> int:
    """Backfill empty session hashes for legacy consultation rows, idempotently."""
    from services.session_identity import (
        get_session_secret,
        hash_session_token,
        make_session_token,
    )

    # Fail startup with the required-secret contract instead of silently
    # skipping the migration or inventing an unstable fallback.
    get_session_secret()

    target = target_engine or engine
    count = 0
    try:
        inspector = inspect(target)
        if "consultations" not in inspector.get_table_names():
            return 0
        columns = {column["name"] for column in inspector.get_columns("consultations")}
        if "session_token_hash" not in columns or "conversation_id" not in columns:
            return 0
        with target.begin() as connection:
            rows = connection.execute(text(
                "SELECT id, conversation_id FROM consultations "
                "WHERE session_token_hash = '' OR session_token_hash IS NULL"
            )).fetchall()
            for row in rows:
                conversation_id = str(row[1] or "")
                token = make_session_token(conversation_id)
                hashed = hash_session_token(token)
                connection.execute(
                    text(
                        "UPDATE consultations SET session_token_hash = :hash "
                        "WHERE id = :id"
                    ),
                    {"hash": hashed, "id": row[0]},
                )
                count += 1
    except Exception:
        logging.getLogger(__name__).warning(
            "Session token hash backfill skipped", exc_info=True
        )
    return count


_ensure_conversation_column = _ensure_consultation_columns


def init_db() -> None:
    """Create all configured tables and run idempotent ownership migration."""
    Base.metadata.create_all(bind=engine)
    _ensure_consultation_columns()
    backfill_session_token_hashes()


__all__ = [
    "DATABASE_URL",
    "backfill_session_token_hashes",
    "engine",
    "init_db",
    "SessionLocal",
]
