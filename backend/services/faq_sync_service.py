"""Synchronize FAQ records from PostgreSQL into the ChromaDB retrieval copy.

The PostgreSQL Faq table remains the source of truth. ChromaDB stores a
retrieval-only copy so FAQ CRUD operations can delete the previous vector and
add the latest active version without leaving stale records behind.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_PII_CONTEXT_NAME_RE = re.compile(r"[\u4e00-\u9fff]{2,4}(?=[\s,，:：]*(?:<phone>|<email>|<id>))")


def _record_value(record: Any, key: str, default: Any = None) -> Any:
    """Read a key from a RecordDict/mapping or from an attribute-style object."""
    if isinstance(record, Mapping):
        return record.get(key, default)
    return getattr(record, key, default)


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
    faq_id = str(_record_value(record, "id", "") or "").strip()
    question = str(_record_value(record, "question", "") or "").strip()
    answer = str(_record_value(record, "answer", "") or "").strip()
    if not faq_id:
        raise ValueError("faq record id is required")
    if not question:
        raise ValueError("faq question is required")
    if not answer:
        raise ValueError("faq answer is required")

    keywords = _as_list(_record_value(record, "keywords", []))
    try:
        version = int(_record_value(record, "version", 1) or 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("faq version must be an integer") from exc
    if version < 1:
        raise ValueError("faq version must be at least 1")

    return {
        "id": faq_id,
        "category": str(_record_value(record, "category", "") or "").strip(),
        "question": question,
        "answer": answer,
        "keywords": keywords,
        "active": _as_bool(_record_value(record, "active", True)),
        "version": version,
    }


def _sanitize_error(error: Exception, faq: Mapping[str, Any]) -> str:
    """Strip FAQ content, contact details, and other PII from exception text."""
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
    raw = _EMAIL_RE.sub("<email>", raw)
    raw = _ID_CARD_RE.sub("<id>", raw)
    raw = _PII_CONTEXT_NAME_RE.sub("<name>", raw)
    sanitized = re.sub(r"\s+", " ", raw).strip()[:500]
    return sanitized or "faq sync failed"


class FaqSyncService:
    """Persist FAQ synchronization state after updating the ChromaKB copy.

    Return contract
    ----------------
    ``sync()`` never raises for Chroma/repository runtime failures. It returns
    a dictionary::

        {
            "success": bool,
            "faq_id": str,
            "version": int,
            "sync_status": "synced" | "failed",
            "document_id": str | None,
            "error": str | None,
            "added": bool,
        }

    Invalid input records raise ``ValueError`` before any external mutation.
    """

    def __init__(self, faq_repository: Any, knowledge_base: Any):
        self.faq_repository = faq_repository
        self.knowledge_base = knowledge_base
        # Backward-compatible alias used by older call sites.
        self.kb = knowledge_base

    def sync(self, faq_record: Any) -> Dict[str, Any]:
        """Delete the old vector, add the active FAQ, and update sync state."""
        faq = _normalize_faq(faq_record)
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
            self.faq_repository.mark_synced(faq_id, version)
        except Exception as exc:
            error = _sanitize_error(exc, faq)
            try:
                self.faq_repository.mark_sync_failed(faq_id, error)
            except Exception:
                # Persisting the failure is best-effort; return the original
                # sync failure so callers still learn the operation failed.
                pass
            return {
                "success": False,
                "faq_id": faq_id,
                "version": version,
                "sync_status": "failed",
                "document_id": document_id,
                "error": error,
                "added": added,
            }

        return {
            "success": True,
            "faq_id": faq_id,
            "version": version,
            "sync_status": "synced",
            "document_id": document_id,
            "error": None,
            "added": added,
        }

    def sync_all(self) -> List[Dict[str, Any]]:
        """Sync every FAQ, including inactive records so stale vectors are removed."""
        return [self.sync(record) for record in self.faq_repository.list_all(active_only=False)]


__all__ = ["FaqSyncService"]
