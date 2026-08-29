"""Repository for consultation/lead records.

Public repository methods intentionally return JSON-serializable dictionaries
instead of SQLAlchemy instances. Callers can pass these directly to FastAPI
response models without managing session lifecycle or detached instances.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.common import RecordDict, coerce_datetime
from db.models import Consultation

_CONSULTATION_FIELDS = set(Consultation.__table__.columns.keys())
_CONSULTATION_FIELDS.discard("id")


class ConsultationStoreError(Exception):
    """Sanitized database failure raised without bound PII."""


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "是", "同意"}
    return bool(value)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _attr(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _datetime_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _contact_value(payload: Mapping[str, Any], key: str, default: str = "") -> str:
    """Read a flattened contact field, falling back to the nested contact dict."""
    direct = payload.get(key)
    if direct not in (None, ""):
        return str(direct).strip()
    contact = payload.get("contact")
    if isinstance(contact, Mapping):
        nested = contact.get(key)
        if nested not in (None, ""):
            return str(nested).strip()
    return default


def _normalize_risk_flags(value: Any) -> List[str]:
    result: List[str] = []
    for item in _as_list(value):
        value_text = getattr(item, "value", item)
        if value_text not in (None, ""):
            result.append(str(value_text))
    return result


def _normalize_lawyers(value: Any) -> List[Dict[str, Any]]:
    lawyers: List[Dict[str, Any]] = []
    for item in _as_list(value):
        if item is None:
            continue
        if isinstance(item, Mapping):
            lawyers.append(dict(item))
        else:
            lawyers.append({"id": str(item), "name": str(item)})
    return lawyers


def _normalize_payload(source: Any, args: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Build a normalized dict from an agent request-like object or a dict."""
    if isinstance(source, Mapping):
        data = dict(source)
    else:
        data = {}
        for key in _CONSULTATION_FIELDS:
            value = _attr(source, key, None)
            if value is not None:
                data[key] = value
        contact = _attr(source, "contact", None)
        if isinstance(contact, Mapping):
            data.setdefault("contact", dict(contact))
        name = _contact_value(data, "name", "") or _attr(source, "name", "")
        phone = _contact_value(data, "phone", "") or _attr(source, "phone", "")
        if name:
            data["name"] = name
        if phone:
            data["phone"] = phone
        entities = _attr(source, "entities", None)
        if isinstance(entities, Mapping) and not data.get("facts"):
            data["facts"] = dict(entities)

    if args:
        data.update(dict(args))

    # Merge both direct and nested contact spellings used by the agent.
    contact = data.get("contact")
    if not isinstance(contact, Mapping):
        contact = {}
    if data.get("name") not in (None, ""):
        data.setdefault("contact_name", data["name"])
    if data.get("phone") not in (None, ""):
        data.setdefault("contact_phone", data["phone"])
    if contact:
        for key, source_key in (
            ("contact_name", "name"),
            ("contact_name", "contact_name"),
            ("contact_phone", "phone"),
            ("contact_phone", "contact_phone"),
            ("city", "city"),
            ("preferred_time", "preferred_time"),
            ("case_stage", "case_stage"),
            ("consent", "consent"),
        ):
            if data.get(key) in (None, "") and contact.get(source_key) not in (None, ""):
                data[key] = contact[source_key]

    if "facts" not in data and "entities" in data:
        data["facts"] = data["entities"]
    if "recommended_lawyer_ids" not in data and "recommended_lawyers" in data:
        ids = [
            str(item.get("id") or item.get("lawyer_id") or "")
            for item in _normalize_lawyers(data.get("recommended_lawyers"))
            if isinstance(item, Mapping)
        ]
        data["recommended_lawyer_ids"] = [item for item in ids if item]

    return data


def _record_to_dict(record: Consultation) -> Dict[str, Any]:
    return RecordDict({
        "id": record.id,
        "request_id": record.request_id,
        "user_id": record.user_id,
        "contact_name": record.contact_name,
        "contact_phone": record.contact_phone,
        "city": record.city,
        "preferred_time": record.preferred_time,
        "consent": bool(record.consent),
        "legal_domain": record.legal_domain,
        "case_stage": record.case_stage,
        "status": record.status,
        "risk_analysis": record.risk_analysis,
        "risk_flags": list(record.risk_flags or []),
        "facts": dict(record.facts or {}),
        "fact_summary": record.fact_summary,
        "conversation_summary": record.conversation_summary,
        "recommended_lawyers": list(record.recommended_lawyers or []),
        "recommended_lawyer_ids": list(record.recommended_lawyer_ids or []),
        "source": record.source,
        "version": record.version,
        "created_at": _datetime_text(record.created_at),
        "updated_at": _datetime_text(record.updated_at),
    })


class ConsultationRepository:
    """Persistence operations for consultation/lead records.

    All read methods return normalized dictionaries. Writes are committed by the
    repository so callers can use the repository as a complete unit of work.
    """

    def __init__(self, session: Session):
        self.session = session

    def _commit(self, error_message: str = "consultation save failed") -> None:
        """Commit or sanitize a database failure. Never logs exception params."""
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise ConsultationStoreError(error_message) from None
        except Exception:
            self.session.rollback()
            raise ConsultationStoreError(error_message) from None

    def _commit_save(self, request_id: str) -> Optional[Consultation]:
        """Commit a save; on a race rollback and re-read the winning row."""
        try:
            self.session.commit()
            return None
        except IntegrityError:
            self.session.rollback()
            existing = self.session.scalar(
                select(Consultation).where(Consultation.request_id == request_id)
            )
            if existing is not None:
                return existing
            raise ConsultationStoreError("consultation save failed") from None
        except Exception:
            self.session.rollback()
            raise ConsultationStoreError("consultation save failed") from None

    # ── Save ──────────────────────────────────────────────────────────────

    def save_from_agent(
        self,
        source: Any,
        args: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist a record from an agent/draft payload or request-like object."""
        payload = _normalize_payload(source, args)
        payload.setdefault("source", "law_agent")
        return self._save(payload)

    def save_public(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Persist a record submitted from the public/front-end form."""
        data = _normalize_payload(payload)
        data.setdefault("source", "public")
        return self._save(data)

    def save_sync(
        self,
        payload: Optional[Mapping[str, Any]] = None,
        **fields: Any,
    ) -> Dict[str, Any]:
        """Compatibility entry point for callers that pass fields as kwargs."""
        data = dict(payload or {})
        data.update(fields)
        return self.save_public(data)

    def _save(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        data = dict(payload)
        request_id = str(data.get("request_id") or "").strip()
        if not request_id:
            request_id = str(uuid4())
            data["request_id"] = request_id

        existing = self.session.scalar(select(Consultation).where(
            Consultation.request_id == request_id
        ))
        if existing is None:
            fields = self._model_fields(data)
            contact_name = str(fields.get("contact_name") or "").strip()
            contact_phone = str(fields.get("contact_phone") or "").strip()
            consent = _as_bool(fields.get("consent", False))
            status = str(fields.get("status") or "").strip()
            if not status:
                status = "PENDING" if contact_name and contact_phone and consent else "DRAFT"
            fields["status"] = status
            fields["consent"] = consent
            record = Consultation(**fields)
            self.session.add(record)
            stored = self._commit_save(request_id)
            if stored is not None:
                return _record_to_dict(stored)
            self.session.refresh(record)
            return _record_to_dict(record)

        for key, value in self._model_fields(data).items():
            if key == "status" and not value:
                continue
            setattr(existing, key, value)
        stored = self._commit_save(request_id)
        if stored is not None:
            return _record_to_dict(stored)
        self.session.refresh(existing)
        return _record_to_dict(existing)

    def _model_fields(self, data: Mapping[str, Any]) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}
        for key in _CONSULTATION_FIELDS:
            if key not in data:
                continue
            value = data[key]
            if key in {"created_at", "updated_at", "last_sync_at"}:
                value = coerce_datetime(value)
            if value is None:
                continue
            if key in {"consent", "active"}:
                value = _as_bool(value)
            elif key == "risk_flags":
                value = _normalize_risk_flags(value)
            elif key == "recommended_lawyer_ids":
                value = _as_list(value)
            elif key == "recommended_lawyers":
                value = _normalize_lawyers(value)
            elif key == "risk_analysis" or key in {"fact_summary", "conversation_summary"}:
                value = str(value)
            fields[key] = value
        return fields

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        record = self.session.get(Consultation, str(record_id))
        return _record_to_dict(record) if record is not None else None

    def get_by_request_id(self, request_id: str) -> Optional[Dict[str, Any]]:
        record = self.session.scalar(select(Consultation).where(
            Consultation.request_id == str(request_id)
        ))
        return _record_to_dict(record) if record is not None else None

    def list_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        safe_limit = max(1, int(limit))
        records = self.session.scalars(
            select(Consultation)
            .order_by(Consultation.created_at.desc(), Consultation.id.desc())
            .limit(safe_limit)
        ).all()
        return [_record_to_dict(record) for record in records]

    # ── Update / Delete ───────────────────────────────────────────────────

    def update_status(self, record_id: str, status: str) -> Optional[Dict[str, Any]]:
        record = self.session.get(Consultation, str(record_id))
        if record is None:
            return None
        record.status = str(status)
        self._commit("consultation update failed")
        self.session.refresh(record)
        return _record_to_dict(record)

    def delete(self, record_id: str) -> bool:
        record = self.session.get(Consultation, str(record_id))
        if record is None:
            return False
        self.session.delete(record)
        self._commit("consultation delete failed")
        return True


__all__ = ["ConsultationRepository", "ConsultationStoreError"]
