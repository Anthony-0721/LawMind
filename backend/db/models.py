"""SQLAlchemy ORM models for LawMind law-firm consultation data."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uuid_str() -> str:
    """Generate a string UUID suitable for String primary/foreign keys."""
    return str(uuid4())


def utcnow() -> datetime:
    """Return the current UTC timestamp (timezone-aware)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base shared by all LawMind database models."""


class Consultation(Base):
    """One structured consultation/lead record created by the agent or web form."""

    __tablename__ = "consultations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    request_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    contact_name: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(30), default="", nullable=False)
    city: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    preferred_time: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    legal_domain: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    case_stage: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    risk_analysis: Mapped[str] = mapped_column(Text, default="", nullable=False)
    risk_flags: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    facts: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    fact_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    conversation_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    recommended_lawyers: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    recommended_lawyer_ids: Mapped[List[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    source: Mapped[str] = mapped_column(String(32), default="law_agent", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Lawyer(Base):
    """Lawyer profile used by the public recommendation and staff UI."""

    __tablename__ = "lawyers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    domain: Mapped[str] = mapped_column(String(64), default="general", nullable=False)
    specialties: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    intro: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    wechat: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Faq(Base):
    """FAQ record managed by staff and synchronized to the retrieval copy."""

    __tablename__ = "law_faq"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    category: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="law_firm", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sync_status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False
    )
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


__all__ = ["Base", "Consultation", "Lawyer", "Faq", "uuid_str", "utcnow"]
