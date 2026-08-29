"""Tests for FAQ -> ChromaDB synchronization.

The service is tested with lightweight fake repository/knowledge-base adapters so
these tests require neither PostgreSQL nor a running ChromaDB server.
"""
from __future__ import annotations

import types
import sys
from typing import Any, Dict, List, Optional

# Keep the test import offline: the real chromadb package is not required for
# unit tests, and the KnowledgeBase class only touches it during instantiation.
if "chromadb" not in sys.modules:
    _fake_chromadb = types.ModuleType("chromadb")
    _fake_chromadb.Settings = lambda **_kwargs: None
    _fake_chromadb.HttpClient = object
    _fake_chromadb.PersistentClient = object
    sys.modules["chromadb"] = _fake_chromadb

from mcp.knowledge_base import KnowledgeBase
from services.faq_sync_service import FaqSyncService


def make_faq(
    faq_id: str = "faq-1",
    *,
    category: str = "criminal",
    question: str = "醉驾怎么处理？",
    answer: str = "需要结合事故、酒精检测、阶段判断。",
    keywords: Optional[List[str]] = None,
    active: bool = True,
    version: int = 1,
) -> Dict[str, Any]:
    return {
        "id": faq_id,
        "category": category,
        "question": question,
        "answer": answer,
        "keywords": keywords or ["醉驾"],
        "source": "law_firm",
        "active": active,
        "version": version,
    }


class FakeKnowledgeBase:
    """Stores vectors in memory and records synchronization calls."""

    def __init__(self, *, fail_add: bool = False, fail_delete: bool = False):
        self.documents: Dict[str, Dict[str, Any]] = {}
        self.delete_calls: List[Dict[str, Any]] = []
        self.add_calls: List[Dict[str, Any]] = []
        self.fail_add = fail_add
        self.fail_delete = fail_delete

    def delete_by_metadata(self, where: Dict[str, Any]) -> int:
        self.delete_calls.append(dict(where))
        if self.fail_delete:
            raise RuntimeError("chroma delete unavailable")
        faq_id = str(where.get("faq_id", ""))
        before = len(self.documents)
        for doc_id in list(self.documents):
            if self.documents[doc_id]["metadata"].get("faq_id") == faq_id:
                del self.documents[doc_id]
        return before - len(self.documents)

    def add_documents(
        self,
        documents: List[Dict[str, Any]],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None,
    ) -> int:
        call = {
            "documents": documents,
            "metadatas": [dict(item) for item in (metadatas or [])],
            "ids": list(ids or []),
        }
        self.add_calls.append(call)
        if self.fail_add:
            raise RuntimeError(
                "chroma add failed: 张三 13800138000 secret@example.com"
            )
        for index, doc in enumerate(documents):
            doc_id = doc.get("id") or (
                (ids[index] if ids and index < len(ids) else f"auto-{index}")
            )
            metadata = dict(doc.get("metadata") or {})
            if metadatas and index < len(metadatas):
                metadata.update(metadatas[index])
            self.documents[str(doc_id)] = {
                "content": doc.get("content", ""),
                "metadata": metadata,
            }
        return len(documents)


class FakeFaqRepository:
    def __init__(self, records: Optional[List[Dict[str, Any]]] = None):
        self.records = list(records or [])
        self.list_all_calls: List[bool] = []
        self.marked_synced: List[tuple[str, int]] = []
        self.marked_failed: List[tuple[str, str]] = []

    def list_all(self, active_only: bool = False) -> List[Dict[str, Any]]:
        self.list_all_calls.append(active_only)
        if active_only:
            return [item for item in self.records if item["active"]]
        return list(self.records)

    def mark_synced(self, faq_id: str, version: int) -> Dict[str, Any]:
        self.marked_synced.append((faq_id, version))
        return make_faq(faq_id, version=version)

    def mark_sync_failed(self, faq_id: str, error: str) -> Dict[str, Any]:
        self.marked_failed.append((faq_id, error))
        return make_faq(faq_id)


class FakeCollection:
    """Minimal Chroma collection for KnowledgeBase-level tests."""

    def __init__(self):
        self.ids: List[str] = []
        self.documents: List[str] = []
        self.metadatas: List[Dict[str, Any]] = []
        self.deleted: Optional[Dict[str, Any]] = None

    def add(self, ids, documents, metadatas):
        self.ids.extend(ids)
        self.documents.extend(documents)
        self.metadatas.extend(metadatas)

    def delete(self, where=None, ids=None):
        self.deleted = where
        return 1

    def query(self, query_texts, n_results):
        return {
            "documents": [["醉驾被查后一般会经过哪些阶段？"]],
            "metadatas": [[{
                "title": "FAQ｜刑事/醉驾｜醉驾问题",
                "chunk_index": 0,
                "faq_id": "faq-1",
                "category": "criminal",
                "active": True,
                "version": 3,
                "source": "law_firm",
            }]],
            "distances": [[0.1]],
        }


def test_create_faq_sync_active_adds_document_with_metadata():
    repo = FakeFaqRepository()
    kb = FakeKnowledgeBase()
    faq = make_faq()
    service = FaqSyncService(repo, kb)

    result = service.sync(faq)

    assert result == {
        "success": True,
        "faq_id": "faq-1",
        "version": 1,
        "sync_status": "synced",
        "document_id": "faq:faq-1",
        "error": None,
        "added": True,
    }
    assert kb.delete_calls == [{"faq_id": "faq-1"}]
    assert list(kb.documents) == ["faq:faq-1"]
    stored = kb.documents["faq:faq-1"]
    assert "醉驾怎么处理？" in stored["content"]
    assert "需要结合事故、酒精检测、阶段判断。" in stored["content"]
    assert "醉驾" in stored["content"]
    assert stored["metadata"] == {
        "faq_id": "faq-1",
        "category": "criminal",
        "question": "醉驾怎么处理？",
        "keywords": ["醉驾"],
        "active": True,
        "source": "law_firm",
        "version": 1,
    }
    assert repo.marked_synced == [("faq-1", 1)]
    assert repo.marked_failed == []


def test_update_removes_old_document_and_adds_new_version():
    repo = FakeFaqRepository()
    kb = FakeKnowledgeBase()
    kb.documents["faq:faq-1"] = {
        "content": "旧问题\n旧回答",
        "metadata": {"faq_id": "faq-1", "version": 1},
    }
    updated = make_faq(
        question="新版问题？",
        answer="新版回答。",
        keywords=["更新"],
        version=2,
    )
    service = FaqSyncService(repo, kb)

    result = service.sync(updated)

    assert result["success"] is True
    assert result["version"] == 2
    assert "faq:faq-1" in kb.documents
    assert kb.documents["faq:faq-1"]["content"] == "新版问题？\n新版回答。\n更新"
    assert kb.documents["faq:faq-1"]["metadata"]["question"] == "新版问题？"
    assert kb.documents["faq:faq-1"]["metadata"]["version"] == 2
    assert repo.marked_synced == [("faq-1", 2)]


def test_disable_removes_document_without_adding_new_one():
    repo = FakeFaqRepository()
    kb = FakeKnowledgeBase()
    kb.documents["faq:faq-1"] = {
        "content": "旧内容",
        "metadata": {"faq_id": "faq-1", "version": 1},
    }
    disabled = make_faq(active=False, version=2)
    service = FaqSyncService(repo, kb)

    result = service.sync(disabled)

    assert result["success"] is True
    assert result["added"] is False
    assert result["sync_status"] == "synced"
    assert kb.delete_calls == [{"faq_id": "faq-1"}]
    assert kb.documents == {}
    assert kb.add_calls == []
    assert repo.marked_synced == [("faq-1", 2)]


def test_sync_failure_marks_failed_with_sanitized_error():
    repo = FakeFaqRepository()
    kb = FakeKnowledgeBase(fail_add=True)
    faq = make_faq(
        question="包含敏感内容的问题",
        answer="敏感答案 13800138000",
        keywords=["私密关键词"],
    )
    service = FaqSyncService(repo, kb)

    result = service.sync(faq)

    assert result["success"] is False
    assert result["sync_status"] == "failed"
    assert result["added"] is False
    assert repo.marked_synced == []
    assert len(repo.marked_failed) == 1
    failed_id, failed_error = repo.marked_failed[0]
    assert failed_id == "faq-1"
    assert "zhang" not in failed_error.lower()
    assert "张三" not in failed_error
    assert "13800138000" not in failed_error
    assert "secret@example.com" not in failed_error
    assert "包含敏感内容的问题" not in failed_error
    assert "敏感答案" not in failed_error
    assert "私密关键词" not in failed_error


def test_sync_all_processes_active_and_inactive_records():
    active = make_faq("faq-active", question="启用问题", answer="启用答案")
    inactive = make_faq(
        "faq-inactive",
        question="停用问题",
        answer="停用答案",
        active=False,
        version=3,
    )
    repo = FakeFaqRepository([active, inactive])
    kb = FakeKnowledgeBase()
    service = FaqSyncService(repo, kb)

    results = service.sync_all()

    assert repo.list_all_calls == [False]
    assert [item["faq_id"] for item in results] == ["faq-active", "faq-inactive"]
    assert all(item["success"] for item in results)
    assert list(kb.documents) == ["faq:faq-active"]
    assert repo.marked_synced == [("faq-active", 1), ("faq-inactive", 3)]


def test_knowledge_base_add_documents_supports_stable_id_and_metadata():
    kb_instance = KnowledgeBase.__new__(KnowledgeBase)
    collection = FakeCollection()
    kb_instance._collection = collection
    metadata = {
        "faq_id": "faq-1",
        "category": "criminal",
        "active": True,
        "source": "law_firm",
        "version": 1,
    }

    count = kb_instance.add_documents(
        [{
            "id": "faq:faq-1",
            "title": "醉驾怎么处理？",
            "content": "醉驾怎么处理？\n需要结合阶段判断。",
            "metadata": metadata,
        }],
        metadatas=[metadata],
    )

    assert count == 1
    assert collection.ids == ["faq:faq-1"]
    assert collection.metadatas[0]["faq_id"] == "faq-1"
    assert collection.metadatas[0]["category"] == "criminal"
    assert collection.metadatas[0]["version"] == 1
    assert collection.metadatas[0]["source"] == "law_firm"


def test_knowledge_base_delete_by_metadata_and_search_metadata():
    kb_instance = KnowledgeBase.__new__(KnowledgeBase)
    collection = FakeCollection()
    kb_instance._collection = collection

    assert kb_instance.delete_by_metadata({"faq_id": "faq-1"}) == 1
    assert collection.deleted == {"faq_id": "faq-1"}

    results = kb_instance.search("醉驾")
    assert results[0]["faq_id"] == "faq-1"
    assert results[0]["category"] == "criminal"
    assert results[0]["metadata"]["faq_id"] == "faq-1"
    assert results[0]["metadata"]["category"] == "criminal"
    assert results[0]["metadata"]["version"] == 3
