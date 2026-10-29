"""RAG 層：
- CurriculumStore：EDB 課程重點（持久化 Chroma + 結構化 JSON 兩份）
- MaterialIndex：每次審查臨時建立的教材索引（用於「反向檢索」偵測遺漏）
"""
import json
import uuid
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from app.config import Settings
from app.schemas import Chunk, Objective, Unit


def build_embedding_fn(model_name: str):
    # Chroma 預設的 all-MiniLM 對中文效果很差，必須換中文/多語模型
    return SentenceTransformerEmbeddingFunction(model_name=model_name, normalize_embeddings=True)


def _objective_document(o: Objective) -> str:
    terms = f"｜關鍵詞：{'、'.join(o.key_terms)}" if o.key_terms else ""
    prefix = f"{o.subtopic}｜" if o.subtopic else ""
    return f"{prefix}{o.text}{terms}"


class CurriculumStore:
    COLLECTION = "edb_curriculum"

    def __init__(self, settings: Settings, embed_fn):
        self.units: dict[str, Unit] = {}
        self._paths: dict[str, Path] = {}
        for path in sorted(Path(settings.curriculum_dir).glob("*.json")):
            unit = Unit.model_validate_json(path.read_text("utf-8"))
            self.units[unit.unit_id] = unit
            self._paths[unit.unit_id] = path
        self.objectives = {o.id: o for u in self.units.values() for o in u.objectives}
        self.unit_of = {o.id: u.unit_id for u in self.units.values() for o in u.objectives}

        self._embed_fn = embed_fn
        self._client = chromadb.PersistentClient(path=str(settings.chroma_path))
        self._col = self._get_collection()

    def _get_collection(self):
        return self._client.get_or_create_collection(
            self.COLLECTION, embedding_function=self._embed_fn, metadata={"hnsw:space": "cosine"})

    def ensure_indexed(self) -> int:
        if self._col.count() != len(self.objectives):
            self.rebuild()
        return self._col.count()

    def rebuild(self) -> None:
        self._client.delete_collection(self.COLLECTION)
        self._col = self._get_collection()
        if not self.objectives:
            return
        ids = list(self.objectives)
        self._col.add(
            ids=ids,
            documents=[_objective_document(self.objectives[i]) for i in ids],
            metadatas=[{"unit_id": self.unit_of[i]} for i in ids],
        )

    def official_texts(self) -> list[str]:
        """供用語檢查使用：官方文件中出現過的詞一律視為官方用語。"""
        return [f"{o.subtopic} {o.text}" for o in self.objectives.values()] + \
               [u.title for u in self.units.values()]

    def unit_path(self, unit_id: str) -> Path:
        return self._paths[unit_id]

    def get_unit(self, unit_id: str) -> Unit:
        return self.units[unit_id]  # KeyError 由 API 層轉為 404

    def search(self, query: str, k: int, unit_id: str | None = None) -> list[Objective]:
        where = {"unit_id": unit_id} if unit_id else None
        res = self._col.query(query_texts=[query], n_results=k, where=where)
        return [self.objectives[i] for i in res["ids"][0] if i in self.objectives]


class MaterialIndex:
    """教材的臨時向量索引。用完必須 close()。"""

    def __init__(self, embed_fn, chunks: list[Chunk]):
        self.chunks = {c.chunk_id: c for c in chunks}
        self._client = chromadb.EphemeralClient()
        self._name = f"mat_{uuid.uuid4().hex[:12]}"
        self._col = self._client.create_collection(
            self._name, embedding_function=embed_fn, metadata={"hnsw:space": "cosine"})
        self._col.add(ids=[c.chunk_id for c in chunks], documents=[c.text for c in chunks])

    def search(self, query: str, k: int) -> list[Chunk]:
        k = min(k, len(self.chunks))
        res = self._col.query(query_texts=[query], n_results=k)
        return [self.chunks[i] for i in res["ids"][0]]

    def keyword_hits(self, terms: list[str], limit: int = 2) -> list[Chunk]:
        """混合檢索：關鍵詞字面命中。小型 embedding 模型對中文專有名詞召回不穩，用此補強。"""
        hits = [c for c in self.chunks.values() if any(t in c.text for t in terms)]
        return hits[:limit]

    def close(self) -> None:
        try:
            self._client.delete_collection(self._name)
        except Exception:  # noqa: BLE001
            pass
