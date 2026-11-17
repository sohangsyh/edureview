"""檢索用 embedding 模型：登記、可用性檢查、載入。

每個模型分開「文件」與「查詢」兩種編碼：jina-v3、gte-Qwen2 及 Cohere 對查詢要加指示或用另一模式，
混用會令檢索質素明顯下降。向量一律正規化，配合 Chroma 的 cosine 距離。
不同模型的向量互不相容，所以課程索引按模型分開存放（見 CurriculumStore）。
"""
import importlib.util
import re
import threading
from dataclasses import dataclass, field

import httpx
import numpy as np

from app.config import Settings


class EmbeddingUnavailable(Exception):
    """所選模型未能使用（未知模型、未安裝套件或未設定 API key）。"""


@dataclass
class EmbeddingSpec:
    key: str
    label: str
    provider: str                       # local / cohere / openai
    model_id: str
    note_zh: str = ""
    note_en: str = ""
    trust_remote_code: bool = False
    doc_kwargs: dict = field(default_factory=dict)
    query_kwargs: dict = field(default_factory=dict)


API_KEY_ENV = {"cohere": "COHERE_API_KEY", "openai": "OPENAI_API_KEY"}


def model_specs(s: Settings) -> dict[str, EmbeddingSpec]:
    specs = [
        EmbeddingSpec("bge-m3", "BAAI/bge-m3", "local", "BAAI/bge-m3",
                      "多語模型，中英教材互相對照穩定。本機執行，約 2.3 GB。",
                      "Multilingual; reliable for matching Chinese and English. Runs locally, about 2.3 GB."),
        EmbeddingSpec("jina-v3", "jina-embeddings-v3", "local", "jinaai/jina-embeddings-v3",
                      "多語模型，查詢與文件分開編碼。本機執行，約 2.3 GB。",
                      "Multilingual, with separate query and document encoding. Runs locally, about 2.3 GB.",
                      trust_remote_code=True,
                      doc_kwargs={"task": "retrieval.passage", "prompt_name": "retrieval.passage"},
                      query_kwargs={"task": "retrieval.query", "prompt_name": "retrieval.query"}),
        EmbeddingSpec("gte-qwen2-7b", "gte-Qwen2-7B-instruct", "local", "Alibaba-NLP/gte-Qwen2-7B-instruct",
                      "檢索質素高，但模型很大（約 15–30 GB），實際上需要 GPU。",
                      "High retrieval quality, but very large (about 15–30 GB); needs a GPU in practice.",
                      trust_remote_code=True, query_kwargs={"prompt_name": "query"}),
        EmbeddingSpec("cohere-v3", "Cohere Embed v3", "cohere", s.cohere_embedding_model,
                      "經 Cohere API 執行，教材文字會傳送到 Cohere。",
                      "Runs through the Cohere API; material text is sent to Cohere."),
        EmbeddingSpec("openai-3", "OpenAI text-embedding-3", "openai", s.openai_embedding_model,
                      "經 OpenAI API 執行，教材文字會傳送到 OpenAI。",
                      "Runs through the OpenAI API; material text is sent to OpenAI."),
    ]
    return {sp.key: sp for sp in specs}


def _normalize(vectors) -> list[list[float]]:
    arr = np.asarray(vectors, dtype=np.float32)
    if arr.size == 0:
        return []
    arr /= np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12
    return arr.tolist()


class LocalEmbedder:
    def __init__(self, spec: EmbeddingSpec):
        self.spec = spec
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.spec.model_id, trust_remote_code=self.spec.trust_remote_code)
        return self._model

    def _encode(self, texts: list[str], kwargs: dict) -> list[list[float]]:
        if not texts:
            return []
        return _normalize(self._load().encode(list(texts), batch_size=16, normalize_embeddings=True, **kwargs))

    def embed_documents(self, texts):
        return self._encode(texts, self.spec.doc_kwargs)

    def embed_queries(self, texts):
        return self._encode(texts, self.spec.query_kwargs)


class _ApiEmbedder:
    batch = 96

    def __init__(self, spec: EmbeddingSpec, api_key: str):
        self.spec = spec
        self._key = api_key

    def _post(self, client: httpx.Client, url: str, body: dict) -> dict:
        res = client.post(url, json=body, headers={"Authorization": f"Bearer {self._key}"})
        if res.status_code >= 400:
            raise RuntimeError(f"{self.spec.label} API 錯誤 {res.status_code}：{res.text[:300]}")
        return res.json()

    def _encode(self, texts: list[str], mode: str) -> list[list[float]]:
        out: list[list[float]] = []
        with httpx.Client(timeout=120) as client:
            for i in range(0, len(texts), self.batch):
                out += self._request(client, list(texts[i:i + self.batch]), mode)
        return _normalize(out)

    def embed_documents(self, texts):
        return self._encode(texts, "document")

    def embed_queries(self, texts):
        return self._encode(texts, "query")


class CohereEmbedder(_ApiEmbedder):
    def _request(self, client, texts, mode):
        data = self._post(client, "https://api.cohere.com/v2/embed", {
            "model": self.spec.model_id, "texts": texts, "embedding_types": ["float"],
            "input_type": "search_query" if mode == "query" else "search_document"})
        return data["embeddings"]["float"]


class OpenAIEmbedder(_ApiEmbedder):
    batch = 256

    def _request(self, client, texts, mode):
        data = self._post(client, "https://api.openai.com/v1/embeddings",
                          {"model": self.spec.model_id, "input": texts})
        return [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]


class EmbeddingRegistry:
    """網頁可逐次選擇模型；settings.embedding_model 為預設。示範模式一律使用字元比對向量。"""

    def __init__(self, settings: Settings):
        self.s = settings
        self.demo = settings.demo_mode
        self.specs = model_specs(settings)
        self.default_key = "demo" if self.demo else self._resolve(settings.embedding_model)
        self._cache: dict[str, object] = {}
        self._lock = threading.Lock()

    def _resolve(self, name: str) -> str:
        """接受登記名稱（bge-m3）或模型 ID（BAAI/bge-m3）；其他名稱當作本機 sentence-transformers 模型。"""
        if name in self.specs:
            return name
        for spec in self.specs.values():
            if spec.model_id == name:
                return spec.key
        key = re.sub(r"[^A-Za-z0-9-]+", "-", name).strip("-")[:40] or "custom"
        self.specs[key] = EmbeddingSpec(key, name, "local", name, "自訂本機模型。", "Custom local model.")
        return key

    def unavailable_reason(self, spec: EmbeddingSpec) -> str | None:
        if self.demo:
            return "demo"
        if spec.provider == "local" and importlib.util.find_spec("sentence_transformers") is None:
            return "no_package"
        if spec.provider in API_KEY_ENV and not self._api_key(spec.provider):
            return "no_key"
        return None

    def _api_key(self, provider: str) -> str:
        return {"cohere": self.s.cohere_api_key, "openai": self.s.openai_api_key}[provider]

    def options(self) -> list[dict]:
        out = []
        for spec in self.specs.values():
            reason = self.unavailable_reason(spec)
            out.append({"key": spec.key, "label": spec.label, "provider": spec.provider,
                        "model_id": spec.model_id, "note_zh": spec.note_zh, "note_en": spec.note_en,
                        "available": reason is None, "reason": reason, "env": API_KEY_ENV.get(spec.provider, "")})
        return out

    def get(self, key: str | None = None):
        key = key or self.default_key
        if self.demo:
            if key != "demo":
                raise EmbeddingUnavailable("示範模式不載入 embedding 模型")
            from app.services.offline import CharNgramEmbedder
            return self._cache.setdefault("demo", CharNgramEmbedder())
        spec = self.specs.get(key)
        if spec is None:
            raise EmbeddingUnavailable(f"未知的 embedding 模型：{key}")
        reason = self.unavailable_reason(spec)
        if reason == "no_package":
            raise EmbeddingUnavailable(f"{spec.label} 需要安裝 sentence-transformers")
        if reason == "no_key":
            raise EmbeddingUnavailable(f"{spec.label} 需要先設定 {API_KEY_ENV[spec.provider]}")
        with self._lock:
            if key not in self._cache:
                if spec.provider == "local":
                    self._cache[key] = LocalEmbedder(spec)
                elif spec.provider == "cohere":
                    self._cache[key] = CohereEmbedder(spec, self.s.cohere_api_key)
                else:
                    self._cache[key] = OpenAIEmbedder(spec, self.s.openai_api_key)
            return self._cache[key]
