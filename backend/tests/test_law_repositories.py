"""Repository layer tests for consultations, lawyers, and FAQs.

These tests intentionally use an in-memory SQLite engine so they do not require
PostgreSQL or a running Docker container.
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.consultation_repository import ConsultationRepository
from db.faq_repository import FaqRepository
from db.lawyer_repository import LawyerRepository
from db.models import Base, Consultation


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


def test_consultation_create_get_list_update_delete(session):
    repo = ConsultationRepository(session)
    saved = repo.save_public({
        "request_id": "req-public-1",
        "contact": {"name": "张*", "phone": "138****1234"},
        "consent": True,
        "legal_domain": "dangerous_driving",
        "risk_analysis": "初步分析",
        "recommended_lawyer_ids": ["lawyer-1"],
    })

    assert saved["status"] == "PENDING"
    assert saved["id"]
    assert saved["contact_name"] == "张*"
    assert saved["contact_phone"] == "138****1234"

    fetched = repo.get_by_id(saved["id"])
    assert fetched is not None
    assert fetched["request_id"] == "req-public-1"

    by_request = repo.get_by_request_id("req-public-1")
    assert by_request is not None
    assert by_request["id"] == saved["id"]

    updated = repo.update_status(saved["id"], "CONTACTED")
    assert updated is not None
    assert updated["status"] == "CONTACTED"

    recent = repo.list_recent(10)
    assert len(recent) == 1
    assert recent[0]["id"] == saved["id"]

    assert repo.delete(saved["id"]) is True
    assert repo.get_by_id(saved["id"]) is None


def test_consultation_save_from_agent_and_idempotent_request_id(session):
    repo = ConsultationRepository(session)
    payload = {
        "request_id": "req-agent-1",
        "user_id": "user-1",
        "contact_name": "张*",
        "contact_phone": "138****1234",
        "consent": True,
        "legal_domain": "dangerous_driving",
        "risk_flags": ["已发生交通事故"],
        "facts": {"has_lawyer": False},
        "risk_analysis": "初步风险分析",
        "recommended_lawyers": [{"id": "lawyer-1", "name": "张律师"}],
        "source": "law_agent",
    }

    first = repo.save_from_agent(payload)
    second = repo.save_from_agent(payload)

    assert first["status"] == "PENDING"
    assert second["id"] == first["id"]
    assert len(repo.list_recent(100)) == 1


def test_consultation_request_id_is_unique_at_database_level(session):
    request_id = f"req-{uuid.uuid4()}"
    consultation = Consultation(
        request_id=request_id,
        contact_name="张*",
        contact_phone="138****1234",
        consent=True,
        legal_domain="dangerous_driving",
        risk_analysis="初步分析",
    )
    session.add(consultation)
    session.commit()

    duplicate = Consultation(
        request_id=request_id,
        contact_name="李*",
        contact_phone="139****5678",
        consent=True,
        legal_domain="labor_dispute",
        risk_analysis="另一次保存",
    )
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_lawyer_seed_filter_and_crud(session):
    repo = LawyerRepository(session)
    seeded = repo.seed_lawyers([
        {
            "name": "张律师（示例）",
            "domain": "criminal",
            "specialties": ["醉驾", "刑事辩护"],
            "intro": "示例律师",
            "active": True,
            "sort_order": 1,
        },
        {
            "name": "王律师（示例）",
            "domain": "civil",
            "specialties": ["劳动争议"],
            "intro": "示例律师",
            "active": True,
            "sort_order": 2,
        },
    ])
    assert seeded == 2

    active = repo.find_active_by_domain("criminal")
    assert len(active) == 1
    assert active[0]["name"] == "张律师（示例）"
    assert active[0]["domain"] == "criminal"
    assert active[0]["specialties"] == ["醉驾", "刑事辩护"]

    all_lawyers = repo.list_all()
    assert len(all_lawyers) == 2

    created = repo.create({
        "name": "李律师（示例）",
        "domain": "civil",
        "specialties": ["婚姻家事"],
        "active": True,
    })
    assert created["active"] is True

    updated = repo.update(created["id"], {"active": False, "intro": "更新简介"})
    assert updated is not None
    assert updated["active"] is False
    assert updated["intro"] == "更新简介"

    toggled = repo.toggle(created["id"])
    assert toggled is not None
    assert toggled["active"] is True
    assert repo.find_active_by_domain("civil")


def test_faq_seed_filter_and_crud(session):
    repo = FaqRepository(session)
    seeded = repo.seed_faqs([
        {
            "category": "criminal",
            "question": "醉驾被查后一般会经过哪些阶段？",
            "answer": "需要结合具体阶段判断。",
            "keywords": ["醉驾", "阶段"],
            "active": True,
        },
        {
            "category": "service",
            "question": "如何预约律师？",
            "answer": "可留下联系方式。",
            "keywords": ["预约律师"],
            "active": True,
        },
    ])
    assert seeded == 2

    all_faqs = repo.list_all()
    assert len(all_faqs) == 2
    assert all(item["sync_status"] == "pending" for item in all_faqs)

    created = repo.create({
        "category": "traffic_accident",
        "question": "交通事故保险理赔流程是什么？",
        "answer": "通常需要报案、提交材料。",
        "keywords": ["交通事故", "保险理赔"],
        "active": True,
    })
    assert created["sync_status"] == "pending"
    assert created["id"]

    fetched = repo.get_by_id(created["id"])
    assert fetched is not None
    assert fetched["category"] == "traffic_accident"

    updated = repo.update(created["id"], {
        "answer": "通常需要报案、提交材料并等待核定。",
        "active": False,
    })
    assert updated is not None
    assert updated["active"] is False
    assert "等待核定" in updated["answer"]

    toggled = repo.toggle(created["id"])
    assert toggled is not None
    assert toggled["active"] is True

    active_only = repo.list_all(active_only=True)
    assert len(active_only) == 3

    assert repo.delete(created["id"]) is True
    assert repo.get_by_id(created["id"]) is None

def test_consultation_save_from_request_like_object(session):
    from types import SimpleNamespace

    repo = ConsultationRepository(session)
    request = SimpleNamespace(
        request_id="req-object-1",
        user_id="user-object-1",
        contact_name="张*",
        contact_phone="138****1234",
        consent=True,
        legal_domain="dangerous_driving",
        entities={"has_lawyer": False},
        risk_flags=[SimpleNamespace(value="已发生交通事故")],
        name="",
        phone="",
        contact={},
    )
    saved = repo.save_from_agent(request, {"city": "上海", "case_stage": "初步调查"})

    assert saved["status"] == "PENDING"
    assert saved["city"] == "上海"
    assert saved["risk_flags"] == ["已发生交通事故"]
    assert saved["facts"] == {"has_lawyer": False}


def test_faq_active_category_filter(session):
    repo = FaqRepository(session)
    repo.create({
        "category": "criminal",
        "question": "启用的 FAQ",
        "answer": "可用",
        "active": True,
    })
    inactive = repo.create({
        "category": "criminal",
        "question": "停用的 FAQ",
        "answer": "不可用",
        "active": False,
    })

    matches = repo.find_active_by_category("criminal")
    assert [item["id"] for item in matches] != [inactive["id"]]
    assert len(matches) == 1


def test_faq_mark_synced_and_mark_failed(session):
    repo = FaqRepository(session)
    faq = repo.create({
        "category": "criminal",
        "question": "同步状态 FAQ",
        "answer": "原始回答",
        "keywords": ["同步"],
    })

    original_version = faq["version"]
    synced = repo.mark_synced(faq["id"], original_version)
    assert synced is not None
    assert synced["sync_status"] == "synced"
    assert synced["version"] == original_version
    assert synced["sync_error"] is None
    assert synced["last_sync_at"] is not None

    stale = repo.mark_synced(faq["id"], original_version + 1)
    assert stale is None

    failed = repo.mark_sync_failed(faq["id"], "chroma unavailable")
    assert failed is not None
    assert failed["sync_status"] == "failed"
    assert failed["sync_error"] == "chroma unavailable"
    assert failed["version"] == original_version


def test_faq_update_preserves_explicit_sync_status(session):
    repo = FaqRepository(session)
    faq = repo.create({
        "category": "criminal",
        "question": "显式同步状态 FAQ",
        "answer": "原始回答",
    })
    repo.update(faq["id"], {"question": "显式同步状态 FAQ 更新"})
    bumped = repo.get_by_id(faq["id"])
    assert bumped is not None
    assert repo.mark_synced(faq["id"], bumped["version"]) is not None

    updated = repo.update(faq["id"], {
        "sync_status": "failed",
        "sync_error": "explicit error",
    })
    assert updated is not None
    assert updated["sync_status"] == "failed"
    assert updated["sync_error"] == "explicit error"
    assert updated["version"] >= 3


def test_consultation_integrity_error_rereturns_existing(session, monkeypatch):
    repo = ConsultationRepository(session)
    payload = {
        "request_id": "req-race-1",
        "contact_name": "张*",
        "contact_phone": "138****1234",
        "consent": True,
        "legal_domain": "dangerous_driving",
    }
    existing = repo.save_public(payload)

    real_scalar = session.scalar
    calls = {"count": 0}

    def first_lookup_returns_none(statement, *args, **kwargs):
        if str(statement).find("consultations") >= 0 and calls["count"] == 0:
            calls["count"] += 1
            return None
        return real_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(session, "scalar", first_lookup_returns_none)
    result = repo.save_public(payload)

    assert result["id"] == existing["id"]
    assert isinstance(result, dict)
    assert len(repo.list_recent(10)) == 1


def test_consultation_db_exception_is_sanitized(session, monkeypatch):
    from db.consultation_repository import ConsultationStoreError

    repo = ConsultationRepository(session)

    def fail_commit():
        raise RuntimeError("db params leaked: 张* 138****1234")

    monkeypatch.setattr(session, "commit", fail_commit)
    with pytest.raises(ConsultationStoreError) as exc_info:
        repo.save_public({
            "request_id": "req-sanitize-1",
            "contact_name": "张*",
            "contact_phone": "138****1234",
            "consent": True,
            "legal_domain": "dangerous_driving",
        })

    message = str(exc_info.value)
    assert message == "consultation save failed"
    assert "张*" not in message
    assert "138****1234" not in message


def test_seeding_is_idempotent(session):
    faqs = [
        {
            "category": "criminal",
            "question": "幂等 FAQ",
            "answer": "答案",
            "keywords": ["幂等"],
            "active": True,
        }
    ]
    lawyers = [
        {
            "name": "幂等律师",
            "domain": "criminal",
            "specialties": ["醉驾"],
            "active": True,
        }
    ]

    faq_repo = FaqRepository(session)
    assert faq_repo.seed_faqs(faqs) == 1
    assert faq_repo.seed_faqs(faqs) == 0
    assert len(faq_repo.list_all()) == 1

    lawyer_repo = LawyerRepository(session)
    assert lawyer_repo.seed_lawyers(lawyers) == 1
    assert lawyer_repo.seed_lawyers(lawyers) == 0
    assert len(lawyer_repo.list_all()) == 1
