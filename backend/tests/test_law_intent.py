import pytest

from core.intent_recognizer import (
    LAW_PATTERNS,
    LAW_RISK_RULES,
    LAW_TEMPLATES,
    LawIntentRecognizer,
)
from core.law_domain import (
    LawEntityExtractor,
    LawIntent,
    LawIntentResult,
    LawRiskFlag,
)


def make_law_recognizer():
    return LawIntentRecognizer(api_key="test-key", model="test-model")


def test_dangerous_driving_result_contract():
    recognizer = make_law_recognizer()
    result = recognizer.recognize_sync(
        "我今天晚上在杭州酒后开车被交警查到，血液酒精 89mg/100ml，"
        "目前已经被刑事拘留，还没有请律师"
    )

    assert isinstance(result, LawIntentResult)
    assert result.intent == LawIntent.DANGEROUS_DRIVING
    assert result.intent_group == "criminal"
    assert result.urgency in {"HIGH", "CRITICAL"}
    assert result.risk_flags == [
        LawRiskFlag.DETENTION,
        LawRiskFlag.NO_LAWYER,
    ]
    assert result.confidence > 0.5
    assert result.source_scores["pattern"] > 0
    assert result.latency_ms >= 0

    assert result.entities["legal_domain"] == ["dangerous_driving"]
    assert result.entities["city"] == ["杭州"]
    assert result.entities["blood_alcohol"] == ["89mg/100ml"]
    assert result.entities["detention_status"] == ["刑事拘留"]
    assert result.entities["has_lawyer"] == ["no"]


def test_criminal_defense_detects_detention_court_and_no_lawyer_risk():
    result = make_law_recognizer().recognize_sync(
        "家人已经被刑事拘留，明天开庭，目前还没有委托律师"
    )

    assert result.intent == LawIntent.CRIMINAL_DEFENSE
    assert set(result.risk_flags) == {
        LawRiskFlag.DETENTION,
        LawRiskFlag.COURT_SOON,
        LawRiskFlag.NO_LAWYER,
    }
    assert result.urgency == "HIGH"


@pytest.mark.parametrize(
    ("message", "risk_flag"),
    [
        ("后天开庭", LawRiskFlag.COURT_SOON),
        ("对方受伤比较严重", LawRiskFlag.INJURY),
        ("发生交通事故后责任认定有争议", LawRiskFlag.TRAFFIC_ACCIDENT),
        ("法院已经立案", LawRiskFlag.FILED),
        ("案件已经移送检察院审查起诉", LawRiskFlag.PROSECUTION),
        ("现在还没有请律师", LawRiskFlag.NO_LAWYER),
    ],
)
def test_required_risk_flags_are_detected(message, risk_flag):
    result = make_law_recognizer().recognize_sync(message)

    assert risk_flag in result.risk_flags


@pytest.mark.parametrize(
    ("message", "expected_intent", "expected_group"),
    [
        ("我可能是醉驾", LawIntent.DANGEROUS_DRIVING, "criminal"),
        ("需要刑事辩护律师", LawIntent.CRIMINAL_DEFENSE, "criminal"),
        (
            "公司拖欠工资，我想申请劳动仲裁",
            LawIntent.LABOR_DISPUTE,
            "civil",
        ),
        ("我想咨询离婚和抚养权", LawIntent.MARRIAGE_FAMILY, "civil"),
        ("合同纠纷，对方违约，想起诉", LawIntent.CONTRACT_DISPUTE, "civil"),
        ("交通事故责任认定和赔偿", LawIntent.TRAFFIC_ACCIDENT, "civil"),
        ("民间借贷，对方欠钱不还，有借条", LawIntent.CIVIL_LOAN, "civil"),
        ("帮我预约律师", LawIntent.LAWYER_APPOINTMENT, "service"),
        ("你们律所怎么收费", LawIntent.LAW_FIRM_SERVICE, "service"),
    ],
)
def test_required_domains_are_recognized(message, expected_intent, expected_group):
    result = make_law_recognizer().recognize_sync(message)

    assert result.intent == expected_intent
    assert result.intent_group == expected_group


def test_law_entity_extractor_extracts_drunk_driving_entities():
    entities = LawEntityExtractor().extract(
        "昨天在杭州醉驾发生交通事故，血液酒精 89mg/100ml，本人没有律师",
        intent=LawIntent.DANGEROUS_DRIVING,
    )

    assert entities["legal_domain"] == ["dangerous_driving"]
    assert entities["incident_time"] == ["昨天"]
    assert entities["city"] == ["杭州"]
    assert entities["blood_alcohol"] == ["89mg/100ml"]
    assert entities["traffic_accident"] == ["yes"]
    assert entities["has_lawyer"] == ["no"]
    assert entities["risk_flags"] == [
        LawRiskFlag.TRAFFIC_ACCIDENT.value,
        LawRiskFlag.NO_LAWYER.value,
    ]


def test_law_pattern_data_covers_all_enum_domains():
    for intent in LawIntent:
        if intent is LawIntent.OTHER:
            continue
        assert LAW_TEMPLATES[intent]
        assert LAW_PATTERNS[intent]

    assert LawRiskFlag.DETENTION in LAW_RISK_RULES
    assert LawRiskFlag.COURT_SOON in LAW_RISK_RULES
    assert LawRiskFlag.INJURY in LAW_RISK_RULES
    assert LawRiskFlag.TRAFFIC_ACCIDENT in LAW_RISK_RULES
    assert LawRiskFlag.FILED in LAW_RISK_RULES
    assert LawRiskFlag.PROSECUTION in LAW_RISK_RULES
    assert LawRiskFlag.NO_LAWYER in LAW_RISK_RULES


def test_brief_requires_traffic_compensation_and_no_lawyer_abbreviation():
    assert "赔偿" in LAW_TEMPLATES[LawIntent.TRAFFIC_ACCIDENT]
    assert "没请律师" in LAW_RISK_RULES[LawRiskFlag.NO_LAWYER]


def test_generic_compensation_does_not_imply_traffic_accident_risk():
    result = make_law_recognizer().recognize_sync("公司拖欠工资，应该赔偿我")

    assert LawRiskFlag.TRAFFIC_ACCIDENT not in result.risk_flags
