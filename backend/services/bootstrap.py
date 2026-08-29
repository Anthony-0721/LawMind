"""Application bootstrap for seeding law data and reconciling FAQ vectors."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

from db.database import init_db
from db.faq_repository import FaqRepository
from db.lawyer_repository import LawyerRepository
from .consultation_service import ConsultationService
from .faq_sync_service import FaqSyncService
from .lawyer_recommendation import LawyerService


def _load_seed(path: Any) -> list[Dict[str, Any]]:
    seed_path = Path(path)
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"seed file must contain a JSON array: {seed_path}")
    return [item for item in raw if isinstance(item, dict)]


def bootstrap_law_data(
    session_factory: Any,
    knowledge_base: Any,
    faq_seed_path: Any,
    lawyer_seed_path: Any,
) -> Dict[str, Any]:
    """Create tables, seed FAQ/lawyer data, and build request-scoped services.

    Returns the session factory, application services, and seed/sync summary
    counters. No long-lived Session is retained by the bootstrap context.
    """
    init_db()
    faq_items = _load_seed(faq_seed_path)
    lawyer_items = _load_seed(lawyer_seed_path)

    with session_factory() as session:
        faq_repository = FaqRepository(session)
        lawyer_repository = LawyerRepository(session)
        faq_seeded = faq_repository.seed_faqs(faq_items)
        lawyer_seeded = lawyer_repository.seed_lawyers(lawyer_items)
        try:
            knowledge_base.delete_faq_vectors()
        except Exception as exc:
            logger.warning("FAQ vector cleanup skipped: %s", type(exc).__name__)

        faq_sync_results = FaqSyncService(
            faq_repository,
            knowledge_base,
        ).sync_all()

    faq_synced = sum(
        1 for result in faq_sync_results if result.get("success") is True
    )
    faq_failed = sum(
        1 for result in faq_sync_results if result.get("success") is not True
    )
    return {
        "session_factory": session_factory,
        "lawyer_service": LawyerService(session_factory),
        "consultation_service": ConsultationService(session_factory),
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
