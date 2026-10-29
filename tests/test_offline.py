"""不需要 API key 或 embedding 模型的單元測試。"""
from pathlib import Path

from app.schemas import Category, Chunk, FindingsOutput, Severity
from app.services.chunker import chunk_pages
from app.services.parser import Page, collapse_cjk_spaces, parse_document
from app.services.reviewer import inline_refs
from app.services.script_check import ScriptChecker, scan_sentence
from app.services.terminology import TerminologyChecker
from app.services.validation import normalize_for_match, quote_in_text

ROOT = Path(__file__).resolve().parent.parent
GLOSSARY = ROOT / "data" / "glossary"


# ---------- 簡體字（硬性規則）----------
def _script(text):
    return ScriptChecker().check([Chunk(chunk_id="t-c1", text=text)])


def test_simplified_always_triggers():
    findings, summary = _script("水受热后会蒸发。")
    assert not summary.passed and summary.simplified_chars == 4      # 热、后、会、发
    f = findings[0]
    assert f.category == Category.simplified and f.severity == Severity.high
    assert f.patch.new_text == "水受熱後會蒸發。"


def test_dual_use_chars_follow_context():
    _, s = _script("頭发乾了。")        # 发 在「頭髮」語境
    hits = scan_sentence("头发干了。")
    assert {h.replacement for h in hits} >= {"頭", "髮"}
    assert not s.passed


def test_mixed_sentence_dual_use_triggers():
    # 整句繁體只寫錯一個共用字，仍須觸發（嚴重度 medium，請老師確認）
    findings, summary = _script("物質受熱后會膨脹。")
    assert not summary.passed
    assert findings[0].severity == Severity.medium and "後" in findings[0].patch.new_text


def test_no_false_positive_on_hk_traditional():
    text = ("同學要了解水循環。每公里路程，舞台上的皇后批准了若干計劃。電台干擾只有一點。"
            "遺傳信息、塑料、數字教育工具、淨水、熱脹冷縮、颱風、乾燥、頭髮、一隻貓、試管口不可對着人。")
    findings, summary = _script(text)
    assert summary.passed and findings == []


def test_simplified_sentence_reported_once_despite_chunk_overlap():
    chunks = [Chunk(chunk_id="p1-c1", text="甲。水受热后会蒸发。"),
              Chunk(chunk_id="p1-c2", text="水受热后会蒸发。乙。")]
    findings, summary = ScriptChecker().check(chunks)
    assert len(findings) == 1 and summary.sentences == 1


# ---------- 用語 ----------
def test_terminology_direct_vs_context_sensitive():
    chunks = [Chunk(chunk_id="t-c1", text="冰熔解成水後，質量不變。同學可觀看視頻。之後再看一段視頻。")]
    findings, hints = TerminologyChecker(GLOSSARY).check(chunks)
    terms = [f for f in findings if "視頻" in f.issue]
    assert len(terms) == 1 and "2 處" in terms[0].issue
    assert not any("質量" in f.issue for f in findings)
    assert hints["t-c1"][0].term == "質量"


def test_official_curriculum_terms_never_flagged_directly():
    text = "DNA 載有遺傳信息。原油可用作製造塑料的原料。"
    findings, hints = TerminologyChecker(GLOSSARY).check([Chunk(chunk_id="t-c1", text=text)])
    assert findings == []                                   # 只作語境提示
    assert {h.term for h in hints["t-c1"]} == {"信息", "塑料"}


def test_glossary_auto_protects_terms_found_in_official_docs():
    checker = TerminologyChecker(GLOSSARY, official_texts=["觀看有關氣候變化的視頻"])
    findings, hints = checker.check([Chunk(chunk_id="t-c1", text="請觀看視頻。")])
    assert findings == [] and "視頻" in checker.protected


# ---------- 解析與切塊 ----------
def test_collapse_cjk_spaces_keeps_english_spacing():
    assert collapse_cjk_spaces("科 學 實 踐 I 和 DNA model") == "科學實踐 I 和 DNA model"


def test_chunker_keeps_pages_and_size():
    chunks = chunk_pages([Page(1, "甲。" * 400), Page(2, "乙。")], size=100, overlap=10)
    assert chunks[0].chunk_id == "p1-c1" and chunks[-1].page == 2
    assert all(len(c.text) <= 112 for c in chunks)


def test_parser_big5_and_line_join():
    pages = parse_document("a.txt", "水受熱後\n會蒸發。\n\n第二段。".encode("big5hkscs"))
    assert "水受熱後會蒸發。" in pages[0].text


def test_quote_verification_tolerates_width_and_spaces():
    full = normalize_for_match("水在 100°C 時一定會沸騰。")
    assert quote_in_text("水在100°C時一定會沸騰。", full)
    assert not quote_in_text("水在 90°C 時沸騰。", full)


def test_tool_schema_has_no_refs():
    schema = inline_refs(FindingsOutput.model_json_schema())
    assert "$ref" not in str(schema) and "$defs" not in schema


# ---------- 英文教材 ----------
def test_british_spelling_preserves_case_and_whole_words():
    text = "The Color of water vapor. Use a voltmeter."
    findings, hints = TerminologyChecker(GLOSSARY).check([Chunk(chunk_id="t-c1", text=text)])
    fixes = {f.patch.new_text for f in findings}
    assert any("Colour" in x for x in fixes) and any("vapour" in x for x in fixes)
    assert not any("voltmetre" in x for x in fixes)          # 儀器名稱不受影響


def test_context_sensitive_english_goes_to_ai():
    _, hints = TerminologyChecker(GLOSSARY).check([Chunk(chunk_id="t-c1", text="The rope is 5 meters long.")])
    assert hints["t-c1"][0].term == "meters"


def test_language_detection():
    from app.services.language import detect_language
    assert detect_language("Water boils at 100 °C at standard pressure.") == "en"
    assert detect_language("蒸發（evaporation）是水由液態變成氣態的過程。") == "zh"


def test_every_subject_has_a_profile():
    from app.prompts.profiles import PROFILES, build_system_prompt
    from app.subjects import load_subjects
    for subject in load_subjects(ROOT / "data" / "subjects.json").values():
        assert subject.profile in PROFILES, subject.code
        prompt = build_system_prompt(subject.profile, "en")
        assert "{{" not in prompt and "English" in prompt
