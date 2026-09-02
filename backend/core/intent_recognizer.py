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
import re
import time
from dataclasses import dataclass, field
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


class IntentCategory(Enum):
    QUERY      = "query"       # 查询信息
    COMPLAINT  = "complaint"   # 投诉不满
    REQUEST    = "request"     # 请求操作
    GREETING   = "greeting"    # 问候
    ESCALATION = "escalation"  # 要求升级/转人工
    TECHNICAL  = "technical"   # 技术问题
    BILLING    = "billing"     # 账单/退款
    ACCOUNT    = "account"     # 账户管理
    FEEDBACK   = "feedback"    # 正面反馈
    ORDER_STATUS = "order_status"        # 订单状态
    LOGISTICS = "logistics"              # 物流配送
    REFUND = "refund"                    # 退款/退货
    INVOICE = "invoice"                  # 发票
    PAYMENT_ISSUE = "payment_issue"      # 支付/扣款异常
    ACCOUNT_SECURITY = "account_security" # 账户安全
    TECHNICAL_LOGIN = "technical_login"  # 登录认证故障
    TECHNICAL_CRASH = "technical_crash"  # 崩溃/错误码
    HUMAN_HANDOFF = "human_handoff"      # 转人工
    OTHER      = "other"


class UrgencyLevel(Enum):
    LOW      = 1
    MEDIUM   = 2
    HIGH     = 3
    CRITICAL = 4


@dataclass
class IntentResult:
    intent:     IntentCategory
    confidence: float
    urgency:    UrgencyLevel
    intent_group: str
    entities:   Dict[str, List[str]]   # 从消息中提取的实体
    reasoning:  str
    latency_ms: float
    source_scores: Dict[str, float] = field(default_factory=dict)


# ── Few-shot 模板（同时用于 LLM 示例和 Embedding 匹配）────────────────────────
_TEMPLATES: Dict[IntentCategory, List[str]] = {
    IntentCategory.QUERY:      ["我的订单状态是什么？", "如何重置密码？", "快递什么时候到？"],
    IntentCategory.COMPLAINT:  ["等了好几个小时！", "服务太差了！", "一直没人处理！"],
    IntentCategory.REQUEST:    ["帮我取消订单", "我需要修改地址", "请协助退款"],
    IntentCategory.GREETING:   ["你好", "嗨，有人吗", "早上好"],
    IntentCategory.ESCALATION: ["我要投诉！", "转人工客服", "找你们经理"],
    IntentCategory.TECHNICAL:  ["应用一直崩溃", "无法登录", "出现500错误"],
    IntentCategory.BILLING:    ["为什么扣了两次款？", "申请退款", "发票问题"],
    IntentCategory.ACCOUNT:    ["修改邮箱", "注销账户", "更新个人信息"],
    IntentCategory.FEEDBACK:   ["服务很棒！", "非常满意", "给个好评"],
    IntentCategory.ORDER_STATUS: ["我的订单现在是什么状态？", "订单有没有发货？", "订单处理到哪一步了？"],
    IntentCategory.LOGISTICS: ["快递什么时候到？", "物流一直不更新", "配送要多久？"],
    IntentCategory.REFUND: ["我要申请退款", "退货退款怎么处理？", "退款多久到账？"],
    IntentCategory.INVOICE: ["帮我开发票", "发票抬头怎么改？", "电子发票在哪里？"],
    IntentCategory.PAYMENT_ISSUE: ["为什么重复扣款？", "支付失败怎么办？", "这个月多扣了钱"],
    IntentCategory.ACCOUNT_SECURITY: ["账户被盗了", "发现异常登录", "我要重置密码"],
    IntentCategory.TECHNICAL_LOGIN: ["登录一直报401", "验证码收不到", "无法登录账号"],
    IntentCategory.TECHNICAL_CRASH: ["应用一直崩溃", "页面报500错误", "系统闪退"],
    IntentCategory.HUMAN_HANDOFF: ["转人工客服", "我要找人工", "请升级处理"],
}

_SPECIFIC_INTENTS = {
    IntentCategory.ORDER_STATUS,
    IntentCategory.LOGISTICS,
    IntentCategory.REFUND,
    IntentCategory.INVOICE,
    IntentCategory.PAYMENT_ISSUE,
    IntentCategory.ACCOUNT_SECURITY,
    IntentCategory.TECHNICAL_LOGIN,
    IntentCategory.TECHNICAL_CRASH,
    IntentCategory.HUMAN_HANDOFF,
}

_GENERIC_INTENTS = {
    IntentCategory.QUERY,
    IntentCategory.BILLING,
    IntentCategory.TECHNICAL,
    IntentCategory.ACCOUNT,
    IntentCategory.ESCALATION,
}

_INTENT_GROUPS: Dict[IntentCategory, IntentCategory] = {
    IntentCategory.ORDER_STATUS: IntentCategory.QUERY,
    IntentCategory.LOGISTICS: IntentCategory.QUERY,
    IntentCategory.REFUND: IntentCategory.BILLING,
    IntentCategory.INVOICE: IntentCategory.BILLING,
    IntentCategory.PAYMENT_ISSUE: IntentCategory.BILLING,
    IntentCategory.ACCOUNT_SECURITY: IntentCategory.ACCOUNT,
    IntentCategory.TECHNICAL_LOGIN: IntentCategory.TECHNICAL,
    IntentCategory.TECHNICAL_CRASH: IntentCategory.TECHNICAL,
    IntentCategory.HUMAN_HANDOFF: IntentCategory.ESCALATION,
}

# 紧急关键词
_URGENCY_KEYWORDS = {
    UrgencyLevel.CRITICAL: ["紧急", "emergency", "urgent", "asap", "立刻"],
    UrgencyLevel.HIGH:     ["今天", "马上", "尽快", "hurry", "now"],
    UrgencyLevel.MEDIUM:   ["这周", "soon", "快点"],
}


def _cosine(a: List[float], b: List[float]) -> float:
    """纯 Python 余弦相似度，不依赖 numpy。"""
    dot = sum(x * y for x, y in zip(a, b))
    na  = sum(x * x for x in a) ** 0.5
    nb  = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class IntentRecognizer:
    """
    端到端意图识别器。

    初始化时不加载任何本地模型，所有 AI 能力通过 Anthropic API 调用。
    模板 Embedding 在首次请求时懒加载并缓存，后续复用。
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        confidence_threshold: float = 0.5,
    ):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client    = AsyncAnthropic(**kwargs)
        self.model     = model
        self.threshold = confidence_threshold
        # 本地字符 n-gram 向量始终可用；如果未来客户端暴露 embeddings 资源，
        # _embed_text 会优先尝试远端向量，否则自动回退本地向量。
        self._embedding_enabled = True

        self._tpl_embeddings: Dict[IntentCategory, List[List[float]]] = {}
        self._cache: Dict[str, IntentResult] = {}
        self.cache_hits   = 0
        self.cache_misses = 0

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    async def recognize(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> IntentResult:
        """
        识别用户意图。

        history 格式：[{"role": "user"/"assistant", "content": "..."}]
        """
        key = self._cache_key(message, history)
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        self.cache_misses += 1

        t0 = time.monotonic()

        # LLM 和 Embedding 并行（Embedding 不可用时跳过）
        llm_task = asyncio.create_task(self._llm_recognize(message, history))
        emb_task = asyncio.create_task(self._embedding_recognize(message)) if self._embedding_enabled else None
        pat      = self._pattern_recognize(message)

        if emb_task:
            llm, emb = await asyncio.gather(llm_task, emb_task)
        else:
            llm = await llm_task
            emb = {"intent": IntentCategory.OTHER, "confidence": 0.0}

        intent, confidence, source_scores = self._vote(llm, emb, pat)
        entities = self._extract_entities(message)
        urgency  = self._urgency(message, intent)

        result = IntentResult(
            intent=intent,
            confidence=confidence,
            urgency=urgency,
            intent_group=self._intent_group(intent),
            entities=entities,
            reasoning=llm.get("reasoning", ""),
            latency_ms=(time.monotonic() - t0) * 1000,
            source_scores=source_scores,
        )

        # LRU 缓存
        if len(self._cache) >= 1000:
            for k in list(self._cache)[:500]:
                del self._cache[k]
        self._cache[key] = result
        return result

    def learn(self, message: str, correct: IntentCategory) -> None:
        """在线学习：将纠正样本加入模板，清除对应 Embedding 缓存。"""
        tpls = _TEMPLATES.setdefault(correct, [])
        if message not in tpls:
            tpls.append(message)
            self._tpl_embeddings.pop(correct, None)  # 下次重新计算
            self._cache.clear()  # 模板更新后旧缓存可能对应过时结果
            logger.info(f"学习新样本 → {correct.value}: {message[:40]}")

    # ── 三路识别策略 ──────────────────────────────────────────────────────────

    async def _llm_recognize(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]],
    ) -> Dict[str, Any]:
        """策略 1：LLM 语义理解（Few-shot + 上下文）。"""
        message = self._clean_text(message)
        # 构建 Few-shot 示例
        examples = "\n".join(
            f'  消息: "{t}" → 意图: {cat.value}'
            for cat, tpls in _TEMPLATES.items()
            for t in tpls[:1]  # 每类取 1 条，控制 prompt 长度
        )
        # 最近 3 轮对话上下文
        ctx = ""
        if history:
            ctx = "\n最近对话:\n" + "\n".join(
                f"  {self._clean_text(m.get('role', 'user'))}: {self._clean_text(m.get('content', ''))}"
                for m in history[-3:]
            )

        prompt = f"""你是客服意图分析专家。根据示例判断用户意图，返回 JSON。
如果用户问题能匹配细粒度业务意图，请优先返回细粒度意图，而不是宽泛大类。
例如退款优先返回 refund，发票优先返回 invoice，登录故障优先返回 technical_login。

        {ctx}
        用户消息: "{message}"

返回格式（仅 JSON，不要其他文字）:
{{"intent": "<意图值>", "confidence": <0-1>, "reasoning": "<一句话说明>"}}

可选意图: {", ".join(c.value for c in IntentCategory)}"""
        prompt = self._clean_text(prompt)

        try:
            resp = await self.client.messages.create(
                model=self.model,
                max_tokens=256,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = extract_text_content(resp.content)
            s, e = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[s:e])
            try:
                data["intent"] = IntentCategory(data["intent"])
            except ValueError:
                data["intent"] = IntentCategory.OTHER
            return data
        except Exception as ex:
            logger.warning(f"LLM 识别失败: {ex}")
            return {"intent": IntentCategory.OTHER, "confidence": 0.0, "reasoning": "LLM 失败", "failed": True}

    async def _embedding_recognize(self, message: str) -> Dict[str, Any]:
        """策略 2：Embedding 向量相似度匹配。"""
        try:
            await self._load_template_embeddings()
            msg_vec = await self._embed_text(message)

            best_cat, best_score = IntentCategory.OTHER, 0.0
            for cat, vecs in self._tpl_embeddings.items():
                score = max(_cosine(msg_vec, v) for v in vecs)
                if score > best_score:
                    best_score, best_cat = score, cat

            return {"intent": best_cat, "confidence": best_score}
        except Exception as ex:
            logger.warning(f"Embedding 识别失败: {ex}")
            return {"intent": IntentCategory.OTHER, "confidence": 0.0}

    def _pattern_recognize(self, message: str) -> Dict[str, Any]:
        """策略 3：关键词模式匹配（同步，零延迟兜底）。"""
        msg = message.lower()
        specific_patterns = {
            IntentCategory.HUMAN_HANDOFF: ["转人工", "人工客服", "找人工"],
            IntentCategory.ORDER_STATUS: ["订单状态", "发货了吗", "处理到哪", "order status"],
            IntentCategory.LOGISTICS: ["物流", "快递", "配送", "运单", "delivery", "shipping"],
            IntentCategory.REFUND: ["退款", "退货", "refund", "return"],
            IntentCategory.INVOICE: ["发票", "抬头", "税号", "invoice"],
            IntentCategory.PAYMENT_ISSUE: ["重复扣款", "多扣", "支付失败", "扣费", "payment failed"],
            IntentCategory.ACCOUNT_SECURITY: ["被盗", "异常登录", "重置密码", "两步验证", "安全"],
            IntentCategory.TECHNICAL_LOGIN: ["无法登录", "登录失败", "401", "验证码"],
            IntentCategory.TECHNICAL_CRASH: ["崩溃", "闪退", "500", "报错", "crash"],
        }
        generic_patterns = {
            IntentCategory.ESCALATION: ["投诉", "经理", "supervisor"],
            IntentCategory.COMPLAINT:  ["太差", "糟糕", "horrible", "等了很久"],
            IntentCategory.QUERY:      ["?", "？", "怎么", "什么", "status"],
            IntentCategory.REQUEST:    ["帮我", "需要", "please", "help"],
            IntentCategory.GREETING:   ["你好", "嗨", "hello", "hi"],
            IntentCategory.BILLING:    ["退款", "扣款", "发票", "refund"],
            IntentCategory.TECHNICAL:  ["崩溃", "报错", "error", "crash"],
            IntentCategory.ACCOUNT:    ["密码", "邮箱", "账户", "password"],
        }

        best_cat, best_score = self._best_pattern_match(msg, specific_patterns)
        if best_cat != IntentCategory.OTHER:
            return {"intent": best_cat, "confidence": best_score}

        best_cat, best_score = self._best_pattern_match(msg, generic_patterns)
        return {"intent": best_cat, "confidence": best_score}

    # ── 投票合并 ──────────────────────────────────────────────────────────────

    def _vote(self, llm: Dict, emb: Dict, pat: Dict) -> tuple[IntentCategory, float, Dict[str, float]]:
        """加权投票。返回最终意图、融合置信度和各路来源得分。"""
        source_scores = {
            "llm": float(llm.get("confidence", 0.0) or 0.0),
            "embedding": float(emb.get("confidence", 0.0) or 0.0),
            "pattern": float(pat.get("confidence", 0.0) or 0.0),
        }
        if llm.get("failed"):
            if emb.get("intent") != IntentCategory.OTHER and emb.get("confidence", 0.0) > 0:
                return emb["intent"], source_scores["embedding"], source_scores
            if pat.get("intent") != IntentCategory.OTHER and pat.get("confidence", 0.0) > 0:
                return pat["intent"], source_scores["pattern"], source_scores
            return IntentCategory.OTHER, 0.0, source_scores

        if self._embedding_enabled:
            weights = [(llm, 0.7), (emb, 0.2), (pat, 0.1)]
        else:
            weights = [(llm, 0.85), (pat, 0.15)]
        scores: Dict[IntentCategory, float] = {}
        for result, w in weights:
            cat  = result.get("intent", IntentCategory.OTHER)
            conf = result.get("confidence", 0.0)
            scores[cat] = scores.get(cat, 0.0) + w * conf

        best = max(scores, key=scores.get)  # type: ignore
        best_score = scores[best]
        pat_intent = pat.get("intent", IntentCategory.OTHER)
        pat_conf = float(pat.get("confidence", 0.0) or 0.0)
        if best in _GENERIC_INTENTS and pat_intent in _SPECIFIC_INTENTS and pat_conf >= 0.5 and best_score < 0.8:
            source_scores["refined_by_pattern"] = pat_conf
            return pat_intent, max(best_score, pat_conf), source_scores
        if best_score < self.threshold:
            return IntentCategory.OTHER, best_score, source_scores
        return best, best_score, source_scores

    # ── 实体提取 ──────────────────────────────────────────────────────────────

    def _extract_entities(self, message: str) -> Dict[str, List[str]]:
        """用规则提取高价值实体，避免每次识别都额外调用 LLM。"""
        message = self._clean_text(message)
        return {
            "order_id": self._unique(re.findall(r"(?:订单号?|order(?:_id)?|#)\s*[:：#]?\s*([A-Za-z0-9_-]{4,32})", message, re.I)),
            "product": [],
            "date": self._unique(re.findall(r"(今天|明天|昨天|本周|这周|下周|\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)", message)),
            "amount": self._unique(re.findall(r"((?:¥|￥)\s*\d+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?\s*(?:元|块|rmb|cny|usd|美元))", message, re.I)),
            "error_code": self._unique(
                re.findall(r"(?:error(?:_code)?|错误码|状态码|http)\s*[:：#]?\s*([45]\d{2})\b", message, re.I)
                + re.findall(r"\b([45]\d{2})\b", message)
            ),
        }

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    async def _load_template_embeddings(self) -> None:
        """懒加载所有模板的 Embedding（只在首次调用时执行）。"""
        missing = [cat for cat in _TEMPLATES if cat not in self._tpl_embeddings]
        if not missing:
            return

        all_texts = [t for cat in missing for t in _TEMPLATES[cat]]
        vecs = [await self._embed_text(text) for text in all_texts]
        idx = 0
        for cat in missing:
            n = len(_TEMPLATES[cat])
            self._tpl_embeddings[cat] = vecs[idx: idx + n]
            idx += n

    async def _embed_text(self, text: str) -> List[float]:
        """
        生成文本向量。

        如果未来接入的官方/兼容客户端提供 embeddings.create，会优先使用远端向量；
        当前 Anthropic SDK 没有该资源时，退化为字符 n-gram 哈希向量。这样不会因为
        Embedding 服务缺失导致三路融合中断。
        """
        embeddings = getattr(self.client, "embeddings", None)
        if embeddings is not None:
            try:
                resp = await embeddings.create(model="voyage-3-lite", input=[text])
                return list(resp.data[0].embedding)
            except Exception as ex:
                logger.warning(f"远端 Embedding 失败，使用本地向量兜底: {ex}")

        return self._local_embedding(text)

    @staticmethod
    def _local_embedding(text: str, dims: int = 256) -> List[float]:
        """稳定的字符 n-gram 哈希向量，用于无远端 Embedding 时的语义近似匹配。"""
        normalized = text.lower().strip()
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

    def _urgency(self, message: str, intent: IntentCategory) -> UrgencyLevel:
        msg = message.lower()
        for level, kws in _URGENCY_KEYWORDS.items():
            if any(kw in msg for kw in kws):
                return level
        if intent in (IntentCategory.ESCALATION, IntentCategory.HUMAN_HANDOFF):
            return UrgencyLevel.HIGH
        if intent == IntentCategory.COMPLAINT:
            return UrgencyLevel.MEDIUM
        return UrgencyLevel.LOW

    def _cache_key(self, message: str, history: Optional[List[Dict[str, str]]] = None) -> str:
        payload = {"message": self._clean_text(message)[:200]}
        if history:
            payload["history"] = [
                {
                    "role": self._clean_text(item.get("role", ""))[:20],
                    "content": self._clean_text(item.get("content", ""))[:160],
                }
                for item in history[-3:]
            ]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _unique(values: List[str]) -> List[str]:
        return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))

    @staticmethod
    def _best_pattern_match(
        message: str,
        patterns: Dict[IntentCategory, List[str]],
    ) -> tuple[IntentCategory, float]:
        best_cat, best_score = IntentCategory.OTHER, 0.0
        for cat, kws in patterns.items():
            hits = sum(1 for kw in kws if kw in message)
            if not hits:
                continue
            # 单个明确业务关键词就给可用置信度；多个关键词命中时提高置信度。
            score = min(1.0, 0.5 + 0.25 * (hits - 1))
            if score > best_score:
                best_score, best_cat = score, cat
        return best_cat, best_score

    @staticmethod
    def _intent_group(intent: IntentCategory) -> str:
        return _INTENT_GROUPS.get(intent, intent).value

    @staticmethod
    def _clean_text(value: Any) -> str:
        """移除 Unicode 代理字符，避免 HTTP 客户端编码 prompt 时崩溃。"""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")

    @property
    def cache_stats(self) -> Dict[str, Any]:
        total = self.cache_hits + self.cache_misses
        return {
            "size": len(self._cache),
            "hits": self.cache_hits,
            "misses": self.cache_misses,
            "hit_rate": self.cache_hits / total if total else 0.0,
        }




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
