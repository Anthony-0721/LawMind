"""Database engine/session bootstrap for LawMind.

Local development and tests default to SQLite; production deployments set
DATABASE_URL to a PostgreSQL DSN.
"""
import os

from sqlalchemy import create_engine
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


def init_db() -> None:
    """Create all configured tables for the current database engine."""
    Base.metadata.create_all(bind=engine)


__all__ = ["DATABASE_URL", "engine", "SessionLocal", "init_db"]
