"""Task 8 review-fix tests: request-scoped services and consultation semantics."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import asyncio

from agents.agent_orchestrator import AgentOrchestrator, AgentType, EscalationAgent, Request
from agents.tools import build_escalation_tools, create_consultation_record
from core.law_domain import LawIntent
from db.consultation_repository import ConsultationRepository
from db.lawyer_repository import LawyerRepository
from db.models import Base
from services.consultation_service import (
    ConsultationService,
    ConsultationServiceError,
    ConsultationValidationError,
)
from services.lawyer_recommendation import LawyerService, LawyerServiceError


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture()
def session(session_factory):
    value = session_factory()
    try:
        yield value
    finally:
        value.close()


class TrackingSession:
    def __init__(self, session):
        self.session = session
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __getattr__(self, name):
        return getattr(self.session, name)

    def close(self):
        if not self.closed:
            self.closed = True
            self.session.close()


class CountingSessionFactory:
    def __init__(self, factory):
        self.factory = factory
        self.calls = 0
        self.sessions = []

    def __call__(self):
        self.calls += 1
        value = TrackingSession(self.factory())
        self.sessions.append(value)
        return value


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


def _incomplete_fallback() -> Dict[str, Any]:
    return {
        "success": False,
        "persisted": False,
        "status": "DRAFT",
        "error": "consultation_incomplete",
    }


def _valid_consent_fallback_payload(request_id: str) -> Dict[str, Any]:
    return {
        "request_id": request_id,
        "contact_name": "张*",
        "contact_phone": "13800138000",
        "legal_domain": "dangerous_driving",
        "city": "上海",
    }


class _FakeLawyerService:
    def recommend(self, domain: str) -> list[Dict[str, Any]]:
        return [{"id": "lawyer-1", "domain": domain}]


class _NoopClient:
    pass


class _FakeKnowledgeBase:
    def delete_faq_vectors(self) -> None:
        return None


# ── LawyerService ────────────────────────────────────────────────────────────


def test_lawyer_recommendation_filters_domain_active_and_sorts(session_factory):
    service = LawyerService(session_factory)
    service.create({"name": "D", "domain": "criminal", "specialties": ["D"], "sort_order": 4})
    service.create({"name": "A", "domain": "criminal", "specialties": ["A"], "sort_order": 1})
    service.create({"name": "Inactive", "domain": "criminal", "active": False, "sort_order": 0})
    service.create({"name": "Other", "domain": "civil", "specialties": ["C"], "sort_order": 0})
    service.create({"name": "B", "domain": "criminal", "specialties": ["B"], "sort_order": 2})
    service.create({"name": "C", "domain": "criminal", "specialties": ["C"], "sort_order": 3})

    result = service.recommend("criminal", limit=3)

    assert [item["name"] for item in result] == ["A", "B", "C"]
    assert len(result) == 3


def test_lawyer_recommendation_maps_legal_intent_and_matches_specialty(session_factory):
    service = LawyerService(session_factory)
    service.create({"name": "刑辩律师", "domain": "criminal", "specialties": ["醉驾"]})

    result = service.recommend("dangerous_driving")

    assert [item["name"] for item in result] == ["刑辩律师"]


def test_no_specialty_match_returns_empty_without_general_fallback(session_factory):
    service = LawyerService(session_factory)
    service.create({"name": "取保律师", "domain": "criminal", "specialties": ["取保候审"]})
    service.create({"name": "普通律师", "domain": "general", "specialties": ["刑事辩护"]})

    assert service.recommend("dangerous_driving") == []


def test_public_lawyer_output_contains_only_public_fields(session_factory):
    service = LawyerService(session_factory)
    created = service.create({
        "name": "张律师",
        "domain": "criminal",
        "specialties": ["醉驾"],
        "intro": "简介",
        "phone": "13800138000",
        "wechat": "lawyer-wechat",
        "email": "lawyer@example.com",
        "active": True,
        "sort_order": 1,
    })

    public = service.get_by_id(created["id"])
    assert public is not None
    assert set(public) == {"id", "name", "specialties", "intro"}

    recommendation = service.recommend("dangerous_driving")
    assert recommendation
    assert set(recommendation[0]) == {"id", "name", "specialties", "intro"}
    for item in recommendation:
        assert "phone" not in item
        assert "wechat" not in item
        assert "email" not in item
        assert "active" not in item
        assert "sort_order" not in item
        assert "created_at" not in item
        assert "updated_at" not in item


def test_lawyer_service_opens_session_per_public_method(session_factory):
    counting = CountingSessionFactory(session_factory)
    service = LawyerService(counting)

    assert service.list_all() == []
    assert service.list_all() == []

    assert counting.calls == 2
    assert all(value.closed for value in counting.sessions)


def test_lawyer_repository_exception_is_sanitized(session_factory, monkeypatch):
    def fail(self, domain):
        raise RuntimeError("phone leaked 13800138000")

    monkeypatch.setattr(LawyerRepository, "find_active_by_domain", fail)

    with pytest.raises(LawyerServiceError) as exc_info:
        LawyerService(session_factory).recommend("criminal")

    message = str(exc_info.value)
    assert "13800138000" not in message
    assert message == "lawyer recommendation failed"


def test_lawyer_service_crud_and_toggle(session_factory):
    service = LawyerService(session_factory)
    created = service.create({"name": "张律师", "domain": "civil", "specialties": ["劳动"]})
    assert created["name"] == "张律师"

    updated = service.update(created["id"], {"name": "张律师2号"})
    assert updated is not None
    assert updated["name"] == "张律师2号"

    inactive = service.toggle(created["id"], False)
    assert inactive is not None
    assert inactive["name"] == "张律师2号"
    assert service.list_all(active_only=True) == []


# ── ConsultationService ──────────────────────────────────────────────────────


def test_consultation_service_opens_session_per_public_method(session_factory):
    counting = CountingSessionFactory(session_factory)
    service = ConsultationService(counting)

    assert service.list_recent(10) == []
    assert service.list_recent(10) == []

    assert counting.calls == 2
    assert all(value.closed for value in counting.sessions)


def test_public_save_requires_valid_contact_and_consent(session_factory):
    service = ConsultationService(session_factory)

    invalid_phone = service.save_public({**_valid_consultation_payload(), "contact_phone": "123"})
    assert invalid_phone == {
        "success": False,
        "persisted": False,
        "status": "DRAFT",
        "error": "consultation_incomplete",
    }
    no_consent = service.save_public({**_valid_consultation_payload(), "consent": False})
    assert no_consent == {
        "success": False,
        "persisted": False,
        "status": "DRAFT",
        "error": "consultation_incomplete",
    }

    saved = service.save_public(_valid_consultation_payload())
    assert saved["status"] == "PENDING"


def test_agent_save_returns_draft_without_consent_and_does_not_persist(session_factory):
    service = ConsultationService(session_factory)
    draft = service.save_from_agent({
        "request_id": "req-draft",
        "contact_name": "张三",
        "contact_phone": "13800138000",
        "consent": False,
        "legal_domain": "dangerous_driving",
    })

    assert draft == {
        "success": False,
        "persisted": False,
        "status": "DRAFT",
        "error": "consultation_incomplete",
    }
    assert service.list_recent(10) == []


def test_agent_save_accepts_request_like_payload(session_factory):
    service = ConsultationService(session_factory)
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


def test_duplicate_request_id_preserves_lifecycle_status(session_factory):
    service = ConsultationService(session_factory)
    payload = _valid_consultation_payload("req-status-preserve")
    first = service.save_public(payload)
    service.update_status(first["id"], "CONTACTED")

    second = service.save_public(payload)

    assert second["id"] == first["id"]
    assert second["status"] == "CONTACTED"
    assert service.get_by_id(first["id"]) is not None
    assert service.get_by_id(first["id"])["status"] == "CONTACTED"


def test_update_status_validates_allowed_values(session_factory):
    service = ConsultationService(session_factory)
    saved = service.save_public(_valid_consultation_payload("req-status-valid"))

    for status in ("PENDING", "CONTACTED", "BOOKED", "CLOSED"):
        updated = service.update_status(saved["id"], status)
        assert updated is not None
        assert updated["status"] == status

    with pytest.raises(ConsultationValidationError):
        service.update_status(saved["id"], "INVALID")

    assert service.get_by_id(saved["id"])["status"] == "CLOSED"


def test_update_status_and_delete(session_factory):
    service = ConsultationService(session_factory)
    saved = service.save_public(_valid_consultation_payload("req-lifecycle"))

    updated = service.update_status(saved["id"], "BOOKED")
    assert updated is not None
    assert updated["status"] == "BOOKED"

    assert service.delete(saved["id"]) is True
    assert service.get_by_id(saved["id"]) is None
    assert service.list_recent(10) == []


def test_consultation_repository_exception_is_sanitized(session_factory, monkeypatch):
    def fail(self, payload):
        raise RuntimeError("db leaked 张* 13800138000")

    monkeypatch.setattr(ConsultationRepository, "save_public", fail)

    with pytest.raises(ConsultationServiceError) as exc_info:
        ConsultationService(session_factory).save_public(_valid_consultation_payload())

    message = str(exc_info.value)
    assert "13800138000" not in message
    assert "张*" not in message
    assert message == "consultation save failed"


def test_chinese_consent_values(session_factory):
    service = ConsultationService(session_factory)

    accepted = service.save_public({
        **_valid_consultation_payload("req-consent-yes"),
        "consent": "同意",
    })
    assert accepted["status"] == "PENDING"

    accepted_yes = service.save_public({
        **_valid_consultation_payload("req-consent-yes2"),
        "consent": "愿意",
    })
    assert accepted_yes["status"] == "PENDING"

    rejected = service.save_public({
        **_valid_consent_fallback_payload("req-consent-no"),
        "consent": "不同意",
    })
    assert rejected == _incomplete_fallback()
    rejected = service.save_public({
        **_valid_consent_fallback_payload("req-consent-no2"),
        "consent": "否",
    })
    assert rejected == _incomplete_fallback()


# ── Escalation tool integration ──────────────────────────────────────────────


def test_create_consultation_record_without_service_fails_cleanly():
    result = create_consultation_record(_request(), _complete_args())

    assert result == {
        "success": False,
        "persisted": False,
        "status": "DRAFT",
        "error": "consultation_service_unavailable",
    }


def test_build_escalation_tools_persists_complete_record(session_factory):
    service = ConsultationService(session_factory)
    tools = build_escalation_tools(
        consultation_service=service,
        lawyer_service=_FakeLawyerService(),
    )

    result = tools["create_consultation_record"].handler(_request(), _complete_args())

    assert result["status"] == "PENDING"
    assert len(service.list_recent(10)) == 1


def test_build_escalation_tools_keeps_draft_without_consent(session_factory):
    service = ConsultationService(session_factory)
    tools = build_escalation_tools(
        consultation_service=service,
        lawyer_service=_FakeLawyerService(),
    )

    args = _complete_args()
    args["consent"] = False
    result = tools["create_consultation_record"].handler(_request(), args)

    assert result == {
        "success": False,
        "persisted": False,
        "status": "DRAFT",
        "error": "consultation_incomplete",
    }
    assert service.list_recent(10) == []


def test_tool_parser_accepts_chinese_consent(session_factory):
    service = ConsultationService(session_factory)
    tools = build_escalation_tools(
        consultation_service=service,
        lawyer_service=_FakeLawyerService(),
    )

    args = _complete_args()
    args["consent"] = "愿意"
    req = _request()
    result = tools["create_consultation_record"].handler(req, args)

    assert result["status"] == "PENDING"
    assert result["consent"] is True
    assert len(service.list_recent(10)) == 1
    saved = service.get_by_request_id(req.request_id)
    assert saved is not None
    assert saved["consent"] is True


def test_unknown_consent_does_not_create_pending(session_factory):
    service = ConsultationService(session_factory)
    tools = build_escalation_tools(
        consultation_service=service,
        lawyer_service=_FakeLawyerService(),
    )

    args = _complete_args()
    args["consent"] = "maybe"
    result = tools["create_consultation_record"].handler(_request(), args)

    assert result == {
        "success": False,
        "persisted": False,
        "status": "DRAFT",
        "error": "consultation_incomplete",
    }
    assert service.list_recent(10) == []


def test_agent_service_incomplete_returns_fallback(session_factory):
    service = ConsultationService(session_factory)
    result = service.save_from_agent({
        "request_id": "req-incomplete",
        "contact_name": "张三",
        "contact_phone": "13800138000",
        "consent": "unknown",
        "legal_domain": "dangerous_driving",
    })

    assert result == {
        "success": False,
        "persisted": False,
        "status": "DRAFT",
        "error": "consultation_incomplete",
    }
    assert service.list_recent(10) == []


def test_escalation_persists_chinese_consent_end_to_end(session_factory):
    service = ConsultationService(session_factory)
    req = Request(
        message="我愿意留资并预约律师",
        user_id="test-user",
        conv_id="conv-1",
        request_id="req-persisted-consent",
        intent=LawIntent.LAWYER_APPOINTMENT,
        contact_name="张三",
        contact_phone="13800138000",
        consent="愿意",
    )
    agent = EscalationAgent(
        _NoopClient(),
        "test-model",
        lawyer_service=_FakeLawyerService(),
        consultation_service=service,
    )
    result = asyncio.run(agent.handle(req))

    assert "create_consultation_record" in result.tools_used
    assert "已记录信息" in result.content
    saved = service.get_by_request_id(req.request_id)
    assert saved is not None
    assert saved["consent"] is True


def test_escalation_does_not_mark_create_used_when_not_persisted():
    class NotPersistedConsultationService:
        def save_from_agent(self, payload):
            return {
                **payload,
                "success": False,
                "persisted": False,
                "status": "DRAFT",
                "error": "consultation_incomplete",
            }

    req = Request(
        message="我愿意留资并预约律师",
        user_id="test-user",
        conv_id="conv-1",
        request_id="req-not-persisted",
        intent=LawIntent.LAWYER_APPOINTMENT,
        contact_name="张三",
        contact_phone="13800138000",
        consent="愿意",
    )
    agent = EscalationAgent(
        _NoopClient(),
        "test-model",
        lawyer_service=_FakeLawyerService(),
        consultation_service=NotPersistedConsultationService(),
    )
    result = asyncio.run(agent.handle(req))

    assert "create_consultation_record" not in result.tools_used
    assert "已记录信息" not in result.content


# ── Orchestrator and bootstrap ───────────────────────────────────────────────


def test_orchestrator_injects_consultation_service_into_escalation_agent(session_factory):
    service = ConsultationService(session_factory)
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


def test_bootstrap_returns_request_scoped_services(monkeypatch):
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
        assert context["session_factory"] is session_factory
        assert context["lawyer_service"] is not None
        assert context["consultation_service"] is not None
        assert "session" not in context
        assert context["lawyer_service"].list_all() == []
        assert context["consultation_service"].list_recent(10) == []
    finally:
        Base.metadata.drop_all(engine)
