"""把網站上傳到 Hugging Face Space（Docker）。GitHub Pages 只能放靜態檔案，不能執行本平台。

用法：HF_TOKEN=hf_xxx python -m scripts.deploy_hf --space 你的帳戶/hk-edu-reviewer
Space 不存在時會自動建立。token 須有 write 權限（https://huggingface.co/settings/tokens）。
"""
import argparse
import os
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
IGNORE = [".git/*", ".github/*", ".venv/*", "*__pycache__*", "*.pyc", ".pytest_cache/*", "*.DS_Store",
          ".env", "tests/*", "docs/*", "deploy/*", "data/app.db", "data/chroma*/*", "data/raw/*",
          "README.md"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", default=os.environ.get("HF_SPACE"), help="例如 yourname/hk-edu-reviewer")
    args = ap.parse_args()
    if not args.space:
        ap.error("請用 --space 或 HF_SPACE 指定 Space")
    api = HfApi()
    api.create_repo(args.space, repo_type="space", space_sdk="docker", exist_ok=True)
    api.upload_folder(repo_id=args.space, repo_type="space", folder_path=ROOT, ignore_patterns=IGNORE,
                      commit_message="Deploy from GitHub")
    # Space 的 README 須以 YAML 設定開頭，與 GitHub 的 README 分開存放
    api.upload_file(repo_id=args.space, repo_type="space", path_or_fileobj=ROOT / "deploy" / "huggingface" / "README.md",
                    path_in_repo="README.md", commit_message="Space README")
    print(f"已上傳。建置進度：https://huggingface.co/spaces/{args.space}")


if __name__ == "__main__":
    main()
