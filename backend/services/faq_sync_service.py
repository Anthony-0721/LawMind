"""Synchronize FAQ records from PostgreSQL into the ChromaDB retrieval copy.

The PostgreSQL Faq table remains the source of truth. ChromaDB stores a
retrieval-only copy so FAQ CRUD operations can delete the previous vector and
add the latest active version without leaving stale records behind.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+86[\s\-]*1[3-9](?:[\s\-]*\d){9}|1[3-9](?:[\s\-]*\d){9})(?!\d)"
)
_MASKED_PHONE_RE = re.compile(r"(?<!\d)(?:\+86[\s\-]*1[3-9]\d{1}\*{2,4}\d{4}|1[3-9]\d{1}\*{2,4}\d{4})(?!\d)")
_LANDLINE_PHONE_RE = re.compile(
    r"(?<!\d)0\d{2,3}[\s\-]?\d{7,8}(?:\s*[-—]?\s*\d{1,6})?(?!\d)"
)
_NAME_AFTER_INDICATOR_RE = re.compile(
    r"((?:姓名|客户|联系人)(?:\s*[:：])?|(?:失败|错误)\s*[:：])(\s*)([\u4e00-\u9fff]{2,4})(?=\s|[,，。:：]|$)"
)
_NAME_BEFORE_PLACEHOLDER_RE = re.compile(
    r"[\u4e00-\u9fff]{2,4}(?=[\s]*<(?:phone|email|id)>)"
)

_VALID_FAQ_CATEGORIES = frozenset({
    "dangerous_driving",
    "criminal",
    "criminal_defense",
    "labor_dispute",
    "marriage_family",
    "contract_dispute",
    "traffic_accident",
    "civil_loan",
    "service",
})


def _record_value(record: Any, key: str, default: Any = None) -> Any:
    """Read a key from a RecordDict/mapping or from an attribute-style object."""
    if record is None:
        return default
    if isinstance(record, Mapping):
        return record.get(key, default)
    try:
        return getattr(record, key, default)
    except (AttributeError, TypeError):
        return default


def _record_id(record: Any) -> str:
    return str(_record_value(record, "id", "") or "").strip()


class _FaqValidationError(ValueError):
    """Validation failure with a cleanup policy for stale Chroma vectors."""

    def __init__(self, message: str, *, cleanup: bool = True):
        super().__init__(message)
        self.cleanup = cleanup


def _record_version(record: Any) -> int:
    """Return a strictly valid positive version or raise ValidationError."""
    raw = _record_value(record, "version", None)
    if raw is None:
        raise _FaqValidationError("faq record version is required", cleanup=False)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _FaqValidationError(
            "faq version must be a strict positive integer", cleanup=False
        )
    if raw <= 0:
        raise _FaqValidationError(
            "faq version must be a positive integer", cleanup=False
        )
    return raw


def _record_version_safe(record: Any) -> int:
    try:
        return _record_version(record)
    except _FaqValidationError:
        return 0


def _utcnow_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "是"}
    return bool(value)


def _normalize_faq(record: Any) -> Dict[str, Any]:
    """Return a validated, normalized FAQ dictionary.

    Raises ValueError when the record cannot be synchronized safely.
    """
    faq_id = _record_id(record)
    question = str(_record_value(record, "question", "") or "").strip()
    answer = str(_record_value(record, "answer", "") or "").strip()
    if not faq_id:
        raise _FaqValidationError("faq record id is required")
    if not question:
        raise _FaqValidationError("faq question is required")
    if not answer:
        raise _FaqValidationError("faq answer is required")

    keywords = _as_list(_record_value(record, "keywords", []))
    version = _record_version(record)
    return {
        "id": faq_id,
        "category": str(_record_value(record, "category", "") or "").strip(),
        "question": question,
        "answer": answer,
        "keywords": keywords,
        "active": _as_bool(_record_value(record, "active", True)),
        "version": version,
    }


def _sanitize_error(error: Exception, faq: Optional[Mapping[str, Any]]) -> str:
    """Strip FAQ content, contact details, and other PII from exception text."""
    faq = faq or {}
    raw = str(error).strip() or error.__class__.__name__
    sensitive_values = [
        faq.get("question"),
        faq.get("answer"),
        *faq.get("keywords", []),
    ]
    for value in sensitive_values:
        text = str(value or "").strip()
        if len(text) >= 4:
            raw = raw.replace(text, "<redacted>")
    raw = _PHONE_RE.sub("<phone>", raw)
    raw = _MASKED_PHONE_RE.sub("<phone>", raw)
    raw = _LANDLINE_PHONE_RE.sub("<phone>", raw)
    raw = _EMAIL_RE.sub("<email>", raw)
    raw = _ID_CARD_RE.sub("<id>", raw)
    raw = _NAME_BEFORE_PLACEHOLDER_RE.sub("**", raw)
    raw = _NAME_AFTER_INDICATOR_RE.sub(r"\1\2<name>", raw)
    sanitized = re.sub(r"\s+", " ", raw).strip()[:500]
    return sanitized or "faq sync failed"


def _apply_record_state(record: Any, **updates: Any) -> None:
    """Mutate the input record when it supports dictionary/attribute writes."""
    if record is None:
        return
    try:
        for key, value in updates.items():
            record[key] = value
        return
    except (TypeError, AttributeError, KeyError):
        pass
    try:
        for key, value in updates.items():
            setattr(record, key, value)
    except (AttributeError, TypeError):
        pass


def _validate_crud_payload(payload: Any, current: Optional[Mapping[str, Any]] = None) -> bool:
    """Validate a FAQ write payload before mutating the repository."""
    if not isinstance(payload, Mapping):
        return False
    data = dict(payload)
    merged = dict(current or {})
    merged.update(data)
    question = str(merged.get("question") or "").strip()
    answer = str(merged.get("answer") or "").strip()
    category = str(merged.get("category") or "").strip()
    if not question or not answer:
        return False
    if category not in _VALID_FAQ_CATEGORIES:
        return False
    if "version" in data and current is not None:
        if not isinstance(data["version"], int) or isinstance(data["version"], bool) or data["version"] <= 0:
            return False
    elif current is not None:
        try:
            _record_version(merged)
        except _FaqValidationError:
            return False
    elif "version" in data:
        try:
            _record_version(data)
        except _FaqValidationError:
            return False
    return True


class FaqSyncService:
    """Persist FAQ synchronization state after updating the ChromaKB copy.

    Constructor: ``FaqSyncService(faq_repository, knowledge_base)``.

    Return contract
    ----------------
    ``sync()`` returns a dictionary and does not raise for record validation,
    Chroma, or repository runtime failures::

        {
            "success": bool,
            "faq_id": str,
            "version": int,
            "sync_status": "synced" | "failed",
            "document_id": str | None,
            "error": str | None,
            "added": bool,
        }

    ``delete()`` returns::

        {"success": True, "faq_id": str, "action": "delete"}

    or on failure::

        {"success": False, "error": "faq_sync_delete_failed"}
    """

    def __init__(self, faq_repository: Any, knowledge_base: Any):
        self.faq_repository = faq_repository
        self.knowledge_base = knowledge_base
        # Backward-compatible alias used by older call sites.
        self.kb = knowledge_base

    def delete(self, faq_id: Any) -> Dict[str, Any]:
        """Remove all Chroma vectors for a FAQ id without re-adding them."""
        normalized_id = str(faq_id or "").strip()
        if not normalized_id:
            return {"success": False, "error": "faq_sync_delete_failed"}
        try:
            self.knowledge_base.delete_by_metadata({"faq_id": normalized_id})
        except Exception:
            return {"success": False, "error": "faq_sync_delete_failed"}
        return {"success": True, "faq_id": normalized_id, "action": "delete"}

    def sync_delete(self, faq_id: Any) -> Dict[str, Any]:
        """Alias for delete used by the production CRUD flow."""
        return self.delete(faq_id)

    def _failure_result(
        self,
        faq_record: Any,
        faq_id: str,
        version: int,
        error: str,
        *,
        added: bool = False,
        cleanup: bool = True,
        persist_failure: bool = True,
    ) -> Dict[str, Any]:
        """Optionally clean vectors, persist failure, and update mutable input."""
        if faq_id and cleanup:
            try:
                self.knowledge_base.delete_by_metadata({"faq_id": faq_id})
            except Exception:
                pass
        failed_state = None
        if persist_failure and faq_id:
            try:
                failed_state = self.faq_repository.mark_sync_failed(faq_id, error)
            except Exception:
                pass
        if isinstance(failed_state, Mapping):
            failed_updates = {
                "sync_status": failed_state.get("sync_status", "failed"),
                "sync_error": failed_state.get("sync_error", error),
                "version": failed_state.get("version", version),
                "last_sync_at": failed_state.get("last_sync_at") or _utcnow_text(),
            }
        else:
            failed_updates = {
                "sync_status": "failed",
                "sync_error": error,
                "version": version,
                "last_sync_at": _utcnow_text(),
            }
        _apply_record_state(faq_record, **failed_updates)
        return {
            "success": False,
            "faq_id": faq_id,
            "version": version,
            "sync_status": "failed",
            "document_id": f"faq:{faq_id}" if faq_id else None,
            "error": error,
            "added": added,
        }

    def _preflight(self, faq: Mapping[str, Any]) -> tuple[bool, Optional[str]]:
        """Check the authoritative DB version before mutating Chroma."""
        getter = getattr(self.faq_repository, "get_by_id", None)
        if getter is None:
            return True, None
        try:
            current = getter(faq["id"])
        except Exception as exc:
            return False, _sanitize_error(exc, faq)
        if not isinstance(current, Mapping):
            return False, "faq_sync_record_not_found"
        try:
            current_version = _record_version(current)
        except _FaqValidationError:
            return False, "faq_sync_record_version_invalid"
        if current_version != faq["version"]:
            return False, "faq_sync_version_mismatch"
        return True, None

    def sync(self, faq_record: Any) -> Dict[str, Any]:
        """Preflight version, then delete/add the FAQ and update sync state."""
        try:
            faq = _normalize_faq(faq_record)
        except _FaqValidationError as exc:
            faq_id = _record_id(faq_record)
            version = _record_version_safe(faq_record)
            error = _sanitize_error(exc, {"id": faq_id})
            return self._failure_result(
                faq_record,
                faq_id,
                version,
                error,
                cleanup=exc.cleanup,
                persist_failure=exc.cleanup,
            )
        except Exception as exc:
            faq_id = _record_id(faq_record)
            version = _record_version_safe(faq_record)
            error = _sanitize_error(exc, {"id": faq_id})
            return self._failure_result(faq_record, faq_id, version, error)

        preflight_ok, preflight_error = self._preflight(faq)
        if not preflight_ok:
            return self._failure_result(
                faq_record,
                faq["id"],
                faq["version"],
                preflight_error or "faq_sync_preflight_failed",
                cleanup=False,
                persist_failure=False,
            )

        faq_id = faq["id"]
        version = faq["version"]
        document_id = f"faq:{faq_id}"
        added = False

        try:
            self.knowledge_base.delete_by_metadata({"faq_id": faq_id})
            if faq["active"]:
                metadata: Dict[str, Any] = {
                    "faq_id": faq_id,
                    "category": faq["category"],
                    "question": faq["question"],
                    "keywords": faq["keywords"],
                    "active": faq["active"],
                    "source": "law_firm",
                    "doc_type": "faq",
                    "version": version,
                }
                keyword_text = " ".join(faq["keywords"])
                content_parts = [faq["question"], faq["answer"]]
                if keyword_text:
                    content_parts.append(keyword_text)
                document = "\n".join(content_parts)
                self.knowledge_base.add_documents(
                    [{
                        "id": document_id,
                        "title": faq["question"],
                        "content": document,
                        "metadata": metadata,
                    }],
                    metadatas=[metadata],
                )
                added = True
            else:
                return self._inactive_failure(faq_record, faq_id, version)
            synced = self.faq_repository.mark_synced(faq_id, version)
            if synced is None:
                error = "faq_sync_mark_synced_failed"
                return self._failure_result(
                    faq_record,
                    faq_id,
                    version,
                    error,
                    added=added,
                )
        except Exception as exc:
            error = _sanitize_error(exc, faq)
            return self._failure_result(faq_record, faq_id, version, error, added=added)

        state: Dict[str, Any] = {"version": version}
        if isinstance(synced, Mapping):
            state = {
                "sync_status": synced.get("sync_status", "synced"),
                "sync_error": synced.get("sync_error"),
                "version": synced.get("version", version),
                "last_sync_at": synced.get("last_sync_at") or _utcnow_text(),
            }
        else:
            state = {
                "sync_status": "synced",
                "sync_error": None,
                "version": version,
                "last_sync_at": _utcnow_text(),
            }
        _apply_record_state(faq_record, **state)
        return {
            "success": True,
            "faq_id": faq_id,
            "version": version,
            "sync_status": "synced",
            "document_id": document_id,
            "error": None,
            "added": added,
        }

    def create_record(self, payload: Any) -> Dict[str, Any]:
        """Validate, create a FAQ through the repository, and synchronize it."""
        if not _validate_crud_payload(payload):
            return {"success": False, "error": "invalid_faq_payload"}
        try:
            record = self.faq_repository.create(payload)
        except Exception as exc:
            error = _sanitize_error(exc, payload if isinstance(payload, Mapping) else {})
            return {"success": False, "error": error}
        if not isinstance(record, Mapping):
            return {"success": False, "error": "faq_create_failed"}
        return self.sync(record)

    def update_record(self, faq_id: Any, payload: Any) -> Dict[str, Any]:
        """Validate, update a FAQ through the repository, and synchronize it."""
        normalized_id = str(faq_id)
        try:
            getter = getattr(self.faq_repository, "get_by_id", None)
            current = getter(normalized_id) if getter is not None else None
            if current is not None and not _validate_crud_payload(payload, current):
                return {"success": False, "faq_id": normalized_id, "error": "invalid_faq_payload"}
            if current is None:
                return {"success": False, "faq_id": normalized_id, "error": "faq_update_failed"}
            record = self.faq_repository.update(normalized_id, payload)
        except Exception:
            return {"success": False, "error": "faq_update_failed"}
        if not isinstance(record, Mapping):
            return {"success": False, "faq_id": normalized_id, "error": "faq_update_failed"}
        return self.sync(record)

    def toggle_record(self, faq_id: Any, active: Optional[bool] = None) -> Dict[str, Any]:
        """Toggle a FAQ; inactive records only remove retrieval vectors."""
        try:
            record = self.faq_repository.toggle(str(faq_id), active)
        except Exception as exc:
            error = _sanitize_error(exc, {"id": str(faq_id)})
            return {"success": False, "error": error}
        if not isinstance(record, Mapping):
            return {"success": False, "faq_id": str(faq_id), "error": "faq_toggle_failed"}
        if not bool(record.get("active", False)):
            return self.sync_delete(str(record.get("id") or faq_id))
        return self.sync(record)

    def delete_record(self, faq_id: Any) -> Dict[str, Any]:
        """Best-effort DB delete, then always remove every retrieval vector."""
        normalized_id = str(faq_id or "").strip()
        db_delete_failed = False
        try:
            deleted = self.faq_repository.delete(normalized_id)
            if deleted is False:
                db_delete_failed = True
        except Exception:
            db_delete_failed = True

        cleanup_result = self.sync_delete(normalized_id)
        if cleanup_result.get("success") is not True:
            return cleanup_result
        if db_delete_failed:
            return {
                "success": False,
                "faq_id": normalized_id,
                "error": "faq_db_delete_failed",
            }
        return cleanup_result

    def _inactive_failure(self, faq_record: Any, faq_id: str, version: int) -> Dict[str, Any]:
        """Inactive records remove stale vectors but are reported as not added."""
        error = "faq_inactive"
        failed_state = None
        try:
            failed_state = self.faq_repository.mark_sync_failed(faq_id, error)
        except Exception:
            pass
        if isinstance(failed_state, Mapping):
            failed_updates = {
                "sync_status": failed_state.get("sync_status", "failed"),
                "sync_error": failed_state.get("sync_error", error),
                "version": failed_state.get("version", version),
                "last_sync_at": failed_state.get("last_sync_at") or _utcnow_text(),
            }
        else:
            failed_updates = {
                "sync_status": "failed",
                "sync_error": error,
                "version": version,
                "last_sync_at": _utcnow_text(),
            }
        _apply_record_state(faq_record, **failed_updates)
        return {
            "success": False,
            "faq_id": faq_id,
            "version": version,
            "sync_status": "failed",
            "document_id": f"faq:{faq_id}",
            "error": error,
            "added": False,
        }

    def sync_all(self) -> List[Dict[str, Any]]:
        """Sync every FAQ; malformed records never abort the batch."""
        results: List[Dict[str, Any]] = []
        for record in self.faq_repository.list_all(active_only=False):
            try:
                results.append(self.sync(record))
            except Exception as exc:
                faq_id = _record_id(record)
                version = _record_version_safe(record)
                error = _sanitize_error(exc, {"id": faq_id})
                results.append(
                    self._failure_result(record, faq_id, version, error)
                )
        return results


__all__ = ["FaqSyncService"]

"""Reusable request-scoped FAQ synchronization facade.

FaqRepository is session-bound, so production API calls need a fresh
repository/session per request. This facade keeps the existing
``FaqSyncService`` contract while making it directly usable from the API.
"""
from typing import Any, Callable, Dict, List, Optional

from db.faq_repository import FaqRepository


class RequestScopedFaqSyncService(FaqSyncService):
    """Create a new FAQ repository/session for each synchronization operation."""

    def __init__(self, session_factory: Any, knowledge_base: Any):
        self.session_factory = session_factory
        self.knowledge_base = knowledge_base

    def _run(self, operation: Callable[[FaqSyncService, FaqRepository], Any]) -> Any:
        session = self.session_factory()
        try:
            repository = FaqRepository(session)
            service = FaqSyncService(repository, self.knowledge_base)
            return operation(service, repository)
        finally:
            session.close()

    def create_record(self, payload: Any) -> Dict[str, Any]:
        return self._run(lambda service, _: service.create_record(payload))

    def update_record(self, faq_id: Any, payload: Any) -> Dict[str, Any]:
        return self._run(lambda service, _: service.update_record(faq_id, payload))

    def toggle_record(self, faq_id: Any, active: Optional[bool] = None) -> Dict[str, Any]:
        return self._run(lambda service, _: service.toggle_record(faq_id, active))

    def delete_record(self, faq_id: Any) -> Dict[str, Any]:
        return self._run(lambda service, _: service.delete_record(faq_id))

    def sync_all(self) -> List[Dict[str, Any]]:
        return self._run(lambda service, _: service.sync_all())

    def list_all(self, active_only: bool = False) -> List[Dict[str, Any]]:
        return self._run(
            lambda _, repository: repository.list_all(active_only=active_only)
        )

    def get_by_id(self, faq_id: Any) -> Optional[Dict[str, Any]]:
        return self._run(lambda _, repository: repository.get_by_id(str(faq_id)))

    def count(self) -> int:
        return len(self.list_all(active_only=False))


__all__ = [
    "FaqSyncService",
    "RequestScopedFaqSyncService",
]
