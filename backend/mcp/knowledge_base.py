"""
RAG 知识库 —— 基于 ChromaDB 的真实检索实现。

功能：
  1. 文档导入：将文本切片后存入 ChromaDB（自动生成 Embedding）
  2. 语义检索：根据 query 从知识库中检索最相关的文档片段
  3. 与 MCP 工具框架集成：作为 knowledge_search 工具的真实 handler

ChromaDB 在这里的角色：
  - memory/ 中用于存储对话记忆（情景记忆 + 用户画像）
  - 这里用于存储知识库文档（RAG 检索）
  两者是不同的 collection，互不干扰。
"""
import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import chromadb

logger = logging.getLogger(__name__)

LAW_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LAW_FAQ_SEED_PATH = LAW_DATA_DIR / "law_faq_seed.json"
LAW_DOMAIN_BRIEFS_DIR = LAW_DATA_DIR / "law_domain_briefs"

_LAW_CATEGORY_LABELS = {
    "dangerous_driving": "危险驾驶",
    "criminal": "刑事/醉驾",
    "criminal_defense": "刑事辩护",
    "labor_dispute": "劳动争议",
    "marriage_family": "婚姻家事",
    "contract_dispute": "合同纠纷",
    "traffic_accident": "交通事故",
    "civil_loan": "民间借贷",
    "service": "律所服务",
}

_LAW_GENERAL_DISCLAIMER = (
    "温馨提示：以上内容仅为一般法律知识，不构成正式法律意见；"
    "具体案件请结合实际材料咨询执业律师，紧急或复杂事项应尽快转人工。"
)


def _law_keywords(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def load_law_faq_documents(
    data_dir: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """从 law_faq_seed.json 加载启用的律所 FAQ，并转换为 RAG 文档。"""
    faq_path = Path(data_dir) / "law_faq_seed.json" if data_dir is not None else LAW_FAQ_SEED_PATH
    if not faq_path.exists():
        raise FileNotFoundError(f"律所 FAQ 种子文件不存在: {faq_path}")

    raw = json.loads(faq_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("law_faq_seed.json 必须是一个数组")

    documents: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or item.get("active") is False:
            continue
        category = str(item.get("category") or "").strip()
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        keywords = _law_keywords(item.get("keywords"))
        if not question or not answer:
            continue

        category_label = _LAW_CATEGORY_LABELS.get(category, category)
        content = (
            f"领域：{category_label}（{category}）\n"
            f"问题：{question}\n"
            f"回答：{answer}\n"
            f"关键词：{'、'.join(keywords)}\n"
            f"{_LAW_GENERAL_DISCLAIMER}"
        )
        documents.append({
            "title": f"FAQ｜{category_label}｜{question}",
            "content": content,
            "metadata": {
                "source": "law_firm",
                "doc_type": "faq",
                "category": category,
                "active": True,
                "keywords": keywords,
            },
        })
    return documents


def load_law_domain_briefs(
    data_dir: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """加载 law_domain_briefs/*.md 中的律所领域知识摘要。"""
    root = LAW_DOMAIN_BRIEFS_DIR if data_dir is None else Path(data_dir)
    if not root.exists():
        raise FileNotFoundError(f"律所领域摘要目录不存在: {root}")

    documents: List[Dict[str, Any]] = []
    for path in sorted(root.glob("*.md")):
        raw_content = path.read_text(encoding="utf-8").strip()
        content = f"{raw_content}\n\n{_LAW_GENERAL_DISCLAIMER}"
        if not content:
            continue
        title = path.stem
        for line in content.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        documents.append({
            "title": title,
            "content": content,
            "metadata": {
                "source": "law_firm",
                "doc_type": "domain_brief",
                "domain": path.stem,
                "category": path.stem,
                "active": True,
            },
        })
    return documents



class KnowledgeBase:
    """
    基于 ChromaDB 的 RAG 知识库。

    ChromaDB 内置了 Embedding 模型（all-MiniLM-L6-v2），
    调用 add() 时自动生成向量，query() 时自动做语义匹配。
    不需要额外调用 Anthropic Embeddings API。
    """

    COLLECTION_NAME = "law_knowledge_base"

    def __init__(
        self,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        chroma_path: str = "./data/chroma",
    ):
        # 优先连接独立 ChromaDB 服务（服务端内置 embedding 模型，客户端无需下载）
        self._use_server = False
        try:
            # HttpClient 默认也会初始化 ChromaDB telemetry；显式关闭避免 posthog 兼容性错误日志。
            self._client = chromadb.HttpClient(
                host=chroma_host,
                port=chroma_port,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
            self._client.heartbeat()
            self._use_server = True
            logger.info(f"知识库 ChromaDB 已连接: {chroma_host}:{chroma_port}")
        except Exception:
            logger.info(f"知识库 ChromaDB 服务不可用，使用本地模式: {chroma_path}")
            self._client = chromadb.PersistentClient(
                path=chroma_path,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )

        # 清理旧客服知识库集合，避免遗留内容参与律所检索。
        try:
            self._client.delete_collection("knowledge_base")
        except Exception:
            logger.info("旧知识库集合不存在或无需清理")

        # 使用服务端时不传 embedding_function，让服务端处理
        # 本地模式时也不传，使用 ChromaDB 默认的（会触发模型下载）
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"description": "LawMind RAG 知识库"},
        )

        # 如果知识库为空，导入默认文档
        if self._collection.count() == 0:
            self._load_default_docs()

    # ── 文档管理 ──────────────────────────────────────────────────────────────

    def add_documents(self, documents: List[Dict[str, str]]) -> int:
        """
        批量导入文档到知识库。

        documents 格式: [{"title": "...", "content": "..."}, ...]
        长文档会自动切片（每片 500 字）。
        """
        ids, docs, metas = [], [], []

        for doc in documents:
            title   = doc.get("title", "")
            content = doc.get("content", "")
            chunks  = self._chunk_text(content, chunk_size=500)

            for i, chunk in enumerate(chunks):
                doc_id = hashlib.md5(f"{title}_{i}_{chunk[:50]}".encode()).hexdigest()
                ids.append(doc_id)
                docs.append(chunk)
                meta = {
                    "title": title,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "source": "law_firm",
                }
                for key, value in (doc.get("metadata") or {}).items():
                    if isinstance(value, (list, dict, tuple)):
                        meta[key] = json.dumps(value, ensure_ascii=False)
                    else:
                        meta[key] = value
                metas.append(meta)

        if ids:
            # ChromaDB 会自动生成 Embedding
            self._collection.add(ids=ids, documents=docs, metadatas=metas)
            logger.info(f"知识库导入 {len(ids)} 个文档片段")

        return len(ids)

    async def add_documents_async(self, documents: List[Dict[str, str]]) -> int:
        """异步导入文档；ChromaDB 客户端为同步实现，因此放入线程池执行。"""
        return await asyncio.to_thread(self.add_documents, documents)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        语义检索：根据 query 返回最相关的文档片段。

        ChromaDB 内部自动将 query 转为向量，与存储的文档向量做余弦相似度匹配。
        """
        results = self._collection.query(
            query_texts=[query],
            n_results=top_k,
        )

        items = []
        if results["documents"] and results["documents"][0]:
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                items.append({
                    "title":    meta.get("title", ""),
                    "content":  doc,
                    "score":    round(1.0 - dist, 4),  # ChromaDB 返回距离，转为相似度
                    "chunk":    meta.get("chunk_index", 0),
                })

        return items

    async def search_async(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """异步检索；ChromaDB 客户端为同步实现，因此放入线程池执行。"""
        return await asyncio.to_thread(self.search, query, top_k)

    @property
    def doc_count(self) -> int:
        return self._collection.count()

    async def doc_count_async(self) -> int:
        """异步获取文档片段数量。"""
        return await asyncio.to_thread(self._collection.count)

    # ── MCP 工具 handler ─────────────────────────────────────────────────────

    async def search_handler(self, params: Dict[str, Any], context: Any) -> List[Dict]:
        """
        作为 MCP 工具的 handler 注册。

        MCPToolManager.register(Tool(
            name="knowledge_search",
            handler=kb.search_handler,
            ...
        ))
        """
        query = params.get("query", "")
        top_k = params.get("top_k", 5)
        return await self.search_async(query, top_k=top_k)

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _chunk_text(self, text: str, chunk_size: int = 500) -> List[str]:
        """将长文本按 chunk_size 切片，保留语义完整性（按句号/换行切分）。"""
        if len(text) <= chunk_size:
            return [text] if text.strip() else []

        chunks = []
        current = ""
        # 按句子切分
        sentences = text.replace("\n", "。").split("。")
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if len(current) + len(sent) + 1 > chunk_size:
                if current:
                    chunks.append(current)
                current = sent
            else:
                current = f"{current}。{sent}" if current else sent

        if current:
            chunks.append(current)

        return chunks

    def _load_default_docs(self) -> None:
        """导入默认法律知识库（律所 FAQ + 领域知识摘要）。"""
        default_docs = load_law_faq_documents()
        domain_briefs = load_law_domain_briefs()
        self.add_documents(default_docs + domain_briefs)
        logger.info(
            "已导入默认律所知识库: FAQ %d 篇，领域摘要 %d 篇",
            len(default_docs),
            len(domain_briefs),
        )
