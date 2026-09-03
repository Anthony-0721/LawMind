"""FastAPI routes for the LawMind public consultation flow and staff console.

The router is intentionally dependency-injected through ``app.state`` (with a
module-level default for simple tests) so it can be tested with fake services
and an in-memory SQLite session factory without starting the production
lifespan or making LLM calls.
"""
from __future__ import annotations

import inspect
import os
import re
import secrets
import uuid
from typing import Any, Dict, List, Mapping, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.intent_recognizer import UrgencyLevel
from core.law_domain import LawIntent, LawRiskFlag
from services.session_identity import (
    derive_user_id,
    get_session_secret,
    hash_session_token,
    make_session_token,
)

_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
_TRUE_CONSENT_TOKENS = frozenset({"1", "true", "yes", "是", "同意", "愿意"})

LEGAL_DOMAIN_LABELS = {
    "dangerous_driving": "醉驾 / 危险驾驶",
    "criminal_defense": "刑事辩护",
    "labor_dispute": "劳动争议",
    "marriage_family": "婚姻家事",
    "contract_dispute": "合同纠纷",
    "traffic_accident": "交通事故",
    "civil_loan": "民间借贷 / 债务纠纷",
    "lawyer_appointment": "预约律师 / 转人工",
    "law_firm_service": "律所服务 / 收费 / 流程",
    "other": "其他 / 暂不明确",
}

URGENCY_LABELS = {
    "LOW": "低",
    "MEDIUM": "中",
    "HIGH": "高",
    "CRITICAL": "紧急",
}

RISK_FLAG_LABELS = {
    "detention": "已刑事拘留",
    "court_soon": "即将开庭",
    "injury": "已发生人身伤亡",
    "traffic_accident": "发生交通事故",
    "filed": "已立案",
    "prosecution": "审查起诉阶段",
    "no_lawyer": "无律师代理",
}

_CONSULTATION_STATUSES = frozenset({"PENDING", "CONTACTED", "BOOKED", "CLOSED"})

PUBLIC_LEGAL_DISCLAIMER = "本回复仅供初步参考，不构成正式法律意见。"


def _ensure_public_legal_disclaimer(value: Any) -> str:
    """Return text with the public legal boundary appended when missing."""
    text = str(value or "")
    if not text:
        return PUBLIC_LEGAL_DISCLAIMER
    if PUBLIC_LEGAL_DISCLAIMER in text:
        return text
    return f"{text}\n\n{PUBLIC_LEGAL_DISCLAIMER}"



class LawChatRequest(BaseModel):
    message: str
    user_id: str = "anonymous"
    conv_id: Optional[str] = None
    conversation_id: Optional[str] = None
    request_id: Optional[str] = None


class LawChatPublicResponse(BaseModel):
    """Whitelisted public chat response; no internal agent/routing data."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    conversation_id: str
    session_token: str
    response: str
    intent: str = ""
    intent_group: str = ""
    legal_domain: str = ""
    case_stage: str = ""
    risk_flags: List[str] = Field(default_factory=list)
    escalated: bool = False
    missing_facts: List[str] = Field(default_factory=list)
    recommended_lawyers: List[Dict[str, Any]] = Field(default_factory=list)
    consultation_draft_id: Optional[str] = None


def _validate_public_consent(value: Any) -> Any:
    if isinstance(value, bool):
        if value is not True:
            raise ValueError("consent must be true")
        return value
    if isinstance(value, str) and value.strip().lower() in _TRUE_CONSENT_TOKENS:
        return value
    raise ValueError("consent must be 是/同意/愿意/yes/true/1 or true")


class LawConsultationPublicRequest(BaseModel):
    """Strict public lead submission schema; internal fields are forbidden."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: Optional[str] = None
    session_token: Optional[str] = None
    name: str
    phone: str
    city: Optional[str] = None
    preferred_time: Optional[str] = None
    consent: Any
    legal_domain: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("name is required")
        return str(value).strip()

    @field_validator("phone")
    @classmethod
    def _phone_valid(cls, value: str) -> str:
        phone = str(value or "").strip()
        if not _MOBILE_RE.fullmatch(phone):
            raise ValueError("phone must be a valid mainland mobile number")
        return phone

    @field_validator("consent", mode="before")
    @classmethod
    def _consent_valid(cls, value: Any) -> Any:
        return _validate_public_consent(value)


class LawTransferRequest(BaseModel):
    """Strict lead-capture schema used by the high-priority transfer endpoint."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: Optional[str] = None
    session_token: Optional[str] = None
    name: str
    phone: str
    city: Optional[str] = None
    preferred_time: Optional[str] = None
    consent: Any
    legal_domain: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("name is required")
        return str(value).strip()

    @field_validator("phone")
    @classmethod
    def _phone_valid(cls, value: str) -> str:
        phone = str(value or "").strip()
        if not _MOBILE_RE.fullmatch(phone):
            raise ValueError("phone must be a valid mainland mobile number")
        return phone

    @field_validator("consent", mode="before")
    @classmethod
    def _consent_valid(cls, value: Any) -> Any:
        return _validate_public_consent(value)


class ConsultationStatusRequest(BaseModel):
    status: str


class LawyerCreateRequest(BaseModel):
    name: str
    domain: str = "general"
    specialties: List[str] = Field(default_factory=list)
    intro: Optional[str] = None
    phone: Optional[str] = None
    wechat: Optional[str] = None
    email: Optional[str] = None
    active: bool = True
    sort_order: int = 0


class LawyerUpdateRequest(BaseModel):
    name: Optional[str] = None
    domain: Optional[str] = None
    specialties: Optional[List[str]] = None
    intro: Optional[str] = None
    phone: Optional[str] = None
    wechat: Optional[str] = None
    email: Optional[str] = None
    active: Optional[bool] = None
    sort_order: Optional[int] = None


class ToggleRequest(BaseModel):
    active: Optional[bool] = None


class FaqCreateRequest(BaseModel):
    category: str = "service"
    question: str
    answer: str
    keywords: List[str] = Field(default_factory=list)
    source: str = "law_firm"
    active: bool = True
    sort_order: int = 0


class FaqUpdateRequest(BaseModel):
    category: Optional[str] = None
    question: Optional[str] = None
    answer: Optional[str] = None
    keywords: Optional[List[str]] = None
    source: Optional[str] = None
    active: Optional[bool] = None
    sort_order: Optional[int] = None


class AdminLoginRequest(BaseModel):
    password: str


class LawRuntime:
    """Simple service container stored on the FastAPI app state."""

    def __init__(self, **services: Any):
        for key, value in services.items():
            setattr(self, key, value)


_lawyer_service = None
_consultation_service = None
_faq_sync_service = None
_orchestrator = None
_memory = None
_knowledge_base = None
_session_factory = None
_default_runtime = LawRuntime()


def configure_law_router(**services: Any) -> APIRouter:
    """Configure the module-level default runtime used by the shared router."""
    global _default_runtime, _lawyer_service, _consultation_service, _faq_sync_service
    global _orchestrator, _memory, _knowledge_base, _session_factory
    _lawyer_service = services.get("lawyer_service")
    _consultation_service = services.get("consultation_service")
    _faq_sync_service = services.get("faq_sync_service")
    _orchestrator = services.get("orchestrator")
    _memory = services.get("memory")
    _knowledge_base = services.get("knowledge_base")
    _session_factory = services.get("session_factory")
    _default_runtime = LawRuntime(**services)
    return law_router


def configure_app_law_services(app: Any, **services: Any) -> None:
    """Attach services to a FastAPI app; call before requests are handled."""
    app.state.law_runtime = LawRuntime(**services)


def _runtime_for(request: Request) -> LawRuntime:
    state = request.app.state
    runtime = getattr(state, "law_runtime", None)
    if runtime is not None and any(
        getattr(runtime, name, None) is not None
        for name in (
            "lawyer_service",
            "consultation_service",
            "faq_sync_service",
            "orchestrator",
            "memory",
            "knowledge_base",
            "session_factory",
        )
    ):
        return runtime
    direct_services = {
        name: getattr(state, name, None)
        for name in (
            "lawyer_service",
            "consultation_service",
            "faq_sync_service",
            "orchestrator",
            "memory",
            "knowledge_base",
            "session_factory",
        )
        if getattr(state, name, None) is not None
    }
    if direct_services:
        return LawRuntime(**direct_services)
    module_services = {
        "lawyer_service": _lawyer_service,
        "consultation_service": _consultation_service,
        "faq_sync_service": _faq_sync_service,
        "orchestrator": _orchestrator,
        "memory": _memory,
        "knowledge_base": _knowledge_base,
        "session_factory": _session_factory,
    }
    module_services = {
        key: value for key, value in module_services.items() if value is not None
    }
    if module_services:
        return LawRuntime(**module_services)
    return _default_runtime


def _get(runtime: LawRuntime, name: str) -> Any:
    return getattr(runtime, name, None)


def _require(runtime: LawRuntime, name: str, message: str) -> Any:
    value = _get(runtime, name)
    if value is None:
        raise HTTPException(status_code=503, detail=message)
    return value


_session_secret = get_session_secret


class _PublicRole:
    def __init__(self, value: str):
        self.value = value


def _lead_payload(
    *,
    name: str,
    phone: str,
    city: Optional[str] = None,
    preferred_time: Optional[str] = None,
    consent: Any,
    legal_domain: Optional[str] = None,
    conversation_id: Optional[str] = None,
    session_token: Optional[str] = None,
    source: str = "public",
) -> Dict[str, Any]:
    """Build a server-trusted public lead payload with ownership token."""
    resolved_conversation_id = conversation_id or str(uuid.uuid4())
    resolved_session_token = session_token or make_session_token(resolved_conversation_id)
    payload = {
        "conversation_id": resolved_conversation_id,
        "session_token": resolved_session_token,
        "session_token_hash": hash_session_token(resolved_session_token),
        "user_id": derive_user_id(resolved_conversation_id),
        "contact_name": str(name or "").strip(),
        "contact_phone": str(phone or "").strip(),
        "consent": consent,
        "request_id": str(uuid.uuid4()),
        "source": source,
        "force_source_update": source == "transfer",
    }
    if city is not None:
        payload["city"] = str(city or "").strip()
    if preferred_time is not None:
        payload["preferred_time"] = str(preferred_time or "").strip()
    if legal_domain is not None:
        payload["legal_domain"] = str(legal_domain or "").strip()
    return payload


def _validate_consultation_ownership(
    consultation_service: Any,
    conversation_id: Optional[str],
    session_token: Optional[str],
) -> None:
    """Reject updates or tampering on conversations owned by another token."""
    if not conversation_id:
        return
    existing = _optional_call(
        consultation_service,
        "get_by_conversation_id",
        str(conversation_id),
    )
    if existing is not None:
        stored_hash = str(existing.get("session_token_hash") or "")
        if not session_token or not secrets.compare_digest(
            hash_session_token(session_token),
            stored_hash,
        ):
            raise HTTPException(status_code=403, detail="会话所有权校验失败")
        return
    if session_token is not None and not secrets.compare_digest(
        str(session_token),
        make_session_token(str(conversation_id)),
    ):
        raise HTTPException(status_code=403, detail="会话令牌无效")


def _mask_name(value: Any) -> str:
    text = str(value or "").strip()
    return f"{text[:1]}*" if text else ""


def _mask_phone(value: Any) -> str:
    text = re.sub(r"\D", "", str(value or ""))
    if len(text) >= 7:
        return f"{text[:3]}****{text[-4:]}"
    return "****"


def _consultation_summary(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(record.get("id") or ""),
        "name": _mask_name(record.get("contact_name")),
        "phone": _mask_phone(record.get("contact_phone")),
        "legal_domain": str(record.get("legal_domain") or ""),
        "status": str(record.get("status") or ""),
        "created_at": str(record.get("created_at") or ""),
    }


def _first_entity_value(entities: Any, key: str) -> str:
    if not isinstance(entities, Mapping):
        return ""
    value = entities.get(key)
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value) if value not in (None, "") else ""


def _entity_list(entities: Any, key: str) -> List[str]:
    if not isinstance(entities, Mapping):
        return []
    value = entities.get(key) or []
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    return [str(item) for item in value if str(item)]


def _sanitize_recommended_lawyers(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        result.append({
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or ""),
            "specialties": [str(x) for x in (item.get("specialties") or [])],
            "intro": str(item.get("intro") or ""),
        })
    return result


def _result_error(error: str = "operation failed") -> HTTPException:
    return HTTPException(status_code=400, detail=error)


def _optional_call(obj: Any, name: str, *args: Any, **kwargs: Any) -> Any:
    fn = getattr(obj, name, None)
    if not callable(fn):
        return None
    try:
        return fn(*args, **kwargs)
    except TypeError:
        return fn()


def _staff_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    if not callable(fn):
        return None
    try:
        return fn(*args, **kwargs)
    except TypeError:
        reduced = {
            key: value
            for key, value in kwargs.items()
            if key != "include_contact"
        }
        if reduced:
            try:
                return fn(*args, **reduced)
            except TypeError:
                pass
        try:
            return fn(*args)
        except TypeError:
            return fn()


def _faq_response(
    service: Any,
    result: Any,
    faq_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(result, Mapping):
        return {}
    resolved_id = faq_id or result.get("faq_id") or result.get("id") or ""
    getter = getattr(service, "get_by_id", None)
    record = None
    if callable(getter) and resolved_id:
        try:
            record = getter(str(resolved_id))
        except TypeError:
            pass
    payload = dict(result)
    if isinstance(record, Mapping):
        payload.update(record)
    return payload


def _faq_service_call(
    service: Any,
    preferred: str,
    aliases: tuple[str, ...],
    *args: Any,
    **kwargs: Any,
) -> Any:
    for name in (preferred, *aliases):
        fn = getattr(service, name, None)
        if not callable(fn):
            continue
        try:
            return fn(*args, **kwargs)
        except TypeError:
            try:
                return fn(*args)
            except TypeError:
                continue
    return None


def _faq_records(service: Any, active_only: bool = False) -> List[Dict[str, Any]]:
    for method_name in ("list_all", "list_faqs", "list"):
        result = _optional_call(service, method_name, active_only=active_only)
        if result is not None:
            return result if isinstance(result, list) else list(result)
    records = getattr(service, "faqs", None)
    if isinstance(records, (list, tuple)):
        return list(records)
    if isinstance(records, dict):
        return list(records.values())
    return []


def _faq_sync_results(service: Any) -> List[Dict[str, Any]]:
    fn = getattr(service, "sync_all", None)
    if callable(fn):
        try:
            result = fn()
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return result.get("results", [])
        except TypeError:
            pass
    reload = getattr(service, "reload", None)
    if callable(reload):
        try:
            result = reload()
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return result.get("results", [])
        except TypeError:
            pass
    return []


def require_law_admin(x_admin_password: str = Header(None)) -> None:
    """Require the staff admin password via the ``X-Admin-Password`` header."""
    expected = (
        os.getenv("LAWMIND_ADMIN_PASSWORD")
        or os.getenv("LAW_FIRM_ADMIN_PASSWORD")
        or ""
    )
    if (
        not expected
        or not x_admin_password
        or not secrets.compare_digest(str(x_admin_password), expected)
    ):
        raise HTTPException(status_code=403, detail="无权限")


law_router = APIRouter(prefix="/law", tags=["律所咨询"])


def create_law_router(**services: Any) -> APIRouter:
    """Return the shared law router, optionally configured with services."""
    if services:
        configure_law_router(**services)
    return law_router


router = law_router
require_admin = require_law_admin

admin_router = APIRouter(
    prefix="/admin",
    tags=["律所咨询-工作人员"],
    dependencies=[Depends(require_law_admin)],
)


# ── Public endpoints ──────────────────────────────────────────────────────────


@law_router.get("/options")
def get_law_options(request: Request) -> Dict[str, Any]:
    legal_domains = [item.value for item in LawIntent]
    urgency_levels = [item.name for item in UrgencyLevel]
    risk_flags = [item.value for item in LawRiskFlag]
    return {
        "legal_domains": legal_domains,
        "legal_domain_options": [
            {
                "value": value,
                "label": LEGAL_DOMAIN_LABELS.get(value, value),
                "group": "criminal" if value in {"dangerous_driving", "criminal_defense"} else "civil" if value in {
                    "labor_dispute",
                    "marriage_family",
                    "contract_dispute",
                    "traffic_accident",
                    "civil_loan",
                } else "service",
            }
            for value in legal_domains
        ],
        "urgency_levels": urgency_levels,
        "urgency_options": [
            {"value": value, "label": URGENCY_LABELS.get(value, value)}
            for value in urgency_levels
        ],
        "risk_flags": risk_flags,
        "risk_flag_options": [
            {"value": value, "label": RISK_FLAG_LABELS.get(value, value)}
            for value in risk_flags
        ],
    }


@law_router.get("/lawyers")
def list_public_lawyers(
    request: Request,
    domain: Optional[str] = Query(None),
) -> List[Dict[str, Any]]:
    runtime = _runtime_for(request)
    lawyer_service = _require(runtime, "lawyer_service", "律师服务未初始化")
    records = lawyer_service.recommend(domain or None, limit=3)
    if isinstance(records, Mapping):
        records = records.get("lawyers") or records.get("items") or []
    return records if isinstance(records, list) else []


@law_router.post("/consultations")
def create_public_consultation(
    body: LawConsultationPublicRequest,
    request: Request,
) -> Dict[str, Any]:
    runtime = _runtime_for(request)
    consultation_service = _require(
        runtime, "consultation_service", "咨询记录服务未初始化"
    )
    _validate_consultation_ownership(
        consultation_service,
        body.conversation_id,
        body.session_token,
    )
    payload = _lead_payload(
        conversation_id=body.conversation_id,
        session_token=body.session_token,
        name=body.name,
        phone=body.phone,
        city=body.city,
        preferred_time=body.preferred_time,
        consent=body.consent,
        legal_domain=body.legal_domain,
        source="public",
    )
    saved = consultation_service.save_public(payload)
    if (
        not isinstance(saved, Mapping)
        or saved.get("success") is False
        or saved.get("persisted") is False
        or str(saved.get("status") or "").upper() not in _CONSULTATION_STATUSES
    ):
        raise HTTPException(status_code=400, detail="咨询信息不完整，请检查后重试")
    return {
        "consultation_id": saved.get("id") or saved.get("consultation_id"),
        "conversation_id": payload.get("conversation_id"),
        "session_token": payload.get("session_token"),
        "status": saved.get("status") or "PENDING",
        "message": _ensure_public_legal_disclaimer("咨询已提交，我们会尽快联系您"),
    }


@law_router.post("/transfer")
def create_transfer_request(
    body: LawTransferRequest,
    request: Request,
) -> Dict[str, Any]:
    runtime = _runtime_for(request)
    consultation_service = _require(
        runtime, "consultation_service", "咨询记录服务未初始化"
    )
    _validate_consultation_ownership(
        consultation_service,
        body.conversation_id,
        body.session_token,
    )
    payload = _lead_payload(
        conversation_id=body.conversation_id,
        session_token=body.session_token,
        name=body.name,
        phone=body.phone,
        city=body.city,
        preferred_time=body.preferred_time,
        consent=body.consent,
        legal_domain=body.legal_domain,
        source="transfer",
    )
    try:
        saved = consultation_service.save_public(payload, source="transfer")
    except TypeError:
        # Simple fakes may only accept a payload dict; the payload already
        # carries source="transfer".
        saved = consultation_service.save_public(payload)
    if (
        not isinstance(saved, Mapping)
        or saved.get("success") is False
        or saved.get("persisted") is False
        or str(saved.get("status") or "").upper() not in _CONSULTATION_STATUSES
    ):
        raise HTTPException(status_code=400, detail="转人工信息不完整，请检查后重试")
    return {
        "consultation_id": saved.get("id") or saved.get("consultation_id"),
        "conversation_id": payload.get("conversation_id"),
        "session_token": payload.get("session_token"),
        "status": saved.get("status") or "PENDING",
        "message": _ensure_public_legal_disclaimer("已收到您的转人工请求，工作人员将尽快与您联系"),
    }


@law_router.post("/chat", response_model=LawChatPublicResponse)
async def law_chat(
    body: LawChatRequest,
    request: Request,
) -> LawChatPublicResponse:
    runtime = _runtime_for(request)
    orchestrator = _require(runtime, "orchestrator", "法律咨询编排器未初始化")
    conversation_id = body.conversation_id or body.conv_id or str(uuid.uuid4())
    user_id = derive_user_id(conversation_id)
    session_token = make_session_token(conversation_id)
    request_id = str(uuid.uuid4())

    memory = _get(runtime, "memory")
    context = ""
    history = None
    if memory is not None and hasattr(memory, "get_context"):
        try:
            memory_call = memory.get_context(
                user_id, conversation_id, query=body.message
            )
            memory_context = (
                await memory_call
                if inspect.isawaitable(memory_call)
                else memory_call
            )
            history = [
                {"role": item.role.value, "content": item.content}
                for item in getattr(memory_context, "recent_messages", [])[-5:]
            ] or None
            context = getattr(memory_context, "to_prompt_text", lambda: "")()
        except Exception:
            context = ""
            history = None

    intent_result = None
    recognize = getattr(orchestrator, "recognize_intent", None)
    if callable(recognize):
        try:
            recognition_call = recognize(body.message, history=history)
            intent_result = (
                await recognition_call
                if inspect.isawaitable(recognition_call)
                else recognition_call
            )
        except TypeError:
            recognition_call = recognize(body.message)
            intent_result = (
                await recognition_call
                if inspect.isawaitable(recognition_call)
                else recognition_call
            )

    intent = _value_or_none(intent_result, "intent")
    entities = _value_or_none(intent_result, "entities") or {}
    urgency = _value_or_none(intent_result, "urgency")
    risk_flags = _value_or_none(intent_result, "risk_flags") or []
    confidence = _float_or_zero(_value_or_none(intent_result, "confidence"))

    from agents.agent_orchestrator import Request as OrcRequest

    orc_body = OrcRequest(
        message=body.message,
        user_id=user_id,
        conv_id=conversation_id,
        context=context,
        history=history,
        entities=entities or {},
        intent=intent,
        intent_group=_value_or_none(intent_result, "intent_group"),
        urgency=urgency,
        risk_flags=list(risk_flags or []),
        intent_confidence=confidence,
        request_id=request_id,
    )

    async def run_orchestrator() -> Any:
        return await orchestrator.run(orc_body)

    try:
        run_call = run_orchestrator()
        result = (
            await run_call
            if inspect.isawaitable(run_call)
            else run_call
        )
    except TypeError:
        # Allow lightweight fake orchestrators that only accept a plain dict.
        run_call = orchestrator.run({
            "message": body.message,
            "user_id": user_id,
            "conv_id": conversation_id,
            "conversation_id": conversation_id,
            "request_id": request_id,
            "context": context,
            "history": history,
            "entities": entities or {},
            "intent": intent,
            "urgency": urgency,
            "risk_flags": list(risk_flags or []),
        })
        result = await run_call if inspect.isawaitable(run_call) else run_call

    resolved_request_id = request_id
    response_text = _ensure_public_legal_disclaimer(
        _value_or_none(result, "response", "") or ""
    )

    if memory is not None:
        add_message = getattr(memory, "add_message", None)
        if callable(add_message):
            for role, content in (
                (_PublicRole("user"), body.message),
                (_PublicRole("assistant"), response_text),
            ):
                message_call = add_message(user_id, conversation_id, role, content)
                if inspect.isawaitable(message_call):
                    await message_call
        update_profile = getattr(memory, "update_profile", None)
        if callable(update_profile):
            profile_call = update_profile(user_id, conversation_id)
            if inspect.isawaitable(profile_call):
                await profile_call

    intent_value = _enum_value(_value_or_none(result, "intent", intent))
    intent_group = str(_value_or_none(result, "intent_group", "") or "")
    entity_source = _value_or_none(result, "entities", entities) or entities
    legal_domain = (
        _first_entity_value(entity_source, "legal_domain")
        or _first_entity_value(entity_source, "domain")
        or intent_value
    )
    case_stage = _first_entity_value(entity_source, "case_stage")
    if not case_stage:
        case_stage = _first_entity_value(
            _value_or_none(result, "facts", {}) or {}, "case_stage"
        )

    risk_value_source = _value_or_none(result, "risk_flags", risk_flags)
    if isinstance(risk_value_source, (list, tuple)):
        risk_values = [_enum_value(item) for item in risk_value_source]
    else:
        risk_values = _entity_list(entity_source, "risk_flags")
        if not risk_values:
            risk_values = _entity_list(
                _value_or_none(result, "facts", {}) or {}, "risk_flags"
            )

    missing_facts: List[str] = []
    missing_result = _value_or_none(result, "missing_facts")
    if isinstance(missing_result, (list, tuple)):
        missing_facts = [str(item) for item in missing_result]
    else:
        missing_result = _value_or_none(result, "missing")
        if isinstance(missing_result, (list, tuple)):
            missing_facts = [str(item) for item in missing_result]
    if not missing_facts:
        try:
            from agents.tools import check_missing_facts

            missing_result = check_missing_facts(orc_body, {})
            if isinstance(missing_result, Mapping):
                missing_facts = [str(item) for item in missing_result.get("missing", [])]
        except Exception:
            missing_facts = []

    # Lead receipts are intentionally handled only by /consultations and /transfer,
    # so /chat never returns a consultation_draft_id in production. The lookup below
    # is kept only for dict-shaped results (fakes, TypeError-fallback run path);
    # OrchestratorResult deliberately carries no consultation_draft field.
    consultation_draft = _value_or_none(result, "consultation_draft")
    if not isinstance(consultation_draft, Mapping):
        consultation_draft = _value_or_none(result, "draft")
    if not isinstance(consultation_draft, Mapping):
        consultation_draft = _value_or_none(result, "consultation")
    if not isinstance(consultation_draft, Mapping):
        consultation_draft = {}

    draft_lawyers = (
        _value_or_none(consultation_draft, "recommended_lawyers")
        or _value_or_none(result, "recommended_lawyers")
    )
    recommended_lawyers = _sanitize_recommended_lawyers(draft_lawyers)
    if not recommended_lawyers:
        lawyer_service = _get(runtime, "lawyer_service")
        recommend = getattr(lawyer_service, "recommend", None) if lawyer_service is not None else None
        if callable(recommend):
            try:
                recommended = recommend(legal_domain or None, limit=3)
                if isinstance(recommended, Mapping):
                    recommended = recommended.get("lawyers") or recommended.get("items") or []
                recommended_lawyers = _sanitize_recommended_lawyers(recommended)
            except Exception:
                recommended_lawyers = []

    consultation_draft_id: Optional[str] = None
    raw_draft_id = (
        consultation_draft.get("id")
        or consultation_draft.get("consultation_id")
        or _value_or_none(result, "consultation_id")
    )
    if raw_draft_id not in (None, ""):
        consultation_draft_id = str(raw_draft_id)

    escalated = bool(_value_or_none(result, "escalated", False))

    return LawChatPublicResponse(
        request_id=resolved_request_id,
        conversation_id=conversation_id,
        session_token=session_token,
        response=response_text,
        intent=intent_value,
        intent_group=intent_group,
        legal_domain=legal_domain,
        case_stage=case_stage,
        risk_flags=risk_values,
        escalated=escalated,
        missing_facts=missing_facts,
        recommended_lawyers=recommended_lawyers,
        consultation_draft_id=consultation_draft_id,
    ).model_dump()

def _value_or_none(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(key, default)
    try:
        return getattr(source, key, default)
    except AttributeError:
        return default


def _enum_value(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# ── Staff/admin endpoints ─────────────────────────────────────────────────────


@law_router.post("/admin/login")
def admin_login(body: AdminLoginRequest) -> Dict[str, Any]:
    expected = (
        os.getenv("LAWMIND_ADMIN_PASSWORD")
        or os.getenv("LAW_FIRM_ADMIN_PASSWORD")
        or ""
    )
    if not expected or not secrets.compare_digest(body.password, expected):
        raise HTTPException(status_code=403, detail="无权限")
    return {"authenticated": True, "success": True}


@admin_router.get("/consultations")
def list_admin_consultations(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
) -> List[Dict[str, Any]]:
    runtime = _runtime_for(request)
    service = _require(runtime, "consultation_service", "咨询记录服务未初始化")
    records = _optional_call(service, "list_recent", limit=limit) or []
    return [
        _consultation_summary(item)
        for item in records
        if isinstance(item, Mapping)
    ]


@admin_router.get("/consultations/{record_id}")
def get_admin_consultation(record_id: str, request: Request) -> Dict[str, Any]:
    runtime = _runtime_for(request)
    service = _require(runtime, "consultation_service", "咨询记录服务未初始化")
    record = service.get_by_id(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="咨询记录不存在")
    return record


@admin_router.patch("/consultations/{record_id}/status")
def update_admin_consultation_status(
    record_id: str,
    body: ConsultationStatusRequest,
    request: Request,
) -> Dict[str, Any]:
    runtime = _runtime_for(request)
    service = _require(runtime, "consultation_service", "咨询记录服务未初始化")
    normalized = body.status.strip().upper()
    if normalized not in _CONSULTATION_STATUSES:
        raise HTTPException(status_code=400, detail="无效的咨询状态")
    record = service.update_status(record_id, normalized)
    if record is None:
        raise HTTPException(status_code=404, detail="咨询记录不存在")
    return record


@admin_router.delete("/consultations/{record_id}")
def delete_admin_consultation(record_id: str, request: Request) -> Dict[str, Any]:
    runtime = _runtime_for(request)
    service = _require(runtime, "consultation_service", "咨询记录服务未初始化")
    deleted = service.delete(record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="咨询记录不存在")
    return {"success": True, "id": record_id}


@admin_router.get("/lawyers")
def list_admin_lawyers(request: Request) -> List[Dict[str, Any]]:
    runtime = _runtime_for(request)
    service = _require(runtime, "lawyer_service", "律师服务未初始化")
    records = _staff_call(
        getattr(service, "list_all", None),
        active_only=False,
        include_contact=True,
    )
    if records is None:
        records = []
    return records


@admin_router.post("/lawyers")
def create_admin_lawyer(
    body: LawyerCreateRequest,
    request: Request,
) -> Dict[str, Any]:
    runtime = _runtime_for(request)
    service = _require(runtime, "lawyer_service", "律师服务未初始化")
    result = _staff_call(
        getattr(service, "create", None),
        body.model_dump(exclude_none=True),
        include_contact=True,
    )
    if result is None:
        raise HTTPException(status_code=400, detail="律师创建失败")
    return result


@admin_router.patch("/lawyers/{lawyer_id}")
def update_admin_lawyer(
    lawyer_id: str,
    body: LawyerUpdateRequest,
    request: Request,
) -> Dict[str, Any]:
    runtime = _runtime_for(request)
    service = _require(runtime, "lawyer_service", "律师服务未初始化")
    record = _staff_call(
        getattr(service, "update", None),
        lawyer_id,
        body.model_dump(exclude_none=True),
        include_contact=True,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="律师不存在")
    return record


@admin_router.patch("/lawyers/{lawyer_id}/toggle")
def toggle_admin_lawyer(
    lawyer_id: str,
    body: Optional[ToggleRequest] = None,
    request: Request = None,
) -> Dict[str, Any]:
    runtime = _runtime_for(request)
    service = _require(runtime, "lawyer_service", "律师服务未初始化")
    active = body.active if body is not None else None
    record = _staff_call(
        getattr(service, "toggle", None),
        lawyer_id,
        active=active,
        include_contact=True,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="律师不存在")
    return record


@admin_router.get("/faqs")
def list_admin_faqs(
    request: Request,
    active_only: bool = Query(False),
) -> List[Dict[str, Any]]:
    runtime = _runtime_for(request)
    service = _require(runtime, "faq_sync_service", "FAQ 同步服务未初始化")
    return _faq_records(service, active_only=active_only)


@admin_router.post("/faqs")
def create_admin_faq(
    body: FaqCreateRequest,
    request: Request,
) -> Dict[str, Any]:
    runtime = _runtime_for(request)
    service = _require(runtime, "faq_sync_service", "FAQ 同步服务未初始化")
    result = _faq_service_call(service, "create_record", ("create",), body.model_dump(exclude_none=True))
    if not isinstance(result, Mapping) or result.get("success") is not True:
        raise _result_error(str(result.get("error", "faq_create_failed")))
    return _faq_response(service, result)


@admin_router.put("/faqs/{faq_id}")
def update_admin_faq(
    faq_id: str,
    body: FaqUpdateRequest,
    request: Request,
) -> Dict[str, Any]:
    runtime = _runtime_for(request)
    service = _require(runtime, "faq_sync_service", "FAQ 同步服务未初始化")
    result = _faq_service_call(
        service,
        "update_record",
        ("update",),
        faq_id,
        body.model_dump(exclude_none=True),
    )
    if not isinstance(result, Mapping) or result.get("success") is not True:
        raise _result_error(str(result.get("error", "faq_update_failed")))
    return _faq_response(service, result, faq_id)


@admin_router.patch("/faqs/{faq_id}/toggle")
def toggle_admin_faq(
    faq_id: str,
    body: Optional[ToggleRequest] = None,
    request: Request = None,
) -> Dict[str, Any]:
    runtime = _runtime_for(request)
    service = _require(runtime, "faq_sync_service", "FAQ 同步服务未初始化")
    active = body.active if body is not None else None
    result = _faq_service_call(service, "toggle_record", ("toggle",), faq_id, active)
    if not isinstance(result, Mapping) or result.get("success") is not True:
        raise _result_error(str(result.get("error", "faq_toggle_failed")))
    return _faq_response(service, result, faq_id)


@admin_router.delete("/faqs/{faq_id}")
def delete_admin_faq(faq_id: str, request: Request) -> Dict[str, Any]:
    runtime = _runtime_for(request)
    service = _require(runtime, "faq_sync_service", "FAQ 同步服务未初始化")
    result = _faq_service_call(service, "delete_record", ("delete",), faq_id)
    if not isinstance(result, Mapping) or result.get("success") is not True:
        raise _result_error(str(result.get("error", "faq_delete_failed")))
    return result


@admin_router.post("/knowledge/reload")
def reload_admin_knowledge(request: Request) -> Dict[str, Any]:
    runtime = _runtime_for(request)
    service = _require(runtime, "faq_sync_service", "FAQ 同步服务未初始化")
    results = _faq_sync_results(service)
    synced = sum(1 for item in results if item.get("success") is True)
    failed = sum(1 for item in results if item.get("success") is not True)
    return {
        "success": True,
        "synced": synced,
        "failed": failed,
        "results": results,
    }


@admin_router.get("/metrics")
def get_admin_metrics(request: Request) -> Dict[str, Any]:
    runtime = _runtime_for(request)
    consultation_service = _get(runtime, "consultation_service")
    lawyer_service = _get(runtime, "lawyer_service")
    faq_service = _get(runtime, "faq_sync_service")

    consultations = _optional_call(consultation_service, "list_recent", limit=500) or []
    lawyers = _staff_call(
        getattr(lawyer_service, "list_all", None),
        active_only=False,
        include_contact=True,
    ) or []
    faqs = _faq_records(faq_service, active_only=False)
    pending = sum(1 for item in consultations if str(item.get("status") or "").upper() == "PENDING")
    active_lawyers = sum(1 for item in lawyers if bool(item.get("active", False)))
    active_faqs = sum(1 for item in faqs if bool(item.get("active", False)))

    return {
        "consultations": {
            "total": len(consultations),
            "pending": pending,
        },
        "lawyers": {
            "total": len(lawyers),
            "active": active_lawyers,
        },
        "faqs": {
            "total": len(faqs),
            "active": active_faqs,
        },
        "total_consultations": len(consultations),
        "pending_consultations": pending,
        "total_lawyers": len(lawyers),
        "active_lawyers": active_lawyers,
        "total_faqs": len(faqs),
        "active_faqs": active_faqs,
    }


law_router.include_router(admin_router)


__all__ = [
    "PUBLIC_LEGAL_DISCLAIMER",
    "LawChatPublicResponse",
    "LawChatRequest",
    "derive_user_id",
    "get_session_secret",
    "hash_session_token",
    "make_session_token",
    "create_law_router",
    "LawConsultationPublicRequest",
    "LawRuntime",
    "admin_router",
    "configure_app_law_services",
    "configure_law_router",
    "law_router",
    "require_admin",
    "require_law_admin",
    "router",
]
