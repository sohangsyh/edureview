"""網上靜態版（app/static/engine.js）必須與 Python 版本結果一致。以 Node 執行 JavaScript，逐項比較。"""
import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from app.prompts.profiles import build_system_prompt
from app.prompts.templates import build_accuracy_prompt, build_coverage_prompt
from app.schemas import Chunk, ReviewReport, Unit
from app.services.chunker import chunk_pages
from app.services.language import detect_language
from app.services.parser import Page, _join_broken_lines
from app.services.patcher import apply_patches
from app.services.renderer import render_markdown
from app.services.script_check import _S2HK, _T2S, ScriptChecker, scan_sentence
from app.services.terminology import TermHint, TerminologyChecker
from scripts import build_static

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"
UNITS = {u.unit_id: u for u in (Unit.model_validate_json(p.read_text("utf-8"))
                                 for p in sorted((ROOT / "data" / "curriculum").glob("*.json")))}
OBJECTIVES = {o.id: o for u in UNITS.values() for o in u.objectives}
OFFICIAL = [f"{o.subtopic} {o.text}" for o in OBJECTIVES.values()] + [u.title for u in UNITS.values()]

SENTENCES = [
    "水受热后会蒸发。", "頭发乾了。", "物質受熱后會膨脹。", "范先生和余老師在后山散步。", "我们了解这个软件的信息。",
    "這段網絡視頻的質量很好，請用優盤保存。", "Water boils at 100 °C; the colour of the vapor is clear.",
    "他把衣服晾干后，放进柜里。", "𠝹開蘋果后，里面有籽。", "測量長度要用米尺（meter rule），不要寫成 meters。",
]
CHUNKS = [{"chunk_id": f"t-c{i + 1}", "page": None, "text": s} for i, s in enumerate(SENTENCES)]


def run_js(cases: list[dict]) -> list:
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 Node.js")
    out = subprocess.run([node, str(ROOT / "tests" / "js" / "engine_harness.cjs")], input=json.dumps({"cases": cases}),
                         capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def dump(models) -> list:
    return [m.model_dump(mode="json") for m in models]


def test_generated_static_files_are_current():
    assert not build_static.stale(), "請執行 python -m scripts.build_static 並提交結果"


def test_text_processing_matches_python():
    texts = [p.read_text("utf-8") for p in sorted(SAMPLES.glob("*.txt"))]
    broken = "物質由細小的粒子組成，粒子受\n熱後會變大。\n\n科 學 實 踐 I\r\nSecond line\n"
    cases = ([{"op": "convert", "text": s} for s in SENTENCES] + [{"op": "scan", "text": s} for s in SENTENCES]
             + [{"op": "join", "text": t} for t in texts + [broken]]
             + [{"op": "chunk", "pages": [{"page_no": 3, "text": _join_broken_lines(t)}]} for t in texts]
             + [{"op": "chunk", "pages": [{"page_no": None, "text": "。".join(["一句很長的句子" * 30] * 8)}]}]
             + [{"op": "lang", "text": t} for t in texts + SENTENCES])
    js = iter(run_js(cases))
    for s in SENTENCES:
        assert next(js) == {"t2s": _T2S.convert(s), "s2hk": _S2HK.convert(s)}, s
    for s in SENTENCES:
        assert next(js) == [asdict(h) for h in scan_sentence(s)], s
    for t in texts + [broken]:
        assert next(js) == _join_broken_lines(t)
    for t in texts:
        assert next(js) == dump(chunk_pages([Page(3, _join_broken_lines(t))]))
    assert next(js) == dump(chunk_pages([Page(None, "。".join(["一句很長的句子" * 30] * 8))]))
    for t in texts + SENTENCES:
        assert next(js) == detect_language(t), t


def test_rule_checks_match_python():
    chunks = [Chunk(**c) for c in CHUNKS]
    js = run_js([{"op": "script_check", "chunks": CHUNKS, "lang": "zh"},
                 {"op": "script_check", "chunks": CHUNKS, "lang": "en"},
                 {"op": "terms", "chunks": CHUNKS}])
    for result, lang in zip(js[:2], ["zh", "en"]):
        findings, summary = ScriptChecker().check(chunks, lang)
        assert result == [dump(findings), summary.model_dump()]
    findings, hints = TerminologyChecker(ROOT / "data" / "glossary", OFFICIAL).check(chunks)
    assert js[2] == [dump(findings), {k: [asdict(h) for h in v] for k, v in hints.items()}]


def test_prompts_match_python():
    unit = UNITS["S1-SAMPLE-01"]
    chunks = [Chunk(**c) for c in CHUNKS[:3]] + [Chunk(chunk_id="p2-c1", page=2, text="A <b> & \"quote\"")]
    pairs = [(unit.objectives[0], chunks[:2]), (unit.objectives[1], chunks[2:])]
    hints = [TermHint("t-c1", "質量", "質量", "按語境判斷 \"mass\"")]
    js = run_js([{"op": "system_prompt", "profile": p, "lang": lang}
                 for p in ("science", "english_language", "nope") for lang in ("zh", "en")]
                + [{"op": "coverage_prompt", "unit_id": unit.unit_id, "mode": mode,
                    "pairs": [[o.id, dump(cs)] for o, cs in pairs]} for mode in ("content", "skills")]
                + [{"op": "accuracy_prompt", "chunks": dump(chunks), "related": [o.id for o in unit.objectives[:2]],
                    "hints": [asdict(h) for h in hints]},
                   {"op": "accuracy_prompt", "chunks": dump(chunks), "related": [], "hints": []}])
    expected = ([build_system_prompt(p, lang) for p in ("science", "english_language", "nope") for lang in ("zh", "en")]
                + [build_coverage_prompt(unit, pairs, mode) for mode in ("content", "skills")]
                + [build_accuracy_prompt(chunks, unit.objectives[:2], hints), build_accuracy_prompt(chunks, [], [])])
    assert js == expected


@pytest.mark.parametrize("sample,unit_id,lang", [
    ("science_water_zh.txt", "S1-SAMPLE-01", "zh"),
    ("science_water_en.txt", "DEMO-SCI-WATER-EN", "en"),
    ("science_water_en.txt", "S1-SAMPLE-01", "zh"),
])
def test_full_offline_review_matches_python(client_factory, sample, unit_id, lang):
    text = (SAMPLES / sample).read_text("utf-8")
    js_report = run_js([{"op": "review", "filename": sample, "text": text, "unit_id": unit_id, "report_lang": lang}])[0]
    py_report = client_factory(sample, text, unit_id, lang)

    def comparable(r):
        r = {k: v for k, v in r.items() if k not in ("model", "embedding_model")}
        # 示範模式的檢索方法不同（Python 用雜湊向量，瀏覽器用字元二元組），覆蓋判斷的依據片段可能不同
        r["coverage"] = [{k: v for k, v in c.items() if k not in ("evidence_chunk_ids", "rationale")} for c in r["coverage"]]
        r["findings"] = [f for f in r["findings"] if f["source"] == "rule"]
        return r
    assert comparable(js_report) == comparable(py_report)

    report = ReviewReport.model_validate(js_report)
    accepted = [f for f in js_report["findings"] if f["patch"]][:3] + [
        {"finding_id": "X1", "patch": {"action": "insert_after", "anchor_text": "", "new_text": "補充"}},
        {"finding_id": "X2", "patch": {"action": "replace", "anchor_text": "不存在的句子", "new_text": "x"}},
        {"finding_id": "X3", "patch": None}]
    md, patched = run_js([{"op": "markdown", "report": report.model_dump(mode="json")},
                          {"op": "patch", "material": text, "findings": accepted}])
    assert md == render_markdown(report)
    r = apply_patches(text, accepted)
    assert patched == {"text": r.text, "applied": r.applied, "unapplied": r.unapplied}


def test_tool_output_validation():
    good = {"findings": [{"category": "factual_error", "severity": "high", "issue": "i", "suggestion": "s", "confidence": 0.9}]}
    bad = {"findings": [{"category": "nonsense", "severity": "high", "issue": "i", "suggestion": "s", "confidence": 2}]}
    assert run_js([{"op": "validate", "schema": "findings", "value": good},
                   {"op": "validate", "schema": "findings", "value": bad},
                   {"op": "validate", "schema": "coverage", "value": {"items": [{"objective_id": "a", "status": "covered"}]}}]) \
        == [0, 2, 1]


@pytest.fixture
def client_factory(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import build_services, create_app
    from tests.test_api import _wait

    cur = tmp_path / "curriculum"
    shutil.copytree(ROOT / "data" / "curriculum", cur)
    settings = Settings(demo_mode=True, chroma_path=tmp_path / "chroma", db_path=tmp_path / "app.db", curriculum_dir=cur)
    with TestClient(create_app(build_services(settings))) as client:
        def review(filename, text, unit_id, lang):
            res = client.post("/api/reviews", files={"file": (filename, text.encode(), "text/plain")},
                              data={"unit_id": unit_id, "report_lang": lang})
            row = _wait(client, res.json()["id"])
            assert row["status"] == "done", row["error"]
            return row["report"]
        yield review
