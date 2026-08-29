"""Lawyer recommendation and management service.

Public service methods return normalized dictionaries with contact details
removed by default. Callers that explicitly need staff contact information can
pass ``include_contact=True``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

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

_CONTACT_FIELDS = ("phone", "wechat", "email")


def law_domain_to_lawyer_domain(value: Any) -> str:
    """Map a legal intent or domain value to the canonical lawyer domain."""
    raw = getattr(value, "value", value)
    return LAW_DOMAIN_TO_LAWYER_DOMAIN.get(str(raw or "").strip(), "general")


def _normalize_domain(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


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


def _record_mapping(record: Any) -> Mapping[str, Any]:
    if isinstance(record, Mapping):
        return record
    return {key: getattr(record, key, None) for key in _LAWYER_RECORD_KEYS}


def _public_record(
    record: Mapping[str, Any],
    include_contact: bool = False,
) -> RecordDict:
    """Return a normalized lawyer dict without default contact fields."""
    record = _record_mapping(record)
    result = RecordDict({key: value for key, value in record.items()})
    if not include_contact:
        for key in _CONTACT_FIELDS:
            result.pop(key, None)
    return result


def recommend_lawyers(repository: Any, intent: Any, limit: int = 3) -> List[Dict[str, Any]]:
    """Plan-compatible helper that recommends active lawyers for an intent."""
    return LawyerService(repository).recommend(intent, limit=limit)


def _specialties_match(record: Mapping[str, Any], terms: tuple[str, ...]) -> bool:
    specialties = [str(item) for item in (record.get("specialties") or [])]
    text = " ".join(specialties).lower()
    return any(term.lower() in text for term in terms)


class LawyerService:
    """Application service for lawyer recommendation and management."""

    def __init__(self, lawyer_repository: LawyerRepository):
        self.lawyer_repository = lawyer_repository

    def recommend(
        self,
        domain: Any,
        limit: int = 3,
        include_contact: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return up to ``limit`` active lawyers for an exact or mapped domain."""
        exact_domain = _normalize_domain(domain)
        mapped_domain = law_domain_to_lawyer_domain(exact_domain)
        domains_to_try = list(dict.fromkeys([
            exact_domain,
            mapped_domain,
            "general",
        ]))

        candidates: List[Dict[str, Any]] = []
        terms = LAW_SPECIALTY_TERMS.get(exact_domain, ())
        finder = getattr(self.lawyer_repository, "find_active_by_domain", None)
        for candidate_domain in domains_to_try:
            if not candidate_domain:
                continue
            if callable(finder):
                matches = finder(candidate_domain)
            else:
                recommender = getattr(self.lawyer_repository, "recommend", None)
                if not callable(recommender):
                    matches = []
                else:
                    try:
                        matches = recommender(candidate_domain, limit=1000)
                    except TypeError:
                        matches = recommender(candidate_domain)
            if not matches:
                continue
            if terms and candidate_domain != "general":
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

        candidates.sort(
            key=lambda item: (
                int(item.get("sort_order") or 0),
                str(item.get("created_at") or ""),
            )
        )
        try:
            safe_limit = max(0, int(limit or 0))
        except (TypeError, ValueError):
            safe_limit = 3
        if safe_limit <= 0:
            return []
        return [
            _public_record(item, include_contact=include_contact)
            for item in candidates[:safe_limit]
        ]

    def find_active_by_domain(
        self,
        domain: Any,
        include_contact: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return public, active lawyer records for a domain."""
        raw_domain = _normalize_domain(domain)
        return [
            _public_record(item, include_contact=include_contact)
            for item in self.lawyer_repository.find_active_by_domain(raw_domain)
        ]

    def get_by_id(
        self,
        lawyer_id: str,
        include_contact: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Return one lawyer with contact details redacted by default."""
        record = self.lawyer_repository.get_by_id(str(lawyer_id))
        if record is None:
            return None
        return _public_record(record, include_contact=include_contact)

    def list_all(
        self,
        active_only: bool = True,
        include_contact: bool = False,
    ) -> List[Dict[str, Any]]:
        """List lawyers with contact details redacted by default."""
        return [
            _public_record(item, include_contact=include_contact)
            for item in self.lawyer_repository.list_all(active_only=active_only)
        ]

    def create(
        self,
        payload: Optional[Mapping[str, Any]] = None,
        include_contact: bool = False,
        **fields: Any,
    ) -> Dict[str, Any]:
        """Create a lawyer profile and return its public representation."""
        record = self.lawyer_repository.create(payload, **fields)
        return _public_record(record, include_contact=include_contact)

    def update(
        self,
        lawyer_id: str,
        payload: Optional[Mapping[str, Any]] = None,
        include_contact: bool = False,
        **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        """Update a lawyer profile and return its public representation."""
        record = self.lawyer_repository.update(str(lawyer_id), payload, **fields)
        if record is None:
            return None
        return _public_record(record, include_contact=include_contact)

    def toggle(
        self,
        lawyer_id: str,
        active: Optional[bool] = None,
        include_contact: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Toggle or set active state and return the public representation."""
        record = self.lawyer_repository.toggle(str(lawyer_id), active)
        if record is None:
            return None
        return _public_record(record, include_contact=include_contact)


__all__ = [
    "LAW_SPECIALTY_TERMS",
    "LAW_DOMAIN_TO_LAWYER",
    "LAW_DOMAIN_TO_LAWYER_DOMAIN",
    "LawyerService",
    "law_domain_to_lawyer_domain",
    "recommend_lawyers",
]