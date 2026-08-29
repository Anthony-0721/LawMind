"""Tests for FAQ -> ChromaDB synchronization.

The service is tested with lightweight fake repository/knowledge-base adapters so
these tests require neither PostgreSQL nor a running ChromaDB server.
"""
from __future__ import annotations

import sys
import types
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
from services.faq_sync_service import FaqSyncService, _sanitize_error


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
        "sync_status": "pending",
        "sync_error": None,
        "last_sync_at": None,
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
            raise RuntimeError(
                "失败：张三 13800138000 021-12345678 138****1234"
            )
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
                "失败：张三 +86 138 1234 5678 138-0013-8000 "
                "021-12345678 138****1234 13800138000 secret@example.com"
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
    def __init__(
        self,
        records: Optional[List[Dict[str, Any]]] = None,
        *,
        fail_mark_synced: bool = False,
    ):
        self.records = list(records or [])
        self.list_all_calls: List[bool] = []
        self.marked_synced: List[tuple[str, int]] = []
        self.marked_failed: List[tuple[str, str]] = []
        self.fail_mark_synced = fail_mark_synced

    def _find(self, faq_id: str) -> Optional[Dict[str, Any]]:
        for item in self.records:
            if item is not None and item.get("id") == faq_id:
                return item
        return None

    def list_all(self, active_only: bool = False) -> List[Dict[str, Any]]:
        self.list_all_calls.append(active_only)
        if active_only:
            return [item for item in self.records if item["active"]]
        return list(self.records)

    def mark_synced(self, faq_id: str, version: int) -> Optional[Dict[str, Any]]:
        self.marked_synced.append((faq_id, version))
        if self.fail_mark_synced:
            return None
        record = self._find(faq_id)
        if record is None:
            return None
        if int(record.get("version") or 1) != int(version):
            return None
        updated = dict(record)
        updated.update({
            "sync_status": "synced",
            "sync_error": None,
            "version": int(version),
            "last_sync_at": "2026-08-29T00:00:00+00:00",
        })
        return updated

    def mark_sync_failed(self, faq_id: str, error: str) -> Optional[Dict[str, Any]]:
        self.marked_failed.append((faq_id, error))
        record = self._find(faq_id)
        if record is None:
            return None
        updated = dict(record)
        updated.update({
            "sync_status": "failed",
            "sync_error": error,
            "last_sync_at": "2026-08-29T00:00:01+00:00",
        })
        return updated


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
    faq = make_faq()
    repo = FakeFaqRepository([faq])
    kb = FakeKnowledgeBase()
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
    updated = make_faq(
        question="新版问题？",
        answer="新版回答。",
        keywords=["更新"],
        version=2,
    )
    repo = FakeFaqRepository([updated])
    kb = FakeKnowledgeBase()
    kb.documents["faq:faq-1"] = {
        "content": "旧问题\n旧回答",
        "metadata": {"faq_id": "faq-1", "version": 1},
    }
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
    disabled = make_faq(active=False, version=2)
    repo = FakeFaqRepository([disabled])
    kb = FakeKnowledgeBase()
    kb.documents["faq:faq-1"] = {
        "content": "旧内容",
        "metadata": {"faq_id": "faq-1", "version": 1},
    }
    service = FaqSyncService(repo, kb)

    result = service.sync(disabled)

    assert result["success"] is False
    assert result["added"] is False
    assert result["sync_status"] == "failed"
    assert result["error"] == "faq_inactive"
    assert kb.delete_calls == [{"faq_id": "faq-1"}]
    assert kb.documents == {}
    assert kb.add_calls == []
    assert repo.marked_synced == []
    assert repo.marked_failed == [("faq-1", "faq_inactive")]


def test_sync_failure_marks_failed_with_sanitized_error_and_updates_input():
    faq = make_faq(
        question="包含敏感内容的问题",
        answer="敏感答案 13800138000",
        keywords=["私密关键词"],
    )
    repo = FakeFaqRepository([faq])
    kb = FakeKnowledgeBase(fail_add=True)
    service = FaqSyncService(repo, kb)

    result = service.sync(faq)

    assert result["success"] is False
    assert result["sync_status"] == "failed"
    assert result["added"] is False
    assert faq["sync_status"] == "failed"
    assert faq["sync_error"] == result["error"]
    assert faq["version"] == 1
    assert faq["last_sync_at"] is not None
    assert repo.marked_synced == []
    assert len(repo.marked_failed) == 1
    failed_id, failed_error = repo.marked_failed[0]
    assert failed_id == "faq-1"
    assert "张三" not in failed_error
    assert "+86" not in failed_error
    assert "138-0013-8000" not in failed_error
    assert "021-12345678" not in failed_error
    assert "138****1234" not in failed_error
    assert "13800138000" not in failed_error
    assert "secret@example.com" not in failed_error
    assert "包含敏感内容的问题" not in failed_error
    assert "敏感答案" not in failed_error
    assert "私密关键词" not in failed_error


def test_pii_sanitizer_handles_formatted_phone_and_indicator_names():
    error = RuntimeError(
        "客户：李四 联系人: 王五 失败：张三 错误：赵六 "
        "+86 138 1234 5678 138-0013-8000 021-12345678 138****1234 "
        "13800138000 test@example.com"
    )
    sanitized = _sanitize_error(error, {})

    assert "李四" not in sanitized
    assert "王五" not in sanitized
    assert "张三" not in sanitized
    assert "赵六" not in sanitized
    assert "+86" not in sanitized
    assert "138-0013-8000" not in sanitized
    assert "021-12345678" not in sanitized
    assert "138****1234" not in sanitized
    assert "13800138000" not in sanitized
    assert "test@example.com" not in sanitized


def test_delete_removes_vector_and_returns_delete_contract():
    repo = FakeFaqRepository()
    kb = FakeKnowledgeBase()
    kb.documents["faq:faq-1"] = {
        "content": "旧内容",
        "metadata": {"faq_id": "faq-1"},
    }
    service = FaqSyncService(repo, kb)

    result = service.delete("faq-1")

    assert result == {"success": True, "faq_id": "faq-1", "action": "delete"}
    assert kb.delete_calls == [{"faq_id": "faq-1"}]
    assert kb.documents == {}


def test_delete_failure_returns_sanitized_fixed_error():
    repo = FakeFaqRepository()
    kb = FakeKnowledgeBase(fail_delete=True)
    service = FaqSyncService(repo, kb)

    result = service.delete("faq-1")

    assert result == {"success": False, "error": "faq_sync_delete_failed"}


def test_sync_all_continues_after_malformed_record():
    malformed = {
        "id": "bad",
        "category": "criminal",
        "answer": "只有答案",
        "keywords": [],
        "active": True,
        "version": 1,
    }
    valid = make_faq("faq-valid", question="有效问题", answer="有效答案")
    repo = FakeFaqRepository([None, malformed, valid])
    kb = FakeKnowledgeBase()
    kb.documents["faq:bad"] = {
        "content": "旧内容",
        "metadata": {"faq_id": "bad"},
    }
    service = FaqSyncService(repo, kb)

    results = service.sync_all()

    assert len(results) == 3
    assert results[0]["success"] is False
    assert results[1]["success"] is False
    assert results[2]["success"] is True
    assert "faq:bad" not in kb.documents
    assert "faq:faq-valid" in kb.documents
    assert repo.marked_failed[0][0] == "bad"
    assert repo.marked_synced == [("faq-valid", 1)]


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
    assert results[0]["success"] is True
    assert results[1]["success"] is False
    assert list(kb.documents) == ["faq:faq-active"]
    assert repo.marked_synced == [("faq-active", 1)]
    assert repo.marked_failed == [("faq-inactive", "faq_inactive")]


def test_stale_version_mark_synced_returns_failure():
    faq = make_faq(version=1)
    repo = FakeFaqRepository([make_faq(version=2)])
    kb = FakeKnowledgeBase()
    service = FaqSyncService(repo, kb)

    result = service.sync(faq)

    assert result["success"] is False
    assert result["error"] == "faq_sync_mark_synced_failed"
    assert kb.documents == {}
    assert repo.marked_synced == [("faq-1", 1)]
    assert repo.marked_failed[-1][0] == "faq-1"


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
