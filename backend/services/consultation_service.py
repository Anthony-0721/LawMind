"""Consultation persistence and validation service with request-scoped sessions."""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Mapping, Optional
from uuid import uuid4

from db.consultation_repository import (
    ConsultationRepository,
    ConsultationStoreError,
)

_CHINESE_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
_VALID_STATUSES = frozenset({"PENDING", "CONTACTED", "BOOKED", "CLOSED"})
_TRUE_TOKENS = frozenset({"1", "true", "yes", "是", "同意", "愿意"})


class ConsultationServiceError(ConsultationStoreError):
    """Sanitized service failure shared by database-bound operations."""


class ConsultationValidationError(ConsultationServiceError):
    """Raised when a consultation payload, consent, or status is invalid."""


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_TOKENS:
            return True
        return False
    return bool(value)


def _contact_value(payload: Mapping[str, Any], key: str) -> str:
    direct = payload.get(key)
    if direct not in (None, ""):
        return str(direct).strip()
    contact = payload.get("contact")
    if isinstance(contact, Mapping):
        nested = contact.get(key)
        if nested not in (None, ""):
            return str(nested).strip()
    return ""


def _normalize_contact(payload: Mapping[str, Any]) -> Dict[str, str]:
    name = _contact_value(payload, "name") or _contact_value(payload, "contact_name")
    phone = _contact_value(payload, "phone") or _contact_value(payload, "contact_phone")
    return {"name": name, "phone": phone}


def _contact_valid(payload: Mapping[str, Any]) -> bool:
    contact = _normalize_contact(payload)
    return bool(contact["name"]) and bool(
        _CHINESE_MOBILE_RE.fullmatch(contact["phone"])
    )


def _consent(payload: Mapping[str, Any]) -> bool:
    direct = payload.get("consent")
    if direct not in (None, ""):
        return _as_bool(direct)
    contact = payload.get("contact")
    if isinstance(contact, Mapping) and contact.get("consent") not in (None, ""):
        return _as_bool(contact.get("consent"))
    return False


def _payload_dict(source: Any) -> Dict[str, Any]:
    """Convert a mapping or request-like object to a normalized dictionary."""
    if isinstance(source, Mapping):
        return dict(source)
    data: Dict[str, Any] = {}
    for key in (
        "request_id",
        "conversation_id",
        "user_id",
        "contact_name",
        "contact_phone",
        "name",
        "phone",
        "city",
        "preferred_time",
        "consent",
        "legal_domain",
        "case_stage",
        "risk_flags",
        "facts",
        "source",
    ):
        value = getattr(source, key, None)
        if value is not None:
            data[key] = value
    contact = getattr(source, "contact", None)
    if contact is not None:
        data["contact"] = contact
    entities = getattr(source, "entities", None)
    if entities and not data.get("facts"):
        data["facts"] = entities
    return data


def _draft_record(payload: Mapping[str, Any], source: str) -> Dict[str, Any]:
    contact = _normalize_contact(payload)
    result = dict(payload)
    result.setdefault("request_id", str(uuid4()))
    result["contact_name"] = contact["name"]
    result["contact_phone"] = contact["phone"]
    result["consent"] = _consent(payload)
    result["status"] = "DRAFT"
    result.setdefault("source", source)
    return result


class ConsultationService:
    """Business rules and safe delegation for consultation/lead records."""

    def __init__(self, session_factory: Callable[[], Any]):
        self.session_factory = session_factory

    def _run(
        self,
        operation: Callable[[ConsultationRepository], Any],
        error_message: str,
    ) -> Any:
        try:
            with self.session_factory() as session:
                return operation(ConsultationRepository(session))
        except ConsultationValidationError:
            raise
        except Exception:
            raise ConsultationServiceError(error_message) from None

    def save_from_agent(
        self,
        payload: Any,
        args: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist complete agent records; return a DRAFT for incomplete ones."""
        data = _payload_dict(payload)
        if args:
            data.update(dict(args))
        data["source"] = "law_agent"

        def save(repository: ConsultationRepository) -> Dict[str, Any]:
            if not _contact_valid(data) or not _consent(data):
                return {
                    "success": False,
                    "persisted": False,
                    "status": "DRAFT",
                    "error": "consultation_incomplete",
                }
            data["status"] = "PENDING"
            return repository.save_from_agent(data)

        return self._run(save, "consultation save failed")

    def save_public(
        self,
        payload: Mapping[str, Any],
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist only publicly submitted records with valid contact/consent."""
        data = dict(payload or {})
        data["source"] = source or data.get("source") or "public"

        def save(repository: ConsultationRepository) -> Dict[str, Any]:
            if not _contact_valid(data) or not _consent(data):
                return {
                    "success": False,
                    "persisted": False,
                    "status": "DRAFT",
                    "error": "consultation_incomplete",
                }
            data["status"] = "PENDING"
            return repository.save_public(data)

        return self._run(save, "consultation save failed")

    def save_sync(
        self,
        payload: Optional[Mapping[str, Any]] = None,
        **fields: Any,
    ) -> Dict[str, Any]:
        """Compatibility alias for callers using kwargs."""
        data = dict(payload or {})
        data.update(fields)
        return self.save_public(data)

    def get_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        return self._run(
            lambda repository: repository.get_by_id(str(record_id)),
            "consultation read failed",
        )

    def get_by_request_id(self, request_id: str) -> Optional[Dict[str, Any]]:
        return self._run(
            lambda repository: repository.get_by_request_id(str(request_id)),
            "consultation read failed",
        )

    def get_by_conversation_id(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        return self._run(
            lambda repository: repository.get_by_conversation_id(str(conversation_id)),
            "consultation read failed",
        )

    def list_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._run(
            lambda repository: repository.list_recent(limit),
            "consultation read failed",
        )

    def update_status(
        self,
        record_id: str,
        status: str,
    ) -> Optional[Dict[str, Any]]:
        raw_status = str(status or "")

        def apply(repository: ConsultationRepository) -> Optional[Dict[str, Any]]:
            normalized = raw_status.strip().upper()
            if normalized not in _VALID_STATUSES:
                raise ConsultationValidationError("invalid consultation status")
            return repository.update_status(str(record_id), normalized)

        return self._run(apply, "consultation update failed")

    def delete(self, record_id: str) -> bool:
        return self._run(
            lambda repository: repository.delete(str(record_id)),
            "consultation delete failed",
        )


__all__ = [
    "ConsultationService",
    "ConsultationServiceError",
    "ConsultationValidationError",
]
