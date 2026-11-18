"""產生網上靜態版（GitHub Pages）需要的檔案。新增或修改課程單元、詞表、prompt 後執行一次，並一併提交。

用法：python -m scripts.build_static
產生：data/manifest.json、app/static/vendor/（OpenCC 字表、prompts.json、tool-schemas.json）
tests/test_static_engine.py 會檢查這些檔案是否最新。
"""
import json
from pathlib import Path

import opencc

from app.config import Settings
from app.prompts.profiles import OUTPUT_LANGUAGE, PROFILES
from app.schemas import CoverageOutput, FindingsOutput
from app.services.reviewer import inline_refs

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "app" / "static" / "vendor"
OPENCC_FILES = ["TSPhrases.txt", "TSCharacters.txt", "STPhrases.txt", "STCharacters.txt", "HKVariants.txt"]


def _json(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def outputs() -> dict[Path, bytes]:
    data = ROOT / "data"
    files = {
        data / "manifest.json": _json({
            "curriculum": sorted(p.name for p in (data / "curriculum").glob("*.json")),
            "glossary": sorted(p.name for p in (data / "glossary").glob("*.json")),
        }),
        VENDOR / "prompts.json": _json({
            "system_template": (ROOT / "app" / "prompts" / "system_prompt.md").read_text("utf-8"),
            "profiles": PROFILES,
            "output_language": OUTPUT_LANGUAGE,
            "llm_model": Settings.model_fields["llm_model"].default,
        }),
        VENDOR / "tool-schemas.json": _json({
            "coverage": inline_refs(CoverageOutput.model_json_schema()),
            "findings": inline_refs(FindingsOutput.model_json_schema()),
        }),
    }
    src = Path(opencc.__file__).parent
    for name in OPENCC_FILES:
        files[VENDOR / "opencc" / name] = (src / "dictionary" / name).read_bytes()
    files[VENDOR / "opencc" / "NOTICE.txt"] = (src / "NOTICE.txt").read_bytes()
    return files


def stale() -> list[Path]:
    return [p for p, content in outputs().items() if not p.exists() or p.read_bytes() != content]


def main() -> None:
    for path, content in outputs().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    print(f"已更新 {VENDOR.relative_to(ROOT)} 及 data/manifest.json")


if __name__ == "__main__":
    main()
