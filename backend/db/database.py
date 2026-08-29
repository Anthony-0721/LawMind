"""Database engine/session bootstrap for LawMind.

Local development and tests default to SQLite; production deployments set
DATABASE_URL to a PostgreSQL DSN.
"""
import os

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


def _ensure_conversation_column() -> None:
    """Add conversation_id to pre-existing consultation tables when missing."""
    try:
        inspector = inspect(engine)
        if "consultations" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("consultations")}
        if "conversation_id" in columns:
            return
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE consultations "
                "ADD COLUMN conversation_id VARCHAR(128) NOT NULL DEFAULT ''"
            ))
    except Exception:
        # Legacy databases without the column should not block startup; the
        # ORM create_all path will handle fresh databases.
        return


def init_db() -> None:
    """Create all configured tables for the current database engine."""
    Base.metadata.create_all(bind=engine)
    _ensure_conversation_column()


__all__ = ["DATABASE_URL", "engine", "SessionLocal", "init_db"]
