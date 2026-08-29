"""Repository for FAQ records managed by staff and synced to ChromaDB later."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.common import RecordDict, coerce_datetime
from db.models import Faq

_FAQ_FIELDS = set(Faq.__table__.columns.keys())
_FAQ_FIELDS.discard("id")


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "是"}
    return bool(value)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _datetime_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _record_to_dict(record: Faq) -> Dict[str, Any]:
    return RecordDict({
        "id": record.id,
        "category": record.category,
        "question": record.question,
        "answer": record.answer,
        "keywords": list(record.keywords or []),
        "source": record.source,
        "active": bool(record.active),
        "sort_order": record.sort_order,
        "version": record.version,
        "sync_status": record.sync_status,
        "sync_error": record.sync_error,
        "last_sync_at": _datetime_text(record.last_sync_at),
        "created_at": _datetime_text(record.created_at),
        "updated_at": _datetime_text(record.updated_at),
    })


class FaqRepository:
    """Persistence operations for FAQ records."""

    def __init__(self, session: Session):
        self.session = session

    # ── Seed / Read ───────────────────────────────────────────────────────

    def seed_faqs(self, faqs: Sequence[Mapping[str, Any]]) -> int:
        """Insert missing FAQs by question; returns the number inserted."""
        existing_questions = {
            str(question).strip()
            for question in self.session.scalars(select(Faq.question)).all()
        }
        seen_questions = set(existing_questions)
        count = 0
        for item in faqs or []:
            if not isinstance(item, Mapping):
                continue
            fields = self._model_fields(dict(item))
            question = str(fields.get("question") or "").strip()
            answer = str(fields.get("answer") or "").strip()
            if not question or not answer:
                continue
            if question in seen_questions:
                continue
            seen_questions.add(question)
            fields["question"] = question
            fields["answer"] = answer
            fields.setdefault("category", "")
            fields.setdefault("keywords", [])
            fields.setdefault("active", True)
            fields.setdefault("source", "law_firm")
            fields.setdefault("sync_status", "pending")
            self.session.add(Faq(**fields))
            count += 1
        self.session.commit()
        return count

    def seed(self, faqs: Sequence[Mapping[str, Any]]) -> int:
        """Alias matching the earlier plan interface."""
        return self.seed_faqs(faqs)

    def list_all(self, active_only: bool = False) -> List[Dict[str, Any]]:
        statement = select(Faq).order_by(
            Faq.sort_order.asc(),
            Faq.category.asc(),
            Faq.created_at.asc(),
        )
        if active_only:
            statement = statement.where(Faq.active.is_(True))
        records = self.session.scalars(statement).all()
        return [_record_to_dict(record) for record in records]

    def get_by_id(self, faq_id: str) -> Optional[Dict[str, Any]]:
        record = self.session.get(Faq, str(faq_id))
        return _record_to_dict(record) if record is not None else None

    def find_by_category(self, category: str) -> List[Dict[str, Any]]:
        records = self.session.scalars(
            select(Faq)
            .where(Faq.category == str(category))
            .order_by(Faq.sort_order.asc(), Faq.created_at.asc())
        ).all()
        return [_record_to_dict(record) for record in records]

    def find_active_by_category(self, category: str) -> List[Dict[str, Any]]:
        records = self.session.scalars(
            select(Faq)
            .where(Faq.category == str(category), Faq.active.is_(True))
            .order_by(Faq.sort_order.asc(), Faq.created_at.asc())
        ).all()
        return [_record_to_dict(record) for record in records]

    # ── Sync support ──────────────────────────────────────────────────────

    def mark_synced(self, faq_id: str, version: int) -> Optional[Dict[str, Any]]:
        """Mark a FAQ as successfully synchronized only at the expected version."""
        record = self.session.get(Faq, str(faq_id))
        if record is None:
            return None
        try:
            current_version = int(record.version or 1)
        except (TypeError, ValueError):
            return None
        if isinstance(version, bool) or not isinstance(version, int):
            return None
        if current_version != version:
            return None
        record.sync_status = "synced"
        record.sync_error = None
        record.last_sync_at = _utcnow()
        record.version = int(version)
        self.session.commit()
        self.session.refresh(record)
        return _record_to_dict(record)

    def mark_sync_failed(
        self,
        faq_id: str,
        error: str,
    ) -> Optional[Dict[str, Any]]:
        """Mark a FAQ synchronization as failed and persist the error text."""
        record = self.session.get(Faq, str(faq_id))
        if record is None:
            return None
        record.sync_status = "failed"
        record.sync_error = str(error)
        record.last_sync_at = _utcnow()
        self.session.commit()
        self.session.refresh(record)
        return _record_to_dict(record)

    # ── CRUD ──────────────────────────────────────────────────────────────

    def create(
        self,
        payload: Optional[Mapping[str, Any]] = None,
        **fields: Any,
    ) -> Dict[str, Any]:
        data = dict(payload or {})
        data.update(fields)
        data.setdefault("category", "")
        data.setdefault("keywords", [])
        data.setdefault("active", True)
        data.setdefault("source", "law_firm")
        data.setdefault("sync_status", "pending")
        record = Faq(**self._model_fields(data))
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return _record_to_dict(record)

    def update(
        self,
        faq_id: str,
        payload: Optional[Mapping[str, Any]] = None,
        **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        record = self.session.get(Faq, str(faq_id))
        if record is None:
            return None
        data = dict(payload or {})
        data.update(fields)
        explicit_sync_status = "sync_status" in data
        explicit_sync_error = "sync_error" in data
        changed = False
        for key, value in self._model_fields(data).items():
            if key == "sync_status" and value is None:
                continue
            if value is None and key in {"sync_error", "last_sync_at"}:
                setattr(record, key, None)
            else:
                setattr(record, key, value)
            changed = True
        if changed:
            record.version = int(record.version or 1) + 1
            if not explicit_sync_status:
                record.sync_status = "pending"
            if not explicit_sync_error:
                record.sync_error = None
        self.session.commit()
        self.session.refresh(record)
        return _record_to_dict(record)

    def toggle(
        self,
        faq_id: str,
        active: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        record = self.session.get(Faq, str(faq_id))
        if record is None:
            return None
        record.active = not bool(record.active) if active is None else _as_bool(active)
        record.version = int(record.version or 1) + 1
        record.sync_status = "pending"
        record.sync_error = None
        self.session.commit()
        self.session.refresh(record)
        return _record_to_dict(record)

    def delete(self, faq_id: str) -> bool:
        record = self.session.get(Faq, str(faq_id))
        if record is None:
            return False
        self.session.delete(record)
        self.session.commit()
        return True

    def _model_fields(self, data: Mapping[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key in _FAQ_FIELDS:
            if key not in data:
                continue
            value = data[key]
            if key in {"created_at", "updated_at", "last_sync_at"}:
                value = coerce_datetime(value)
            if value is None:
                if key in {"created_at", "updated_at"}:
                    continue
                result[key] = None
                continue
            if key in {"active"}:
                value = _as_bool(value)
            elif key in {"keywords"}:
                value = _as_list(value)
            elif key in {"sort_order", "version"}:
                value = int(value)
            result[key] = value
        return result


__all__ = ["FaqRepository"]
