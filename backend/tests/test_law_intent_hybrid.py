# -*- coding: utf-8 -*-
"""Three-source fusion behavior for LawIntentRecognizer."""
import asyncio
import json
import types

from core.intent_recognizer import LawIntentRecognizer
from core.law_domain import LawIntent


class _Messages:
    def __init__(self, owner):
        self.owner = owner

    async def create(self, **_kwargs):
        text = json.dumps({
            "intent": self.owner.intent,
            "confidence": self.owner.confidence,
            "reasoning": "test",
        })
        return types.SimpleNamespace(content=[{"type": "text", "text": text}])


class FakeClient:
    def __init__(self, intent: str = "labor_dispute", confidence: float = 0.9):
        self.intent = intent
        self.confidence = confidence
        self.messages = _Messages(self)


def _hybrid(intent: str = "labor_dispute", confidence: float = 0.9,
            use_llm: bool = True, use_embedding: bool = True) -> LawIntentRecognizer:
    return LawIntentRecognizer(
        api_key="k",
        model="m",
        client=FakeClient(intent, confidence),
        use_llm=use_llm,
        use_embedding=use_embedding,
    )


def test_recognize_without_client_matches_pure_rules():
    rec = LawIntentRecognizer(api_key="test-key", model="test-model")
    a = asyncio.run(rec.recognize("酒后开车被交警查到"))
    b = rec.recognize_sync("酒后开车被交警查到")
    assert a.intent == b.intent
    assert a.confidence == b.confidence
    assert a.source_scores["pattern"] == b.source_scores["pattern"]
    assert a.source_scores["llm"] == 0.0
    assert a.source_scores["embedding"] == 0.0


def test_three_source_fusion_populates_all_scores():
    rec = _hybrid(intent="labor_dispute", confidence=0.9)
    result = asyncio.run(rec.recognize("公司拖欠工资，想申请劳动仲裁"))
    assert result.intent == LawIntent.LABOR_DISPUTE
    assert result.source_scores["pattern"] > 0
    assert result.source_scores["llm"] > 0
    assert result.source_scores["embedding"] > 0
    assert result.source_scores["fused"] > 0


def test_rule_anchor_keeps_strong_match_vs_llm_other():
    rec = _hybrid(intent="other", confidence=0.8)
    result = asyncio.run(rec.recognize("酒后开车被交警查到"))
    assert result.intent == LawIntent.DANGEROUS_DRIVING


def test_embedding_only_fusion_disables_llm():
    rec = _hybrid(use_llm=False, use_embedding=True)
    result = asyncio.run(rec.recognize("公司拖欠工资，想申请劳动仲裁"))
    assert result.source_scores["llm"] == 0.0
    assert result.source_scores["embedding"] > 0
    assert result.source_scores["pattern"] > 0


def test_low_confidence_fuses_to_other():
    rec = _hybrid(intent="civil_loan", confidence=0.1)
    result = asyncio.run(rec.recognize("随便聊聊"))
    assert result.intent == LawIntent.OTHER


def test_llm_can_lift_a_paraphrase_that_rules_miss():
    # A plain-language question with no strong domain keyword; let the LLM vote.
    rec = _hybrid(intent="contract_dispute", confidence=0.85)
    result = asyncio.run(rec.recognize("我付了钱对方一直不交货"))
    assert result.intent == LawIntent.CONTRACT_DISPUTE