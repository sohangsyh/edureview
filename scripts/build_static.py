"""產生網上靜態版（GitHub Pages）需要的檔案。新增或修改課程單元或詞表後執行一次，並一併提交。

用法：python -m scripts.build_static
產生：data/manifest.json、app/static/vendor/opencc/（簡繁字表）
tests/test_static_engine.py 會檢查這些檔案是否最新。
"""
import json
from pathlib import Path

import opencc

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
