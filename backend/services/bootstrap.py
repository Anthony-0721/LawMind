"""Application bootstrap for seeding law data and reconciling FAQ vectors."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from db.database import init_db
from db.faq_repository import FaqRepository
from db.lawyer_repository import LawyerRepository
from .faq_sync_service import FaqSyncService


def _load_seed(path: Any) -> list[Dict[str, Any]]:
    seed_path = Path(path)
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"seed file must contain a JSON array: {seed_path}")
    return [item for item in raw if isinstance(item, dict)]


def bootstrap_law_data(
    session_local: Any,
    knowledge_base: Any,
    faq_seed_path: Any,
    lawyer_seed_path: Any,
) -> Dict[str, Any]:
    """Create tables, seed FAQs/lawyers, and reconcile FAQ retrieval vectors.

    Returns a JSON-serializable summary with the number of newly seeded rows
    and the results returned by ``FaqSyncService.sync_all``.
    """
    init_db()
    faq_items = _load_seed(faq_seed_path)
    lawyer_items = _load_seed(lawyer_seed_path)

    session = session_local()
    try:
        faq_repository = FaqRepository(session)
        lawyer_repository = LawyerRepository(session)
        faq_seeded = faq_repository.seed_faqs(faq_items)
        lawyer_seeded = lawyer_repository.seed_lawyers(lawyer_items)
        knowledge_base.delete_faq_vectors()
        faq_sync_results = FaqSyncService(
            faq_repository,
            knowledge_base,
        ).sync_all()
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()

    faq_synced = sum(
        1 for result in faq_sync_results if result.get("success") is True
    )
    faq_failed = sum(
        1 for result in faq_sync_results if result.get("success") is not True
    )
    return {
        "faq_seeded": faq_seeded,
        "lawyer_seeded": lawyer_seeded,
        "faq_sync_results": faq_sync_results,
        "faq_sync": faq_sync_results,
        "faq_synced": faq_synced,
        "faq_failed": faq_failed,
        "synced": faq_synced,
        "failed": faq_failed,
    }


__all__ = ["bootstrap_law_data"]
