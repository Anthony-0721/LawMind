"""Lawyer recommendation and management service with request-scoped persistence."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

from db.common import RecordDict
from db.lawyer_repository import LawyerRepository

LAW_DOMAIN_TO_LAWYER_DOMAIN: Dict[str, str] = {
    "dangerous_driving": "criminal",
    "criminal_defense": "criminal",
    "labor_dispute": "civil",
    "marriage_family": "civil",
    "contract_dispute": "civil",
    "traffic_accident": "civil",
    "civil_loan": "civil",
    "criminal": "criminal",
    "civil": "civil",
    "general": "general",
    "contract": "civil",
    "marriage": "civil",
    "labor": "civil",
    "traffic": "civil",
    "loan": "civil",
    "lawyer_appointment": "general",
    "law_firm_service": "general",
    "other": "general",
}

LAW_DOMAIN_TO_LAWYER = LAW_DOMAIN_TO_LAWYER_DOMAIN

LAW_SPECIALTY_TERMS: Dict[str, tuple[str, ...]] = {
    "dangerous_driving": ("醉驾", "危险驾驶", "刑事", "辩护"),
    "criminal_defense": ("刑事", "辩护", "取保候审", "审查起诉", "拘留"),
    "labor_dispute": ("劳动", "仲裁", "工资", "劳动争议"),
    "marriage_family": ("婚姻", "离婚", "抚养", "财产"),
    "contract_dispute": ("合同", "违约", "欠款", "买卖"),
    "traffic_accident": ("交通", "事故", "赔偿", "肇事"),
    "civil_loan": ("民间借贷", "借款", "借贷", "债务", "欠款"),
}

_LAWYER_RECORD_KEYS = (
    "id",
    "name",
    "domain",
    "specialties",
    "intro",
    "phone",
    "wechat",
    "email",
    "active",
    "sort_order",
    "created_at",
    "updated_at",
)


class LawyerServiceError(Exception):
    """Sanitized lawyer service failure without repository/PII details."""


def law_domain_to_lawyer_domain(value: Any) -> str:
    """Map a legal intent or domain value to the canonical lawyer domain."""
    raw = getattr(value, "value", value)
    return LAW_DOMAIN_TO_LAWYER_DOMAIN.get(str(raw or "").strip(), "general")


def _normalize_domain(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _record_mapping(record: Any) -> Mapping[str, Any]:
    if isinstance(record, Mapping):
        return record
    return {key: getattr(record, key, None) for key in _LAWYER_RECORD_KEYS}


def _public_record(record: Any) -> RecordDict:
    """Return only the public lawyer fields exposed to clients/agents."""
    data = _record_mapping(record)
    return RecordDict({
        "id": data.get("id"),
        "name": data.get("name"),
        "specialties": list(data.get("specialties") or []),
        "intro": data.get("intro"),
    })


def _staff_record(record: Any) -> RecordDict:
    """Return the full staff lawyer record, including contact/active fields."""
    return RecordDict(dict(_record_mapping(record)))


def _is_active(record: Mapping[str, Any]) -> bool:
    value = record.get("active", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "否"}
    return bool(value)


def _specialties_match(record: Mapping[str, Any], terms: tuple[str, ...]) -> bool:
    specialties = [str(item) for item in (record.get("specialties") or [])]
    text = " ".join(specialties).lower()
    return any(term.lower() in text for term in terms)


def _recommend_with_repository(
    repository: Any,
    domain: Any,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Recommend active lawyers from a repository, strictly specialty-matched."""
    exact_domain = _normalize_domain(domain)
    mapped_domain = law_domain_to_lawyer_domain(exact_domain)
    domains_to_try = list(dict.fromkeys([exact_domain, mapped_domain]))
    terms = LAW_SPECIALTY_TERMS.get(exact_domain, ())
    finder = getattr(repository, "find_active_by_domain", None)

    if not exact_domain:
        candidates = [
            item
            for item in repository.list_all(active_only=True)
            if _is_active(_record_mapping(item))
        ]
        candidates.sort(key=lambda item: int(item.get("sort_order") or 0))
        safe_limit = max(0, int(limit or 3))
        return [_public_record(item) for item in candidates[:safe_limit]]

    candidates: List[Dict[str, Any]] = []
    for candidate_domain in domains_to_try:
        if not candidate_domain:
            continue
        if callable(finder):
            matches = finder(candidate_domain)
        else:
            recommender = getattr(repository, "recommend", None)
            if not callable(recommender):
                continue
            try:
                matches = recommender(candidate_domain, limit=1000)
            except TypeError:
                matches = recommender(candidate_domain)
        if not matches:
            continue
        matches = [
            item for item in matches
            if _is_active(_record_mapping(item))
        ]
        if not matches:
            continue
        if terms:
            matched = [
                item for item in matches
                if _specialties_match(item, terms)
            ]
            if matched:
                candidates.extend(matched)
                break
            continue
        candidates.extend(matches)
        break

    candidates.sort(key=lambda item: int(item.get("sort_order") or 0))
    try:
        safe_limit = max(0, int(limit or 0))
    except (TypeError, ValueError):
        safe_limit = 3
    return [_public_record(item) for item in candidates[:safe_limit]]


def recommend_lawyers(
    repository_or_factory: Any,
    intent: Any,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Plan-compatible helper backed by the request-scoped service."""
    if callable(repository_or_factory):
        return LawyerService(repository_or_factory).recommend(intent, limit=limit)
    try:
        return _recommend_with_repository(repository_or_factory, intent, limit)
    except Exception:
        raise LawyerServiceError("lawyer recommendation failed") from None


class LawyerService:
    """Application service for lawyer recommendation and management."""

    def __init__(self, session_factory: Callable[[], Any]):
        self.session_factory = session_factory

    def _run(self, operation: Callable[[LawyerRepository], Any], error: str) -> Any:
        try:
            with self.session_factory() as session:
                return operation(LawyerRepository(session))
        except Exception:
            raise LawyerServiceError(error) from None

    def recommend(
        self,
        domain: Any,
        limit: int = 3,
        include_contact: bool = False,
    ) -> List[Dict[str, Any]]:
        return self._run(
            lambda repository: _recommend_with_repository(repository, domain, limit),
            "lawyer recommendation failed",
        )

    def find_active_by_domain(
        self,
        domain: Any,
        include_contact: bool = False,
    ) -> List[Dict[str, Any]]:
        return self._run(
            lambda repository: [
                (_staff_record(item) if include_contact else _public_record(item))
                for item in repository.find_active_by_domain(_normalize_domain(domain))
                if _is_active(_record_mapping(item))
            ],
            "lawyer lookup failed",
        )

    def get_by_id(self, lawyer_id: str, include_contact: bool = False) -> Optional[Dict[str, Any]]:
        def lookup(repository: LawyerRepository) -> Optional[Dict[str, Any]]:
            record = repository.get_by_id(str(lawyer_id))
            if record is None:
                return None
            return _staff_record(record) if include_contact else _public_record(record)

        return self._run(lookup, "lawyer lookup failed")

    def list_all(
        self,
        active_only: bool = True,
        include_contact: bool = False,
    ) -> List[Dict[str, Any]]:
        def list_records(repository: LawyerRepository) -> List[Dict[str, Any]]:
            records = repository.list_all(active_only=active_only)
            if active_only:
                records = [
                    item for item in records
                    if _is_active(_record_mapping(item))
                ]
            return [
                (_staff_record(item) if include_contact else _public_record(item))
                for item in records
            ]

        return self._run(list_records, "lawyer lookup failed")

    def create(
        self,
        payload: Optional[Mapping[str, Any]] = None,
        include_contact: bool = False,
        **fields: Any,
    ) -> Dict[str, Any]:
        return self._run(
            lambda repository: (
                _staff_record(repository.create(payload, **fields))
                if include_contact
                else _public_record(repository.create(payload, **fields))
            ),
            "lawyer create failed",
        )

    def update(
        self,
        lawyer_id: str,
        payload: Optional[Mapping[str, Any]] = None,
        include_contact: bool = False,
        **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        def apply(repository: LawyerRepository) -> Optional[Dict[str, Any]]:
            record = repository.update(str(lawyer_id), payload, **fields)
            if record is None:
                return None
            return _staff_record(record) if include_contact else _public_record(record)

        return self._run(apply, "lawyer update failed")

    def toggle(
        self,
        lawyer_id: str,
        active: Optional[bool] = None,
        include_contact: bool = False,
    ) -> Optional[Dict[str, Any]]:
        def apply(repository: LawyerRepository) -> Optional[Dict[str, Any]]:
            record = repository.toggle(str(lawyer_id), active)
            if record is None:
                return None
            return _staff_record(record) if include_contact else _public_record(record)

        return self._run(apply, "lawyer update failed")


__all__ = [
    "LAW_DOMAIN_TO_LAWYER",
    "LAW_DOMAIN_TO_LAWYER_DOMAIN",
    "LAW_SPECIALTY_TERMS",
    "LawyerService",
    "LawyerServiceError",
    "law_domain_to_lawyer_domain",
    "recommend_lawyers",
]
