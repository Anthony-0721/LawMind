"""Law-specific domain enums, constants, result model and entity extraction.

This module is intentionally independent of the old customer-service
intent recognizer. Task 3 will consume LawIntent/LawRiskFlag from here when
it replaces the generic Agent types.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class LawIntent(str, Enum):
    DANGEROUS_DRIVING = "dangerous_driving"
    CRIMINAL_DEFENSE = "criminal_defense"
    LABOR_DISPUTE = "labor_dispute"
    MARRIAGE_FAMILY = "marriage_family"
    CONTRACT_DISPUTE = "contract_dispute"
    TRAFFIC_ACCIDENT = "traffic_accident"
    CIVIL_LOAN = "civil_loan"
    LAWYER_APPOINTMENT = "lawyer_appointment"
    LAW_FIRM_SERVICE = "law_firm_service"
    OTHER = "other"


class LawRiskFlag(str, Enum):
    DETENTION = "detention"
    COURT_SOON = "court_soon"
    INJURY = "injury"
    TRAFFIC_ACCIDENT = "traffic_accident"
    FILED = "filed"
    PROSECUTION = "prosecution"
    NO_LAWYER = "no_lawyer"


@dataclass
class LawIntentResult:
    """Deterministic result returned by LawIntentRecognizer."""

    intent: LawIntent
    intent_group: str
    urgency: Any
    risk_flags: List[LawRiskFlag]
    entities: Dict[str, List[str]]
    confidence: float
    source_scores: Dict[str, float] = field(default_factory=dict)
    latency_ms: float = 0.0

    @property
    def urgency_level(self) -> Any:
        """Return the existing UrgencyLevel enum for the string/named urgency."""
        from core.intent_recognizer import UrgencyLevel

        if isinstance(self.urgency, UrgencyLevel):
            return self.urgency
        return UrgencyLevel[self.urgency]


LAW_INTENT_GROUPS: Dict[LawIntent, str] = {
    LawIntent.DANGEROUS_DRIVING: "criminal",
    LawIntent.CRIMINAL_DEFENSE: "criminal",
    LawIntent.LABOR_DISPUTE: "civil",
    LawIntent.MARRIAGE_FAMILY: "civil",
    LawIntent.CONTRACT_DISPUTE: "civil",
    LawIntent.TRAFFIC_ACCIDENT: "civil",
    LawIntent.CIVIL_LOAN: "civil",
    LawIntent.LAWYER_APPOINTMENT: "service",
    LawIntent.LAW_FIRM_SERVICE: "service",
    LawIntent.OTHER: "other",
}

# Few-shot style templates shared with later LLM-based versions.
LAW_TEMPLATES: Dict[LawIntent, List[str]] = {
    LawIntent.DANGEROUS_DRIVING: [
        "酒后开车被查",
        "醉驾被查",
        "危险驾驶被查",
        "血液酒精超标",
        "吹气检测阳性",
    ],
    LawIntent.CRIMINAL_DEFENSE: [
        "涉嫌犯罪",
        "刑事拘留",
        "辩护律师",
        "取保候审",
        "移送检察院",
    ],
    LawIntent.LABOR_DISPUTE: [
        "劳动仲裁",
        "拖欠工资",
        "违法解除",
        "克扣工资",
        "违法辞退",
    ],
    LawIntent.MARRIAGE_FAMILY: [
        "离婚",
        "抚养权",
        "财产分割",
        "抚养费",
        "彩礼返还",
    ],
    LawIntent.CONTRACT_DISPUTE: [
        "合同纠纷",
        "违约",
        "解除合同",
        "追讨货款",
        "定金返还",
    ],
    LawIntent.TRAFFIC_ACCIDENT: [
        "交通事故",
        "责任认定",
        "追尾",
        "交通肇事",
        "赔偿",
        "保险理赔",
    ],
    LawIntent.CIVIL_LOAN: [
        "民间借贷",
        "欠钱",
        "借条",
        "借款合同",
        "债务纠纷",
    ],
    LawIntent.LAWYER_APPOINTMENT: [
        "预约律师",
        "转人工",
        "咨询律师",
        "联系律师",
        "律师推荐",
    ],
    LawIntent.LAW_FIRM_SERVICE: [
        "怎么收费",
        "咨询流程",
        "律所地址",
        "代理费",
        "服务流程",
    ],
    LawIntent.OTHER: [],
}

# Strong local keywords used by the offline LawIntentRecognizer.
LAW_PATTERNS: Dict[LawIntent, List[str]] = {
    LawIntent.DANGEROUS_DRIVING: [
        "醉驾",
        "酒驾",
        "危险驾驶",
        "酒后开车",
        "血液酒精",
        "酒精含量",
        "吹气检测",
        "醉驾被查",
        "危险驾驶被查",
    ],
    LawIntent.CRIMINAL_DEFENSE: [
        "刑事拘留",
        "被拘留",
        "涉嫌犯罪",
        "刑事辩护",
        "辩护律师",
        "取保候审",
        "看守所",
        "移送检察院",
        "审查起诉",
        "开庭",
    ],
    LawIntent.LABOR_DISPUTE: [
        "劳动仲裁",
        "拖欠工资",
        "违法解除",
        "克扣工资",
        "违法辞退",
        "劳动合同",
        "加班费",
    ],
    LawIntent.MARRIAGE_FAMILY: [
        "离婚",
        "抚养权",
        "财产分割",
        "抚养费",
        "彩礼",
        "家暴",
    ],
    LawIntent.CONTRACT_DISPUTE: [
        "合同纠纷",
        "违约",
        "解除合同",
        "货款",
        "定金",
        "起诉",
    ],
    LawIntent.TRAFFIC_ACCIDENT: [
        "交通事故",
        "责任认定",
        "撞车",
        "追尾",
        "交通肇事",
        "赔偿",
        "事故认定",
        "保险理赔",
    ],
    LawIntent.CIVIL_LOAN: [
        "民间借贷",
        "欠钱",
        "欠款",
        "借条",
        "借款",
        "债务",
        "还款",
    ],
    LawIntent.LAWYER_APPOINTMENT: [
        "预约律师",
        "转人工",
        "咨询律师",
        "联系律师",
        "找律师",
        "律师推荐",
    ],
    LawIntent.LAW_FIRM_SERVICE: [
        "怎么收费",
        "咨询流程",
        "律所地址",
        "代理费",
        "收费标准",
        "服务流程",
        "工作时间",
    ],
    LawIntent.OTHER: [],
}

# Priority is used as a deterministic tie-breaker. Drunk/dangerous driving is
# the first product focus and should not be displaced by a generic signal.
LAW_PATTERN_PRIORITY: List[LawIntent] = [
    LawIntent.DANGEROUS_DRIVING,
    LawIntent.CRIMINAL_DEFENSE,
    LawIntent.LABOR_DISPUTE,
    LawIntent.MARRIAGE_FAMILY,
    LawIntent.CONTRACT_DISPUTE,
    LawIntent.TRAFFIC_ACCIDENT,
    LawIntent.CIVIL_LOAN,
    LawIntent.LAWYER_APPOINTMENT,
    LawIntent.LAW_FIRM_SERVICE,
    LawIntent.OTHER,
]

LAW_RISK_RULES: Dict[LawRiskFlag, List[str]] = {
    LawRiskFlag.DETENTION: [
        "刑事拘留",
        "被拘留",
        "已经关了",
        "羁押",
        "看守所",
    ],
    LawRiskFlag.COURT_SOON: [
        "今天开庭",
        "明天开庭",
        "后天开庭",
        "马上开庭",
        "近期开庭",
        "本周开庭",
        "下周开庭",
    ],
    LawRiskFlag.INJURY: [
        "受伤",
        "死亡",
        "重伤",
        "轻伤",
        "骨折",
        "伤残",
        "人身伤害",
    ],
    LawRiskFlag.TRAFFIC_ACCIDENT: [
        "交通事故",
        "撞车",
        "追尾",
        "交通肇事",
        "事故认定",
        "肇事逃逸",
    ],
    LawRiskFlag.FILED: [
        "立案",
        "已经立案",
        "受理案件",
    ],
    LawRiskFlag.PROSECUTION: [
        "审查起诉",
        "移送检察院",
        "提起公诉",
        "检察院起诉",
    ],
    LawRiskFlag.NO_LAWYER: [
        "没有律师",
        "没有请律师",
        "还没有律师",
        "还没有委托律师",
        "还没请律师",
        "没请律师",
        "没有聘请律师",
        "未委托律师",
        "尚未委托律师",
        "没有代理人",
    ],
}


_NEGATION_MARKERS = ("没有", "没", "未", "尚未", "不", "无", "并非", "不是")


def _is_negated_at(text: str, keyword: str, index: int) -> bool:
    context = text[max(0, index - 6):index]
    return any(marker in context for marker in _NEGATION_MARKERS)


def has_unnegated_keyword(text: str, keyword: str) -> bool:
    """Return True when keyword occurs at least once without a nearby negation."""
    index = text.find(keyword)
    while index != -1:
        if not _is_negated_at(text, keyword, index):
            return True
        index = text.find(keyword, index + 1)
    return False


def detect_law_risk_flags(message: str) -> List[LawRiskFlag]:
    """Return every non-negated risk flag whose keyword is present."""
    text = str(message or "")
    flags: List[LawRiskFlag] = []
    for flag, keywords in LAW_RISK_RULES.items():
        for keyword in keywords:
            if flag is LawRiskFlag.NO_LAWYER:
                detected = keyword in text
            else:
                detected = has_unnegated_keyword(text, keyword)
            if detected:
                flags.append(flag)
                break
    return flags


class LawEntityExtractor:
    """Rule-based extractor for the law-domain entity schema used by Task 3."""

    _CITY_RE = re.compile(
        r"(北京|上海|广州|深圳|杭州|成都|武汉|南京|西安|重庆|天津|苏州|"
        r"郑州|长沙|青岛|厦门|福州|合肥|昆明|宁波|无锡|佛山|东莞|珠海)"
    )
    _INCIDENT_TIME_RE = re.compile(
        r"(今天|今晚|昨天|前天|明天|后天|本周|这周|下周|上周|今年|去年|"
        r"\d{1,2}月\d{1,2}日|\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)"
    )
    _BLOOD_ALCOHOL_RE = re.compile(
        r"((?:(?:血液酒精|酒精含量)\s*[:：]?\s*)?(?:\d+(?:\.\d+)?)\s*(?:mg/100ml|mg/dL|mg\/100ml|毫克/100毫升))"
        r"|(?:血液酒精|酒精含量)\s*[:：]?\s*(\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )
    _AMOUNT_RE = re.compile(
        r"((?:¥|￥)?\s*\d+(?:\.\d+)?\s*(?:万元|万|元|块))"
        r"|((?:¥|￥)\s*\d+(?:\.\d+)?)"
    )

    def extract(
        self,
        message: str,
        intent: Optional[LawIntent] = None,
    ) -> Dict[str, List[str]]:
        text = str(message or "")
        risk_flags = detect_law_risk_flags(text)
        risk_values = [flag.value for flag in risk_flags]
        blood_alcohol = self._blood_alcohol(text)
        disputed_amount = self._amounts(text)
        legal_domain = [intent.value] if intent is not None else []

        return {
            "legal_domain": legal_domain,
            "case_stage": self._case_stage(text),
            "party_role": self._party_role(text),
            "incident_time": self._unique(self._INCIDENT_TIME_RE.findall(text)),
            "city": self._unique(self._CITY_RE.findall(text)),
            "blood_alcohol": blood_alcohol,
            "traffic_accident": ["yes"] if LawRiskFlag.TRAFFIC_ACCIDENT in risk_flags else [],
            "injury_or_death": self._injury_or_death(text),
            "detention_status": self._detention_status(text),
            "has_lawyer": self._has_lawyer(text, risk_flags),
            "disputed_amount": disputed_amount,
            "risk_flags": risk_values,
        }

    @staticmethod
    def _blood_alcohol(text: str) -> List[str]:
        values: List[str] = []
        for match in LawEntityExtractor._BLOOD_ALCOHOL_RE.finditer(text):
            value = match.group(1) or match.group(2)
            if value:
                value = re.sub(r"^(?:血液酒精|酒精含量)\s*[:：]?\s*", "", value)
                values.append(value.replace(" ", ""))
        return LawEntityExtractor._unique(values)

    @staticmethod
    def _amounts(text: str) -> List[str]:
        values: List[str] = []
        for match in LawEntityExtractor._AMOUNT_RE.finditer(text):
            value = match.group(1) or match.group(2)
            if value:
                values.append(value.replace(" ", ""))
        return LawEntityExtractor._unique(values)

    @staticmethod
    def _case_stage(text: str) -> List[str]:
        if "审查起诉" in text and has_unnegated_keyword(text, "审查起诉"):
            return ["审查起诉"]
        if any(
            item in text and has_unnegated_keyword(text, item)
            for item in ("提起公诉", "检察院起诉", "起诉")
        ):
            return ["起诉"]
        if "开庭" in text and has_unnegated_keyword(text, "开庭"):
            return ["开庭"]
        if "立案" in text and has_unnegated_keyword(text, "立案"):
            return ["立案"]
        if "取保候审" in text and has_unnegated_keyword(text, "取保候审"):
            return ["取保候审"]
        if any(
            item in text and has_unnegated_keyword(text, item)
            for item in ("刑事拘留", "被拘留", "拘留")
        ):
            return ["拘留"]
        if "侦查" in text and has_unnegated_keyword(text, "侦查"):
            return ["侦查"]
        return []

    @staticmethod
    def _party_role(text: str) -> List[str]:
        if any(item in text for item in ("家人", "家属")):
            return ["家属"]
        if "当事人" in text:
            return ["当事人"]
        if "朋友" in text:
            return ["朋友"]
        if any(item in text for item in ("配偶", "妻子", "丈夫", "老婆")):
            return ["配偶"]
        if any(item in text for item in ("本人", "我自己", "我")):
            return ["本人"]
        return []

    @staticmethod
    def _injury_or_death(text: str) -> List[str]:
        return LawEntityExtractor._unique(
            [
                item
                for item in ("重伤", "轻伤", "受伤", "死亡", "骨折", "伤残")
                if item in text and has_unnegated_keyword(text, item)
            ]
        )

    @staticmethod
    def _detention_status(text: str) -> List[str]:
        if "取保候审" in text and has_unnegated_keyword(text, "取保候审"):
            return ["取保候审"]
        if any(
            item in text and has_unnegated_keyword(text, item)
            for item in ("释放", "出来了")
        ):
            return ["释放"]
        if "刑事拘留" in text and has_unnegated_keyword(text, "刑事拘留"):
            return ["刑事拘留"]
        if any(
            item in text and has_unnegated_keyword(text, item)
            for item in ("被拘留", "拘留", "羁押", "看守所")
        ):
            return ["拘留"]
        return []

    @staticmethod
    def _has_lawyer(text: str, risk_flags: List[LawRiskFlag]) -> List[str]:
        if LawRiskFlag.NO_LAWYER in risk_flags:
            return ["no"]
        if any(item in text for item in ("有律师", "已委托律师", "委托了律师", "我的律师", "聘请了律师")):
            return ["yes"]
        return ["unknown"]

    @staticmethod
    def _unique(values: List[str]) -> List[str]:
        return list(dict.fromkeys(value for value in values if value))
