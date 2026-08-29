"""Task 5 review: law skills injection, seed data, knowledge migration and compliance."""
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

SKILL_INJECTION_CASES = {
    "criminal": [
        "醉驾", "酒驾", "危险驾驶", "交警", "事故", "肇事", "逃逸",
        "拘留", "警察", "刑事", "取保", "开庭", "犯罪", "涉嫌",
    ],
    "civil": [
        "车祸", "责任认定", "保险", "工伤", "家暴", "离婚", "抚养权",
        "劳动", "工资", "仲裁", "合同", "欠款", "借条", "纠纷",
    ],
    "escalation": [
        "找律师", "联系律师", "转人工", "人工", "预约", "咨询律师", "律师推荐",
    ],
    "reception": [
        "咨询", "收费", "流程", "地址", "时间", "预约", "法律服务", "帮助",
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

OLD_BRAND_PATTERN = re.compile("retired" + "brand", re.IGNORECASE)


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
    """Allow the test file to import knowledge_base without installing ChromaDB."""
    if "chromadb" in sys.modules:
        return sys.modules["chromadb"]
    fake = types.ModuleType("chromadb")
    fake.Settings = object
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
    assert 30 <= len(faq_items) <= 50
    for item in faq_items:
        assert item["category"]
        assert item["question"]
        assert item["answer"]
        assert isinstance(item["keywords"], list) and item["keywords"]
        assert item["active"] is True

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
    assert (BRIEFS_DIR / "dangerous_driving.md").exists()
    assert (BRIEFS_DIR / "criminal_defense.md").exists()


def test_skill_files_have_required_front_matter_sections_and_broad_keywords():
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
        for keyword in SKILL_INJECTION_CASES[expected_agent]:
            assert keyword in meta["keywords"], f"{path} missing keyword: {keyword}"


def test_common_first_turn_phrases_inject_matching_skills():
    manager = SkillManager(str(SKILLS_DIR))
    manager.load()
    assert manager.errors == []

    expected_titles = {
        "criminal": "刑事咨询规范",
        "civil": "民事咨询规范",
        "escalation": "人工升级与留资规范",
        "reception": "律所前台接待规范",
    }
    for agent_type, keywords in SKILL_INJECTION_CASES.items():
        for keyword in keywords:
            prompt = manager.prompt_for(f"{keyword}该怎么处理", agent_type)
            assert expected_titles[agent_type] in prompt, (agent_type, keyword)
            assert len(prompt) > 100, (agent_type, keyword)


def test_no_old_brand_or_real_like_pii_in_backend_seeds():
    for path in BACKEND_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".py", ".json", ".md", ".yml", ".yaml", ".txt", ".env", ".example"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not OLD_BRAND_PATTERN.search(text), path

    faq_text = (DATA_DIR / "law_faq_seed.json").read_text(encoding="utf-8")
    lawyer_text = (DATA_DIR / "lawyers_seed.json").read_text(encoding="utf-8")
    combined = faq_text + lawyer_text
    assert not re.search(r"1[3-9]\d{9}", combined)
    assert "身份证号" not in combined


def test_loaders_return_law_documents_with_source_and_disclaimer():
    kb_module = _load_knowledge_base_module()
    faq_docs = kb_module.load_law_faq_documents()
    assert len(faq_docs) == 49
    for doc in faq_docs:
        assert "问题：" in doc["content"]
        assert "回答：" in doc["content"]
        assert "不构成正式法律意见" in doc["content"]
        assert doc["metadata"]["source"] == "law_firm"
        assert doc["metadata"]["category"]
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


class FakeCollection:
    """Minimal ChromaDB-compatible collection fixture for unit tests."""

    def __init__(self):
        self.added_documents = []
        self.added_metadatas = []
        self.added_ids = []
        self.count_value = 0

    def count(self):
        return self.count_value

    def add(self, ids, documents, metadatas):
        self.added_ids.extend(ids)
        self.added_documents.extend(documents)
        self.added_metadatas.extend(metadatas)
        self.count_value += len(ids)

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


def test_search_handler_returns_law_faq_content_with_disclaimer():
    kb_module = _load_knowledge_base_module()
    kb = kb_module.KnowledgeBase.__new__(kb_module.KnowledgeBase)
    kb._collection = FakeCollection()
    results = asyncio.run(kb.search_handler({"query": "醉驾"}, None))

    assert results
    assert results[0]["content"].startswith("问题：醉驾")
    assert "不构成正式法律意见" in results[0]["content"]
    assert "刑事" in results[0]["title"]
