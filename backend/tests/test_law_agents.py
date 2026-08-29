"""LawMind Task 3 agent migration tests.

These tests intentionally focus on profiles, routing, escalation behavior and
the production recognizer switch.  Law tools arrive in Task 4, so tool
availability is not asserted here.
"""
import asyncio
import ast
import json
from pathlib import Path

from agents.agent_orchestrator import (
    AgentOrchestrator,
    AgentType,
    CivilConsultationAgent,
    CriminalDefenseAgent,
    EscalationAgent,
    OrchestratorResult,
    ReceptionAgent,
    Request,
)
from core.intent_recognizer import (
    IntentCategory,
    IntentRecognizer,
    LawIntentRecognizer,
    UrgencyLevel,
)
from core.law_domain import LawIntent, LawRiskFlag
from core.skill_loader import SkillManager

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class FakeClient:
    """Fake LLM client that fails if EscalationAgent tries to call it."""

    def __init__(self):
        self.called = False

    @property
    def messages(self):
        return self

    async def create(self, **_kwargs):
        self.called = True
        raise AssertionError("EscalationAgent must not call the LLM")


class RaisingSkillManager:
    """SkillManager that fails so EscalationAgent can prove deterministic fallback."""

    def prompt_for(self, message, agent_type):
        raise RuntimeError("simulated skill manager failure")


class FakeToolClient:
    """Fake client that triggers one failed RAG search, then returns text."""

    def __init__(self):
        self.calls = 0

    @property
    def messages(self):
        return self

    async def create(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            from types import SimpleNamespace
            return SimpleNamespace(content=[
                {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "search_law_knowledge",
                    "input": {"query": "醉驾"},
                }
            ])
        from types import SimpleNamespace
        return SimpleNamespace(content=[{"type": "text", "text": "完成"}])


def make_request(
    message: str,
    intent: LawIntent = LawIntent.OTHER,
    *,
    urgency: UrgencyLevel = UrgencyLevel.MEDIUM,
    risk_flags=None,
    confidence: float = 0.9,
    contact_name: str = "",
    contact_phone: str = "",
    city: str = "",
    preferred_time: str = "",
    consent: bool = False,
    entities=None,
) -> Request:
    return Request(
        message=message,
        user_id="test-user",
        conv_id="test-conv",
        intent=intent,
        intent_group="other",
        urgency=urgency,
        intent_confidence=confidence,
        risk_flags=list(risk_flags or []),
        contact_name=contact_name,
        contact_phone=contact_phone,
        city=city,
        preferred_time=preferred_time,
        consent=consent,
        entities=dict(entities or {}),
    )


def make_orchestrator() -> AgentOrchestrator:
    return AgentOrchestrator(api_key="test-key", model="test-model", client=FakeClient())


def imported_recognizers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "core.intent_recognizer":
            names.update(alias.name for alias in node.names)
    return names


def test_agent_profiles_are_distinct_and_complete():
    agent_classes = [
        ReceptionAgent,
        CriminalDefenseAgent,
        CivilConsultationAgent,
        EscalationAgent,
    ]

    profiles = [agent_cls.profile for agent_cls in agent_classes]

    assert len({profile.role for profile in profiles}) == 4
    assert {agent_cls.agent_type for agent_cls in agent_classes} == {
        AgentType.RECEPTION,
        AgentType.CRIMINAL,
        AgentType.CIVIL,
        AgentType.ESCALATION,
    }
    for profile in profiles:
        assert profile.mission
        assert profile.workflow
        assert profile.input_contract
        assert profile.output_contract
        assert profile.handoff_conditions
        assert profile.max_tokens > 0

    assert CriminalDefenseAgent.profile.temperature <= 0.2
    assert EscalationAgent.profile.tool_scope == (
        "search_law_knowledge",
        "recommend_lawyer",
        "validate_contact",
        "create_consultation_record",
        "build_handoff_summary",
    )


def test_criminal_and_civil_and_reception_routing_by_law_intent():
    orchestrator = make_orchestrator()

    routing_cases = [
        (LawIntent.DANGEROUS_DRIVING, AgentType.CRIMINAL),
        (LawIntent.CRIMINAL_DEFENSE, AgentType.CRIMINAL),
        (LawIntent.LABOR_DISPUTE, AgentType.CIVIL),
        (LawIntent.MARRIAGE_FAMILY, AgentType.CIVIL),
        (LawIntent.CONTRACT_DISPUTE, AgentType.CIVIL),
        (LawIntent.TRAFFIC_ACCIDENT, AgentType.CIVIL),
        (LawIntent.CIVIL_LOAN, AgentType.CIVIL),
        (LawIntent.LAWYER_APPOINTMENT, AgentType.ESCALATION),
        (LawIntent.LAW_FIRM_SERVICE, AgentType.RECEPTION),
        (LawIntent.OTHER, AgentType.RECEPTION),
    ]

    for intent, expected_agent in routing_cases:
        decision = orchestrator._route_decision(
            make_request(
                f"test message {intent.value}",
                intent=intent,
                confidence=0.95,
            )
        )
        assert decision.primary_agent == expected_agent, intent


def test_critical_detention_routes_to_escalation_agent():
    orchestrator = make_orchestrator()
    req = make_request(
        "家人被刑事拘留",
        intent=LawIntent.CRIMINAL_DEFENSE,
        urgency=UrgencyLevel.HIGH,
        risk_flags=[LawRiskFlag.DETENTION, LawRiskFlag.NO_LAWYER],
    )

    decision = orchestrator._route_decision(req)

    assert decision.primary_agent == AgentType.ESCALATION
    assert orchestrator._best_agent(AgentType.ESCALATION) is not None


def test_orchestrator_routes_detention_to_escalation_without_llm_call():
    orchestrator = make_orchestrator()
    req = make_request(
        "家人已经被刑事拘留，需要律师",
        intent=LawIntent.CRIMINAL_DEFENSE,
        urgency=UrgencyLevel.HIGH,
        risk_flags=[LawRiskFlag.DETENTION],
    )

    result = asyncio.run(orchestrator.run(req))

    assert result.primary_agent == AgentType.ESCALATION
    assert result.escalated is True
    assert "转人工" in result.response


def test_escalation_returns_deterministic_handoff_without_llm_call():
    client = FakeClient()
    agent = EscalationAgent(client, "test-model")
    req = make_request(
        "帮我预约律师",
        intent=LawIntent.LAWYER_APPOINTMENT,
        risk_flags=[LawRiskFlag.DETENTION],
    )

    result = asyncio.run(agent.handle(req))

    assert result.escalate is True
    assert result.success is True
    assert "转人工" in result.content
    assert client.called is False


def test_escalation_runtime_skill_prompt_is_populated():
    skill_manager = SkillManager(str(BACKEND_ROOT / "skills" / "law_firm"))
    skill_manager.load()
    agent = EscalationAgent(FakeClient(), "test-model", skill_manager=skill_manager)
    req = make_request(
        "我想留联系方式并转人工客服",
        intent=LawIntent.LAWYER_APPOINTMENT,
        risk_flags=[LawRiskFlag.DETENTION],
    )

    result = asyncio.run(agent.handle(req))

    assert agent._last_skill_prompt
    assert "人工升级与留资规范" in agent._last_skill_prompt
    assert result.metadata.get("skill_prompt_injected") is True
    assert result.metadata.get("skill_prompt_used") is True
    assert result.metadata.get("skill_prompt_chars", 0) > 0
    assert "人工升级与留资规范" not in result.content


def test_escalation_routes_and_propagates_skill_prompt_for_detention():
    skill_manager = SkillManager(str(BACKEND_ROOT / "skills" / "law_firm"))
    skill_manager.load()
    orchestrator = AgentOrchestrator(
        api_key="test-key",
        model="test-model",
        client=FakeClient(),
        skill_manager=skill_manager,
    )
    req = make_request(
        "家人已经被刑事拘留，需要律师",
        intent=LawIntent.CRIMINAL_DEFENSE,
        risk_flags=[LawRiskFlag.DETENTION],
    )

    result = asyncio.run(orchestrator.run(req))

    assert result.primary_agent == AgentType.ESCALATION
    assert result.escalated is True
    assert result.skill_prompt
    assert "人工升级与留资规范" in result.skill_prompt


def test_escalation_routes_and_propagates_skill_prompt_for_contact():
    skill_manager = SkillManager(str(BACKEND_ROOT / "skills" / "law_firm"))
    skill_manager.load()
    orchestrator = AgentOrchestrator(
        api_key="test-key",
        model="test-model",
        client=FakeClient(),
        skill_manager=skill_manager,
    )
    req = make_request(
        "我想留联系方式并转人工客服",
        intent=LawIntent.LAWYER_APPOINTMENT,
    )

    result = asyncio.run(orchestrator.run(req))

    assert result.primary_agent == AgentType.ESCALATION
    assert result.skill_prompt
    assert "人工升级与留资规范" in result.skill_prompt


def test_parallel_result_propagates_escalation_skill_prompt():
    from agents.agent_orchestrator import RoutingDecision

    skill_manager = SkillManager(str(BACKEND_ROOT / "skills" / "law_firm"))
    skill_manager.load()
    orchestrator = AgentOrchestrator(
        api_key="test-key",
        model="test-model",
        client=FakeClient(),
        skill_manager=skill_manager,
    )
    req = make_request("我想转人工", intent=LawIntent.LAWYER_APPOINTMENT)
    result = asyncio.run(
        orchestrator.run_parallel(req, RoutingDecision(AgentType.ESCALATION))
    )
    assert result.skill_prompt
    assert "人工升级与留资规范" in result.skill_prompt


def test_escalation_skill_manager_failure_keeps_deterministic_success():
    agent = EscalationAgent(FakeClient(), "test-model", skill_manager=RaisingSkillManager())
    req = make_request("我想转人工", intent=LawIntent.LAWYER_APPOINTMENT)

    result = asyncio.run(agent.handle(req))

    assert result.success is True
    assert result.escalate is True
    assert agent._last_skill_prompt == ""
    assert result.skill_prompt == ""
    assert result.metadata.get("skill_prompt_injected") is False


def test_old_intent_recognizer_api_remains_importable():
    assert IntentCategory.OTHER is not None
    assert callable(IntentRecognizer)
    assert hasattr(IntentRecognizer, "__init__")


def test_production_path_uses_law_intent_recognizer():
    orchestrator_src = BACKEND_ROOT / "agents" / "agent_orchestrator.py"
    api_src = BACKEND_ROOT / "api" / "main.py"

    assert "LawIntentRecognizer" in imported_recognizers(orchestrator_src)
    assert "IntentRecognizer" not in imported_recognizers(orchestrator_src)
    assert "LawIntentRecognizer" in imported_recognizers(api_src)
    assert "IntentRecognizer" not in imported_recognizers(api_src)

    orchestrator = make_orchestrator()
    assert isinstance(orchestrator._intent_recognizer, LawIntentRecognizer)

def test_court_soon_routes_to_escalation_agent():
    orchestrator = make_orchestrator()
    req = make_request(
        "明天开庭，需要律师",
        intent=LawIntent.CRIMINAL_DEFENSE,
        urgency=UrgencyLevel.HIGH,
        risk_flags=[LawRiskFlag.COURT_SOON, LawRiskFlag.NO_LAWYER],
    )

    decision = orchestrator._route_decision(req)

    assert decision.primary_agent == AgentType.ESCALATION


def test_other_risk_flags_route_to_domain_agents_not_escalation():
    orchestrator = make_orchestrator()
    cases = [
        (
            LawIntent.CRIMINAL_DEFENSE,
            [LawRiskFlag.FILED, LawRiskFlag.PROSECUTION, LawRiskFlag.NO_LAWYER],
            AgentType.CRIMINAL,
        ),
        (
            LawIntent.TRAFFIC_ACCIDENT,
            [LawRiskFlag.INJURY, LawRiskFlag.TRAFFIC_ACCIDENT, LawRiskFlag.NO_LAWYER],
            AgentType.CIVIL,
        ),
        (
            LawIntent.OTHER,
            [LawRiskFlag.FILED, LawRiskFlag.PROSECUTION, LawRiskFlag.NO_LAWYER],
            AgentType.CRIMINAL,
        ),
        (
            LawIntent.OTHER,
            [LawRiskFlag.INJURY, LawRiskFlag.TRAFFIC_ACCIDENT, LawRiskFlag.NO_LAWYER],
            AgentType.CIVIL,
        ),
    ]

    for intent, risk_flags, expected in cases:
        req = make_request(
            f"测试 {intent.value}",
            intent=intent,
            urgency=UrgencyLevel.HIGH,
            risk_flags=risk_flags,
        )
        decision = orchestrator._route_decision(req)
        assert decision.primary_agent == expected, intent

def test_critical_urgent_unknown_message_bypasses_clarification_and_escalates():
    orchestrator = make_orchestrator()
    req = Request(
        message="非常着急，不知道怎么办",
        user_id="test-user",
        conv_id="test-conv",
    )

    result = asyncio.run(orchestrator.run(req))

    assert result.primary_agent == AgentType.ESCALATION
    assert result.escalated is True


def test_low_confidence_other_with_detention_bypasses_clarification_and_escalates():
    orchestrator = make_orchestrator()
    req = make_request(
        "不知道怎么办",
        intent=LawIntent.OTHER,
        urgency=UrgencyLevel.MEDIUM,
        risk_flags=[LawRiskFlag.DETENTION],
        confidence=0.0,
    )

    result = asyncio.run(orchestrator.run(req))

    assert result.primary_agent == AgentType.ESCALATION
    assert result.escalated is True


def test_escalation_handle_calls_tools_and_creates_record_when_complete():
    class FakeLawyerService:
        def recommend(self, legal_domain):
            return [{"id": "lawyer-1", "legal_domain": legal_domain}]

    class FakeConsultationService:
        def save_from_agent(self, payload):
            return {**payload, "id": "consultation-1"}

    client = FakeClient()
    agent = EscalationAgent(
        client,
        "test-model",
        lawyer_service=FakeLawyerService(),
        consultation_service=FakeConsultationService(),
    )
    req = make_request(
        "我愿意留资并预约律师",
        intent=LawIntent.LAWYER_APPOINTMENT,
        contact_name="张三",
        contact_phone="13800138000",
        city="上海",
        preferred_time="2026-09-01 10:00",
        consent=True,
    )

    result = asyncio.run(agent.handle(req))

    assert result.tools_used == [
        "recommend_lawyer",
        "build_handoff_summary",
        "create_consultation_record",
    ]
    assert [trace["tool_name"] for trace in result.tool_traces] == result.tools_used
    assert client.called is False
    for trace in result.tool_traces:
        assert "13800138000" not in json.dumps(trace, ensure_ascii=False)
        assert "张三" not in json.dumps(trace, ensure_ascii=False)


def test_escalation_accepts_contacted_status_on_retry():
    class FakeLawyerService:
        def recommend(self, legal_domain):
            return [{"id": "lawyer-1", "legal_domain": legal_domain}]

    class LifecycleConsultationService:
        def __init__(self):
            self.saved = None

        def save_from_agent(self, payload):
            if self.saved is None:
                self.saved = {**payload, "id": "consultation-1", "status": "PENDING"}
            return self.saved

    service = LifecycleConsultationService()
    agent = EscalationAgent(
        FakeClient(),
        "test-model",
        lawyer_service=FakeLawyerService(),
        consultation_service=service,
    )
    req = make_request(
        "我愿意留资并预约律师",
        intent=LawIntent.LAWYER_APPOINTMENT,
        contact_name="张三",
        contact_phone="13800138000",
        consent="愿意",
    )
    asyncio.run(agent.handle(req))
    service.saved["status"] = "CONTACTED"

    retry = asyncio.run(agent.handle(req))

    assert "create_consultation_record" in retry.tools_used
    assert "已记录信息" in retry.content
    assert "暂未保存" not in retry.content


def test_escalation_does_not_create_record_without_complete_contact_and_consent():
    client = FakeClient()
    agent = EscalationAgent(client, "test-model")
    req = make_request("帮我预约律师", intent=LawIntent.LAWYER_APPOINTMENT)

    result = asyncio.run(agent.handle(req))

    assert result.tools_used == ["build_handoff_summary"]
    trace_names = [trace["tool_name"] for trace in result.tool_traces]
    assert "recommend_lawyer" in trace_names
    assert "create_consultation_record" not in trace_names
    assert client.called is False


def test_failed_rag_search_is_not_counted_as_tool_used():
    agent = CriminalDefenseAgent(FakeToolClient(), "test-model")

    content = asyncio.run(agent._call_llm(make_request("醉驾咨询")))

    assert content == "完成"
    assert agent._last_tools_used == []
    assert len(agent._last_tool_traces) == 1
    assert agent._last_tool_traces[0]["tool_name"] == "search_law_knowledge"
    assert agent._last_tool_traces[0]["result_success"] is False


def test_api_knowledge_used_requires_successful_rag_trace():
    from api.main import _knowledge_used

    failed = OrchestratorResult(
        request_id="req-1",
        response="resp",
        agent_type=AgentType.RECEPTION,
        intent=LawIntent.OTHER,
        tool_traces=[
            {
                "tool_name": "search_law_knowledge",
                "success": True,
                "result_success": False,
            }
        ],
    )
    success = OrchestratorResult(
        request_id="req-2",
        response="resp",
        agent_type=AgentType.RECEPTION,
        intent=LawIntent.OTHER,
        tool_traces=[
            {
                "tool_name": "search_law_knowledge",
                "success": True,
                "result_success": True,
            }
        ],
    )

    assert _knowledge_used(failed) is False
    assert _knowledge_used(success) is True


def test_agent_orchestrator_injects_lawyer_service_into_escalation_agent():
    class FakeLawyerService:
        def recommend(self, intent):
            return [{"id": "lawyer-1"}]

    service = FakeLawyerService()
    orchestrator = AgentOrchestrator(
        api_key="test-key",
        model="test-model",
        client=FakeClient(),
        lawyer_service=service,
    )

    agent = orchestrator._best_agent(AgentType.ESCALATION)
    assert agent is not None
    assert agent._lawyer_service is service


def test_escalation_uses_entities_legal_domain_override():
    class FakeLawyerService:
        def __init__(self):
            self.seen = None

        def recommend(self, legal_domain):
            self.seen = legal_domain
            return [{"id": "lawyer-1", "legal_domain": legal_domain}]

    service = FakeLawyerService()
    agent = EscalationAgent(FakeClient(), "test-model", lawyer_service=service)
    req = make_request(
        "刑事拘留但需要民事律师",
        intent=LawIntent.CRIMINAL_DEFENSE,
        entities={"legal_domain": ["civil"]},
    )

    result = asyncio.run(agent.handle(req))

    assert service.seen == "civil"
    assert "recommend_lawyer" in result.tools_used
    assert "recommend_lawyer" in [trace["tool_name"] for trace in result.tool_traces]


def test_failed_escalation_tool_is_not_counted_as_used():
    agent = EscalationAgent(FakeClient(), "test-model")
    req = make_request("帮我预约律师", intent=LawIntent.LAWYER_APPOINTMENT)

    result = asyncio.run(agent.handle(req))

    assert "recommend_lawyer" not in result.tools_used
    assert "recommend_lawyer" in [trace["tool_name"] for trace in result.tool_traces]
