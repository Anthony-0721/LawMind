"""API tests for the LawMind public and staff endpoints.

These tests use an in-memory SQLite session factory for real consultation and
lawyer services, a fake FAQ sync service, and a fake orchestrator so no LLM
call is made.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.law_routes import configure_app_law_services, law_router
from db.models import Base
from services.consultation_service import ConsultationService
from services.faq_sync_service import RequestScopedFaqSyncService
from services.lawyer_recommendation import LawyerService


class FakeFaqSyncService:
    """In-memory FAQ sync fake used by admin FAQ tests."""

    def __init__(self) -> None:
        self.records: Dict[str, Dict[str, Any]] = {}
        self.next_id = 1

    def create_record(self, payload: Any) -> Dict[str, Any]:
        faq_id = "faq-%s" % self.next_id
        self.next_id += 1
        record = dict(payload)
        record.update({
            "id": faq_id,
            "version": 1,
            "sync_status": "synced",
            "sync_error": None,
            "success": True,
            "faq_id": faq_id,
        })
        self.records[faq_id] = record
        return record

    def update_record(self, faq_id: str, payload: Any) -> Dict[str, Any]:
        current = self.records.get(str(faq_id))
        if current is None:
            return {"success": False, "error": "faq_update_failed"}
        current.update(payload)
        current["version"] = int(current.get("version", 1)) + 1
        current["sync_status"] = "synced"
        current["success"] = True
        current["faq_id"] = str(faq_id)
        return current

    def toggle_record(self, faq_id: str, active: Optional[bool] = None) -> Dict[str, Any]:
        current = self.records.get(str(faq_id))
        if current is None:
            return {"success": False, "error": "faq_toggle_failed"}
        current["active"] = not bool(current.get("active", True)) if active is None else bool(active)
        current["version"] = int(current.get("version", 1)) + 1
        current["success"] = True
        current["faq_id"] = str(faq_id)
        return current

    def delete_record(self, faq_id: str) -> Dict[str, Any]:
        if str(faq_id) not in self.records:
            return {"success": False, "error": "faq_delete_failed"}
        self.records.pop(str(faq_id))
        return {"success": True, "faq_id": str(faq_id), "action": "delete"}

    def list_all(self, active_only: bool = False) -> List[Dict[str, Any]]:
        records = list(self.records.values())
        if active_only:
            records = [item for item in records if bool(item.get("active", True))]
        return records

    def sync_all(self) -> List[Dict[str, Any]]:
        return [
            {"success": True, "faq_id": faq_id, "version": record.get("version", 1)}
            for faq_id, record in self.records.items()
        ]


class FakeKnowledgeBase:
    """Minimal ChromaDB stand-in for request-scoped FAQ sync tests."""

    def delete_by_metadata(self, metadata: Dict[str, Any]) -> int:
        return 0

    def add_documents(self, documents: List[Any], metadatas: Optional[List[Dict[str, Any]]] = None) -> int:
        return len(documents)


class FakeIntentResult:
    intent = "dangerous_driving"
    intent_group = "criminal"
    entities = {"legal_domain": ["dangerous_driving"], "city": ["上海"]}
    urgency = "HIGH"
    risk_flags: List[str] = []
    confidence = 0.95
    source_scores: Dict[str, float] = {"pattern": 0.95}


class FakeOrchestrator:
    async def recognize_intent(self, message: str, history: Optional[List[Dict[str, str]]] = None):
        return FakeIntentResult()

    async def run(self, request: Any) -> Dict[str, Any]:
        return {
            "request_id": getattr(request, "request_id", "req-chat"),
            "response": "这是模拟的法律咨询回复（未调用 LLM）",
            "intent": "dangerous_driving",
            "agent_type": "criminal",
            "agent_types": ["criminal"],
            "primary_agent": "criminal",
            "supporting_agents": [],
            "tools_used": [],
            "escalated": False,
            "latency_ms": 1.1,
        }


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
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@pytest.fixture()
def consultation_service(session_factory):
    return ConsultationService(session_factory)


@pytest.fixture()
def lawyer_service(session_factory):
    return LawyerService(session_factory)


@pytest.fixture()
def faq_service():
    return FakeFaqSyncService()


@pytest.fixture()
def client(
    consultation_service,
    lawyer_service,
    faq_service,
):
    app = FastAPI()
    app.include_router(law_router)
    configure_app_law_services(
        app,
        lawyer_service=lawyer_service,
        consultation_service=consultation_service,
        faq_sync_service=faq_service,
    )
    return TestClient(app)


def _valid_consultation() -> Dict[str, Any]:
    return {
        "request_id": "req-public-1",
        "contact_name": "张三",
        "contact_phone": "13800138000",
        "consent": True,
        "legal_domain": "dangerous_driving",
        "city": "上海",
    }


def test_public_options_and_active_lawyers(client, lawyer_service):
    options = client.get("/law/options")
    assert options.status_code == 200
    data = options.json()
    assert "dangerous_driving" in data["legal_domains"]
    assert "CRITICAL" in data["urgency_levels"]
    assert "detention" in data["risk_flags"]

    lawyer_service.create({
        "name": "张律师",
        "domain": "criminal",
        "specialties": ["刑事辩护"],
        "intro": "刑事辩护经验丰富",
        "phone": "13800000001",
    })
    lawyer_service.create({
        "name": "停用律师",
        "domain": "criminal",
        "active": False,
    })

    response = client.get("/law/lawyers", params={"domain": "criminal"})
    assert response.status_code == 200
    public_lawyers = response.json()
    assert [item["name"] for item in public_lawyers] == ["张律师"]
    assert all(
        set(item.keys()) == {"id", "name", "specialties", "intro"}
        for item in public_lawyers
    )
    assert "phone" not in public_lawyers[0]


def test_public_consultation_happy_path(client, consultation_service):
    response = client.post("/law/consultations", json=_valid_consultation())
    assert response.status_code == 200
    data = response.json()
    assert data["consultation_id"]
    assert data["request_id"] == "req-public-1"
    assert data["status"] == "PENDING"
    assert data["message"]

    records = consultation_service.list_recent(limit=10)
    assert len(records) == 1
    assert records[0]["status"] == "PENDING"


def test_invalid_consultation_returns_422_and_no_pending(client, consultation_service):
    response = client.post("/law/consultations", json={
        "request_id": "req-invalid-1",
        "contact_name": "张三",
        "contact_phone": "13800138000",
        "consent": False,
    })
    assert response.status_code == 422
    assert consultation_service.list_recent(limit=10) == []

    response = client.post("/law/consultations", json={
        "request_id": "req-invalid-2",
        "contact_name": "张三",
        "contact_phone": "123",
        "consent": True,
    })
    assert response.status_code == 422
    assert consultation_service.list_recent(limit=10) == []


def test_admin_auth_requires_password(client, monkeypatch):
    monkeypatch.setenv("LAWMIND_ADMIN_PASSWORD", "admin-secret")
    assert client.get("/law/admin/consultations").status_code == 403
    assert client.get(
        "/law/admin/consultations", headers={"X-Admin-Password": "wrong"}
    ).status_code == 403
    assert client.get(
        "/law/admin/consultations", headers={"X-Admin-Password": "admin-secret"}
    ).status_code == 200


def test_admin_consultation_crud(client, monkeypatch, consultation_service):
    monkeypatch.setenv("LAWMIND_ADMIN_PASSWORD", "admin-secret")
    headers = {"X-Admin-Password": "admin-secret"}
    created = client.post("/law/consultations", json=_valid_consultation())
    record_id = created.json()["consultation_id"]

    listed = client.get("/law/admin/consultations", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [record_id]

    detail = client.get(f"/law/admin/consultations/{record_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == record_id

    updated = client.patch(
        f"/law/admin/consultations/{record_id}/status",
        json={"status": "CONTACTED"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "CONTACTED"

    deleted = client.delete(f"/law/admin/consultations/{record_id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["success"] is True
    assert consultation_service.get_by_id(record_id) is None


def test_admin_lawyer_crud(client, monkeypatch):
    monkeypatch.setenv("LAWMIND_ADMIN_PASSWORD", "admin-secret")
    headers = {"X-Admin-Password": "admin-secret"}
    created = client.post("/law/admin/lawyers", json={
        "name": "李律师",
        "domain": "civil",
        "specialties": ["合同"],
    }, headers=headers)
    assert created.status_code == 200
    lawyer_id = created.json()["id"]

    listed = client.get("/law/admin/lawyers", headers=headers)
    assert listed.status_code == 200
    assert any(item["id"] == lawyer_id for item in listed.json())

    updated = client.patch(
        f"/law/admin/lawyers/{lawyer_id}",
        json={"intro": "合同纠纷资深律师"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["intro"] == "合同纠纷资深律师"

    toggled = client.patch(
        f"/law/admin/lawyers/{lawyer_id}/toggle",
        json={"active": False},
        headers=headers,
    )
    assert toggled.status_code == 200
    assert toggled.json()["active"] is False


def test_admin_faq_crud_with_sync_fake(client, monkeypatch, faq_service):
    monkeypatch.setenv("LAWMIND_ADMIN_PASSWORD", "admin-secret")
    headers = {"X-Admin-Password": "admin-secret"}
    created = client.post("/law/admin/faqs", json={
        "category": "criminal",
        "question": "取保候审需要什么条件？",
        "answer": "需要结合案件阶段、社会危险性等判断。",
        "keywords": ["取保候审"],
    }, headers=headers)
    assert created.status_code == 200
    faq_id = created.json()["faq_id"]
    assert faq_id in faq_service.records

    listed = client.get("/law/admin/faqs", headers=headers)
    assert listed.status_code == 200
    assert faq_id in [item["id"] for item in listed.json()]

    updated = client.put(
        f"/law/admin/faqs/{faq_id}",
        json={"answer": "更新后的回答。"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["answer"] == "更新后的回答。"

    toggled = client.patch(
        f"/law/admin/faqs/{faq_id}/toggle",
        json={"active": False},
        headers=headers,
    )
    assert toggled.status_code == 200
    assert toggled.json()["active"] is False

    deleted = client.delete(f"/law/admin/faqs/{faq_id}", headers=headers)
    assert deleted.status_code == 200
    assert faq_id not in faq_service.records


def test_knowledge_reload_and_metrics(client, monkeypatch, faq_service):
    monkeypatch.setenv("LAWMIND_ADMIN_PASSWORD", "admin-secret")
    headers = {"X-Admin-Password": "admin-secret"}
    client.post("/law/consultations", json=_valid_consultation())
    client.post("/law/admin/faqs", json={
        "category": "criminal",
        "question": "常见问题",
        "answer": "答案",
    }, headers=headers)

    reloaded = client.post("/law/admin/knowledge/reload", headers=headers)
    assert reloaded.status_code == 200
    assert reloaded.json()["success"] is True

    metrics = client.get("/law/admin/metrics", headers=headers)
    assert metrics.status_code == 200
    data = metrics.json()
    assert data["total_consultations"] == 1
    assert data["total_faqs"] == 1
    assert data["active_lawyers"] == 0


def test_law_chat_uses_fake_orchestrator_without_llm():
    app = FastAPI()
    app.include_router(law_router)
    configure_app_law_services(app, orchestrator=FakeOrchestrator())
    client = TestClient(app)
    response = client.post("/law/chat", json={
        "message": "我醉驾被查了",
        "conversation_id": "conv-chat-1",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["request_id"]
    assert data["conversation_id"] == "conv-chat-1"
    assert data["response"]
    assert data["consultation_draft"]["status"] == "DRAFT"
    assert data["draft"]["status"] == "DRAFT"


def test_admin_faq_crud_with_sqlite_and_request_scoped_sync(monkeypatch, session_factory):
    monkeypatch.setenv("LAWMIND_ADMIN_PASSWORD", "admin-secret")
    headers = {"X-Admin-Password": "admin-secret"}
    app = FastAPI()
    app.include_router(law_router)
    configure_app_law_services(
        app,
        faq_sync_service=RequestScopedFaqSyncService(
            session_factory,
            FakeKnowledgeBase(),
        ),
    )
    client = TestClient(app)

    created = client.post("/law/admin/faqs", json={
        "category": "criminal",
        "question": "SQLite FAQ",
        "answer": "答案",
        "keywords": ["sqlite"],
    }, headers=headers)
    assert created.status_code == 200
    faq_id = created.json()["faq_id"]

    listed = client.get("/law/admin/faqs", headers=headers)
    assert listed.status_code == 200
    assert faq_id in [item["id"] for item in listed.json()]

    updated = client.put(
        f"/law/admin/faqs/{faq_id}",
        json={"answer": "更新后的 SQLite FAQ"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["answer"] == "更新后的 SQLite FAQ"

    toggled = client.patch(
        f"/law/admin/faqs/{faq_id}/toggle",
        headers=headers,
    )
    assert toggled.status_code == 200

    deleted = client.delete(f"/law/admin/faqs/{faq_id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["success"] is True


def test_admin_login_returns_authenticated(client, monkeypatch):
    monkeypatch.setenv("LAW_FIRM_ADMIN_PASSWORD", "legacy-secret")
    response = client.post("/law/admin/login", json={"password": "legacy-secret"})
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
