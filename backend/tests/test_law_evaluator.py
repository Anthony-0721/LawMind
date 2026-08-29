"""LawMind evaluator compatibility and default-case tests for Task 3 review fixes."""

import asyncio

from core.intent_recognizer import LawIntentRecognizer
from evaluation.evaluator import (
    DEFAULT_DIALOG_CASES,
    DEFAULT_INTENT_CASES,
    EndToEndEvaluator,
    IntentEvaluator,
    IntentTestCase,
)


class FakeClient:
    """Fake LLM client; EndToEndEvaluator intent-only runs must not call it."""

    def __init__(self):
        self.called = False

    @property
    def messages(self):
        return self

    async def create(self, **_kwargs):
        self.called = True
        raise AssertionError("intent-only evaluator run must not call the LLM")


def make_recognizer() -> LawIntentRecognizer:
    return LawIntentRecognizer(api_key="test-key", model="test-model")


def test_intent_evaluator_accepts_law_intent_result_without_reasoning():
    evaluator = IntentEvaluator(make_recognizer())

    metrics = asyncio.run(evaluator.evaluate([
        IntentTestCase("醉驾被查了会怎么样？", "dangerous_driving"),
    ]))

    assert metrics["correct"] == 1
    assert metrics["total"] == 1
    assert metrics["cases"][0]["predicted"] == "dangerous_driving"
    assert metrics["cases"][0]["reasoning"] == ""


def test_end_to_end_evaluator_accepts_law_intent_recognizer_without_llm():
    client = FakeClient()
    evaluator = EndToEndEvaluator(
        orchestrator=None,
        recognizer=make_recognizer(),
        api_key="test-key",
        model="test-model",
        client=client,
    )

    report = asyncio.run(evaluator.run(intent_cases=[
        IntentTestCase("醉驾被查了会怎么样？", "dangerous_driving"),
    ]))

    assert client.called is False
    assert len(report.results) == 1
    assert report.avg_scores["intent_accuracy"] == 1.0
    assert report.results[0].passed is True


def test_default_intent_cases_are_law_mind_domains():
    expected = {
        "dangerous_driving",
        "criminal_defense",
        "labor_dispute",
        "marriage_family",
        "contract_dispute",
        "traffic_accident",
        "civil_loan",
        "lawyer_appointment",
    }

    assert {case.expected_intent for case in DEFAULT_INTENT_CASES} == expected
    assert len(DEFAULT_INTENT_CASES) == len(expected)


def test_default_dialog_cases_are_law_consultation_examples():
    text = " ".join(
        str(case.get("question") or " ".join(case.get("turns", [])))
        for case in DEFAULT_DIALOG_CASES
    )

    for keyword in (
        "醉驾",
        "刑事拘留",
        "离婚",
        "拖欠工资",
        "合同",
        "交通事故",
        "民间借贷",
    ):
        assert keyword.lower() in text.lower(), keyword