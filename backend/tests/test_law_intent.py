import pytest

from core.intent_recognizer import (
    LAW_PATTERNS,
    LAW_RISK_RULES,
    LAW_TEMPLATES,
    LawIntentRecognizer,
    UrgencyLevel,
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
    assert result.urgency == UrgencyLevel.CRITICAL
    assert result.urgency_level == UrgencyLevel.CRITICAL
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


def test_high_risk_detention():
    result = make_law_recognizer().recognize_sync("家人已经被刑事拘留")

    assert LawRiskFlag.DETENTION in result.risk_flags
    assert result.urgency == UrgencyLevel.CRITICAL
    assert result.urgency_level == UrgencyLevel.CRITICAL


def test_pure_detention_without_extra_urgency_keywords_is_critical():
    result = make_law_recognizer().recognize_sync("目前处于刑事拘留状态")

    assert LawRiskFlag.DETENTION in result.risk_flags
    assert result.urgency == UrgencyLevel.CRITICAL


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
    assert result.urgency == UrgencyLevel.CRITICAL


def test_no_lawyer_elevates_urgency_to_high():
    result = make_law_recognizer().recognize_sync("现在还没有请律师")

    assert LawRiskFlag.NO_LAWYER in result.risk_flags
    assert result.urgency == UrgencyLevel.HIGH
    assert result.urgency_level == UrgencyLevel.HIGH


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
    ("message", "risk_flag"),
    [
        ("家人没有被刑事拘留", LawRiskFlag.DETENTION),
        ("没有发生交通事故", LawRiskFlag.TRAFFIC_ACCIDENT),
    ],
)
def test_negated_risk_phrases_do_not_trigger(message, risk_flag):
    result = make_law_recognizer().recognize_sync(message)

    assert risk_flag not in result.risk_flags


def test_negated_urgency_does_not_escalate():
    result = make_law_recognizer().recognize_sync("不紧急")

    assert result.urgency == UrgencyLevel.MEDIUM


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


def test_unknown_traffic_accident_entity_is_empty():
    entities = LawEntityExtractor().extract("我想咨询离婚")

    assert entities["traffic_accident"] == []


@pytest.mark.parametrize(
    ("message", "expected_roles"),
    [
        ("当事人想咨询合同纠纷", ["当事人"]),
        ("本人想咨询离婚", ["本人"]),
    ],
)
def test_party_role_supports_common_first_person_values(message, expected_roles):
    entities = LawEntityExtractor().extract(message)

    assert entities["party_role"] == expected_roles


def test_urgency_uses_existing_enum_type():
    result = make_law_recognizer().recognize_sync("家人已经被刑事拘留")

    assert isinstance(result.urgency, UrgencyLevel)
    assert result.urgency_level is UrgencyLevel.CRITICAL


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
    assert "交通事故" in LAW_TEMPLATES[LawIntent.TRAFFIC_ACCIDENT]
    assert "没请律师" in LAW_RISK_RULES[LawRiskFlag.NO_LAWYER]


def test_generic_compensation_does_not_imply_traffic_accident_risk():
    result = make_law_recognizer().recognize_sync("公司拖欠工资，应该赔偿我")

    assert LawRiskFlag.TRAFFIC_ACCIDENT not in result.risk_flags


@pytest.mark.parametrize(
    ("message", "field", "expected"),
    [
        ("家人没有被刑事拘留", "detention_status", []),
        ("没有立案", "case_stage", []),
        ("没有受伤", "injury_or_death", []),
    ],
)
def test_negated_phrases_leave_entity_fields_unknown(message, field, expected):
    entities = LawEntityExtractor().extract(message)

    assert entities[field] == expected


def test_scope_aware_negation_suppresses_any_type_phrase():
    result = make_law_recognizer().recognize_sync("没有发生任何类型的交通事故")

    assert LawRiskFlag.TRAFFIC_ACCIDENT not in result.risk_flags
    assert result.entities["traffic_accident"] == []


def test_double_negation_does_not_trigger_no_lawyer_risk():
    result = make_law_recognizer().recognize_sync("并不是没有律师")

    assert LawRiskFlag.NO_LAWYER not in result.risk_flags
    assert result.urgency == UrgencyLevel.MEDIUM


def test_unlicensed_driving_is_not_treated_as_negation():
    result = make_law_recognizer().recognize_sync("无证驾驶发生交通事故")

    assert LawRiskFlag.TRAFFIC_ACCIDENT in result.risk_flags
    assert result.intent in {
        LawIntent.DANGEROUS_DRIVING,
        LawIntent.TRAFFIC_ACCIDENT,
    }


def test_minor_in_phrase_does_not_negate_detention():
    result = make_law_recognizer().recognize_sync("未成年人被刑事拘留")

    assert LawRiskFlag.DETENTION in result.risk_flags
    assert result.urgency == UrgencyLevel.CRITICAL


def test_self_negated_accident_does_not_raise_traffic_risk():
    result = make_law_recognizer().recognize_sync("本人没有发生事故")

    assert LawRiskFlag.TRAFFIC_ACCIDENT not in result.risk_flags


@pytest.mark.parametrize(
    ("message", "risk_flag"),
    [
        ("工伤事故", LawRiskFlag.TRAFFIC_ACCIDENT),
        ("无事故", LawRiskFlag.TRAFFIC_ACCIDENT),
        ("事故没有发生", LawRiskFlag.TRAFFIC_ACCIDENT),
        ("交通事故没有发生", LawRiskFlag.TRAFFIC_ACCIDENT),
        ("未受伤", LawRiskFlag.INJURY),
        ("尚未立案", LawRiskFlag.FILED),
    ],
)
def test_third_review_negation_and_domain_scope_are_not_traffic(message, risk_flag):
    result = make_law_recognizer().recognize_sync(message)

    assert risk_flag not in result.risk_flags


def test_unlicensed_driving_final_intent_is_dangerous_driving():
    result = make_law_recognizer().recognize_sync("无证驾驶发生交通事故")

    assert result.intent == LawIntent.DANGEROUS_DRIVING
    assert LawRiskFlag.TRAFFIC_ACCIDENT in result.risk_flags
    assert result.entities["traffic_accident"] == ["yes"]


@pytest.mark.parametrize(
    "message",
    [
        "并不是没请律师",
        "不是没请律师",
        "并非未委托律师",
    ],
)
def test_third_review_double_negative_lawyer_forms_do_not_trigger(message):
    result = make_law_recognizer().recognize_sync(message)

    assert LawRiskFlag.NO_LAWYER not in result.risk_flags
    assert result.urgency == UrgencyLevel.MEDIUM


def test_later_positive_lawyer_clause_overrides_earlier_negative_clause():
    result = make_law_recognizer().recognize_sync(
        "没有律师，但是我已经委托了律师"
    )

    assert LawRiskFlag.NO_LAWYER not in result.risk_flags
    assert result.urgency == UrgencyLevel.MEDIUM
    assert result.entities["has_lawyer"] == ["yes"]


def test_production_safety_incident_assessment_is_not_traffic_risk():
    result = make_law_recognizer().recognize_sync("生产安全事故认定")

    assert LawRiskFlag.TRAFFIC_ACCIDENT not in result.risk_flags


def test_production_safety_responsibility_is_not_traffic_intent():
    result = make_law_recognizer().recognize_sync("生产安全事故责任认定")

    assert result.intent != LawIntent.TRAFFIC_ACCIDENT


@pytest.mark.parametrize(
    ("message", "risk_flag"),
    [
        ("对方受伤不轻", LawRiskFlag.INJURY),
        ("法院已经立案不能拖", LawRiskFlag.FILED),
        ("刚刚发生交通事故没有大碍", LawRiskFlag.TRAFFIC_ACCIDENT),
    ],
)
def test_positive_events_are_not_suppressed_by_generic_markers(message, risk_flag):
    result = make_law_recognizer().recognize_sync(message)

    assert risk_flag in result.risk_flags


@pytest.mark.parametrize(
    "message",
    [
        "不构成交通事故",
        "不是交通事故",
        "不属于交通事故",
        "没有构成交通事故",
        "没有造成交通事故",
    ],
)
def test_fifth_review_event_negations_suppress_traffic(message):
    result = make_law_recognizer().recognize_sync(message)

    assert LawRiskFlag.TRAFFIC_ACCIDENT not in result.risk_flags


@pytest.mark.parametrize(
    "message",
    [
        "发生交通事故后还没请律师",
        "没请律师发生交通事故",
        "没有律师发生交通事故",
    ],
)
def test_fifth_review_no_lawyer_phrases_do_not_suppress_traffic(message):
    result = make_law_recognizer().recognize_sync(message)

    assert LawRiskFlag.TRAFFIC_ACCIDENT in result.risk_flags
    assert LawRiskFlag.NO_LAWYER in result.risk_flags


@pytest.mark.parametrize(
    "message",
    [
        "追尾了",
        "肇事逃逸",
    ],
)
def test_fifth_review_readds_traffic_specific_terms(message):
    result = make_law_recognizer().recognize_sync(message)

    assert LawRiskFlag.TRAFFIC_ACCIDENT in result.risk_flags


@pytest.mark.parametrize(
    "message",
    [
        "并不是未委托律师",
        "不是未委托律师",
        "并未没有律师",
        "并没有没有律师",
    ],
)
def test_fifth_review_extra_no_lawyer_double_negations(message):
    result = make_law_recognizer().recognize_sync(message)

    assert LawRiskFlag.NO_LAWYER not in result.risk_flags

@pytest.mark.parametrize(
    "message",
    [
        "尚未委托律师发生交通事故",
        "发生交通事故后尚未委托律师",
        "没有请发生交通事故",
    ],
)
def test_hotfix_no_lawyer_phrases_do_not_suppress_traffic(message):
    result = make_law_recognizer().recognize_sync(message)

    assert LawRiskFlag.TRAFFIC_ACCIDENT in result.risk_flags
    assert LawRiskFlag.NO_LAWYER in result.risk_flags


@pytest.mark.parametrize(
    ("message", "risk_flag"),
    [
        ("尚未发生交通事故", LawRiskFlag.TRAFFIC_ACCIDENT),
        ("尚未立案", LawRiskFlag.FILED),
    ],
)
def test_hotfix_precise_event_negations_still_suppress(message, risk_flag):
    result = make_law_recognizer().recognize_sync(message)

    assert risk_flag not in result.risk_flags


@pytest.mark.parametrize(
    "message",
    [
        "没有请假",
        "没有请别人",
        "并不是没有请",
        "没请假",
        "没请别人",
        "未请假",
        "未请别人",
    ],
)
def test_hotfix2_no_lawyer_invite_context_avoids_overmatch(message):
    result = make_law_recognizer().recognize_sync(message)

    assert LawRiskFlag.NO_LAWYER not in result.risk_flags


@pytest.mark.parametrize(
    "message",
    [
        "没有请发生交通事故",
        "没请发生交通事故",
        "未请发生交通事故",
        "没有请律师",
        "没请律师",
    ],
)
def test_hotfix2_no_lawyer_invite_context_still_detects(message):
    result = make_law_recognizer().recognize_sync(message)

    assert LawRiskFlag.NO_LAWYER in result.risk_flags


@pytest.mark.parametrize(
    ("message", "risk_flag"),
    [
        ("尚未委托律师发生交通事故", LawRiskFlag.TRAFFIC_ACCIDENT),
        ("发生交通事故后尚未委托律师", LawRiskFlag.TRAFFIC_ACCIDENT),
        ("尚未委托律师追尾", LawRiskFlag.TRAFFIC_ACCIDENT),
        ("尚未委托律师死亡", LawRiskFlag.INJURY),
    ],
)
def test_hotfix2_pending_no_lawyer_does_not_suppress_events(message, risk_flag):
    result = make_law_recognizer().recognize_sync(message)

    assert risk_flag in result.risk_flags
    assert LawRiskFlag.NO_LAWYER in result.risk_flags


@pytest.mark.parametrize(
    ("message", "risk_flag"),
    [
        ("尚未追尾", LawRiskFlag.TRAFFIC_ACCIDENT),
        ("尚未撞车", LawRiskFlag.TRAFFIC_ACCIDENT),
        ("尚未死亡", LawRiskFlag.INJURY),
        ("尚未受理案件", LawRiskFlag.FILED),
        ("尚未提起公诉", LawRiskFlag.PROSECUTION),
    ],
)
def test_hotfix2_pending_event_negations_suppress_risk(message, risk_flag):
    result = make_law_recognizer().recognize_sync(message)

    assert risk_flag not in result.risk_flags


@pytest.mark.parametrize(
    "message",
    [
        "没有请律师",
        "没请律师",
        "未请律师",
        "尚未请律师",
        "尚未委托律师",
        "尚未请发生交通事故",
        "尚未请",
        "没有请发生交通事故",
        "没请发生交通事故",
    ],
)
def test_hotfix3_required_no_lawyer_phrases_are_detected(message):
    result = make_law_recognizer().recognize_sync(message)

    assert LawRiskFlag.NO_LAWYER in result.risk_flags


@pytest.mark.parametrize(
    "message",
    [
        "尚未请发生交通事故",
        "发生交通事故后尚未请",
        "尚未请律师发生交通事故",
        "发生交通事故后尚未请律师",
    ],
)
def test_hotfix3_no_lawyer_pending_phrases_do_not_suppress_traffic(message):
    result = make_law_recognizer().recognize_sync(message)

    assert LawRiskFlag.TRAFFIC_ACCIDENT in result.risk_flags
    assert LawRiskFlag.NO_LAWYER in result.risk_flags


@pytest.mark.parametrize(
    "message",
    [
        "尚未请假",
        "尚未请别人",
        "尚未请病假",
    ],
)
def test_hotfix3_pending_invite_context_avoids_overmatch(message):
    result = make_law_recognizer().recognize_sync(message)

    assert LawRiskFlag.NO_LAWYER not in result.risk_flags


@pytest.mark.parametrize(
    ("message", "expected_traffic"),
    [
        ("尚未请，发生交通事故", True),
        ("没有请，发生交通事故", True),
        ("尚未请但发生交通事故", True),
        ("尚未请。", False),
        ("没有请。", False),
        ("尚未请，尚未发生交通事故", False),
    ],
)
def test_hotfix4_bare_invite_at_clause_boundary(message, expected_traffic):
    result = make_law_recognizer().recognize_sync(message)

    assert LawRiskFlag.NO_LAWYER in result.risk_flags
    assert (LawRiskFlag.TRAFFIC_ACCIDENT in result.risk_flags) is expected_traffic
