"""Task 5: law skills, seed data and law knowledge-base loading tests."""
import asyncio
import importlib
import json
import sys
import types
from pathlib import Path

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
    "免责声明",
    "敏感信息保护",
)


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
    """Allow the test file to import knowledge_base without installing ChromaDB.

    KnowledgeBase is exercised through methods only, so the module only needs to
    exist for the `import chromadb` statement.
    """
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

    brief_paths = {path.name for path in BRIEFS_DIR.glob("*.md")}
    assert len(brief_paths) == 7
    assert brief_paths == EXPECTED_BRIEFS


def test_skill_files_have_required_front_matter_and_content():
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


def test_loaders_return_law_documents_and_domain_briefs():
    kb_module = _load_knowledge_base_module()
    faq_docs = kb_module.load_law_faq_documents()
    assert len(faq_docs) == 49
    assert all("问题：" in doc["content"] and "回答：" in doc["content"] for doc in faq_docs)
    combined = "".join(doc["content"] for doc in faq_docs)
    assert "醉驾" in combined
    assert "退款政策" not in combined
    assert "订单查询" not in combined

    brief_docs = kb_module.load_law_domain_briefs()
    assert len(brief_docs) == 7
    assert any("刑事" in doc["title"] for doc in brief_docs)
    assert any("劳动争议" in doc["title"] for doc in brief_docs)
    assert any("律所接待" in doc["title"] for doc in brief_docs)


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
        return {
            "documents": [["问题：醉驾被查后一般会经过哪些阶段？\n回答：通常先由公安机关调查。"]],
            "metadatas": [[{"title": "FAQ｜刑事/醉驾｜醉驾被查后一般会经过哪些阶段？", "chunk_index": 0}]],
            "distances": [[0.1]],
        }


def test_knowledge_base_loads_law_docs_instead_of_old_customer_docs():
    kb_module = _load_knowledge_base_module()
    kb = kb_module.KnowledgeBase.__new__(kb_module.KnowledgeBase)
    kb._collection = FakeCollection()
    kb._load_default_docs()

    assert kb._collection.count_value > 0
    loaded_text = "\n".join(kb._collection.added_documents)
    assert "醉驾" in loaded_text
    assert "劳动争议" in loaded_text
    assert "退款政策" not in loaded_text
    assert "订单查询" not in loaded_text
    assert any(meta.get("category") == "criminal" for meta in kb._collection.added_metadatas)
    assert any(meta.get("doc_type") == "domain_brief" for meta in kb._collection.added_metadatas)


def test_search_handler_returns_law_faq_content():
    kb_module = _load_knowledge_base_module()
    kb = kb_module.KnowledgeBase.__new__(kb_module.KnowledgeBase)
    kb._collection = FakeCollection()
    results = asyncio.run(kb.search_handler({"query": "醉驾"}, None))

    assert results
    assert results[0]["content"].startswith("问题：醉驾")
    assert "醉驾" in results[0]["content"]
    assert "刑事" in results[0]["title"]
