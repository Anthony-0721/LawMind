"""API tests for the LawMind public and staff endpoints.

These tests use an in-memory SQLite session factory for real consultation and
lawyer services, a fake FAQ sync service, and a fake orchestrator so no LLM
call is made.
"""
from __future__ import annotations

from types import SimpleNamespace
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
    entities = {
        "legal_domain": ["dangerous_driving"],
        "case_stage": ["拘留"],
        "city": ["上海"],
        "risk_flags": ["detention"],
    }
    urgency = "HIGH"
    risk_flags: List[str] = ["detention"]
    confidence = 0.95
    source_scores: Dict[str, float] = {"pattern": 0.95}


class FakeOrchestrator:
    async def recognize_intent(self, message: str, history: Optional[List[Dict[str, str]]] = None):
        return FakeIntentResult()

    async def run(self, request: Any) -> Dict[str, Any]:
        return {
            "request_id": "server-orchestrator-request",
            "response": "这是模拟的法律咨询回复（未调用 LLM）",
            "intent": "dangerous_driving",
            "agent_type": "criminal",
            "agent_types": ["criminal"],
            "primary_agent": "criminal",
            "supporting_agents": [],
            "tools_used": [],
            "escalated": False,
            "latency_ms": 1.1,
            "entities": FakeIntentResult.entities,
            "missing_facts": ["incident_time"],
            "recommended_lawyers": [],
            "consultation_draft": {
                "id": "draft-1",
                "contact_name": "张三",
                "contact_phone": "13800138000",
            },
        }


class FakeMemory:
    def __init__(self) -> None:
        self.get_context_user_id: Optional[str] = None
        self.get_context_conversation_id: Optional[str] = None
        self.messages: List[Any] = []
        self.profile_user_id: Optional[str] = None

    async def get_context(self, user_id: str, conversation_id: str, query: str):
        self.get_context_user_id = user_id
        self.get_context_conversation_id = conversation_id
        return SimpleNamespace(
            recent_messages=[],
            to_prompt_text=lambda: "",
        )

    async def add_message(self, user_id: str, conversation_id: str, role: Any, content: str) -> None:
        self.messages.append((user_id, conversation_id, role, content))

    async def update_profile(self, user_id: str, conversation_id: str) -> None:
        self.profile_user_id = user_id


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
        "conversation_id": "conv-public-1",
        "name": "张三",
        "phone": "13800001234",
        "consent": True,
        "city": "上海",
        "legal_domain": "dangerous_driving",
    }


def _valid_transfer() -> Dict[str, Any]:
    return {
        "name": "李四",
        "phone": "13900139000",
        "consent": "是",
        "city": "北京",
        "legal_domain": "labor_dispute",
    }


def test_public_options_and_active_lawyers(client, lawyer_service):
    options = client.get("/law/options")
    assert options.status_code == 200
    data = options.json()
    assert "dangerous_driving" in data["legal_domains"]
    assert "CRITICAL" in data["urgency_levels"]
    assert "detention" in data["risk_flags"]

    for index in range(4):
        lawyer_service.create({
            "name": f"张律师{index}",
            "domain": "criminal",
            "specialties": ["刑事", "辩护"],
            "intro": "刑事辩护经验丰富",
            "phone": f"1380000000{index}",
        })
    lawyer_service.create({
        "name": "停用律师",
        "domain": "criminal",
        "specialties": ["刑事"],
        "active": False,
    })

    response = client.get("/law/lawyers", params={"domain": "dangerous_driving"})
    assert response.status_code == 200
    public_lawyers = response.json()
    assert len(public_lawyers) == 3
    assert "停用律师" not in [item["name"] for item in public_lawyers]
    assert all(
        set(item.keys()) == {"id", "name", "specialties", "intro"}
        for item in public_lawyers
    )
    assert "phone" not in public_lawyers[0]

    all_active = client.get("/law/lawyers").json()
    assert len(all_active) == 3


def test_public_consultation_happy_path(client, consultation_service):
    response = client.post("/law/consultations", json=_valid_consultation())
    assert response.status_code == 200
    data = response.json()
    assert data["consultation_id"]
    assert data["status"] == "PENDING"
    assert data["message"]
    assert "request_id" not in data

    records = consultation_service.list_recent(limit=10)
    assert len(records) == 1
    assert records[0]["status"] == "PENDING"
    assert records[0]["conversation_id"] == "conv-public-1"


def test_invalid_consultation_returns_422_and_no_pending(client, consultation_service):
    invalid_payloads = [
        {**_valid_consultation(), "consent": False},
        {**_valid_consultation(), "phone": "123"},
        {**_valid_consultation(), "consent": "maybe"},
    ]
    for payload in invalid_payloads:
        response = client.post("/law/consultations", json=payload)
        assert response.status_code == 422
    assert consultation_service.list_recent(limit=10) == []


def test_public_consultation_strict_schema_rejects_internal_fields(client, consultation_service):
    internal_payloads = [
        {**_valid_consultation(), "request_id": "client-req"},
        {**_valid_consultation(), "risk_flags": ["detention"]},
        {**_valid_consultation(), "status": "PENDING"},
        {**_valid_consultation(), "source": "law_agent"},
        {**_valid_consultation(), "version": 1},
        {**_valid_consultation(), "created_at": "2026-01-01T00:00:00Z"},
        {**_valid_consultation(), "updated_at": "2026-01-01T00:00:00Z"},
        {**_valid_consultation(), "facts": {"foo": "bar"}},
        {**_valid_consultation(), "consent": 1},
        {**_valid_consultation(), "consent": [True]},
    ]
    for payload in internal_payloads:
        response = client.post("/law/consultations", json=payload)
        assert response.status_code == 422, payload
    assert consultation_service.list_recent(limit=10) == []


def test_public_consultation_existing_conversation_updates_only_public_fields(
    client,
    consultation_service,
    monkeypatch,
):
    monkeypatch.setenv("LAWMIND_ADMIN_PASSWORD", "admin-secret")
    headers = {"X-Admin-Password": "admin-secret"}
    first = client.post("/law/consultations", json={
        "conversation_id": "conv-update-1",
        "name": "张三",
        "phone": "13800138000",
        "consent": True,
        "city": "上海",
        "legal_domain": "dangerous_driving",
    })
    record_id = first.json()["consultation_id"]
    original = consultation_service.get_by_id(record_id)
    original_request_id = original["request_id"]

    client.patch(
        f"/law/admin/consultations/{record_id}/status",
        json={"status": "CONTACTED"},
        headers=headers,
    )

    second = client.post("/law/consultations", json={
        "conversation_id": "conv-update-1",
        "name": "李四",
        "phone": "13900139000",
        "consent": True,
        "city": "北京",
        "preferred_time": "明天上午",
        "legal_domain": "contract_dispute",
    })
    assert second.status_code == 200
    assert second.json()["consultation_id"] == record_id

    records = consultation_service.list_recent(limit=10)
    assert len(records) == 1
    record = consultation_service.get_by_conversation_id("conv-update-1")
    assert record["status"] == "CONTACTED"
    assert record["contact_name"] == "李四"
    assert record["contact_phone"] == "13900139000"
    assert record["city"] == "北京"
    assert record["preferred_time"] == "明天上午"
    assert record["legal_domain"] == "dangerous_driving"
    assert record["source"] == "public"
    assert record["request_id"] == original_request_id


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
    assert detail.json()["contact_name"] == "张三"

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


def test_admin_consultation_list_masks_contact_fields(client, monkeypatch):
    monkeypatch.setenv("LAWMIND_ADMIN_PASSWORD", "admin-secret")
    headers = {"X-Admin-Password": "admin-secret"}
    client.post("/law/consultations", json=_valid_consultation())

    listed = client.get("/law/admin/consultations", headers=headers)
    assert listed.status_code == 200
    item = listed.json()[0]
    assert set(item.keys()) == {
        "id",
        "name",
        "phone",
        "legal_domain",
        "status",
        "created_at",
    }
    assert item["name"] == "张*"
    assert item["phone"] == "138****1234"
    assert "contact_name" not in item
    assert "contact_phone" not in item

    details = client.get(f"/law/admin/consultations/{item['id']}", headers=headers)
    assert details.json()["contact_name"] == "张三"
    assert details.json()["contact_phone"] == "13800001234"


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


def test_transfer_persists_lead(client, consultation_service):
    response = client.post("/law/transfer", json=_valid_transfer())
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"consultation_id", "status", "message"}
    assert data["consultation_id"]
    assert data["status"] == "PENDING"
    assert data["message"]

    records = consultation_service.list_recent(limit=10)
    assert len(records) == 1
    assert records[0]["status"] == "PENDING"
    assert records[0]["source"] == "transfer"
    assert records[0]["contact_name"] == "李四"
    assert records[0]["contact_phone"] == "13900139000"


def test_transfer_rejects_internal_fields(client, consultation_service):
    response = client.post("/law/transfer", json={
        **_valid_transfer(),
        "request_id": "client-transfer-request",
    })
    assert response.status_code == 422
    assert consultation_service.list_recent(limit=10) == []


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


def test_law_chat_uses_whitelisted_response_and_server_token():
    app = FastAPI()
    app.include_router(law_router)
    memory = FakeMemory()
    configure_app_law_services(
        app,
        orchestrator=FakeOrchestrator(),
        memory=memory,
    )
    client = TestClient(app)
    response = client.post("/law/chat", json={
        "message": "我醉驾被查了",
        "conversation_id": "client-conv",
        "user_id": "evil-user",
        "request_id": "client-request",
    })
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {
        "request_id",
        "conversation_id",
        "response",
        "intent",
        "intent_group",
        "legal_domain",
        "case_stage",
        "risk_flags",
        "missing_facts",
        "recommended_lawyers",
        "consultation_draft_id",
    }
    assert data["request_id"] != "client-request"
    assert data["conversation_id"] == "client-conv"
    assert data["response"]
    assert data["intent"] == "dangerous_driving"
    assert data["case_stage"] == "拘留"
    assert data["risk_flags"] == ["detention"]
    assert data["missing_facts"] == ["incident_time"]
    assert data["consultation_draft_id"] == "draft-1"
    assert "agent_type" not in data
    assert "entities" not in data
    assert "phone" not in data

    assert memory.get_context_user_id.startswith("lawmind-session:")
    assert memory.get_context_user_id != "evil-user"
    assert memory.get_context_conversation_id == "client-conv"
    assert memory.profile_user_id.startswith("lawmind-session:")

    generated = client.post("/law/chat", json={"message": "生成会话"})
    assert generated.status_code == 200
    assert generated.json()["conversation_id"].startswith("lawmind-session:")


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
