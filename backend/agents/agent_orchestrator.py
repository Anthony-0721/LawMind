"""

LawMind 律所多 Agent 路由与编排

路由策略（三层决策）：
  1. 意图路由 —— 根据 LawIntent 直接映射到律所 Agent
  2. 性能路由 —— 同类 Agent 有多个时，选成功率最高、延迟最低的
  3. 降级路由 —— 刑事/民事专属 Agent 不可用时降级到 ReceptionAgent

并行协作：
  - 同一问题同时涉及刑事与民事领域时，可并行派发
  - 结果由 Orchestrator 合并后返回

升级机制：
  - 刑事拘留、即将开庭、明确预约律师或紧急度 CRITICAL 时转人工升级
"""
import asyncio
import inspect
import json
import logging
import os
import time
import uuid
from collections import deque
from datetime import datetime
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from anthropic import AsyncAnthropic

from agents.tools import (
    AgentToolSpec,
    build_shared_law_rag_tools,
    civil_tools,
    criminal_tools,
    build_escalation_tools,
    reception_tools,
    resolve_legal_domain,
    validate_contact,
)
from core.intent_recognizer import LawIntentRecognizer, UrgencyLevel
from core.law_domain import LawIntent, LawRiskFlag
from core.llm_utils import extract_text_content

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────────

class AgentType(Enum):
    RECEPTION  = "reception"   # 律所接待与领域判断
    CRIMINAL   = "criminal"    # 刑事辩护/醉驾风险
    CIVIL      = "civil"       # 民事法律咨询
    ESCALATION = "escalation"  # 人工升级与留资交接


@dataclass(frozen=True)
class AgentProfile:

    role: str
    mission: str
    workflow: Tuple[str, ...]
    input_contract: Tuple[str, ...]
    output_contract: Tuple[str, ...]
    handoff_conditions: Tuple[str, ...] = ()
    tool_scope: Tuple[str, ...] = ()
    model: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 1024


_LEGACY_ENV_PREFIX = "RETIRED_"

_CLARIFICATION_RESPONSE = (
    "我还不能确定您希望咨询哪个法律领域。请补充是刑事辩护、劳动争议、婚姻家事、合同纠纷、交通事故、民间借贷，还是需要预约律师？"
)


def _env_raw(name: str, default: str) -> str:
    """Read current LAWMIND config, then fall back to the legacy deployment env name."""
    value = os.getenv(name)
    if value not in (None, ""):
        return value
    suffix = name[len("LAWMIND_"):] if name.startswith("LAWMIND_") else name
    legacy = os.getenv(_LEGACY_ENV_PREFIX + suffix, default)
    return legacy if legacy not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    """读取可选浮点配置；错误配置不应阻塞服务启动。"""
    try:
        return float(_env_raw(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("忽略非法浮点配置 %s=%r", name, _env_raw(name, str(default)))
        return default


def _env_int(name: str, default: int) -> int:
    """读取可选整数配置；错误配置不应阻塞服务启动。"""
    try:
        return int(_env_raw(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("忽略非法整数配置 %s=%r", name, _env_raw(name, str(default)))
        return default


@dataclass
class AgentStats:
    """Agent 运行时统计，供 Monitor 和路由决策使用。"""
    total:     int   = 0
    success:   int   = 0
    total_ms:  float = 0.0
    monitor_penalty: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 1.0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.total if self.total else 0.0

    def routing_score(self) -> float:
        """路由评分：成功率高、延迟低的 Agent 得分高。"""
        latency_score = 1.0 / (1.0 + self.avg_ms / 1000)
        base_score = self.success_rate * 0.7 + latency_score * 0.3
        return base_score * max(0.0, 1.0 - self.monitor_penalty)


@dataclass
class AgentResponse:
    agent_type:  AgentType
    content:     str
    success:     bool
    confidence:  float = 1.0
    latency_ms:  float = 0.0
    escalate:    bool  = False   # 是否需要升级
    tools_used:  List[str] = field(default_factory=list)
    tool_traces: List[Dict[str, Any]] = field(default_factory=list)
    skill_prompt: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Request:
    message:     str
    user_id:     str
    conv_id:     str
    context:     str = ""        # 来自 MemoryManager 的格式化上下文
    history:     Optional[List[Dict[str, str]]] = None  # 对话历史，传给意图识别
    entities:    Dict[str, List[str]] = field(default_factory=dict)
    intent:      Optional[LawIntent] = None
    intent_group: Optional[str] = None
    urgency:     Optional[UrgencyLevel]   = None
    risk_flags:  List[LawRiskFlag] = field(default_factory=list)
    intent_confidence: float = 1.0
    request_id:  str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    contact_name: str = ""
    contact_phone: str = ""
    name: str = ""
    phone: str = ""
    contact: Dict[str, Any] = field(default_factory=dict)
    legal_domain: str = ""
    city: str = ""
    preferred_time: str = ""
    case_stage: str = ""
    consent: bool = False


def _entity_value(req: Request, key: str, default: str = "") -> str:
    raw = getattr(req, key, None)
    if raw not in (None, ""):
        return str(raw)
    values = (getattr(req, "entities", None) or {}).get(key)
    if values:
        first = values[0] if isinstance(values, (list, tuple)) else values
        if first not in (None, ""):
            return str(first)
    if req.context:
        try:
            parsed = json.loads(req.context)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            value = parsed.get(key)
            if value not in (None, ""):
                return str(value)
    return default


_TRUE_CONSENT_TOKENS = frozenset({"1", "true", "yes", "是", "同意", "愿意"})
_PERSISTED_CONSULTATION_STATUSES = frozenset({
    "PENDING",
    "CONTACTED",
    "BOOKED",
    "CLOSED",
})


def _parse_consent(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_CONSENT_TOKENS:
            return True
        return False
    return bool(value)


def _is_persisted_consultation(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("success") is False or result.get("persisted") is False:
        return False
    return result.get("status") in _PERSISTED_CONSULTATION_STATUSES


def _request_contact_args(req: Request) -> Dict[str, Any]:
    name = _entity_value(req, "contact_name", "") or _entity_value(req, "name", "")
    phone = _entity_value(req, "contact_phone", "") or _entity_value(req, "phone", "")
    contact = getattr(req, "contact", None)
    if isinstance(contact, dict):
        name = name or str(contact.get("contact_name") or contact.get("name") or "")
        phone = phone or str(contact.get("contact_phone") or contact.get("phone") or "")
    city = _entity_value(req, "city", "")
    preferred_time = _entity_value(req, "preferred_time", "")
    case_stage = _entity_value(req, "case_stage", "")
    legal_domain = _entity_value(req, "legal_domain", "")
    consent_raw = getattr(req, "consent", False)
    if not consent_raw:
        consent_raw = _entity_value(req, "consent", "")
    if isinstance(contact, dict):
        city = city or str(contact.get("city") or "")
        preferred_time = preferred_time or str(contact.get("preferred_time") or "")
        case_stage = case_stage or str(contact.get("case_stage") or "")
        if not consent_raw:
            consent_raw = contact.get("consent", False)
    consent = _parse_consent(consent_raw)
    return {
        "name": name,
        "phone": phone,
        "contact": {"name": name, "phone": phone} if name and phone else {},
        "consent": consent,
        "city": city,
        "preferred_time": preferred_time,
        "case_stage": case_stage,
        "legal_domain": legal_domain,
    }


@dataclass
class OrchestratorResult:
    request_id:  str
    response:    str
    agent_type:  AgentType
    intent:      Optional[LawIntent]
    escalated:   bool  = False
    latency_ms:  float = 0.0
    agent_types: List[AgentType] = field(default_factory=list)
    primary_agent: Optional[AgentType] = None
    supporting_agents: List[AgentType] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    tool_traces: List[Dict[str, Any]] = field(default_factory=list)
    skill_prompt: str = ""
    routing_reason: str = ""
    routing_confidence: float = 0.0


@dataclass
class RoutingDecision:
    """一次请求的结构化路由决策。"""
    primary_agent: AgentType
    supporting_agents: List[AgentType] = field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0

    @property
    def agent_types(self) -> List[AgentType]:
        return [self.primary_agent] + self.supporting_agents

    @property
    def multi_agent(self) -> bool:
        return bool(self.supporting_agents)


# ── 基础 Agent ────────────────────────────────────────────────────────────────

class BaseAgent:
    """所有 Agent 的基类，封装 LLM 调用、角色契约和统计。"""

    agent_type: AgentType
    system_prompt: str
    profile: AgentProfile

    def __init__(
        self,
        client: AsyncAnthropic,
        model: str,
        skill_manager: Optional[Any] = None,
        profile: Optional[AgentProfile] = None,
    ):
        self._client = client
        self.profile = profile or self.profile
        self._model  = self.profile.model or model
        self._skill_manager = skill_manager
        self.stats   = AgentStats()
        self._last_tools_used: List[str] = []
        self._last_tool_traces: List[Dict[str, Any]] = []
        self._last_skill_prompt: str = ""
        self._shared_tools: Dict[str, AgentToolSpec] = {}

    def get_tools(self) -> Dict[str, AgentToolSpec]:
        """返回该角色真实可调用的工具白名单。"""
        return dict(self._shared_tools)

    def set_shared_tools(self, tools: Optional[Dict[str, AgentToolSpec]]) -> None:
        self._shared_tools = dict(tools or {})

    @staticmethod
    def _redact_pii(payload: Any) -> Any:
        """Return a copy with PII masked recursively in dicts and lists."""
        sensitive_keys = {
            "contact_name",
            "name",
            "phone",
            "contact_phone",
            "wechat",
            "email",
            "id_number",
            "identity_number",
        }
        if isinstance(payload, dict):
            return {
                key: "***" if key in sensitive_keys else BaseAgent._redact_pii(value)
                for key, value in payload.items()
            }
        if isinstance(payload, list):
            return [BaseAgent._redact_pii(item) for item in payload]
        if isinstance(payload, tuple):
            return tuple(BaseAgent._redact_pii(item) for item in payload)
        return payload

    async def _invoke_available_tool(
        self,
        name: str,
        req: Request,
        args: Dict[str, Any],
        tool_use_id: str,
    ) -> tuple[Optional[Any], Optional[Dict[str, Any]]]:
        """Invoke a whitelisted tool without requiring an LLM round trip."""
        spec = self.get_tools().get(name)
        if spec is None:
            return None, None
        tool_t0 = time.monotonic()
        call_success = True
        result_success: Optional[bool] = None
        error_text = ""
        result: Any = None
        try:
            result = spec.handler(req, args)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, dict) and "success" in result:
                result_success = bool(result.get("success"))
            else:
                result_success = True
        except Exception as ex:
            call_success = False
            result_success = False
            error_text = str(ex)
            result = {"success": False, "error": error_text}
        trace = {
            "agent_type": self.agent_type.value,
            "tool_name": name,
            "tool_use_id": tool_use_id,
            "input": self._redact_pii(dict(args or {})),
            "success": call_success,
            "result_success": result_success,
            "latency_ms": round((time.monotonic() - tool_t0) * 1000, 1),
            "cached": bool(result.get("cached")) if isinstance(result, dict) else False,
            "reranked": bool(result.get("reranked")) if isinstance(result, dict) else False,
            "error": error_text,
        }
        return result, trace

    async def handle(self, req: Request) -> AgentResponse:
        t0 = time.monotonic()
        self.stats.total += 1
        self._last_tools_used = []
        self._last_tool_traces = []
        try:
            content = await self._call_llm(req)
            ms = (time.monotonic() - t0) * 1000
            self.stats.success += 1
            self.stats.total_ms += ms
            escalate = self._needs_escalation(content)
            return AgentResponse(
                agent_type=self.agent_type,
                content=content,
                success=True,
                latency_ms=ms,
                escalate=escalate,
                tools_used=list(self._last_tools_used),
                tool_traces=list(self._last_tool_traces),
            )
        except Exception as ex:
            ms = (time.monotonic() - t0) * 1000
            self.stats.total_ms += ms
            logger.error(f"{self.agent_type.value} 处理失败: {ex}")
            return AgentResponse(
                agent_type=self.agent_type,
                content="抱歉，处理您的请求时出现问题，请稍后重试。",
                success=False,
                latency_ms=ms,
                tool_traces=list(self._last_tool_traces),
            )

    async def _call_llm(self, req: Request) -> str:
        def _clean(s: str) -> str:
            return s.encode("utf-8", errors="ignore").decode("utf-8")

        messages = []
        if req.context:
            messages.append({"role": "user", "content": f"[背景信息]\n{_clean(req.context)}"})
            messages.append({"role": "assistant", "content": "好的，我已了解背景信息。"})
        if req.entities:
            entities_text = json.dumps(req.entities, ensure_ascii=False)
            messages.append({"role": "user", "content": f"[结构化实体]\n{_clean(entities_text)}"})
            messages.append({"role": "assistant", "content": "好的，我会结合这些结构化实体处理。"})
        role_packet = self._build_role_packet(req)
        if role_packet:
            messages.append({"role": "user", "content": f"[角色输入契约]\n{_clean(role_packet)}"})
            messages.append({"role": "assistant", "content": "好的，我会按照该角色的输入和输出契约处理。"})
        messages.append({"role": "user", "content": _clean(req.message)})

        tools = self.get_tools()
        tools_used: List[str] = []
        tool_traces: List[Dict[str, Any]] = []
        for _ in range(3):
            request_kwargs: Dict[str, Any] = {
                "model": self._model,
                "max_tokens": self.profile.max_tokens,
                "temperature": self.profile.temperature,
                "system": self._build_system_prompt(req),
                "messages": messages,
            }
            if tools:
                request_kwargs["tools"] = [
                    {
                        "name": spec.name,
                        "description": spec.description,
                        "input_schema": spec.input_schema,
                    }
                    for spec in tools.values()
                ]
            resp = await self._client.messages.create(**request_kwargs)
            tool_uses = [block for block in (resp.content or []) if self._block_type(block) == "tool_use"]
            if not tool_uses:
                self._last_tools_used = tools_used
                self._last_tool_traces = tool_traces
                return extract_text_content(resp.content)

            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for block in tool_uses:
                name = self._block_value(block, "name")
                tool_use_id = self._block_value(block, "id")
                args = self._block_value(block, "input") or {}
                spec = tools.get(name)
                tool_t0 = time.monotonic()
                call_success = True
                result_success: Optional[bool] = None
                error_text = ""
                if spec is None:
                    call_success = False
                    result: Any = {"success": False, "error": f"工具不在 {self.agent_type.value} Agent 白名单中"}
                    error_text = result["error"]
                else:
                    try:
                        self._validate_tool_input(spec, args)
                        result = spec.handler(req, args)
                        if inspect.isawaitable(result):
                            result = await result
                        if isinstance(result, dict) and "success" in result:
                            result_success = bool(result.get("success"))
                        if call_success and result_success is not False:
                            tools_used.append(name)
                    except Exception as ex:
                        call_success = False
                        logger.warning("Agent 工具 %s 执行失败: %s", name, ex)
                        error_text = str(ex)
                        result = {"success": False, "error": error_text}
                tool_latency_ms = (time.monotonic() - tool_t0) * 1000
                if not error_text and isinstance(result, dict):
                    error_text = str(result.get("error", "") or "")
                tool_traces.append(
                    {
                        "agent_type": self.agent_type.value,
                        "tool_name": name,
                        "tool_use_id": tool_use_id,
                        "input": self._redact_pii(dict(args)),
                        "success": call_success,
                        "result_success": result_success,
                        "latency_ms": round(tool_latency_ms, 1),
                        "cached": bool(result.get("cached")) if isinstance(result, dict) else False,
                        "reranked": bool(result.get("reranked")) if isinstance(result, dict) else False,
                        "error": error_text,
                    }
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
            messages.append({"role": "user", "content": tool_results})

        self._last_tools_used = tools_used
        self._last_tool_traces = tool_traces
        raise RuntimeError(f"{self.agent_type.value} 工具调用超过最大轮数")

    @staticmethod
    def _block_type(block: Any) -> Optional[str]:
        if isinstance(block, dict):
            return block.get("type")
        return getattr(block, "type", None)

    @staticmethod
    def _block_value(block: Any, key: str) -> Any:
        if isinstance(block, dict):
            return block.get(key)
        return getattr(block, key, None)

    @staticmethod
    def _validate_tool_input(spec: AgentToolSpec, args: Any) -> None:
        if not isinstance(args, dict):
            raise ValueError("工具参数必须是 JSON 对象")
        schema = spec.input_schema
        for field_name in schema.get("required", []):
            if field_name not in args:
                raise ValueError(f"缺少必需参数: {field_name}")
        properties = schema.get("properties", {})
        unknown = set(args) - set(properties)
        if unknown and schema.get("additionalProperties") is False:
            raise ValueError(f"不允许的工具参数: {', '.join(sorted(unknown))}")
        type_map = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "array": list, "object": dict}
        for key, value in args.items():
            expected = properties.get(key, {}).get("type")
            if expected in type_map and not isinstance(value, type_map[expected]):
                raise ValueError(f"参数 {key} 类型错误，期望 {expected}")

    def _build_system_prompt(self, req: Request) -> str:
        """把角色契约和动态 Skills 拼入 system prompt。"""
        profile_prompt = (
            f"\n\n[角色契约]\n"
            f"角色：{self.profile.role}\n"
            f"职责：{self.profile.mission}\n"
            f"处理流程：{' -> '.join(self.profile.workflow)}\n"
            f"可用输入：{'；'.join(self.profile.input_contract)}\n"
            f"输出要求：{'；'.join(self.profile.output_contract)}\n"
            f"升级条件：{'；'.join(self.profile.handoff_conditions) or '无，按通用客服规则处理'}\n"
            f"允许的数据/工具范围：{'、'.join(self.profile.tool_scope) or '仅使用当前请求上下文'}\n"
            "不要声称已形成正式法律意见，或完成未提供的查询、留资、律师推荐等操作；缺少证据时明确说明需要补充或人工确认。"
        )
        base_prompt = f"{self.system_prompt}{profile_prompt}"
        if self._skill_manager is None:
            return base_prompt
        skill_prompt = self._skill_manager.prompt_for(req.message, self.agent_type.value)
        if not skill_prompt:
            return base_prompt
        return f"{base_prompt}\n\n[动态 Skills]\n{skill_prompt}"

    def _build_role_packet(self, req: Request) -> str:
        """给子 Agent 的确定性输入包；子类可补充领域字段。"""
        packet = {
            "agent_type": self.agent_type.value,
            "intent": req.intent.value if req.intent else None,
            "intent_group": req.intent_group,
            "urgency": req.urgency.name if req.urgency else None,
            "intent_confidence": round(req.intent_confidence, 4),
            "risk_flags": [flag.value for flag in req.risk_flags],
            "available_entities": req.entities or {},
        }
        return json.dumps(packet, ensure_ascii=False)

    def _needs_escalation(self, content: str) -> bool:
        """检测 Agent 是否建议升级（简单关键词检测）。"""
        keywords = ["转人工", "人工客服", "escalate", "specialist", "无法处理"]
        return any(kw in content for kw in keywords)


class ReceptionAgent(BaseAgent):
    """律所首层接待：识别领域、检查缺失信息并处理律所服务问题。"""

    agent_type = AgentType.RECEPTION
    profile = AgentProfile(
        role="律所接待与领域判断",
        mission="识别法律领域、确认缺失信息、处理律所服务/收费咨询；不确定时引导补充关键事实。",
        workflow=("复述诉求", "识别法律领域", "检查缺失字段", "给出下一步"),
        input_contract=("用户消息", "对话历史", "意图与紧急度", "风险信号", "结构化实体"),
        output_contract=("先回应核心问题", "说明已识别领域", "列出缺失关键信息", "给出下一步或升级边界"),
        handoff_conditions=("用户明确要求预约律师或转人工", "刑事拘留等高风险信号", "多次无法判断法律领域"),
        tool_scope=(
            "search_law_knowledge",
            "identify_legal_domain",
            "check_missing_facts",
            "build_reception_summary",
        ),
        temperature=0.2,
        max_tokens=900,
    )
    system_prompt = (
        "你是律所接待与分诊助手。优先确认客户身份、地区、案件时间和事实；"
        "不承诺诉讼结果，不提供正式法律意见。业务不明确时只问必要问题。"
    )

    def get_tools(self) -> Dict[str, AgentToolSpec]:
        tools = dict(super().get_tools())
        if "search_law_knowledge" not in tools:
            tools.update(build_shared_law_rag_tools(None))
        tools.update(reception_tools())
        return tools


class CriminalDefenseAgent(BaseAgent):
    """刑事辩护/醉驾咨询：整理事实、阶段、证据与风险。"""

    agent_type = AgentType.CRIMINAL
    profile = AgentProfile(
        role="刑事辩护与醉驾风险评估",
        mission="分析刑事/醉驾咨询的事实、案件阶段和风险信号，给出初步信息、需补充材料和下一步建议。",
        workflow=(
            "确认咨询对象与事实",
            "识别案件阶段",
            "评估拘留/程序/时效风险",
            "给出材料与下一步",
            "判断是否转人工",
        ),
        input_contract=(
            "用户消息",
            "对话历史",
            "当事人身份",
            "案件阶段",
            "发生时间",
            "城市",
            "血液酒精数值",
            "拘留状态",
            "委托律师状态",
            "风险信号",
        ),
        output_contract=(
            "复述关键事实",
            "说明当前可判断的案件阶段与主要法律风险",
            "列出需补充信息",
            "给出下一步与升级条件",
            "不承诺结果、不提供正式法律意见",
        ),
        handoff_conditions=("刑事拘留或羁押", "即将开庭", "无律师且高风险", "用户明确要求人工"),
        tool_scope=(
            "search_law_knowledge",
            "extract_criminal_facts",
            "check_criminal_stage",
            "assess_criminal_risk",
        ),
        temperature=0.1,
        max_tokens=1100,
    )
    system_prompt = (
        "你是刑事法律咨询助手。严格按事实和法定期限陈述；"
        "涉及拘留、开庭、取保候审、量刑时不得承诺结果。"
    )

    def get_tools(self) -> Dict[str, AgentToolSpec]:
        tools = dict(super().get_tools())
        if "search_law_knowledge" not in tools:
            tools.update(build_shared_law_rag_tools(None))
        tools.update(criminal_tools())
        return tools


class CivilConsultationAgent(BaseAgent):
    """民事法律咨询：覆盖劳动、婚姻、合同、交通、借贷等常见民事领域。"""

    agent_type = AgentType.CIVIL
    profile = AgentProfile(
        role="民事法律咨询",
        mission="识别劳动争议、婚姻家事、合同纠纷、交通事故、民间借贷等民事领域的事实与程序，给出咨询路径。",
        workflow=(
            "复述诉求",
            "识别民事领域",
            "梳理事实与证据",
            "判断诉讼/仲裁程序",
            "给出下一步并判断升级",
        ),
        input_contract=(
            "用户消息",
            "对话历史",
            "民事领域",
            "发生时间",
            "城市",
            "争议金额",
            "合同/证据情况",
            "风险信号",
        ),
        output_contract=(
            "复述诉求与关键事实",
            "说明对应民事领域和大致程序",
            "列出需补充材料",
            "给出下一步与升级条件",
            "不承诺结果、不提供正式法律意见",
        ),
        handoff_conditions=("用户明确要求人工或预约律师", "人身/大额/紧急民事风险", "材料复杂需人工核验"),
        tool_scope=(
            "search_law_knowledge",
            "extract_civil_facts",
            "determine_procedure",
            "assess_civil_risk",
        ),
        temperature=0.2,
        max_tokens=1100,
    )
    system_prompt = (
        "你是民事法律咨询助手。区分劳动仲裁、婚姻家事、合同、交通事故、民间借贷等程序；"
        "不承诺胜诉、执行或赔偿结果。"
    )

    def get_tools(self) -> Dict[str, AgentToolSpec]:
        tools = dict(super().get_tools())
        if "search_law_knowledge" not in tools:
            tools.update(build_shared_law_rag_tools(None))
        tools.update(civil_tools())
        return tools


class EscalationAgent(BaseAgent):
    """人工升级与留资交接节点。

    该节点不调用 LLM：它生成确定性的转人工回复并标记 escalate=True。
    get_tools() 已接入推荐律师、校验联系方式、咨询记录草稿和交接摘要工具。
    """

    agent_type = AgentType.ESCALATION
    profile = AgentProfile(
        role="人工升级与留资交接",
        mission="确认升级原因，整理法律风险与已知信息，引导留资或预约律师，并生成确定性交接回复。",
        workflow=("确认升级原因", "整理法律风险与已知信息", "引导留资/预约律师", "生成交接摘要"),
        input_contract=("用户消息", "意图", "紧急度", "风险信号", "结构化实体", "对话背景"),
        output_contract=("转人工原因", "已知信息与风险摘要", "留资/预约下一步", "保守的后续说明"),
        handoff_conditions=("刑事拘留", "用户明确要求人工", "紧急或高风险场景", "需要预约律师或留资"),
        tool_scope=(
            "search_law_knowledge",
            "recommend_lawyer",
            "validate_contact",
            "create_consultation_record",
            "build_handoff_summary",
        ),
        temperature=0.0,
        max_tokens=500,
    )
    system_prompt = (
        "你负责律所人工升级与留资交接，不要续写提供正式法律意见；"
        "只确认原因、整理摘要并引导用户联系人工客服或预约律师。"
    )

    def __init__(
        self,
        client: AsyncAnthropic,
        model: str,
        skill_manager: Optional[Any] = None,
        profile: Optional[AgentProfile] = None,
        lawyer_service: Optional[Any] = None,
        consultation_service: Optional[Any] = None,
    ):
        super().__init__(client, model, skill_manager, profile)
        self._lawyer_service = lawyer_service
        self._consultation_service = consultation_service

    def get_tools(self) -> Dict[str, AgentToolSpec]:
        tools = dict(super().get_tools())
        if "search_law_knowledge" not in tools:
            tools.update(build_shared_law_rag_tools(None))
        tools.update(build_escalation_tools(
            self._consultation_service,
            self._lawyer_service,
        ))
        return tools

    async def handle(self, req: Request) -> AgentResponse:
        t0 = time.monotonic()
        self.stats.total += 1
        skill_prompt = ""
        if self._skill_manager is not None:
            try:
                skill_prompt = self._skill_manager.prompt_for(req.message, self.agent_type.value)
            except Exception:
                skill_prompt = ""
                logger.warning("Escalation Skill prompt 注入失败，继续确定性人工交接")
        self._last_skill_prompt = skill_prompt
        intent = req.intent.value if req.intent else "unknown"
        urgency = req.urgency.name if req.urgency else "UNKNOWN"
        risk_values = [flag.value for flag in req.risk_flags]
        entities = json.dumps(req.entities or {}, ensure_ascii=False)
        consultation_recorded = False
        content = (
            "已收到您的法律咨询。为保障您的权益，现将该问题标记为转人工处理。\n\n"
            f"升级原因：意图={intent}，紧急度={urgency}\n"
            f"风险信号：{', '.join(risk_values) if risk_values else '暂无'}\n"
        )
        tools = self.get_tools()
        tools_used: List[str] = []
        tool_traces: List[Dict[str, Any]] = []
        recommended_lawyers: List[Dict[str, Any]] = []
        contact_args = _request_contact_args(req)
        legal_domain = resolve_legal_domain(req, contact_args)

        if "recommend_lawyer" in tools:
            recommend_args = {"legal_domain": legal_domain}
            recommend_result, trace = await self._invoke_available_tool(
                "recommend_lawyer",
                req,
                recommend_args,
                "escalation-recommend",
            )
            if trace is not None:
                tool_traces.append(trace)
                recommend_success = (
                    trace.get("success") is not False
                    and trace.get("result_success") is not False
                )
                if recommend_success:
                    tools_used.append("recommend_lawyer")
                    if isinstance(recommend_result, list):
                        recommended_lawyers = recommend_result
                    elif isinstance(recommend_result, dict):
                        recommended_lawyers = list(recommend_result.get("lawyers") or [])

        if "build_handoff_summary" in tools:
            _, trace = await self._invoke_available_tool(
                "build_handoff_summary",
                req,
                contact_args,
                "escalation-handoff-summary",
            )
            if trace is not None:
                tool_traces.append(trace)
                if trace.get("success") is not False and trace.get("result_success") is not False:
                    tools_used.append("build_handoff_summary")

        contact_valid = bool(contact_args.get("name") and contact_args.get("phone"))
        if contact_valid:
            contact_valid = validate_contact(req, contact_args)["valid"]

        if contact_valid and contact_args.get("consent") and "create_consultation_record" in tools:
            create_args = dict(contact_args)
            create_args["recommended_lawyers"] = recommended_lawyers
            create_args["legal_domain"] = legal_domain
            create_result, trace = await self._invoke_available_tool(
                "create_consultation_record",
                req,
                create_args,
                "escalation-create-record",
            )
            if trace is not None:
                tool_traces.append(trace)
                if (
                    trace.get("success") is not False
                    and trace.get("result_success") is not False
                    and _is_persisted_consultation(create_result)
                ):
                    tools_used.append("create_consultation_record")
                    consultation_recorded = True

        if consultation_recorded:
            content += f"已记录信息：{entities}\n"
        else:
            content += "咨询记录暂未保存，请保持会话以便人工客服跟进。\n"
        content += "人工客服或律师会根据会话记录继续核验。请不要发送身份证号、银行卡号或短信验证码等敏感信息。\n"

        self._last_tools_used = tools_used
        self._last_tool_traces = tool_traces
        ms = (time.monotonic() - t0) * 1000
        self.stats.success += 1
        self.stats.total_ms += ms
        return AgentResponse(
            agent_type=self.agent_type,
            content=content,
            success=True,
            latency_ms=ms,
            escalate=True,
            tools_used=tools_used,
            tool_traces=tool_traces,
            skill_prompt=self._last_skill_prompt,
            metadata={
                "skill_prompt_injected": bool(skill_prompt),
                "skill_prompt_used": bool(skill_prompt),
                "skill_prompt_chars": len(skill_prompt),
            },
        )


class ResponseComposer:
    """多 Agent 汇总节点，统一主次、去重和输出边界。"""

    def __init__(self, client: AsyncAnthropic, model: str, skill_manager: Optional[Any] = None):
        self._client = client
        self._model = model
        self._skill_manager = skill_manager

    async def compose(self, req: Request, responses: List[AgentResponse]) -> str:
        successful = [response for response in responses if response.success and response.content.strip()]
        if not successful:
            return "抱歉，所有 Agent 均处理失败。"
        if len(successful) == 1:
            return successful[0].content

        evidence = "\n\n".join(
            f"[{response.agent_type.value} Agent 输出]\n{response.content}"
            for response in successful
        )
        prompt = (
            "你是律所多 Agent Response Composer，负责把多个专业 Agent 的结果合并成一条最终回复。\n"
            "要求：以主 Agent 的结论为主，按法律咨询风险优先级组织内容；去掉重复和冲突表述；"
            "不能补造检索结果、律师推荐、合同审核或正式法律意见；如果结论冲突，明确说明需要人工核验；"
            "保留必要的补充材料和升级边界。只输出给用户看的中文回复，不要提及 Agent。\n\n"
            f"主 Agent：{successful[0].agent_type.value}\n"
            f"用户问题：{req.message}\n"
            f"候选结果：\n{evidence}"
        )
        if self._skill_manager is not None:
            skill = self._skill_manager.prompt_for(req.message, "reception")
            if skill:
                prompt += f"\n\n[通用客服输出边界]\n{skill}"
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=_env_int("LAWMIND_COMPOSER_MAX_TOKENS", 1000),
                temperature=_env_float("LAWMIND_COMPOSER_TEMPERATURE", 0.1),
                messages=[{"role": "user", "content": prompt}],
            )
            content = extract_text_content(response.content).strip()
            if content:
                return content
        except Exception as ex:
            logger.warning("Response Composer 失败，使用确定性合并: %s", ex)

        # 汇总节点不可用时保留主次标签，避免丢失某个专业 Agent 的结论。
        return "\n\n".join(
            f"{response.content}" if index == 0 else f"补充说明：\n{response.content}"
            for index, response in enumerate(successful)
        )


# ── 编排器 ────────────────────────────────────────────────────────────────────

class AgentOrchestrator:
    """LawMind 律所多 Agent 编排器。

    路由逻辑：
      1. LawIntent 与 LawRiskFlag 决策，刑事/民事/接待/升级按领域映射
      2. 同类多实例时按 routing_score() 选最优
      3. 刑事/民事专属 Agent 失败时降级到 ReceptionAgent
    """

    def __init__(
        self,
        api_key:  str,
        base_url: Optional[str] = None,
        model:    str = "claude-3-5-sonnet-20241022",
        skill_manager: Optional[Any] = None,
        rag_tool_manager: Optional[Any] = None,
        client: Optional[Any] = None,
        lawyer_service: Optional[Any] = None,
        consultation_service: Optional[Any] = None,
    ):
        if client is None:
            kwargs: Dict[str, Any] = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            client = AsyncAnthropic(**kwargs)

        self._intent_recognizer = LawIntentRecognizer(
            api_key=api_key,
            model=model,
            base_url=base_url,
            use_llm=True,
            use_embedding=True,
        )
        self._skill_manager = skill_manager
        self._composer = ResponseComposer(client, model, skill_manager)
        self._shared_tools: Dict[str, AgentToolSpec] = {}
        self._recent_tool_traces = deque(maxlen=_env_int("LAWMIND_TOOL_TRACE_MAX", 200))

        # Agent 池：每种律所角色可有多个实例（水平扩展）
        self._pool: Dict[AgentType, List[BaseAgent]] = {
            AgentType.RECEPTION: [self._make_agent(ReceptionAgent, client, model, skill_manager)],
            AgentType.CRIMINAL: [self._make_agent(CriminalDefenseAgent, client, model, skill_manager)],
            AgentType.CIVIL: [self._make_agent(CivilConsultationAgent, client, model, skill_manager)],
            AgentType.ESCALATION: [self._make_agent(
                EscalationAgent,
                client,
                model,
                skill_manager,
                lawyer_service=lawyer_service,
                consultation_service=consultation_service,
            )],
        }
        self.set_shared_tools(build_shared_law_rag_tools(rag_tool_manager))

    @staticmethod
    def _make_agent(
        agent_cls: type[BaseAgent],
        client: AsyncAnthropic,
        default_model: str,
        skill_manager: Optional[Any],
        lawyer_service: Optional[Any] = None,
        consultation_service: Optional[Any] = None,
    ) -> BaseAgent:
        """按角色创建 Agent，并允许用环境变量覆盖该角色的模型。

        可使用更强模型，通用接待可使用更快模型，升级节点本身不需要调用 LLM。
        """
        profile = agent_cls.profile
        agent_env_name = f"{agent_cls.agent_type.value.upper()}_MODEL"
        env_name = "LAWMIND_" + agent_env_name
        model = (
            os.getenv(env_name, "").strip()
            or os.getenv(_LEGACY_ENV_PREFIX + agent_env_name, "").strip()
            or profile.model
        )
        configured_profile = replace(profile, model=model) if model else profile
        kwargs: Dict[str, Any] = {"profile": configured_profile}
        if agent_cls is EscalationAgent:
            kwargs["lawyer_service"] = lawyer_service
            kwargs["consultation_service"] = consultation_service
        return agent_cls(client, default_model, skill_manager, **kwargs)

    def set_skill_manager(self, skill_manager: Optional[Any]) -> None:
        """更新 SkillManager 引用，供运行时重载或测试替换使用。"""
        self._skill_manager = skill_manager
        self._composer._skill_manager = skill_manager
        for agents in self._pool.values():
            for agent in agents:
                agent._skill_manager = skill_manager

    def set_shared_tools(self, tools: Optional[Dict[str, AgentToolSpec]]) -> None:
        """更新所有 Agent 共享的工具白名单。"""
        self._shared_tools = dict(tools or {})
        for agents in self._pool.values():
            for agent in agents:
                agent.set_shared_tools(self._shared_tools)

    async def recognize_intent(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
    ):
        """对外暴露意图识别，供 API 层先判断是否需要 RAG 等前置能力。"""
        return await self._intent_recognizer.recognize(message, history=history)

    def _record_tool_trace(self, result: OrchestratorResult) -> None:
        trace = {
            "request_id": result.request_id,
            "timestamp": datetime.now().isoformat(),
            "intent": result.intent.value if result.intent else None,
            "primary_agent": result.primary_agent.value if result.primary_agent else None,
            "supporting_agents": [agent.value for agent in result.supporting_agents],
            "tools_used": list(result.tools_used),
            "tool_calls": list(result.tool_traces),
            "escalated": result.escalated,
            "latency_ms": round(result.latency_ms, 1),
        }
        self._recent_tool_traces.append(trace)

    def get_tool_trace(self, request_id: str) -> Optional[Dict[str, Any]]:
        for trace in reversed(self._recent_tool_traces):
            if trace.get("request_id") == request_id:
                return trace
        return None

    def get_recent_tool_traces(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._recent_tool_traces:
            return []
        limit = max(1, min(int(limit or 20), len(self._recent_tool_traces)))
        return list(reversed(list(self._recent_tool_traces)[-limit:]))

    # ── 主入口 ────────────────────────────────────────────────────────────────

    async def run(self, req: Request) -> OrchestratorResult:
        """
        处理一次请求的完整流程：
          意图识别 → 路由选 Agent → 执行 → 检查升级 → 返回结果
        """
        t0 = time.monotonic()

        # 1. 意图识别（如果调用方已识别则跳过）
        if req.intent is None:
            intent_result = await self._intent_recognizer.recognize(req.message, history=req.history)
            req.intent  = intent_result.intent
            req.intent_group = intent_result.intent_group
            req.urgency = intent_result.urgency
            req.entities = dict(intent_result.entities or {})
            req.risk_flags = list(intent_result.risk_flags or [])
            req.intent_confidence = intent_result.confidence

        if self._needs_clarification(req):
            result = OrchestratorResult(
                request_id=req.request_id,
                response=_CLARIFICATION_RESPONSE,
                agent_type=AgentType.RECEPTION,
                intent=req.intent,
                escalated=False,
                latency_ms=(time.monotonic() - t0) * 1000,
                agent_types=[AgentType.RECEPTION],
                primary_agent=AgentType.RECEPTION,
                routing_reason="低置信度 OTHER 法律意图，先澄清用户领域",
                routing_confidence=req.intent_confidence,
            )
            self._record_tool_trace(result)
            return result

        # 复杂问题自动并行协作，例如同一句同时涉及刑事和民事领域。
        decision = self._route_decision(req)
        if decision.multi_agent:
            return await self.run_parallel(req, decision)

        # 2. 执行主 Agent（含降级）
        response = await self._execute(req, decision.primary_agent)

        # 4. 升级检查
        escalated = False
        if (
            response.escalate
            or req.urgency == UrgencyLevel.CRITICAL
            or LawRiskFlag.DETENTION in req.risk_flags
            or LawRiskFlag.COURT_SOON in req.risk_flags
            or req.intent == LawIntent.LAWYER_APPOINTMENT
        ):
            escalated = True
            logger.warning(f"请求 {req.request_id} 触发升级: urgency={req.urgency}, risk_flags={[f.value for f in req.risk_flags]}")
            # 生产环境：此处创建工单、通知人工客服

        result = OrchestratorResult(
            request_id=req.request_id,
            response=response.content,
            agent_type=response.agent_type,
            intent=req.intent,
            escalated=escalated,
            latency_ms=(time.monotonic() - t0) * 1000,
            agent_types=[response.agent_type],
            primary_agent=decision.primary_agent,
            supporting_agents=[],
            tools_used=list(response.tools_used),
            tool_traces=list(response.tool_traces),
            skill_prompt=response.skill_prompt,
            routing_reason=decision.reason,
            routing_confidence=decision.confidence,
        )
        self._record_tool_trace(result)
        return result

    async def run_parallel(self, req: Request, decision: RoutingDecision) -> OrchestratorResult:
        """
        并行派发给多个 Agent，合并结果。
        适用于同时涉及刑事与民事等复合法律领域。
        """
        t0 = time.monotonic()
        agent_types = decision.agent_types
        tasks = [self._execute(req, at) for at in agent_types]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        valid_responses = [r for r in responses if isinstance(r, AgentResponse)]
        combined = await self._composer.compose(req, valid_responses)
        escalated = any(isinstance(r, AgentResponse) and r.escalate for r in responses)
        tools_used = list(dict.fromkeys(
            tool_name
            for response in valid_responses
            for tool_name in response.tools_used
        ))
        tool_traces = [
            trace
            for response in valid_responses
            for trace in response.tool_traces
        ]
        escalation_skill_prompt = next(
            (
                response.skill_prompt
                for response in valid_responses
                if response.agent_type == AgentType.ESCALATION and response.skill_prompt
            ),
            "",
        )
        result = OrchestratorResult(
            request_id=req.request_id,
            response=combined,
            agent_type=decision.primary_agent,
            intent=req.intent,
            escalated=escalated,
            latency_ms=(time.monotonic() - t0) * 1000,
            agent_types=[
                r.agent_type for r in responses
                if isinstance(r, AgentResponse) and r.success
            ] or agent_types,
            primary_agent=decision.primary_agent,
            supporting_agents=decision.supporting_agents,
            tools_used=tools_used,
            tool_traces=tool_traces,
            skill_prompt=escalation_skill_prompt,
            routing_reason=decision.reason,
            routing_confidence=decision.confidence,
        )
        self._record_tool_trace(result)
        return result

    # ── 路由逻辑 ──────────────────────────────────────────────────────────────

    def _route_decision(self, req: Request) -> RoutingDecision:
        """
        律所结构化路由决策。

        刑事拘留、即将开庭/CRITICAL 紧急度优先升级；律师预约也直接升级；
        随后按领域分数选择主 Agent 和可能的跨领域辅助 Agent。
        """
        if (
            req.urgency == UrgencyLevel.CRITICAL
            or LawRiskFlag.DETENTION in req.risk_flags
            or LawRiskFlag.COURT_SOON in req.risk_flags
        ):
            return RoutingDecision(
                primary_agent=AgentType.ESCALATION,
                reason="刑事拘留、即将开庭或紧急度为 CRITICAL，触发人工升级路由",
                confidence=1.0,
            )

        if req.intent == LawIntent.LAWYER_APPOINTMENT:
            return RoutingDecision(
                primary_agent=AgentType.ESCALATION,
                reason=f"意图为 {req.intent.value}，触发预约律师/人工升级路由",
                confidence=max(req.intent_confidence, 0.8),
            )

        scores = self._domain_scores(req)
        available_scores = {
            agent_type: score
            for agent_type, score in scores.items()
            if self._pool.get(agent_type)
        }
        if not available_scores:
            return RoutingDecision(
                primary_agent=AgentType.RECEPTION,
                reason="无可用律所 Agent，降级到 ReceptionAgent",
                confidence=0.1,
            )

        ordered = sorted(available_scores.items(), key=lambda item: item[1], reverse=True)
        primary_agent, primary_score = ordered[0]
        supporting_agents = [
            agent_type
            for agent_type, score in ordered[1:]
            if agent_type not in (AgentType.ESCALATION, AgentType.RECEPTION)
            and score >= 0.45
            and score >= primary_score * 0.55
        ]

        reason = self._routing_reason(req, available_scores, primary_agent, supporting_agents)
        return RoutingDecision(
            primary_agent=primary_agent,
            supporting_agents=supporting_agents,
            reason=reason,
            confidence=round(min(primary_score, 1.0), 3),
        )

    def _domain_scores(self, req: Request) -> Dict[AgentType, float]:
        """按法律意图、领域关键词、实体和风险信号为律所 Agent 打分。"""
        msg = req.message.lower()
        scores = {
            AgentType.RECEPTION: 0.15,
            AgentType.CRIMINAL: 0.0,
            AgentType.CIVIL: 0.0,
        }

        criminal_intents = {
            LawIntent.DANGEROUS_DRIVING,
            LawIntent.CRIMINAL_DEFENSE,
        }
        civil_intents = {
            LawIntent.LABOR_DISPUTE,
            LawIntent.MARRIAGE_FAMILY,
            LawIntent.CONTRACT_DISPUTE,
            LawIntent.TRAFFIC_ACCIDENT,
            LawIntent.CIVIL_LOAN,
        }
        if req.intent in criminal_intents:
            scores[AgentType.CRIMINAL] += 0.8
        if req.intent in civil_intents:
            scores[AgentType.CIVIL] += 0.8
        if req.intent in (LawIntent.LAW_FIRM_SERVICE, LawIntent.OTHER):
            scores[AgentType.RECEPTION] += 0.8

        criminal_kws = [
            "犯罪", "刑事案件", "刑事犯罪", "醉驾", "酒驾", "危险驾驶",
            "刑事拘留", "被拘留", "羁押", "刑事辩护", "取保候审",
            "审查起诉", "开庭", "看守所",
        ]
        civil_kws = [
            "劳动争议", "劳动纠纷", "辞退", "不给工资", "不发工资",
            "婚姻家事", "婚姻", "婚姻纠纷", "家庭纠纷", "离婚", "抚养权",
            "合同纠纷", "违约", "交通事故", "车祸",
            "民间借贷", "欠钱", "欠款", "借条", "债务",
        ]
        reception_kws = [
            "律所服务", "你能做什么", "能咨询什么", "你们能提供什么法律服务",
            "怎么收费", "收费标准", "律所地址", "咨询流程", "服务流程",
            "代理费", "工作时间", "预约律师", "律师推荐", "转人工",
        ]

        criminal_hits = sum(1 for kw in criminal_kws if kw in msg)
        civil_hits = sum(1 for kw in civil_kws if kw in msg)
        reception_hits = sum(1 for kw in reception_kws if kw in msg)

        scores[AgentType.CRIMINAL] += min(0.45, criminal_hits * 0.18)
        scores[AgentType.CIVIL] += min(0.45, civil_hits * 0.18)
        scores[AgentType.RECEPTION] += min(0.35, reception_hits * 0.12)

        if LawRiskFlag.FILED in req.risk_flags or LawRiskFlag.PROSECUTION in req.risk_flags:
            scores[AgentType.CRIMINAL] += 0.8
        if LawRiskFlag.TRAFFIC_ACCIDENT in req.risk_flags or LawRiskFlag.INJURY in req.risk_flags:
            scores[AgentType.CIVIL] += 0.8
        if LawRiskFlag.NO_LAWYER in req.risk_flags:
            scores[AgentType.CRIMINAL] += 0.3
            scores[AgentType.CIVIL] += 0.3
        if req.entities.get("blood_alcohol"):
            scores[AgentType.CRIMINAL] += 0.2
        if req.entities.get("disputed_amount"):
            scores[AgentType.CIVIL] += 0.15

        return {agent_type: round(score, 3) for agent_type, score in scores.items()}

    @staticmethod
    def _routing_reason(
        req: Request,
        scores: Dict[AgentType, float],
        primary_agent: AgentType,
        supporting_agents: List[AgentType],
    ) -> str:
        score_text = ", ".join(
            f"{agent_type.value}={score:.2f}"
            for agent_type, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
        )
        support_text = ", ".join(agent.value for agent in supporting_agents) or "none"
        intent = req.intent.value if req.intent else "unknown"
        return (
            f"intent={intent}, group={req.intent_group or 'unknown'}, "
            f"primary={primary_agent.value}, supporting={support_text}, scores=[{score_text}]"
        )

    @staticmethod
    def _has_assistant_history(history: Optional[List[Dict[str, str]]]) -> bool:
        """Return True when the conversation already contains an assistant turn."""
        return any(
            item.get("role") == "assistant"
            for item in (history or [])
        )

    @staticmethod
    def _needs_clarification(req: Request) -> bool:
        """低置信度且无明确意图时先追问，但高风险条件必须跳过澄清直接升级。

        If the assistant has already replied in this conversation, route the
        follow-up to ReceptionAgent with context instead of returning the same
        hard-coded sentence again.
        """
        if (
            req.urgency == UrgencyLevel.CRITICAL
            or LawRiskFlag.DETENTION in req.risk_flags
            or LawRiskFlag.COURT_SOON in req.risk_flags
            or req.intent == LawIntent.LAWYER_APPOINTMENT
        ):
            return False
        if req.intent != LawIntent.OTHER:
            return False
        text = (req.message or "").strip()
        if len(text) <= 2:
            return False
        if AgentOrchestrator._has_assistant_history(req.history):
            return False
        return req.intent_confidence < 0.5

    def _best_agent(self, agent_type: AgentType) -> Optional[BaseAgent]:
        """
        性能路由：从同类 Agent 中选 routing_score() 最高的。
        这是"基于在线表现动态调整路由"的核心。
        """
        agents = self._pool.get(agent_type, [])
        if not agents:
            return None
        return max(agents, key=lambda a: a.stats.routing_score())

    async def _execute(self, req: Request, agent_type: AgentType) -> AgentResponse:
        """执行律所 Agent，失败时降级到 ReceptionAgent。"""
        agent = self._best_agent(agent_type)
        if agent is None:
            agent = self._best_agent(AgentType.RECEPTION)
        if agent is None:
            return AgentResponse(
                agent_type=AgentType.RECEPTION,
                content="服务暂时不可用，请稍后重试。",
                success=False,
            )

        response = await agent.handle(req)

        # 专属刑事/民事 Agent 失败时降级到 ReceptionAgent；Escalation 不使用 LLM，不降级。
        if not response.success and agent_type not in (AgentType.RECEPTION, AgentType.ESCALATION):
            logger.warning(f"{agent_type.value} 失败，降级到 ReceptionAgent")
            fallback = self._best_agent(AgentType.RECEPTION)
            if fallback:
                response = await fallback.handle(req)

        return response

    # ── 统计（供 Monitor 读取）────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        result = {}
        for agent_type, agents in self._pool.items():
            for i, agent in enumerate(agents):
                key = f"{agent_type.value}_{i}"
                result[key] = {
                    "total":        agent.stats.total,
                    "success_rate": round(agent.stats.success_rate, 3),
                    "avg_ms":       round(agent.stats.avg_ms, 1),
                    "monitor_penalty": round(agent.stats.monitor_penalty, 3),
                    "routing_score": round(agent.stats.routing_score(), 3),
                    "role": agent.profile.role,
                    "workflow": list(agent.profile.workflow),
                    "tool_scope": list(agent.profile.tool_scope),
                    "available_tools": list(agent.get_tools()),
                    "model": agent._model,
                }
        return result

    def update_routing_penalties(self, penalties: Dict[str, float]) -> None:
        """
        接收 Monitor 的在线表现反馈，动态调整路由惩罚项。

        penalties 的 key 使用 get_stats() 中的 agent key，例如 technical_0。
        """
        for agent_type, agents in self._pool.items():
            for i, agent in enumerate(agents):
                key = f"{agent_type.value}_{i}"
                penalty = penalties.get(key, 0.0)
                agent.stats.monitor_penalty = min(max(penalty, 0.0), 0.9)
