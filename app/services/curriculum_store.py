"""RAG 層：
- CurriculumStore：EDB 課程重點（持久化 Chroma + 結構化 JSON 兩份）。每個 embedding 模型各有一個索引，
  第一次使用某模型時自動建立。
- MaterialIndex：每次審查臨時建立的教材索引（用於「反向檢索」偵測遺漏）
向量由 app.services.embeddings 計算後直接交給 Chroma，查詢與文件可用不同編碼。
"""
import hashlib
import re
import threading
import uuid
from pathlib import Path

import chromadb

from app.config import Settings
from app.schemas import Chunk, Objective, Unit


def _objective_document(o: Objective) -> str:
    terms = f"｜關鍵詞：{'、'.join(o.key_terms)}" if o.key_terms else ""
    prefix = f"{o.subtopic}｜" if o.subtopic else ""
    return f"{prefix}{o.text}{terms}"


def _collection_name(embedder) -> str:
    # Chroma 名稱只可用英數、. _ -；加上模型 ID 的雜湊，換了 API 模型版本會另建索引
    spec = embedder.spec
    digest = hashlib.sha1(f"{spec.provider}:{spec.model_id}".encode()).hexdigest()[:8]
    return f"edb_curriculum_{re.sub(r'[^A-Za-z0-9-]+', '-', spec.key)[:32]}_{digest}"


class CurriculumStore:
    def __init__(self, settings: Settings):
        self.units: dict[str, Unit] = {}
        self._paths: dict[str, Path] = {}
        for path in sorted(Path(settings.curriculum_dir).glob("*.json")):
            unit = Unit.model_validate_json(path.read_text("utf-8"))
            self.units[unit.unit_id] = unit
            self._paths[unit.unit_id] = path
        self.objectives = {o.id: o for u in self.units.values() for o in u.objectives}
        self.unit_of = {o.id: u.unit_id for u in self.units.values() for o in u.objectives}

        self._client = chromadb.PersistentClient(path=str(settings.chroma_path))
        self._lock = threading.Lock()

    def _collection(self, embedder):
        return self._client.get_or_create_collection(
            _collection_name(embedder), embedding_function=None, metadata={"hnsw:space": "cosine"})

    def ensure_indexed(self, embedder) -> int:
        with self._lock:
            col = self._collection(embedder)
            if col.count() != len(self.objectives):
                col = self._rebuild(embedder)
            return col.count()

    def rebuild(self, embedder) -> None:
        with self._lock:
            self._rebuild(embedder)

    def _rebuild(self, embedder):
        try:
            self._client.delete_collection(_collection_name(embedder))
        except Exception:  # noqa: BLE001  未曾建立
            pass
        col = self._collection(embedder)
        if self.objectives:
            ids = list(self.objectives)
            docs = [_objective_document(self.objectives[i]) for i in ids]
            col.add(ids=ids, documents=docs, embeddings=embedder.embed_documents(docs),
                    metadatas=[{"unit_id": self.unit_of[i]} for i in ids])
        return col

    def official_texts(self) -> list[str]:
        """供用語檢查使用：官方文件中出現過的詞一律視為官方用語。"""
        return [f"{o.subtopic} {o.text}" for o in self.objectives.values()] + \
               [u.title for u in self.units.values()]

    def unit_path(self, unit_id: str) -> Path:
        return self._paths[unit_id]

    def get_unit(self, unit_id: str) -> Unit:
        return self.units[unit_id]  # KeyError 由 API 層轉為 404

    def search(self, query_vectors: list[list[float]], k: int, embedder,
               unit_id: str | None = None) -> list[list[Objective]]:
        if not query_vectors:
            return []
        where = {"unit_id": unit_id} if unit_id else None
        res = self._collection(embedder).query(query_embeddings=query_vectors, n_results=k, where=where)
        return [[self.objectives[i] for i in ids if i in self.objectives] for ids in res["ids"]]


class MaterialIndex:
    """教材的臨時向量索引。用完必須 close()。"""

    def __init__(self, embedder, chunks: list[Chunk]):
        self.chunks = {c.chunk_id: c for c in chunks}
        self._client = chromadb.EphemeralClient()
        self._name = f"mat_{uuid.uuid4().hex[:12]}"
        self._col = self._client.create_collection(
            self._name, embedding_function=None, metadata={"hnsw:space": "cosine"})
        texts = [c.text for c in chunks]
        self._col.add(ids=[c.chunk_id for c in chunks], documents=texts,
                      embeddings=embedder.embed_documents(texts))

    def search(self, query_vectors: list[list[float]], k: int) -> list[list[Chunk]]:
        if not query_vectors:
            return []
        res = self._col.query(query_embeddings=query_vectors, n_results=min(k, len(self.chunks)))
        return [[self.chunks[i] for i in ids] for ids in res["ids"]]

    def keyword_hits(self, terms: list[str], limit: int = 2) -> list[Chunk]:
        """混合檢索：關鍵詞字面命中。小型 embedding 模型對中文專有名詞召回不穩，用此補強。"""
        hits = [c for c in self.chunks.values() if any(t in c.text for t in terms)]
        return hits[:limit]

    def close(self) -> None:
        try:
            self._client.delete_collection(self._name)
        except Exception:  # noqa: BLE001
            pass
