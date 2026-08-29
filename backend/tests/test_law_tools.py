"""LawMind Task 4 law tool tests.

Covers whitelist isolation, shared RAG tool fallback, required parameters,
deterministic tool contracts, lawyer-service fallback and contact validation.
"""
import asyncio
from typing import Any, Dict, List, Optional

import pytest

from agents.agent_orchestrator import (
    BaseAgent,
    CivilConsultationAgent,
    CriminalDefenseAgent,
    EscalationAgent,
    ReceptionAgent,
    Request,
)
from agents.tools import (
    assess_civil_risk,
    assess_criminal_risk,
    build_handoff_summary,
    build_reception_summary,
    build_shared_law_rag_tools,
    check_criminal_stage,
    check_missing_facts,
    civil_tools,
    create_consultation_record,
    criminal_tools,
    determine_procedure,
    escalation_tools,
    extract_civil_facts,
    extract_criminal_facts,
    identify_legal_domain,
    reception_tools,
    recommend_lawyer,
    validate_contact,
)
from core.intent_recognizer import UrgencyLevel
from core.law_domain import LawIntent, LawRiskFlag


class FakeClient:
    """Fake Anthropic client; Agent construction/whitelists must not call it."""

    @property
    def messages(self):
        return self

    async def create(self, **_kwargs):
        raise AssertionError("FakeClient must not be called")


def make_request(
    message: str = "测试法律咨询",
    intent: LawIntent = LawIntent.CRIMINAL_DEFENSE,
    *,
    urgency: UrgencyLevel = UrgencyLevel.HIGH,
    risk_flags: Optional[List[LawRiskFlag]] = None,
    entities: Optional[Dict[str, Any]] = None,
) -> Request:
    return Request(
        message=message,
        user_id="test-user",
        conv_id="test-conv",
        request_id="req-123",
        intent=intent,
        intent_group="criminal",
        urgency=urgency,
        risk_flags=list(risk_flags or []),
        entities=dict(entities or {}),
    )


def agent_tools(agent_cls) -> set[str]:
    return set(agent_cls(FakeClient(), "test-model").get_tools())


def test_agent_tool_scopes_are_isolated():
    assert agent_tools(ReceptionAgent) == {
        "search_law_knowledge",
        "identify_legal_domain",
        "check_missing_facts",
        "build_reception_summary",
    }
    assert agent_tools(CriminalDefenseAgent) == {
        "search_law_knowledge",
        "extract_criminal_facts",
        "check_criminal_stage",
        "assess_criminal_risk",
    }
    assert agent_tools(CivilConsultationAgent) == {
        "search_law_knowledge",
        "extract_civil_facts",
        "determine_procedure",
        "assess_civil_risk",
    }
    assert agent_tools(EscalationAgent) == {
        "search_law_knowledge",
        "recommend_lawyer",
        "validate_contact",
        "create_consultation_record",
        "build_handoff_summary",
    }
    assert "recommend_lawyer" not in agent_tools(CriminalDefenseAgent)
    assert "create_consultation_record" not in agent_tools(CivilConsultationAgent)


def test_shared_law_search_tool_present_and_query_required():
    tools = build_shared_law_rag_tools(None)

    assert set(tools) == {"search_law_knowledge"}
    spec = tools["search_law_knowledge"]
    assert spec.name == "search_law_knowledge"
    assert spec.input_schema["required"] == ["query"]

    with pytest.raises(ValueError, match="query"):
        BaseAgent._validate_tool_input(spec, {})


def test_shared_law_search_returns_fallback_when_rag_manager_missing():
    spec = build_shared_law_rag_tools(None)["search_law_knowledge"]

    result = asyncio.run(spec.handler(make_request(), {"query": "醉驾"}))

    assert result["success"] is False
    assert result["error"] == "RAG 工具未初始化"
    assert result["query"] == "醉驾"
    assert result["results"] == []
    assert result["fallback"]


def test_reception_tools_have_expected_shapes():
    req = make_request(
        intent=LawIntent.CRIMINAL_DEFENSE,
        entities={"case_stage": ["拘留"]},
        risk_flags=[LawRiskFlag.DETENTION, LawRiskFlag.NO_LAWYER],
    )

    domain = identify_legal_domain(req, {})
    assert domain["intent"] == LawIntent.CRIMINAL_DEFENSE
    assert domain["risk_flags"] == req.risk_flags

    missing = check_missing_facts(req, {})
    assert missing["legal_domain"] == LawIntent.CRIMINAL_DEFENSE
    assert set(missing["required_fields"]) == {"case_stage", "incident_time", "city"}
    assert set(missing["missing"]) == {"incident_time", "city"}

    summary = build_reception_summary(req, {})
    assert summary["request_id"] == req.request_id
    assert summary["intent"] == LawIntent.CRIMINAL_DEFENSE
    assert summary["facts"] == req.entities
    assert summary["risk_flags"] == req.risk_flags
    assert summary["summary"]


def test_criminal_tools_have_expected_shapes():
    req = make_request(
        intent=LawIntent.CRIMINAL_DEFENSE,
        urgency=UrgencyLevel.HIGH,
        entities={
            "case_stage": ["拘留"],
            "incident_time": ["2026-08-01"],
            "city": ["上海"],
        },
        risk_flags=[LawRiskFlag.DETENTION, LawRiskFlag.NO_LAWYER],
    )

    facts = extract_criminal_facts(req, {})
    assert facts["facts"] == req.entities
    assert facts["risk_flags"] == req.risk_flags

    stage = check_criminal_stage(req, {})
    assert stage["case_stage"] == "拘留"
    assert stage["need_confirm"] is False

    risk = assess_criminal_risk(req, {})
    assert risk["risk_level"] == "HIGH"
    assert risk["risk_flags"] == req.risk_flags


def test_civil_tools_have_expected_shapes():
    req = make_request(
        intent=LawIntent.LABOR_DISPUTE,
        urgency=UrgencyLevel.MEDIUM,
        entities={
            "incident_time": ["2026-07-01"],
            "city": ["北京"],
            "disputed_amount": ["50000"],
        },
        risk_flags=[LawRiskFlag.FILED],
    )

    facts = extract_civil_facts(req, {})
    assert facts["facts"] == req.entities
    assert facts["risk_flags"] == req.risk_flags

    procedure = determine_procedure(req, {})
    assert procedure["intent"] == LawIntent.LABOR_DISPUTE
    assert procedure["procedure"] == "劳动仲裁"
    assert procedure["need_confirm"] is False

    risk = assess_civil_risk(req, {})
    assert risk["risk_level"] == "MEDIUM"
    assert risk["risk_flags"] == req.risk_flags


def test_recommend_lawyer_missing_service_returns_fallback():
    req = make_request(intent=LawIntent.CRIMINAL_DEFENSE)

    assert recommend_lawyer(req, {}) == {
        "success": False,
        "reason": "lawyer_service_not_configured",
        "lawyers": [],
    }
    assert escalation_tools()["recommend_lawyer"].handler(req, {}) == {
        "success": False,
        "reason": "lawyer_service_not_configured",
        "lawyers": [],
    }


def test_recommend_lawyer_uses_optional_service():
    class FakeLawyerService:
        def recommend(self, intent):
            return [{"id": "lawyer-1", "intent": intent.value}]

    req = make_request(intent=LawIntent.DANGEROUS_DRIVING)

    assert recommend_lawyer(req, {}, FakeLawyerService()) == [
        {"id": "lawyer-1", "intent": "dangerous_driving"}
    ]
    assert escalation_tools(FakeLawyerService())["recommend_lawyer"].handler(req, {}) == [
        {"id": "lawyer-1", "intent": "dangerous_driving"}
    ]


def test_validate_contact_positive_and_negative():
    req = make_request()

    ok = validate_contact(req, {"name": "张三", "phone": "13800138000"})
    assert ok["valid"] is True
    assert ok["contact"] == {"name": "张三", "phone": "13800138000"}
    assert ok["errors"] == []

    missing_name = validate_contact(req, {"name": "", "phone": "13800138000"})
    assert missing_name["valid"] is False
    assert "name_required" in missing_name["errors"]

    invalid_phone = validate_contact(req, {"name": "李四", "phone": "12345"})
    assert invalid_phone["valid"] is False
    assert "phone_invalid" in invalid_phone["errors"]
    assert invalid_phone["contact"] == {}

    non_chinese_prefix = validate_contact(req, {"name": "王五", "phone": "12800138000"})
    assert non_chinese_prefix["valid"] is False
    assert "phone_invalid" in non_chinese_prefix["errors"]


def test_validate_contact_tool_requires_name_and_phone():
    spec = escalation_tools()["validate_contact"]
    assert spec.input_schema["required"] == ["name", "phone"]

    with pytest.raises(ValueError, match="name"):
        BaseAgent._validate_tool_input(spec, {"phone": "13800138000"})
    with pytest.raises(ValueError, match="phone"):
        BaseAgent._validate_tool_input(spec, {"name": "张三"})


def test_create_consultation_record_draft_contract():
    req = make_request(
        intent=LawIntent.DANGEROUS_DRIVING,
        entities={"case_stage": ["拘留"], "blood_alcohol": ["89mg/100ml"]},
        risk_flags=[LawRiskFlag.DETENTION, LawRiskFlag.NO_LAWYER],
    )
    draft = create_consultation_record(
        req,
        {
            "recommended_lawyers": [{"id": "lawyer-1"}],
            "contact": {"name": "张三", "phone": "13800138000"},
        },
    )

    assert draft["request_id"] == "req-123"
    assert draft["user_id"] == "test-user"
    assert draft["legal_domain"] == LawIntent.DANGEROUS_DRIVING
    assert draft["risk_flags"] == req.risk_flags
    assert draft["facts"] == req.entities
    assert draft["risk_analysis"]
    assert draft["recommended_lawyers"] == [{"id": "lawyer-1"}]
    assert draft["contact"] == {"name": "张三", "phone": "13800138000"}
    assert draft["status"] == "PENDING"
    assert set(draft) == {
        "request_id",
        "user_id",
        "legal_domain",
        "risk_flags",
        "facts",
        "risk_analysis",
        "recommended_lawyers",
        "contact",
        "status",
    }


def test_build_handoff_summary_shape():
    req = make_request(
        intent=LawIntent.CRIMINAL_DEFENSE,
        risk_flags=[LawRiskFlag.DETENTION],
        entities={"case_stage": ["拘留"]},
    )

    filled = build_handoff_summary(
        req,
        {"name": "张三", "phone": "13800138000"},
    )
    assert filled["request_id"] == "req-123"
    assert filled["intent"] == LawIntent.CRIMINAL_DEFENSE
    assert filled["risk_flags"] == req.risk_flags
    assert filled["contact_filled"] is True
    assert filled["summary"]

    not_filled = build_handoff_summary(req, {})
    assert not_filled["contact_filled"] is False
    assert not_filled["summary"]


def test_tool_collection_names_are_stable():
    assert set(reception_tools()) == {
        "identify_legal_domain",
        "check_missing_facts",
        "build_reception_summary",
    }
    assert set(criminal_tools()) == {
        "extract_criminal_facts",
        "check_criminal_stage",
        "assess_criminal_risk",
    }
    assert set(civil_tools()) == {
        "extract_civil_facts",
        "determine_procedure",
        "assess_civil_risk",
    }
    assert set(escalation_tools()) == {
        "recommend_lawyer",
        "validate_contact",
        "create_consultation_record",
        "build_handoff_summary",
    }
