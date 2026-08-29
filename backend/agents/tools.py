"""LawMind Agent 工具定义与实现。

所有 Agent 工具集中在这里。编排器只负责：
  1. 根据 Agent 类型暴露工具白名单
  2. 执行 LLM 返回的 tool_use
  3. 将工具结果回传给 LLM

这套工具保持确定性、可离线测试，并明确输送 LawIntent / LawRiskFlag /
结构化实体。需要律师推荐或咨询保存的外部能力通过参数注入，未配置时返回
明确的降级结果，不在工具内伪造数据库写入。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, TYPE_CHECKING, Union

from core.law_domain import LawIntent, LAW_INTENT_GROUPS

if TYPE_CHECKING:
    from agents.agent_orchestrator import Request


AgentToolHandler = Callable[["Request", Dict[str, Any]], Union[Any, Awaitable[Any]]]

CHINESE_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
CRIMINAL_REQUIRED_FIELDS = ("case_stage", "incident_time", "city")
CIVIL_REQUIRED_FIELDS = ("incident_time", "city", "disputed_amount")

CIVIL_PROCEDURES: Dict[LawIntent, str] = {
    LawIntent.LABOR_DISPUTE: "劳动仲裁",
    LawIntent.MARRIAGE_FAMILY: "离婚诉讼/调解",
    LawIntent.CONTRACT_DISPUTE: "民事诉讼",
    LawIntent.TRAFFIC_ACCIDENT: "交通事故赔偿诉讼/调解",
    LawIntent.CIVIL_LOAN: "民间借贷诉讼",
}


@dataclass(frozen=True)
class AgentToolSpec:
    """Agent 可见工具的定义和执行函数。"""

    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: AgentToolHandler


def make_tool(
    name: str,
    description: str,
    properties: Dict[str, Any],
    handler: AgentToolHandler,
    required: Optional[List[str]] = None,
) -> AgentToolSpec:
    """创建带 JSON Schema 的 Agent 工具。"""
    return AgentToolSpec(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
        handler=handler,
    )


# ── 通用辅助 ──────────────────────────────────────────────────────────────────


def _intent_value(req: Request) -> str:
    if req.intent is not None:
        return req.intent.value
    return "unknown"


def _risk_flags(req: Request) -> List[str]:
    return [
        getattr(flag, "value", str(flag))
        for flag in (req.risk_flags or [])
    ]


def _urgency_name(req: Request) -> str:
    if req.urgency is None:
        return "MEDIUM"
    return getattr(req.urgency, "name", str(req.urgency))


def _first_entity(req: Request, key: str, default: Any = "unknown") -> Any:
    raw = (getattr(req, "entities", None) or {}).get(key)
    if not raw:
        return default
    if isinstance(raw, list):
        return raw[0] if raw else default
    return raw[0] if isinstance(raw, (tuple, set)) and raw else raw


def _domain_name(req: Request) -> str:
    """返回 criminal / civil / service / other 这样的领域组名。"""
    if req.intent in LAW_INTENT_GROUPS:
        return LAW_INTENT_GROUPS[req.intent]
    if req.intent_group:
        return req.intent_group
    return "other"


def resolve_legal_domain(
    req: Request,
    args: Optional[Dict[str, Any]] = None,
) -> str:
    """Resolve legal domain with args -> entity -> intent precedence."""
    if args:
        arg_value = args.get("legal_domain")
        if arg_value not in (None, ""):
            return str(arg_value)
    entity_value = _first_entity(req, "legal_domain", None)
    if entity_value not in (None, ""):
        return str(entity_value)
    if req.intent is not None:
        return req.intent.value
    return "other"


def _legal_domain_value(
    req: Request,
    args: Optional[Dict[str, Any]] = None,
) -> str:
    """Backward-compatible wrapper for the shared legal-domain resolver."""
    return resolve_legal_domain(req, args)


def _required_fields_for(req: Request) -> List[str]:
    if _domain_name(req) == "criminal":
        return list(CRIMINAL_REQUIRED_FIELDS)
    if _domain_name(req) == "civil":
        return list(CIVIL_REQUIRED_FIELDS)
    return list(CRIMINAL_REQUIRED_FIELDS)


# ── 共享 RAG 工具 ─────────────────────────────────────────────────────────────


async def search_law_knowledge(
    req: Request,
    args: Dict[str, Any],
    tool_manager: Optional[Any] = None,
) -> Dict[str, Any]:
    """检索律所 FAQ 和领域知识；缺少 RAG 能力时返回离线降级结果。"""
    query = str(args.get("query") or req.message or "").strip()
    if not query:
        return {
            "success": False,
            "error": "query 不能为空",
            "query": "",
            "results": [],
        }
    if tool_manager is None:
        return {
            "success": False,
            "query": query,
            "error": "RAG 工具未初始化",
            "results": [],
            "reranked": False,
            "fallback": (
                "知识库检索暂不可用，请基于当前已提取事实继续处理；"
                "不要编造具体法条，涉及高风险情况请转人工确认。"
            ),
        }
    if not callable(getattr(tool_manager, "search_with_rewrite", None)):
        return {
            "success": False,
            "query": query,
            "error": "RAG 工具未配置检索方法",
            "results": [],
            "reranked": False,
            "fallback": "无法调用知识库检索，请使用已有领域知识和人工核验。",
        }

    top_k = args.get("top_k", 5) or 5
    try:
        result = await tool_manager.search_with_rewrite(
            "knowledge_search",
            query,
            top_k=int(top_k),
        )
    except Exception as exc:  # pragma: no cover - depends on external manager
        return {
            "success": False,
            "query": query,
            "error": f"知识库检索失败: {exc}",
            "results": [],
            "reranked": False,
            "fallback": "检索服务异常，请使用现有事实继续处理并提示人工核验。",
        }

    if isinstance(result, dict):
        if result.get("success") is False:
            return {
                "success": False,
                "query": query,
                "error": result.get("error", "知识库检索失败"),
                "results": [],
                "reranked": False,
                "fallback": "未检索到律所内部资料，以下为一般法律知识。",
            }
        data = result.get("data") or result.get("results") or []
        reranked = bool(result.get("reranked", False))
    elif isinstance(result, list):
        data = result
        reranked = False
    elif not getattr(result, "success", False):
        return {
            "success": False,
            "query": query,
            "error": getattr(result, "error", "知识库检索失败"),
            "results": [],
            "reranked": False,
            "fallback": "未检索到律所内部资料，以下为一般法律知识。",
        }
    else:
        data = getattr(result, "data", [])
        reranked = bool(getattr(result, "reranked", False))

    return {
        "success": True,
        "query": query,
        "top_k": int(top_k),
        "results": data,
        "reranked": reranked,
        "fallback": None,
    }


def make_rag_handler(tool_manager: Optional[Any]) -> AgentToolHandler:
    """返回可以由 AgentToolSpec 直接调用的 RAG handler。"""
    async def handler(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
        return await search_law_knowledge(req, args, tool_manager)
    return handler


def build_shared_law_rag_tools(tool_manager: Optional[Any]) -> Dict[str, AgentToolSpec]:
    """构建所有 Agent 共享的律所知识库工具。"""
    return {
        "search_law_knowledge": make_tool(
            name="search_law_knowledge",
            description="检索律所 FAQ 和领域知识；未配置 RAG 时返回明确降级结果。",
            properties={
                "query": {"type": "string", "description": "用户问题或检索关键词"},
                "top_k": {"type": "integer", "description": "返回结果条数，默认 5"},
            },
            handler=make_rag_handler(tool_manager),
            required=["query"],
        ),
    }


# ── 接待工具 ──────────────────────────────────────────────────────────────────


def identify_legal_domain(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """识别当前咨询的法律领域并返回风险信号。"""
    return {
        "intent": _intent_value(req),
        "legal_domain": _legal_domain_value(req),
        "domain": _domain_name(req),
        "risk_flags": _risk_flags(req),
    }


def check_missing_facts(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """按刑事/民事领域计算当前缺失的关键事实字段。"""
    required = _required_fields_for(req)
    missing = [field for field in required if not req.entities.get(field)]
    return {
        "legal_domain": _legal_domain_value(req),
        "domain": _domain_name(req),
        "required_fields": required,
        "missing": missing,
        "known_entities": dict(req.entities or {}),
    }


def build_reception_summary(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """生成接待层结构化摘要，供分诊和转交后续 Agent 使用。"""
    missing_result = check_missing_facts(req, args)
    risk_text = ", ".join(
        getattr(flag, "value", str(flag)) for flag in (req.risk_flags or [])
    ) or "暂无"
    missing_text = "、".join(missing_result["missing"]) or "无"
    summary = (
        f"已识别领域：{_intent_value(req)}；"
        f"领域组：{_domain_name(req)}；"
        f"风险信号：{risk_text}；缺失关键信息：{missing_text}。"
    )
    return {
        "request_id": req.request_id,
        "intent": _intent_value(req),
        "legal_domain": _legal_domain_value(req),
        "risk_flags": _risk_flags(req),
        "facts": dict(req.entities or {}),
        "missing": missing_result["missing"],
        "summary": summary,
    }


def reception_tools() -> Dict[str, AgentToolSpec]:
    return {
        "identify_legal_domain": make_tool(
            "identify_legal_domain",
            "识别当前法律咨询的意图、领域组和风险信号。",
            {},
            identify_legal_domain,
        ),
        "check_missing_facts": make_tool(
            "check_missing_facts",
            "按刑事/民事领域返回仍缺的关键事实字段。",
            {},
            check_missing_facts,
        ),
        "build_reception_summary": make_tool(
            "build_reception_summary",
            "生成接待层结构化摘要，供后续领域 Agent 和转人工使用。",
            {},
            build_reception_summary,
        ),
    }


# ── 刑事工具 ──────────────────────────────────────────────────────────────────


def extract_criminal_facts(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """从当前请求提取刑事/醉驾关键事实与风险信号。"""
    return {
        "facts": dict(req.entities or {}),
        "risk_flags": _risk_flags(req),
        "legal_domain": _legal_domain_value(req),
    }


def check_criminal_stage(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """检查刑事案件阶段是否已知。"""
    stage = _first_entity(req, "case_stage", "unknown")
    return {
        "case_stage": stage,
        "need_confirm": stage in (None, "", "unknown"),
        "available_stages": list(req.entities.get("case_stage") or []),
    }


def assess_criminal_risk(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """输出刑事风险等级和风险信号。"""
    return {
        "risk_level": _urgency_name(req),
        "risk_flags": _risk_flags(req),
    }


def criminal_tools() -> Dict[str, AgentToolSpec]:
    return {
        "extract_criminal_facts": make_tool(
            "extract_criminal_facts",
            "提取刑事/醉驾关键事实，供 Agent 复述和补充追问。",
            {},
            extract_criminal_facts,
        ),
        "check_criminal_stage": make_tool(
            "check_criminal_stage",
            "检查刑事案件阶段是否已知，未知时提示追问。",
            {},
            check_criminal_stage,
        ),
        "assess_criminal_risk": make_tool(
            "assess_criminal_risk",
            "判断刑事风险等级并返回风险信号。",
            {},
            assess_criminal_risk,
        ),
    }


# ── 民事工具 ──────────────────────────────────────────────────────────────────


def extract_civil_facts(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """从当前请求提取民事关键事实与风险信号。"""
    return {
        "facts": dict(req.entities or {}),
        "risk_flags": _risk_flags(req),
        "legal_domain": _legal_domain_value(req),
    }


def determine_procedure(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """根据民事意图判断最可能的法律程序。"""
    intent_key = req.intent if req.intent is not None else args.get("intent", "unknown")
    intent_value = (
        req.intent.value
        if req.intent is not None
        else str(args.get("intent", "unknown"))
    )
    if isinstance(intent_key, str):
        try:
            procedure = CIVIL_PROCEDURES.get(LawIntent(intent_key))
        except ValueError:
            procedure = None
    else:
        procedure = CIVIL_PROCEDURES.get(intent_key)
    if procedure is None:
        procedure = str(args.get("procedure", "待确认"))
    return {
        "intent": intent_value,
        "procedure": procedure,
        "need_confirm": procedure == "待确认",
        "legal_domain": _legal_domain_value(req),
    }


def assess_civil_risk(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """输出民事风险等级和风险信号。"""
    return {
        "risk_level": _urgency_name(req),
        "risk_flags": _risk_flags(req),
    }


def civil_tools() -> Dict[str, AgentToolSpec]:
    return {
        "extract_civil_facts": make_tool(
            "extract_civil_facts",
            "提取劳动、婚姻、合同、交通、借贷等民事关键事实。",
            {},
            extract_civil_facts,
        ),
        "determine_procedure": make_tool(
            "determine_procedure",
            "根据民事领域判断仲裁、调解或诉讼程序。",
            {},
            determine_procedure,
        ),
        "assess_civil_risk": make_tool(
            "assess_civil_risk",
            "判断民事风险等级并返回风险信号。",
            {},
            assess_civil_risk,
        ),
    }


# ── 升级与留资工具 ────────────────────────────────────────────────────────────


def recommend_lawyer(
    req: Request,
    args: Dict[str, Any],
    lawyer_service: Optional[Any] = None,
) -> Any:
    """推荐对口律师；未注入推荐服务时返回明确降级结果。"""
    if lawyer_service is None:
        return {
            "success": False,
            "reason": "lawyer_service_not_configured",
            "lawyers": [],
        }
    return lawyer_service.recommend(resolve_legal_domain(req, args))


def _contact_from_args(args: Dict[str, Any]) -> Dict[str, str]:
    contact = args.get("contact")
    if not isinstance(contact, dict):
        contact = {}
    return {
        "name": str(
            args.get("name")
            or args.get("contact_name")
            or contact.get("name")
            or contact.get("contact_name")
            or ""
        ).strip(),
        "phone": str(
            args.get("phone")
            or args.get("contact_phone")
            or contact.get("phone")
            or contact.get("contact_phone")
            or ""
        ).strip(),
    }


def _merge_request_contact(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """Merge contact fields carried by Request into tool args."""
    merged = dict(args)
    source = getattr(req, "contact", None)
    if isinstance(source, dict):
        for dest, src_key in (
            ("name", "name"),
            ("name", "contact_name"),
            ("phone", "phone"),
            ("phone", "contact_phone"),
        ):
            if not merged.get(dest) and source.get(src_key):
                merged[dest] = source[src_key]
        if "consent" not in merged and "consent" in source:
            merged["consent"] = source.get("consent")
        if not merged.get("city") and source.get("city"):
            merged["city"] = source.get("city")
        if not merged.get("preferred_time") and source.get("preferred_time"):
            merged["preferred_time"] = source.get("preferred_time")
        if not merged.get("case_stage") and source.get("case_stage"):
            merged["case_stage"] = source.get("case_stage")
    entities = getattr(req, "entities", None) or {}
    for key in ("name", "phone", "contact_name", "contact_phone", "legal_domain", "city", "preferred_time", "consent", "case_stage"):
        if key == "consent":
            if key not in merged:
                value = getattr(req, key, None)
                if value not in (None, ""):
                    merged[key] = value
                else:
                    entity_values = entities.get(key)
                    if entity_values:
                        first = entity_values[0] if isinstance(entity_values, (list, tuple)) else entity_values
                        if first not in (None, ""):
                            merged[key] = first
        elif not merged.get(key):
            value = getattr(req, key, None)
            if value not in (None, ""):
                merged[key] = value
            else:
                entity_values = entities.get(key)
                if entity_values:
                    first = entity_values[0] if isinstance(entity_values, (list, tuple)) else entity_values
                    if first not in (None, ""):
                        merged[key] = first
    return merged


def validate_contact(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """校验留资姓名与 11 位中国手机号码，返回结构化结果。"""
    args = _merge_request_contact(req, args)
    name = _contact_from_args(args)["name"]
    phone = _contact_from_args(args)["phone"]
    errors: List[str] = []
    if not name:
        errors.append("name_required")
    if not CHINESE_MOBILE_RE.fullmatch(phone):
        errors.append("phone_invalid")
    valid = not errors
    return {
        "valid": valid,
        "success": valid,
        "name": name,
        "phone": phone,
        "contact": {"name": name, "phone": phone} if valid else {},
        "errors": errors,
    }


def _as_bool(value: Any) -> bool:
    """Convert boolean-like tool input to a real bool.

    Chinese consent values are supported: true for 是/同意/愿意 and false for
    否/不同意/不愿意.
    """
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "是", "同意", "愿意"}:
            return True
        return False
    return bool(value)


def create_consultation_record(
    req: Request,
    args: Dict[str, Any],
    lawyer_service: Optional[Any] = None,
    consultation_service: Optional[Any] = None,
) -> Dict[str, Any]:
    """生成咨询记录；完整且有效时可通过咨询服务持久化。

    未注入咨询服务、信息不完整或未同意时返回确定性的 DRAFT 草稿。
    """
    if (
        consultation_service is None
        and lawyer_service is not None
        and hasattr(lawyer_service, "save_from_agent")
        and not hasattr(lawyer_service, "recommend")
    ):
        consultation_service = lawyer_service
        lawyer_service = None
    elif (
        consultation_service is not None
        and lawyer_service is not None
        and hasattr(lawyer_service, "save_from_agent")
        and hasattr(consultation_service, "recommend")
    ):
        consultation_service, lawyer_service = lawyer_service, consultation_service

    if consultation_service is None:
        return {
            "success": False,
            "persisted": False,
            "status": "DRAFT",
            "error": "consultation_service_unavailable",
        }

    raw_lawyers = args.get("recommended_lawyers") or []
    recommended_lawyers = (
        list(raw_lawyers)
        if isinstance(raw_lawyers, (list, tuple))
        else []
    )
    if not recommended_lawyers and lawyer_service is not None:
        result = recommend_lawyer(req, args, lawyer_service)
        if isinstance(result, list):
            recommended_lawyers = result
        elif isinstance(result, dict):
            recommended_lawyers = list(result.get("lawyers") or [])

    effective_args = _merge_request_contact(req, dict(args))

    contact = _contact_from_args(effective_args)
    contact_valid = validate_contact(req, effective_args)["valid"]
    consent = _as_bool(effective_args.get("consent", False))
    if not contact.get("name") and not contact.get("phone"):
        contact = {}
    risk_flags = _risk_flags(req)
    risk_text = ", ".join(risk_flags) or "暂无"
    risk_analysis = (
        f"风险等级：{_urgency_name(req)}；风险信号：{risk_text}；"
        "当前为初步分析，需律师或人工进一步核验。"
    )
    now = datetime.now(timezone.utc).isoformat()
    case_stage_arg = effective_args.get("case_stage")
    case_stage = str(
        case_stage_arg
        if case_stage_arg not in (None, "")
        else _first_entity(req, "case_stage", "unknown")
    )
    city = str(effective_args.get("city") or _first_entity(req, "city", "") or "")
    preferred_time = str(
        effective_args.get("preferred_time")
        or _first_entity(req, "preferred_time", "")
        or ""
    )
    draft = {
        "request_id": req.request_id,
        "user_id": req.user_id,
        "legal_domain": resolve_legal_domain(req, effective_args),
        "risk_flags": risk_flags,
        "facts": dict(req.entities or {}),
        "risk_analysis": risk_analysis,
        "recommended_lawyers": recommended_lawyers,
        "contact": contact,
        "consent": consent,
        "city": city,
        "preferred_time": preferred_time,
        "case_stage": case_stage,
        "source": "law_agent",
        "created_at": now,
        "updated_at": now,
        "version": 1,
        "status": "PENDING" if contact_valid and consent else "DRAFT",
    }

    if consultation_service is not None and draft["status"] == "PENDING":
        try:
            return consultation_service.save_from_agent(draft)
        except Exception:
            return {
                **draft,
                "success": False,
                "error": "consultation_save_failed",
                "persisted": False,
            }
    return {
        "success": False,
        "persisted": False,
        "status": "DRAFT",
        "error": "consultation_incomplete",
    }


def build_handoff_summary(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """生成交给人工客服/律师的结构化交接摘要。"""
    args = _merge_request_contact(req, args)
    contact = _contact_from_args(args)
    contact_filled = bool(contact.get("name") and contact.get("phone"))
    risk_text = ", ".join(
        getattr(flag, "value", str(flag)) for flag in (req.risk_flags or [])
    ) or "暂无"
    summary = (
        f"意图：{_intent_value(req)}；风险信号：{risk_text}；"
        f"联系方式：{'已填写' if contact_filled else '未填写'}。"
    )
    return {
        "request_id": req.request_id,
        "intent": _intent_value(req),
        "risk_flags": _risk_flags(req),
        "contact_filled": contact_filled,
        "summary": summary,
    }


def build_escalation_tools(
    consultation_service: Optional[Any] = None,
    lawyer_service: Optional[Any] = None,
) -> Dict[str, AgentToolSpec]:
    """构建升级 Agent 工具；咨询/律师服务均为可选注入。

    未配置时保持确定性的草稿/降级契约；配置后完整留资会持久化。
    """

    if lawyer_service is None:
        recommend_handler = recommend_lawyer
    else:
        def recommend_handler(req: Request, args: Dict[str, Any]) -> Any:
            return recommend_lawyer(req, args, lawyer_service)

    def create_handler(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
        return create_consultation_record(
            req,
            args,
            lawyer_service,
            consultation_service,
        )
    return {
        "recommend_lawyer": make_tool(
            "recommend_lawyer",
            "推荐对口律师；未配置律师服务时明确提示不可用。",
            {"legal_domain": {"type": "string", "description": "可选领域覆盖"}},
            recommend_handler,
        ),
        "validate_contact": make_tool(
            "validate_contact",
            "校验留资姓名与 11 位中国手机号码。",
            {
                "name": {"type": "string", "description": "联系人姓名"},
                "phone": {"type": "string", "description": "11 位中国手机号码"},
            },
            validate_contact,
            required=["name", "phone"],
        ),
        "create_consultation_record": make_tool(
            "create_consultation_record",
            "生成咨询记录；完整且有效时通过咨询服务持久化，否则返回 DRAFT 草稿。",
            {
                "recommended_lawyers": {
                    "type": "array",
                    "description": "推荐律师列表",
                    "items": {"type": "object"},
                },
                "contact": {"type": "object", "description": "姓名与手机号"},
                "name": {"type": "string", "description": "联系人姓名"},
                "phone": {"type": "string", "description": "11 位中国手机号码"},
                "consent": {"type": "boolean", "description": "是否同意律所联系"},
                "city": {"type": "string", "description": "所在城市"},
                "preferred_time": {"type": "string", "description": "期望联系时间"},
                "case_stage": {"type": "string", "description": "案件阶段"},
                "legal_domain": {"type": "string", "description": "法律领域覆盖"},
            },
            create_handler,
        ),
        "build_handoff_summary": make_tool(
            "build_handoff_summary",
            "生成转人工/律师交接摘要。",
            {
                "contact": {"type": "object", "description": "可选联系方式"},
                "name": {"type": "string", "description": "联系人姓名"},
                "phone": {"type": "string", "description": "11 位中国手机号码"},
            },
            build_handoff_summary,
        ),
    }


def escalation_tools(
    lawyer_service: Optional[Any] = None,
    consultation_service: Optional[Any] = None,
) -> Dict[str, AgentToolSpec]:
    """Backward-compatible alias accepting the legacy lawyer-first ordering."""
    if (
        consultation_service is None
        and lawyer_service is not None
        and hasattr(lawyer_service, "save_from_agent")
        and not hasattr(lawyer_service, "recommend")
    ):
        consultation_service = lawyer_service
        lawyer_service = None
    elif (
        consultation_service is not None
        and lawyer_service is not None
        and hasattr(consultation_service, "recommend")
        and hasattr(lawyer_service, "save_from_agent")
    ):
        lawyer_service, consultation_service = consultation_service, lawyer_service
    return build_escalation_tools(consultation_service, lawyer_service)
