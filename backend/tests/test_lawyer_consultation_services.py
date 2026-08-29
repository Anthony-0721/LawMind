"""Task 8 service tests: lawyer recommendations and consultation persistence."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.agent_orchestrator import AgentOrchestrator, AgentType, Request
from agents.tools import build_escalation_tools, create_consultation_record
from core.law_domain import LawIntent
from db.consultation_repository import ConsultationRepository, ConsultationStoreError
from db.lawyer_repository import LawyerRepository
from db.models import Base
from services.consultation_service import (
    ConsultationService,
    ConsultationServiceError,
    ConsultationValidationError,
)
from services.lawyer_recommendation import LawyerService


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def _valid_consultation_payload(request_id: str = "req-svc-1") -> Dict[str, Any]:
    return {
        "request_id": request_id,
        "contact_name": "张*",
        "contact_phone": "13800138000",
        "consent": True,
        "legal_domain": "dangerous_driving",
        "city": "上海",
    }


def _request() -> Request:
    return Request(
        message="我要联系律师",
        user_id="user-1",
        conv_id="conv-1",
        request_id="req-agent-1",
        intent=LawIntent.LAWYER_APPOINTMENT,
    )


def _complete_args() -> Dict[str, Any]:
    return {
        "contact": {"name": "张三", "phone": "13800138000"},
        "consent": True,
        "city": "上海",
        "preferred_time": "2026-09-01 10:00",
        "legal_domain": "dangerous_driving",
    }


class _FakeLawyerService:
    def recommend(self, domain: str) -> list[Dict[str, Any]]:
        return [{"id": "lawyer-1", "domain": domain}]


# ── LawyerService ────────────────────────────────────────────────────────────


def test_lawyer_recommendation_filters_domain_active_and_sorts(session):
    repo = LawyerRepository(session)
    repo.create({"name": "D", "domain": "criminal", "specialties": ["D"], "sort_order": 4})
    repo.create({"name": "A", "domain": "criminal", "specialties": ["A"], "sort_order": 1})
    repo.create({"name": "Inactive", "domain": "criminal", "active": False, "sort_order": 0})
    repo.create({"name": "Other", "domain": "civil", "specialties": ["C"], "sort_order": 0})
    repo.create({"name": "B", "domain": "criminal", "specialties": ["B"], "sort_order": 2})
    repo.create({"name": "C", "domain": "criminal", "specialties": ["C"], "sort_order": 3})

    service = LawyerService(repo)
    result = service.recommend("criminal", limit=3)

    assert [item["name"] for item in result] == ["A", "B", "C"]
    assert len(result) == 3


def test_lawyer_recommendation_maps_legal_intent_to_backing_domain(session):
    repo = LawyerRepository(session)
    repo.create({"name": "刑辩律师", "domain": "criminal", "specialties": ["醉驾"]})

    result = LawyerService(repo).recommend("dangerous_driving")

    assert [item["name"] for item in result] == ["刑辩律师"]


def test_lawyer_recommendation_requires_specialty_match(session):
    repo = LawyerRepository(session)
    repo.create({"name": "醉驾律师", "domain": "criminal", "specialties": ["醉驾"], "sort_order": 2})
    repo.create({"name": "取保律师", "domain": "criminal", "specialties": ["取保候审"], "sort_order": 1})

    result = LawyerService(repo).recommend("dangerous_driving")

    assert [item["name"] for item in result] == ["醉驾律师"]


def test_lawyer_service_does_not_leak_contact_by_default(session):
    repo = LawyerRepository(session)
    created = repo.create({
        "name": "张律师",
        "domain": "criminal",
        "phone": "13800138000",
        "wechat": "lawyer-wechat",
        "email": "lawyer@example.com",
    })
    service = LawyerService(repo)

    public = service.get_by_id(created["id"])
    assert public is not None
    assert "phone" not in public
    assert "wechat" not in public
    assert "email" not in public

    explicit = service.get_by_id(created["id"], include_contact=True)
    assert explicit is not None
    assert explicit["phone"] == "13800138000"


def test_lawyer_service_crud_and_toggle(session):
    repo = LawyerRepository(session)
    service = LawyerService(repo)
    created = service.create({
        "name": "张律师",
        "domain": "civil",
        "specialties": ["劳动"],
        "sort_order": 1,
    })
    assert len(service.list_all()) == 1

    updated = service.update(created["id"], {"name": "张律师2号", "sort_order": 2})
    assert updated is not None
    assert updated["name"] == "张律师2号"

    inactive = service.toggle(created["id"], False)
    assert inactive is not None
    assert inactive["active"] is False
    assert service.list_all(active_only=True) == []

    active = service.toggle(created["id"], True)
    assert active is not None
    assert active["active"] is True


# ── ConsultationService ──────────────────────────────────────────────────────


def test_public_save_requires_valid_contact_and_consent(session):
    repo = ConsultationRepository(session)
    service = ConsultationService(repo)

    with pytest.raises(ConsultationValidationError):
        service.save_public({**_valid_consultation_payload(), "contact_phone": "123"})
    with pytest.raises(ConsultationValidationError):
        service.save_public({**_valid_consultation_payload(), "consent": False})

    saved = service.save_public(_valid_consultation_payload())
    assert saved["status"] == "PENDING"


def test_agent_save_returns_draft_without_consent_and_does_not_persist(session):
    repo = ConsultationRepository(session)
    service = ConsultationService(repo)

    draft = service.save_from_agent({
        "request_id": "req-draft",
        "contact_name": "张三",
        "contact_phone": "13800138000",
        "consent": False,
        "legal_domain": "dangerous_driving",
    })

    assert draft["status"] == "DRAFT"
    assert repo.list_recent(10) == []


def test_agent_save_accepts_request_like_payload(session):
    repo = ConsultationRepository(session)
    service = ConsultationService(repo)
    saved = service.save_from_agent(
        _request(),
        {
            "contact_name": "张三",
            "contact_phone": "13800138000",
            "consent": True,
            "legal_domain": "dangerous_driving",
        },
    )
    assert saved["status"] == "PENDING"
    assert saved["source"] == "law_agent"


def test_agent_save_persists_complete_valid_contact(session):
    repo = ConsultationRepository(session)
    service = ConsultationService(repo)

    saved = service.save_from_agent(_valid_consultation_payload("req-agent-complete"))

    assert saved["status"] == "PENDING"
    assert saved["source"] == "law_agent"
    assert len(repo.list_recent(10)) == 1


def test_duplicate_request_id_returns_existing_record(session):
    repo = ConsultationRepository(session)
    service = ConsultationService(repo)
    payload = _valid_consultation_payload("req-duplicate")

    first = service.save_public(payload)
    second = service.save_public(payload)

    assert second["id"] == first["id"]
    assert len(repo.list_recent(10)) == 1


def test_update_status_and_delete(session):
    repo = ConsultationRepository(session)
    service = ConsultationService(repo)
    saved = service.save_public(_valid_consultation_payload("req-lifecycle"))

    updated = service.update_status(saved["id"], "CONTACTED")
    assert updated is not None
    assert updated["status"] == "CONTACTED"
    assert service.get_by_request_id("req-lifecycle") is not None

    assert service.delete(saved["id"]) is True
    assert service.get_by_id(saved["id"]) is None
    assert service.list_recent(10) == []


def test_service_exceptions_are_sanitized(session, monkeypatch):
    repo = ConsultationRepository(session)
    service = ConsultationService(repo)

    def fail(payload):
        raise RuntimeError("db leaked 张* 13800138000")

    monkeypatch.setattr(repo, "save_public", fail)
    with pytest.raises(ConsultationServiceError) as exc_info:
        service.save_public(_valid_consultation_payload())

    message = str(exc_info.value)
    assert "13800138000" not in message
    assert "张*" not in message


# ── Escalation tool integration ──────────────────────────────────────────────


def test_build_escalation_tools_persists_complete_record(session):
    repo = ConsultationRepository(session)
    consultation_service = ConsultationService(repo)
    tools = build_escalation_tools(
        consultation_service=consultation_service,
        lawyer_service=_FakeLawyerService(),
    )

    req = _request()
    result = tools["create_consultation_record"].handler(req, _complete_args())

    assert result["status"] == "PENDING"
    assert len(repo.list_recent(10)) == 1


def test_build_escalation_tools_keeps_draft_without_consent(session):
    repo = ConsultationRepository(session)
    consultation_service = ConsultationService(repo)
    tools = build_escalation_tools(
        consultation_service=consultation_service,
        lawyer_service=_FakeLawyerService(),
    )

    req = _request()
    args = _complete_args()
    args["consent"] = False
    result = tools["create_consultation_record"].handler(req, args)

    assert result["status"] == "DRAFT"
    assert repo.list_recent(10) == []


def test_create_consultation_record_falls_back_without_service():
    result = create_consultation_record(_request(), _complete_args())

    assert result["status"] == "PENDING"
    assert result["source"] == "law_agent"

class _NoopClient:
    """Minimal client for orchestrator construction; no network calls."""


class _FakeKnowledgeBase:
    def delete_faq_vectors(self) -> None:
        return None


def test_orchestrator_injects_consultation_service_into_escalation_agent(session):
    service = ConsultationService(ConsultationRepository(session))
    orchestrator = AgentOrchestrator(
        api_key="test-key",
        model="test-model",
        client=_NoopClient(),
        lawyer_service=_FakeLawyerService(),
        consultation_service=service,
    )

    agent = orchestrator._best_agent(AgentType.ESCALATION)
    assert agent is not None
    assert agent._consultation_service is service
    assert agent._lawyer_service is not None


def test_bootstrap_returns_live_services(monkeypatch):
    from services import bootstrap as bootstrap_module

    monkeypatch.setattr(bootstrap_module, "init_db", lambda: None)
    monkeypatch.setattr(bootstrap_module, "_load_seed", lambda _path: [])

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        context = bootstrap_module.bootstrap_law_data(
            session_factory,
            _FakeKnowledgeBase(),
            Path("faq_seed.json"),
            Path("lawyers_seed.json"),
        )
        assert context["lawyer_service"] is not None
        assert context["consultation_service"] is not None
        assert context["consultation_repository"] is not None
        assert context["lawyer_service"].list_all() == []
        assert context["consultation_service"].list_recent(10) == []
        session = context["session"]
        close = getattr(session, "close", None)
        if callable(close):
            close()
    finally:
        Base.metadata.drop_all(engine)
