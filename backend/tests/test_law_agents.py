"""LawMind Task 3 agent migration tests.

These tests intentionally focus on profiles, routing, escalation behavior and
the production recognizer switch.  Law tools arrive in Task 4, so tool
availability is not asserted here.
"""
import asyncio
import ast
from pathlib import Path

from agents.agent_orchestrator import (
    AgentOrchestrator,
    AgentType,
    CivilConsultationAgent,
    CriminalDefenseAgent,
    EscalationAgent,
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


def make_request(
    message: str,
    intent: LawIntent = LawIntent.OTHER,
    *,
    urgency: UrgencyLevel = UrgencyLevel.MEDIUM,
    risk_flags=None,
    confidence: float = 0.9,
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