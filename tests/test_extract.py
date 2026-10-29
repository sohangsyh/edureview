"""課程文件擷取：以合成 PDF 模擬教育局文件的版面（每字之間有空格、延展部分為藍色）。"""
import pymupdf

from scripts.extract_curriculum import (EXT_OPEN, ExtractedObjective, ExtractedUnit, build_unit,
                                        cn_to_int, locate, parse_manual, split_units, styled_page_text)

REGEX = r"^單元\s*([一二三四五六七八九十]+)\s*[：:]\s*(.+)$"
BLUE = (0.1, 0.3, 0.9)


def _pdf():
    doc = pymupdf.open()
    toc = doc.new_page()
    toc.insert_text((50, 80), "目錄", fontname="china-t")
    toc.insert_text((50, 110), "單元五：地球與太空", fontname="china-t")      # 目錄頁：不應被當成單元起點
    p = doc.new_page()
    p.insert_text((50, 80), "單元五：地球與太空", fontname="china-t")
    p.insert_text((50, 110), "概述", fontname="china-t")
    p.insert_text((50, 140), "說 出 節 約 用 水 的 不 同 方 法", fontname="china-t")
    p.insert_text((50, 170), "製作表格展示淨水的分佈(S)", fontname="china-t", color=BLUE)
    q = doc.new_page()
    q.insert_text((50, 80), "單元六：生物與環境", fontname="china-t")
    q.insert_text((50, 110), "概述", fontname="china-t")
    g = doc.new_page()
    g.insert_text((50, 80), "詞彙", fontname="china-t")
    return [styled_page_text(pg) for pg in doc]


def test_chinese_numerals():
    assert [cn_to_int(x) for x in ("一", "十", "十三", "二十")] == [1, 10, 13, 20]


def test_styled_text_collapses_spaces_and_marks_extension():
    pages = _pdf()
    assert "說出節約用水的不同方法" in pages[1]
    assert f"{EXT_OPEN}製作表格展示淨水的分佈(S)" in pages[1]


def test_split_units_skips_toc_and_stops_at_glossary():
    units = split_units(_pdf(), REGEX)
    assert [(u["unit_no"], u["start"], u["end"]) for u in units] == [(5, 1, 2), (6, 2, 3)]


def test_build_unit_verifies_verbatim_text():
    pages = _pdf()
    unit_pages = {2: pages[1]}
    extracted = ExtractedUnit(levels=["S1", "S9"], objectives=[
        ExtractedObjective(subtopic="5.4 地球資源", text="說出節約用水的不同方法", tier="core",
                           kind="outcome", skill=False, key_terms=["節約用水", "不存在的詞"]),
        ExtractedObjective(subtopic="5.2 地球", text="製作表格展示淨水的分佈", tier="extension",
                           kind="outcome", skill=True, key_terms=["淨水"]),
        ExtractedObjective(subtopic="5.4 地球資源", text="模型捏造的學習成果", tier="core",
                           kind="outcome", skill=False, key_terms=[]),
    ])
    source = {"curriculum_id": "SCI-S13-2025", "title_zh": "測試", "pdf_zh": "https://example"}
    unit, problems = build_unit(extracted, {"unit_no": 5, "title": "地球與太空", "start": 1, "end": 2},
                                unit_pages, source, subject="SCI", lang="zh")
    assert unit.unit_id == "SCI-S13-2025-ZH-U05" and unit.subject == "SCI" and unit.levels == ["S1"]
    o1, o2, o3 = unit.objectives
    assert o1.id == "SCI-S13-2025-ZH-U05-01" and o1.page == 2 and o1.key_terms == ["節約用水"]
    assert o2.tier == "extension" and o2.skill and o2.page == 2
    assert o3.page is None and "未能" in o3.notes and len(problems) == 1
    assert unit.verified is False
    assert locate("說 出 節約用水", unit_pages) == 2


def test_manual_unit_ranges():
    units = parse_manual("1:閱讀:12-15; 2:寫作:16")
    assert units == [{"unit_no": 1, "title": "閱讀", "start": 11, "end": 15},
                     {"unit_no": 2, "title": "寫作", "start": 15, "end": 16}]
