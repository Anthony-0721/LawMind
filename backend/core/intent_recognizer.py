"""
亮点：端到端意图识别

三路融合策略：
  1. LLM 语义理解（权重 70%）—— 主力，理解复杂语义和上下文
  2. Embedding 向量相似度（权重 20%）—— 快速匹配常见表达
  3. 关键词模式匹配（权重 10%）—— 零延迟兜底

三路结果通过加权投票合并，置信度低于阈值时降级为 OTHER。
LLM 和 Embedding 并行调用，不串行等待。
"""
import asyncio
import hashlib
import json
import logging
import time
from enum import Enum
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic

from core.llm_utils import extract_text_content

from core import law_domain as law_domain

from core.law_domain import (
    LAW_INTENT_GROUPS,
    LAW_PATTERNS,
    LAW_PATTERN_PRIORITY,
    LAW_TEMPLATES,
    LawEntityExtractor,
    LawIntent,
    LawIntentResult,
    LawRiskFlag,
    detect_law_risk_flags,
    has_unnegated_keyword,
)

LAW_RISK_RULES = law_domain.LAW_RISK_RULES

logger = logging.getLogger(__name__)


class UrgencyLevel(Enum):
    LOW      = 1
    MEDIUM   = 2
    HIGH     = 3
    CRITICAL = 4



def _cosine(a: List[float], b: List[float]) -> float:
    """纯 Python 余弦相似度，不依赖 numpy。"""
    dot = sum(x * y for x, y in zip(a, b))
    na  = sum(x * x for x in a) ** 0.5
    nb  = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _clean_any(value: Any) -> str:
    """UTF-8 safe text used by the law recognizer cache key and embeddings."""
    if value is None:
        return ""
    text = str(value)
    return text.encode("utf-8", errors="ignore").decode("utf-8")

# ── Law-specific intent recognizer ───────────────────────────────────────────
class LawIntentRecognizer:
    """Law-firm intent recognizer with three-source fusion.

    The deterministic rule layer is the anchor (and the only source used when no
    LLM client / embedding is configured). When a client exists, an LLM semantic
    pass and a local n-gram vector similarity pass are fused with the rule scores
    using configurable weights. Risk flags and entity extraction are always
    rule-based so escalation decisions stay deterministic and auditable.
    """

    DEFAULT_WEIGHTS = {"pattern": 0.1, "llm": 0.7, "embedding": 0.2}

    def __init__(
        self,
        api_key: str,
        model: str = "test-model",
        base_url: Optional[str] = None,
        client: Optional[Any] = None,
        use_llm: Optional[bool] = None,
        use_embedding: bool = False,
        confidence_threshold: float = 0.5,
        weights: Optional[Dict[str, float]] = None,
        max_cache: int = 1000,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.entity_extractor = LawEntityExtractor()
        self.confidence_threshold = confidence_threshold
        self._weights = dict(self.DEFAULT_WEIGHTS)
        if weights:
            self._weights.update(weights)
        self.max_cache = max_cache

        if client is not None:
            self._client = client
        elif base_url or (api_key and api_key not in ("", "test-key")):
            kwargs: Dict[str, Any] = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            self._client = AsyncAnthropic(**kwargs)
        else:
            self._client = None

        if use_llm is None:
            use_llm = self._client is not None
        # Both the LLM and the (local n-gram) embedding pass are only active when
        # a real client exists. Pure-rule construction stays deterministic.
        self._llm_enabled = bool(use_llm and self._client is not None)
        self._embedding_enabled = bool(use_embedding and self._client is not None)

        self._tpl_embeddings: Dict[LawIntent, List[List[float]]] = {}
        self._cache: Dict[str, LawIntentResult] = {}
        self.cache_hits = 0
        self.cache_misses = 0

    async def recognize(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> LawIntentResult:
        """Async entry point used by the agent/API layers (three-source fusion)."""
        key = self._cache_key(message, history)
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        self.cache_misses += 1

        started = time.monotonic()
        text = self._clean_text(message)

        rule_intent, rule_conf, rule_scores = self._match_intent(text)

        llm_res = None
        emb_res = None
        if self._llm_enabled and self._embedding_enabled:
            # 并发两路，避免串行等待一路 RTT；两路各自 try/except 兜底，互不影响。
            llm_res, emb_res = await asyncio.gather(
                self._llm_recognize(message, history),
                self._embedding_recognize(message),
            )
        else:
            if self._llm_enabled:
                llm_res = await self._llm_recognize(message, history)
            if self._embedding_enabled:
                emb_res = await self._embedding_recognize(message)

        if not self._llm_enabled and not self._embedding_enabled:
            result = self._build_result(text, rule_intent, rule_conf, rule_scores, started)
        else:
            intent, confidence, source_scores = self._fuse(
                rule_intent, rule_conf, rule_scores, llm_res, emb_res
            )
            result = self._build_result(text, intent, confidence, source_scores, started)

        if len(self._cache) >= self.max_cache:
            for k in list(self._cache)[: self.max_cache // 2]:
                del self._cache[k]
        self._cache[key] = result
        return result

    def recognize_sync(self, message: str) -> LawIntentResult:
        """Deterministic, offline recognition (rule/anchor path)."""
        started = time.monotonic()
        text = self._clean_text(message)
        intent, confidence, source_scores = self._match_intent(text)
        return self._build_result(text, intent, confidence, source_scores, started)

    def _build_result(
        self,
        text: str,
        intent: LawIntent,
        confidence: float,
        source_scores: Dict[str, float],
        started: float,
    ) -> LawIntentResult:
        risk_flags = detect_law_risk_flags(text)
        entities = self.entity_extractor.extract(text, intent=intent)
        entities["risk_flags"] = [flag.value for flag in risk_flags]
        return LawIntentResult(
            intent=intent,
            intent_group=LAW_INTENT_GROUPS.get(intent, "other"),
            urgency=self._urgency(text, risk_flags),
            risk_flags=risk_flags,
            entities=entities,
            confidence=confidence,
            source_scores=source_scores,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )

    def _match_intent(
        self,
        message: str,
    ) -> tuple[LawIntent, float, Dict[str, float]]:
        """Match local templates/patterns and return a deterministic result."""
        scores: Dict[LawIntent, float] = {}

        for intent in LAW_PATTERN_PRIORITY:
            if intent is LawIntent.OTHER:
                continue
            pattern_hits = [
                item
                for item in LAW_PATTERNS[intent]
                if item in message and has_unnegated_keyword(message, item)
            ]
            template_hits = [
                item
                for item in LAW_TEMPLATES[intent]
                if item in message and has_unnegated_keyword(message, item)
            ]
            if pattern_hits or template_hits:
                hit_count = len(pattern_hits) + len(template_hits)
                scores[intent] = 0.6 + min(0.35, 0.1 * (hit_count - 1))

        if not scores:
            return (
                LawIntent.OTHER,
                0.0,
                {"pattern": 0.0, "llm": 0.0, "embedding": 0.0},
            )

        best_score = max(scores.values())
        candidates = [intent for intent, score in scores.items() if score == best_score]
        best = min(candidates, key=lambda intent: LAW_PATTERN_PRIORITY.index(intent))
        return best, best_score, {"pattern": best_score, "llm": 0.0, "embedding": 0.0}

    async def _llm_recognize(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Optional[tuple[LawIntent, float]]:
        if self._client is None:
            return None
        try:
            examples = []
            for intent in LAW_PATTERN_PRIORITY:
                if intent is LawIntent.OTHER:
                    continue
                tpls = LAW_TEMPLATES.get(intent) or LAW_PATTERNS.get(intent) or []
                if tpls:
                    examples.append(f'  "{tpls[0]}" -> {intent.value}')
            ctx = ""
            if history:
                ctx = "\n最近对话:\n" + "\n".join(
                    f"  {self._clean_text(m.get('role', 'user'))}: {self._clean_text(m.get('content', ''))}"
                    for m in history[-3:]
                )
            intent_values = ", ".join(i.value for i in LAW_PATTERN_PRIORITY)
            prompt = self._clean_text(
                f"""你是律所法律意图分类助手。根据用户消息判断其属于哪个法律领域，返回 JSON。
如果无法判断，返回 other。

示例:
{chr(10).join(examples)}
{ctx}
用户消息: "{self._clean_text(message)}"

返回格式（仅 JSON）:
{{"intent": "<意图值>", "confidence": <0-1>, "reasoning": "<一句话说明>"}}

可选意图: {intent_values}"""
            )
            resp = await self._client.messages.create(
                model=self.model,
                max_tokens=128,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = extract_text_content(resp.content)
            s, e = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[s:e])
            try:
                intent = LawIntent(data["intent"])
            except (ValueError, KeyError):
                intent = LawIntent.OTHER
            try:
                conf = float(data.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            return intent, min(max(conf, 0.0), 1.0)
        except Exception as ex:
            logger.warning(f"LLM 意图识别失败: {ex}")
            return None

    async def _embedding_recognize(self, message: str) -> Optional[tuple[LawIntent, float]]:
        try:
            await self._load_template_embeddings()
            msg_vec = self._local_embedding(self._clean_text(message))
            best_intent, best_score = LawIntent.OTHER, 0.0
            for intent, vecs in self._tpl_embeddings.items():
                score = max(_cosine(msg_vec, v) for v in vecs)
                if score > best_score:
                    best_score, best_intent = score, intent
            if best_intent is LawIntent.OTHER or best_score <= 0.0:
                return None
            return best_intent, min(best_score, 1.0)
        except Exception as ex:
            logger.warning(f"Embedding 意图识别失败: {ex}")
            return None

    async def _load_template_embeddings(self) -> None:
        missing = [
            intent
            for intent in LawIntent
            if intent not in self._tpl_embeddings and (LAW_TEMPLATES.get(intent) or LAW_PATTERNS.get(intent))
        ]
        if not missing:
            return
        for intent in missing:
            texts = [t for t in (LAW_TEMPLATES.get(intent) or []) if t] or [
                t for t in (LAW_PATTERNS.get(intent) or []) if t
            ]
            self._tpl_embeddings[intent] = [self._local_embedding(t) for t in texts]

    def _fuse(
        self,
        rule_intent: LawIntent,
        rule_conf: float,
        rule_scores: Dict[str, float],
        llm_res: Optional[tuple[LawIntent, float]],
        emb_res: Optional[tuple[LawIntent, float]],
    ) -> tuple[LawIntent, float, Dict[str, float]]:
        # Only sources that actually produce a signal participate in the vote;
        # otherwise a zero-confidence rule miss would dilute a strong LLM hit.
        sources = []
        if rule_intent is not LawIntent.OTHER or rule_conf > 0:
            sources.append(("pattern", rule_intent, rule_conf))
        if llm_res is not None and (llm_res[0] is not LawIntent.OTHER or llm_res[1] > 0):
            sources.append(("llm", llm_res[0], llm_res[1]))
        if emb_res is not None and (emb_res[0] is not LawIntent.OTHER or emb_res[1] > 0):
            sources.append(("embedding", emb_res[0], emb_res[1]))

        if not sources:
            return rule_intent, rule_conf, rule_scores

        total_w = sum(self._weights.get(name, 0.0) for name, _, _ in sources)
        if total_w <= 0.0:
            return rule_intent, rule_conf, rule_scores

        score_map: Dict[LawIntent, float] = {}
        for name, intent, conf in sources:
            w = self._weights.get(name, 0.0)
            if w <= 0.0:
                continue
            score_map[intent] = score_map.get(intent, 0.0) + (w / total_w) * conf

        best = max(score_map, key=lambda k: score_map[k])
        best_score = score_map[best]

        # Rule anchor: never let a strong deterministic match become OTHER.
        if best is LawIntent.OTHER and rule_intent is not LawIntent.OTHER and rule_conf >= 0.6:
            best = rule_intent
            best_score = max(best_score, rule_conf)

        if best_score < self.confidence_threshold:
            best = LawIntent.OTHER

        source_scores = {
            "pattern": round(float(rule_conf), 4),
            "llm": round(float(llm_res[1]) if llm_res else 0.0, 4),
            "embedding": round(float(emb_res[1]) if emb_res else 0.0, 4),
            "fused": round(float(best_score), 4),
        }
        if best is not LawIntent.OTHER and best is not rule_intent and rule_conf >= 0.6:
            source_scores["refined_by_pattern"] = round(float(rule_conf), 4)
        return best, best_score, source_scores

    @staticmethod
    def _cache_key(message: str, history: Optional[List[Dict[str, str]]] = None) -> str:
        payload = {"message": _clean_any(message)[:200]}
        if history:
            payload["history"] = [
                {
                    "role": _clean_any(item.get("role", ""))[:20],
                    "content": _clean_any(item.get("content", ""))[:160],
                }
                for item in history[-3:]
            ]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _local_embedding(text: str, dims: int = 256) -> List[float]:
        normalized = _clean_any(text).lower().strip()
        vec = [0.0] * dims
        tokens = set()
        for n in (1, 2, 3):
            if len(normalized) >= n:
                tokens.update(normalized[i:i + n] for i in range(len(normalized) - n + 1))
        if not tokens:
            tokens.add(normalized)
        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % dims
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        return vec

    @staticmethod
    def _urgency(message: str, risk_flags: List[LawRiskFlag]) -> UrgencyLevel:
        if (
            has_unnegated_keyword(message, "紧急")
            or has_unnegated_keyword(message, "非常着急")
        ) and "不紧急" not in message:
            return UrgencyLevel.CRITICAL
        if LawRiskFlag.DETENTION in risk_flags:
            return UrgencyLevel.CRITICAL
        if LawRiskFlag.COURT_SOON in risk_flags:
            return UrgencyLevel.HIGH
        if LawRiskFlag.NO_LAWYER in risk_flags:
            return UrgencyLevel.HIGH
        if any(
            flag in risk_flags
            for flag in (
                LawRiskFlag.INJURY,
                LawRiskFlag.TRAFFIC_ACCIDENT,
                LawRiskFlag.FILED,
                LawRiskFlag.PROSECUTION,
            )
        ):
            return UrgencyLevel.HIGH
        return UrgencyLevel.MEDIUM

    @staticmethod
    def _clean_text(value: Any) -> str:
        if value is None:
            return ""
        text = str(value)
        return text.encode("utf-8", errors="ignore").decode("utf-8")
