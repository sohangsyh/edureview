"""把 data/curriculum/*.json 寫入 Chroma。
用法：python -m scripts.ingest_curriculum [--rebuild]"""
import argparse

from app.config import get_settings
from app.services.curriculum_store import CurriculumStore, build_embedding_fn


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="刪除並重建索引")
    args = ap.parse_args()
    s = get_settings()
    store = CurriculumStore(s, build_embedding_fn(s.embedding_model))
    if args.rebuild:
        store.rebuild()
    print(f"已索引 {store.ensure_indexed()} 個課程重點，共 {len(store.units)} 個單元")


if __name__ == "__main__":
    main()
