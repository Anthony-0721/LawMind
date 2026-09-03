"""LawMind evaluator compatibility, baseline, and legal-boundary tests.

Task 12 checks the LawMind defaults, the fake-LLM-judge path of
``EndToEndEvaluator.run()``, and the shipped regression baseline.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from core.intent_recognizer import LawIntentRecognizer
from evaluation.evaluator import (
    DEFAULT_DIALOG_CASES,
    DEFAULT_INTENT_CASES,
    EndToEndEvaluator,
    IntentEvaluator,
    IntentTestCase,
    RUNTIME_BASELINE_PATH,
    SHIPPED_BASELINE_PATH,
)


class FakeJudgeClient:
    """Fake Anthropic client used as the LLM-as-judge in evaluator tests."""

    def __init__(self):
        self.calls = 0
        self.prompts = []

    @property
    def messages(self):
        return self

    async def create(self, **_kwargs):
        self.calls += 1
        self.prompts.append(_kwargs.get("messages", [{}])[0].get("content", ""))
        return SimpleNamespace(content=[
            {
                "type": "text",
                "text": (
                    '{"relevance":0.95,"accuracy":0.95,'
                    '"completeness":0.9,"helpfulness":0.92}'
                ),
            }
        ])


class FakeEvaluatorOrchestrator:
    """Returns a dialog response that preserves the legal boundary."""

    DISCLAIMER = "以上内容仅供参考，不构成正式法律意见。"

    async def run(self, _request):
        return SimpleNamespace(
            response=f"这是模拟法律回复。{self.DISCLAIMER}",
            agent_type=SimpleNamespace(value="reception"),
            intent=None,
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


def test_end_to_end_evaluator_runs_law_intent_with_fake_judge():
    client = FakeJudgeClient()
    evaluator = EndToEndEvaluator(
        orchestrator=FakeEvaluatorOrchestrator(),
        recognizer=make_recognizer(),
        api_key="test-key",
        model="test-model",
        client=client,
    )

    report = asyncio.run(evaluator.run(
        intent_cases=[
            IntentTestCase("醉驾被查了会怎么样？", "dangerous_driving"),
        ],
        dialog_cases=[
            {"question": "醉驾被查了会怎么样？"},
        ],
    ))

    assert client.calls == 1
    assert report.total == 2
    assert report.passed == 2
    assert report.avg_scores["intent_accuracy"] == 1.0
    assert report.avg_scores["relevance"] == 0.95
    assert report.results[-1].metadata["response"].endswith("不构成正式法律意见。")


def test_shipped_law_baseline_is_valid_and_loaded():
    shipped = (
        Path(__file__).resolve().parents[1]
        / "data" / "eval" / "law_baseline.json"
    )
    assert shipped.exists()
    report = EndToEndEvaluator._report_from_dict(
        json.loads(shipped.read_text(encoding="utf-8"))
    )
    assert report.total >= 1
    assert report.passed >= 1
    assert report.avg_scores.get("intent_accuracy", 0.0) >= 0.9
    assert report.results[0].test_id == "intent_recognition"

def test_runtime_and_shipped_baseline_paths_are_separate():
    assert RUNTIME_BASELINE_PATH.endswith("runtime_law_baseline.json")
    assert SHIPPED_BASELINE_PATH.endswith("law_baseline.json")
    assert RUNTIME_BASELINE_PATH != SHIPPED_BASELINE_PATH


def test_runtime_baseline_missing_loads_shipped_for_comparison():
    evaluator = EndToEndEvaluator(
        orchestrator=None,
        recognizer=make_recognizer(),
        api_key="test-key",
        model="test-model",
        client=FakeClient(),
        baseline_path=str(Path("__missing_runtime_baseline__.json")),
        shipped_baseline_path=SHIPPED_BASELINE_PATH,
    )
    assert evaluator._baseline is not None
    assert evaluator._baseline.results[0].test_id == "intent_recognition"


def test_runtime_baseline_save_does_not_write_shipped(monkeypatch):
    writes = []

    def fake_write_text(self, *args, **kwargs):
        writes.append(str(self))

    monkeypatch.setattr(Path, "write_text", fake_write_text)
    runtime = "runtime_law_baseline.json"
    evaluator = EndToEndEvaluator(
        orchestrator=None,
        recognizer=make_recognizer(),
        api_key="test-key",
        model="test-model",
        client=FakeClient(),
        baseline_path=runtime,
        shipped_baseline_path=SHIPPED_BASELINE_PATH,
    )

    asyncio.run(evaluator.run(intent_cases=[
        IntentTestCase("醉驾被查了会怎么样？", "dangerous_driving"),
    ]))

    assert runtime in writes
    assert str(Path(SHIPPED_BASELINE_PATH)) not in writes


def test_save_refuses_to_overwrite_shipped_baseline(monkeypatch):
    writes = []

    def fake_write_text(self, *args, **kwargs):
        writes.append(str(self))

    monkeypatch.setattr(Path, "write_text", fake_write_text)
    evaluator = EndToEndEvaluator(
        orchestrator=None,
        recognizer=make_recognizer(),
        api_key="test-key",
        model="test-model",
        client=FakeClient(),
        baseline_path=SHIPPED_BASELINE_PATH,
        shipped_baseline_path=SHIPPED_BASELINE_PATH,
    )

    asyncio.run(evaluator.run(intent_cases=[
        IntentTestCase("醉驾被查了会怎么样？", "dangerous_driving"),
    ]))

    assert writes == []


class FailingJudgeClient:
    """LLM-as-judge client that always fails, to exercise P3-G handling."""

    def __init__(self):
        self.calls = 0

    @property
    def messages(self):
        return self

    async def create(self, **_kwargs):
        self.calls += 1
        raise RuntimeError("judge unavailable")


def test_p3g_judge_failure_excluded_from_pass_rate_and_flagged():
    evaluator = EndToEndEvaluator(
        orchestrator=FakeEvaluatorOrchestrator(),
        recognizer=make_recognizer(),
        api_key="test-key",
        model="test-model",
        client=FailingJudgeClient(),
    )
    report = asyncio.run(evaluator.run(
        intent_cases=[IntentTestCase("醉驾被查了会怎么样？", "dangerous_driving")],
        dialog_cases=[{"question": "醉驾被查了会怎么样？"}],
    ))
    # intent result evaluated+passes; dialog judge failed → excluded from pass_rate
    assert report.judge_failures == 1
    assert report.passed == 1
    assert report.pass_rate == 1.0
    dialog = [r for r in report.results if r.test_id.startswith("dialog")][0]
    assert dialog.metadata.get("judge_failed") is True
    assert "未评测" in dialog.detail

    # 四维质量分同样不得包含兜底 0.5，否则回归检测会被判分器故障污染。
    assert report.avg_scores.get("relevance") is None
    assert report.avg_scores.get("accuracy") is None
    assert report.avg_scores.get("completeness") is None
    assert report.avg_scores.get("helpfulness") is None
