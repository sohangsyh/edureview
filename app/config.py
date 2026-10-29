"""集中管理設定，全部可由環境變數或 .env 覆寫。"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    # 平台
    demo_mode: bool = False            # True = 離線示範模式（不用 API key、不下載模型）
    access_code: str = ""              # 設定後，使用網站須先輸入存取碼
    db_path: Path = BASE_DIR / "data" / "app.db"

    # LLM
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"
    llm_max_tokens: int = 8000
    llm_concurrency: int = 4

    # RAG
    # 多語模型：英文教材也能對照中文課程文件（反之亦然）。只審中文教材可改用 BAAI/bge-small-zh-v1.5（較小）
    embedding_model: str = "BAAI/bge-m3"
    chroma_path: Path = BASE_DIR / "data" / "chroma"
    evidence_top_k: int = 3        # 每個課程重點從教材檢索幾個片段
    curriculum_top_k: int = 4      # 每個教材片段檢索幾個相關課程重點

    # 資料
    curriculum_dir: Path = BASE_DIR / "data" / "curriculum"
    glossary_dir: Path = BASE_DIR / "data" / "glossary"
    subjects_path: Path = BASE_DIR / "data" / "subjects.json"
    prompts_dir: Path = BASE_DIR / "app" / "prompts"

    # 處理
    chunk_size: int = 700          # 以字元計（中文無空格分詞）
    chunk_overlap: int = 100
    accuracy_batch_size: int = 4   # 每次 LLM 呼叫審查幾個片段
    coverage_group_size: int = 8   # 每次 LLM 呼叫判斷幾個課程重點
    max_upload_mb: int = 20
    min_confidence: float = 0.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
