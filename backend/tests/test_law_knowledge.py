"""Second Task 5 review: skill injection, escalation runtime prompt, Chroma cleanup and env compatibility."""
import asyncio
import importlib
import json
import re
import sys
import types
from pathlib import Path

from core.skill_loader import SkillManager

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_ROOT / "data"
SKILLS_DIR = BACKEND_ROOT / "skills" / "law_firm"
BRIEFS_DIR = DATA_DIR / "law_domain_briefs"

SKILLS = {
    "front_desk_reception": "reception",
    "criminal_consultation": "criminal",
    "civil_consultation": "civil",
    "escalation_and_intake": "escalation",
}

EXPECTED_BRIEFS = {
    "dangerous_driving.md",
    "criminal_defense.md",
    "labor_dispute.md",
    "marriage_family.md",
    "contract_dispute.md",
    "traffic_accident.md",
    "civil_loan.md",
    "reception_process.md",
}

CONTENT_SECTIONS = (
    "角色定位",
    "处理流程",
    "关键案情追问",
    "升级条件",
    "禁止事项",
    "免责声明",
    "敏感信息保护",
)

SKILL_REQUIRED_KEYWORDS = {
    "criminal": [
        "看守所", "逮捕", "起诉", "辩护", "醉驾", "酒驾", "危险驾驶", "交警",
        "肇事", "逃逸", "拘留", "警察", "刑事", "取保", "开庭", "犯罪", "涉嫌",
    ],
    "civil": [
        "违法辞退", "辞退", "违约", "彩礼", "民间借贷", "借款", "抚养费",
        "追尾", "车祸", "责任认定", "保险", "工伤", "家暴", "离婚", "劳动",
        "工资", "仲裁", "合同", "欠款", "借条", "纠纷", "赔偿",
    ],
    "escalation": [
        "联系方式", "人工客服", "咨询记录", "找律师", "联系律师", "转人工",
        "人工", "预约", "咨询律师", "律师推荐",
    ],
    "reception": [
        "代理费", "接待", "咨询", "收费", "流程", "地址", "时间", "预约",
        "法律服务", "帮助",
    ],
}

REPRESENTATIVE_PHRASES = {
    "criminal": [
        "我家人被关在看守所了，现在应该怎么办？",
        "警察说他已经被逮捕，下一步可能是审查起诉。",
        "希望找刑事辩护律师看看这个案件。",
        "交警处理醉驾后可能涉嫌危险驾驶犯罪。",
        "这次肇事逃逸后，家人正在申请取保候审。",
        "目前已拘留，收到开庭通知，涉嫌犯罪。",
    ],
    "civil": [
        "公司违法辞退我，我准备申请劳动仲裁。",
        "合同违约和彩礼返还应该怎么处理？",
        "民间借贷的借款还没有还，我有借条和聊天记录。",
        "孩子抚养费、追尾责任认定和保险理赔都想问。",
        "车祸后公司是否应该按工伤处理？",
        "家暴离婚后，抚养权、工资和赔偿怎么主张？",
    ],
    "escalation": [
        "我想留联系方式并转人工客服。",
        "请帮我找律师预约咨询。",
        "咨询律师推荐和咨询记录怎么查看？",
    ],
    "reception": [
        "请问代理费和接待时间是什么？",
        "我想咨询律所收费、流程和地址。",
        "预约法律服务需要帮助。",
    ],
}

OLD_CUSTOMER_TITLES = (
    "退款政策",
    "订单查询",
    "账户安全",
    "技术故障排查",
    "会员与积分",
    "配送说明",
)

# Sentinel prefix that must never be honoured: config lives under LAWMIND_ only.
RETIRED_ENV_PREFIX = "RETIRED_"

# Non-LAWMIND_ environment names legitimately read by production code (third-party
# SDK conventions, infra wiring, and the retained admin-password alias).
ALLOWED_FOREIGN_ENV_VARS = {
    "ALERT_WEBHOOK_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "API_HOST",
    "API_PORT",
    "APP_ENV",
    "CHROMA_HOST",
    "CHROMA_PERSIST_DIRECTORY",
    "CHROMA_PORT",
    "DATABASE_URL",
    "EVAL_BASELINE_PATH",
    "EVAL_SHIPPED_BASELINE_PATH",
    "LAW_FIRM_ADMIN_PASSWORD",
    "LOG_LEVEL",
    "MONITOR_INTERVAL",
    "PROMETHEUS_PORT",
    "REDIS_URL",
}


def _parse_front_matter(text: str):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta = {}
    end = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = idx
            break
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    body = "\n".join(lines[end + 1:]) if end is not None else text
    return meta, body


def _fake_chromadb_module():
    if "chromadb" in sys.modules:
        return sys.modules["chromadb"]
    fake = types.ModuleType("chromadb")
    fake.Settings = lambda **_kwargs: None
    fake.HttpClient = object
    fake.PersistentClient = object
    sys.modules["chromadb"] = fake
    return fake


def _load_knowledge_base_module():
    _fake_chromadb_module()
    return importlib.import_module("mcp.knowledge_base")


def test_seed_files_exist_and_have_expected_counts_ranges():
    faq_path = DATA_DIR / "law_faq_seed.json"
    lawyers_path = DATA_DIR / "lawyers_seed.json"
    assert faq_path.exists()
    assert lawyers_path.exists()

    faq_items = json.loads(faq_path.read_text(encoding="utf-8"))
    assert 60 <= len(faq_items) <= 100
    for item in faq_items:
        assert item["category"]
        assert item["question"]
        assert item["answer"]
        assert isinstance(item["keywords"], list) and item["keywords"]
        assert item["active"] is True
    official = [item for item in faq_items if item.get("source") == "official_law"]
    assert len(official) == 20
    assert all("【法律依据】" in item["answer"] for item in official)
    assert all("【核对日期】" in item["answer"] for item in official)

    lawyers = json.loads(lawyers_path.read_text(encoding="utf-8"))
    assert 3 <= len(lawyers) <= 5
    for lawyer in lawyers:
        assert lawyer["name"]
        assert lawyer["domain"]
        assert isinstance(lawyer["specialties"], list) and lawyer["specialties"]
        assert lawyer["intro"]
        assert lawyer["active"] is True
        assert isinstance(lawyer["sort_order"], int)
        assert "phone" not in lawyer
        assert "email" not in lawyer
        assert "wechat" not in lawyer
        assert "id_card" not in lawyer

    brief_paths = {path.name for path in BRIEFS_DIR.glob("*.md")}
    assert brief_paths == EXPECTED_BRIEFS


def test_skill_files_have_required_front_matter_sections_and_keywords():
    for folder, expected_agent in SKILLS.items():
        path = SKILLS_DIR / folder / "SKILL.md"
        assert path.exists(), path
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_front_matter(text)

        assert meta.get("name"), path
        assert meta.get("description"), path
        assert meta.get("keywords"), path
        assert meta.get("agents") == expected_agent, path
        assert meta.get("enabled") in {"true", "True", "1"}, path
        assert len(body) > 200, path
        for section in CONTENT_SECTIONS:
            assert section in body, f"{path} missing section: {section}"
        for keyword in SKILL_REQUIRED_KEYWORDS[expected_agent]:
            assert keyword in meta["keywords"], f"{path} missing keyword: {keyword}"


def test_representative_skill_phrases_inject_matching_skills():
    manager = SkillManager(str(SKILLS_DIR))
    manager.load()
    assert manager.errors == []

    expected_titles = {
        "criminal": "刑事咨询规范",
        "civil": "民事咨询规范",
        "escalation": "人工升级与留资规范",
        "reception": "律所前台接待规范",
    }
    for agent_type, phrases in REPRESENTATIVE_PHRASES.items():
        for phrase in phrases:
            prompt = manager.prompt_for(phrase, agent_type)
            assert expected_titles[agent_type] in prompt, (agent_type, phrase)
            assert len(prompt) > 100, (agent_type, phrase)

    # Reception should also catch common criminal first-turn wording.
    for phrase in REPRESENTATIVE_PHRASES["criminal"]:
        prompt = manager.prompt_for(phrase, "reception")
        assert "律所前台接待规范" in prompt, phrase


def test_escalation_skill_injects_without_message_keywords():
    manager = SkillManager(str(SKILLS_DIR))
    manager.load()
    prompt = manager.prompt_for("这是一段与关键词无关的随机内容", "escalation")
    assert "人工升级与留资规范" in prompt


def test_per_agent_model_env_ignores_legacy_prefix(monkeypatch):
    from agents.agent_orchestrator import AgentOrchestrator, CriminalDefenseAgent

    primary = "LAWMIND_CRIMINAL_MODEL"
    monkeypatch.setenv(primary, "lawmind-criminal-model")
    monkeypatch.setenv(RETIRED_ENV_PREFIX + "CRIMINAL_MODEL", "legacy-criminal-model")
    agent = AgentOrchestrator._make_agent(
        CriminalDefenseAgent, None, "default-model", None
    )
    assert agent._model == "lawmind-criminal-model"

    # The retired deployment name must never be honoured again.
    monkeypatch.delenv(primary)
    agent = AgentOrchestrator._make_agent(
        CriminalDefenseAgent, None, "default-model", None
    )
    assert agent._model == "default-model"


def test_api_env_readers_ignore_legacy_prefix(monkeypatch):
    import api.main as api_main

    primary = "LAWMIND_SKILLS_DIR"
    monkeypatch.setenv(primary, "/primary/skills")
    monkeypatch.setenv(RETIRED_ENV_PREFIX + "SKILLS_DIR", "/legacy/skills")
    assert api_main._env_str(primary, "/default") == "/primary/skills"

    monkeypatch.delenv(primary)
    assert api_main._env_str(primary, "/default") == "/default"

    primary_int = "LAWMIND_SKILLS_MAX_PROMPT_CHARS"
    monkeypatch.setenv(primary_int, "9000")
    monkeypatch.setenv(RETIRED_ENV_PREFIX + "SKILLS_MAX_PROMPT_CHARS", "7000")
    assert api_main._env_int(primary_int, 5000) == 9000

    monkeypatch.setenv(primary_int, "not-a-number")
    assert api_main._env_int(primary_int, 5000) == 5000


def test_orchestrator_env_reader_ignores_legacy_prefix(monkeypatch):
    from agents.agent_orchestrator import _env_int

    primary = "LAWMIND_TOOL_TRACE_MAX"
    monkeypatch.setenv(primary, "777")
    monkeypatch.setenv(RETIRED_ENV_PREFIX + "TOOL_TRACE_MAX", "321")
    assert _env_int(primary, 200) == 777

    monkeypatch.delenv(primary)
    assert _env_int(primary, 200) == 200


def test_production_config_is_lawmind_scoped_and_seeds_are_pii_free():
    # Every literal environment name read by production code must be LAWMIND_-scoped
    # or a registered infrastructure name. This blocks reintroducing an alternate-prefix
    # config fallback without having to name any specific legacy prefix.
    get_env_literal = re.compile(r'os\.getenv\("([A-Za-z_][A-Za-z0-9_]*)"')
    offenders = []
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name in get_env_literal.findall(text):
            if name.startswith("LAWMIND_") or name in ALLOWED_FOREIGN_ENV_VARS:
                continue
            offenders.append("%s: %s" % (path.name, name))
    assert not offenders, offenders

    faq_text = (DATA_DIR / "law_faq_seed.json").read_text(encoding="utf-8")
    lawyer_text = (DATA_DIR / "lawyers_seed.json").read_text(encoding="utf-8")
    combined = faq_text + lawyer_text
    assert not re.search(r"1[3-9]\d{9}", combined)
    assert "身份证号" not in combined


def test_loaders_return_law_documents_with_source_and_disclaimer():
    kb_module = _load_knowledge_base_module()
    faq_docs = kb_module.load_law_faq_documents()
    assert len(faq_docs) == 69
    for doc in faq_docs:
        assert "问题：" in doc["content"]
        assert "回答：" in doc["content"]
        assert "不构成正式法律意见" in doc["content"]
        assert doc["metadata"]["source"] == "law_firm"
        assert doc["metadata"]["category"]
    official = [doc for doc in faq_docs if "【法律依据】" in doc["content"]]
    assert len(official) == 20
    assert all("【核对日期】" in doc["content"] for doc in official)
    assert all("【来源】" in doc["content"] for doc in official)
    assert any("80mg/100ml" in doc["content"] for doc in official)
    combined = "".join(doc["content"] for doc in faq_docs)
    assert "醉驾" in combined
    for old_title in OLD_CUSTOMER_TITLES:
        assert old_title not in combined

    brief_docs = kb_module.load_law_domain_briefs()
    assert len(brief_docs) == 8
    assert any(doc["metadata"]["category"] == "dangerous_driving" for doc in brief_docs)
    assert any(doc["metadata"]["category"] == "criminal_defense" for doc in brief_docs)
    assert all(doc["metadata"]["source"] == "law_firm" for doc in brief_docs)
    assert all(doc["metadata"]["category"] for doc in brief_docs)
    assert all("不构成正式法律意见" in doc["content"] for doc in brief_docs)


def test_criminal_and_dangerous_driving_briefs_are_separate():
    criminal = (BRIEFS_DIR / "criminal_defense.md").read_text(encoding="utf-8")
    dangerous = (BRIEFS_DIR / "dangerous_driving.md").read_text(encoding="utf-8")
    assert "刑事辩护领域摘要" in criminal
    assert "醉驾" not in criminal
    assert "危险驾驶" not in criminal
    assert "危险驾驶领域摘要" in dangerous
    assert "醉驾" in dangerous


class FakeCollection:
    """Minimal ChromaDB-compatible collection fixture for unit tests."""

    def __init__(self, count=0):
        self.added_documents = []
        self.added_metadatas = []
        self.added_ids = []
        self.count_value = count
        self.deleted_where = None

    def count(self):
        return self.count_value

    def add(self, ids, documents, metadatas):
        self.added_ids.extend(ids)
        self.added_documents.extend(documents)
        self.added_metadatas.extend(metadatas)
        self.count_value += len(ids)

    def delete(self, where=None, ids=None):
        self.deleted_where = where
        return 1

    def query(self, query_texts, n_results):
        content = (
            "问题：醉驾被查后一般会经过哪些阶段？\n"
            "回答：通常先由公安机关调查。\n"
            "温馨提示：以上内容仅为一般法律知识，不构成正式法律意见。"
        )
        return {
            "documents": [[content]],
            "metadatas": [[{
                "title": "FAQ｜刑事/醉驾｜醉驾被查后一般会经过哪些阶段？",
                "chunk_index": 0,
                "source": "law_firm",
                "category": "criminal",
            }]],
            "distances": [[0.1]],
        }


class FakeChromaClient:
    def __init__(self):
        self.deleted = []
        self.collection = FakeCollection(count=1)

    def heartbeat(self):
        return None

    def delete_collection(self, name):
        self.deleted.append(name)

    def get_or_create_collection(self, name, metadata=None):
        assert name == "law_knowledge_base"
        return self.collection


def test_knowledge_base_deletes_legacy_collection(monkeypatch):
    kb_module = _load_knowledge_base_module()
    client = FakeChromaClient()
    monkeypatch.setattr(kb_module.chromadb, "HttpClient", lambda *args, **kwargs: client)
    kb_module.KnowledgeBase(chroma_host="fake", chroma_port=1, chroma_path="fake")
    assert client.deleted == ["knowledge_base"]


def test_knowledge_base_uses_law_collection_and_loads_only_law_docs():
    kb_module = _load_knowledge_base_module()
    assert kb_module.KnowledgeBase.COLLECTION_NAME == "law_knowledge_base"

    kb = kb_module.KnowledgeBase.__new__(kb_module.KnowledgeBase)
    kb._collection = FakeCollection()
    kb._load_default_docs()

    assert kb._collection.count_value > 0
    loaded_text = "\n".join(kb._collection.added_documents)
    assert "醉驾" in loaded_text
    assert "劳动争议" in loaded_text
    assert "不构成正式法律意见" in loaded_text
    for old_title in OLD_CUSTOMER_TITLES:
        assert old_title not in loaded_text
        assert old_title not in "".join(
            meta.get("title", "") for meta in kb._collection.added_metadatas
        )

    assert all(meta.get("source") == "law_firm" for meta in kb._collection.added_metadatas)
    assert all(meta.get("category") for meta in kb._collection.added_metadatas)
    assert any(meta.get("doc_type") == "domain_brief" for meta in kb._collection.added_metadatas)
    assert any(meta.get("category") == "dangerous_driving" for meta in kb._collection.added_metadatas)


def test_knowledge_base_delete_faq_vectors_only_removes_faq_docs():
    kb_module = _load_knowledge_base_module()
    kb = kb_module.KnowledgeBase.__new__(kb_module.KnowledgeBase)
    collection = FakeCollection()
    kb._collection = collection

    assert kb.delete_faq_vectors() == 1
    assert collection.deleted_where == {"doc_type": "faq"}


def test_knowledge_base_default_docs_exclude_seed_faqs():
    kb_module = _load_knowledge_base_module()
    kb = kb_module.KnowledgeBase.__new__(kb_module.KnowledgeBase)
    kb._collection = FakeCollection()
    kb._load_default_docs()

    assert kb._collection.count_value > 0
    metas = kb._collection.added_metadatas
    assert all(meta.get("doc_type") != "faq" for meta in metas)
    assert all("faq_id" not in meta for meta in metas)
    assert all(not str(meta.get("title", "")).startswith("FAQ｜") for meta in metas)


def test_search_handler_returns_law_faq_content_with_disclaimer():
    kb_module = _load_knowledge_base_module()
    kb = kb_module.KnowledgeBase.__new__(kb_module.KnowledgeBase)
    kb._collection = FakeCollection()
    results = asyncio.run(kb.search_handler({"query": "醉驾"}, None))

    assert results
    assert results[0]["content"].startswith("问题：醉驾")
    assert "不构成正式法律意见" in results[0]["content"]
    assert "刑事" in results[0]["title"]
