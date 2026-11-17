"""把 data/curriculum/*.json 寫入 Chroma。每個 embedding 模型各有一個索引。
用法：python -m scripts.ingest_curriculum [--rebuild] [--model bge-m3|jina-v3|gte-qwen2-7b|cohere-v3|openai-3]"""
import argparse

from app.config import get_settings
from app.services.curriculum_store import CurriculumStore
from app.services.embeddings import EmbeddingRegistry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="刪除並重建索引")
    ap.add_argument("--model", default=None, help="embedding 模型；預設為 EMBEDDING_MODEL")
    args = ap.parse_args()
    s = get_settings()
    embedder = EmbeddingRegistry(s).get(args.model)
    store = CurriculumStore(s)
    if args.rebuild:
        store.rebuild(embedder)
    print(f"[{embedder.spec.label}] 已索引 {store.ensure_indexed(embedder)} 個課程重點，共 {len(store.units)} 個單元")


if __name__ == "__main__":
    main()
