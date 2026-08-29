"""Repository for lawyer management and public lawyer recommendations."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.common import RecordDict, coerce_datetime
from db.models import Lawyer

_LAWYER_FIELDS = set(Lawyer.__table__.columns.keys())
_LAWYER_FIELDS.discard("id")


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "是"}
    return bool(value)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _datetime_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _record_to_dict(record: Lawyer) -> Dict[str, Any]:
    return RecordDict({
        "id": record.id,
        "name": record.name,
        "domain": record.domain,
        "specialties": list(record.specialties or []),
        "intro": record.intro,
        "phone": record.phone,
        "wechat": record.wechat,
        "email": record.email,
        "active": bool(record.active),
        "sort_order": record.sort_order,
        "created_at": _datetime_text(record.created_at),
        "updated_at": _datetime_text(record.updated_at),
    })


class LawyerRepository:
    """Persistence and lookup operations for lawyer profiles."""

    def __init__(self, session: Session):
        self.session = session

    # ── Seed / Read ───────────────────────────────────────────────────────

    def seed_lawyers(self, lawyers: Sequence[Mapping[str, Any]]) -> int:
        """Insert missing lawyers by name+domain; returns the number inserted."""
        existing_keys = {
            (str(name).strip(), str(domain).strip())
            for name, domain in self.session.execute(
                select(Lawyer.name, Lawyer.domain)
            ).all()
        }
        seen_keys = set(existing_keys)
        count = 0
        for item in lawyers or []:
            if not isinstance(item, Mapping):
                continue
            fields = self._model_fields(dict(item))
            if not fields.get("name"):
                continue
            fields.setdefault("active", True)
            fields.setdefault("domain", "general")
            fields.setdefault("specialties", [])
            name = str(fields["name"]).strip()
            domain = str(fields["domain"]).strip()
            if not name:
                continue
            fields["name"] = name
            fields["domain"] = domain
            key = (name, domain)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            self.session.add(Lawyer(**fields))
            count += 1
        self.session.commit()
        return count

    def seed(self, lawyers: Sequence[Mapping[str, Any]]) -> int:
        """Alias matching the earlier plan interface."""
        return self.seed_lawyers(lawyers)

    def find_active_by_domain(self, domain: str) -> List[Dict[str, Any]]:
        records = self.session.scalars(
            select(Lawyer)
            .where(Lawyer.domain == str(domain), Lawyer.active.is_(True))
            .order_by(Lawyer.sort_order.asc(), Lawyer.created_at.asc())
        ).all()
        return [_record_to_dict(record) for record in records]

    def recommend(self, domain: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Return up to ``limit`` active lawyers for a domain."""
        return self.find_active_by_domain(domain)[:max(1, int(limit))]

    def list_all(self, active_only: bool = True) -> List[Dict[str, Any]]:
        statement = select(Lawyer).order_by(
            Lawyer.sort_order.asc(),
            Lawyer.domain.asc(),
            Lawyer.created_at.asc(),
        )
        if active_only:
            statement = statement.where(Lawyer.active.is_(True))
        records = self.session.scalars(statement).all()
        return [_record_to_dict(record) for record in records]

    def get_by_id(self, lawyer_id: str) -> Optional[Dict[str, Any]]:
        record = self.session.get(Lawyer, str(lawyer_id))
        return _record_to_dict(record) if record is not None else None

    # ── CRUD ──────────────────────────────────────────────────────────────

    def create(
        self,
        payload: Optional[Mapping[str, Any]] = None,
        **fields: Any,
    ) -> Dict[str, Any]:
        data = dict(payload or {})
        data.update(fields)
        data.setdefault("active", True)
        data.setdefault("domain", "general")
        data.setdefault("specialties", [])
        record = Lawyer(**self._model_fields(data))
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return _record_to_dict(record)

    def update(
        self,
        lawyer_id: str,
        payload: Optional[Mapping[str, Any]] = None,
        **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        record = self.session.get(Lawyer, str(lawyer_id))
        if record is None:
            return None
        data = dict(payload or {})
        data.update(fields)
        for key, value in self._model_fields(data).items():
            setattr(record, key, value)
        self.session.commit()
        self.session.refresh(record)
        return _record_to_dict(record)

    def toggle(
        self,
        lawyer_id: str,
        active: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        record = self.session.get(Lawyer, str(lawyer_id))
        if record is None:
            return None
        record.active = not bool(record.active) if active is None else _as_bool(active)
        self.session.commit()
        self.session.refresh(record)
        return _record_to_dict(record)

    def _model_fields(self, data: Mapping[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key in _LAWYER_FIELDS:
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
            elif key in {"specialties"}:
                value = _as_list(value)
            elif key == "sort_order":
                value = int(value)
            result[key] = value
        return result


__all__ = ["LawyerRepository"]
