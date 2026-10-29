"""JSON 報告 → 老師可讀的 Markdown（按報告語言輸出中文或英文）。"""
from app.schemas import ReviewReport

L = {
    "zh": {
        "category": {"simplified": "簡體字", "omission": "內容遺漏", "factual_error": "內容錯誤", "imprecise": "表述不精確",
                     "terminology": "用語", "language": "語文", "safety": "安全"},
        "severity": {"high": "高", "medium": "中", "low": "低"},
        "status": {"covered": "已處理", "partial": "部分處理", "missing": "缺漏", "unassessed": "未能評估"},
        "action": {"insert_after": "於下句之後插入", "replace": "取代", "delete": "刪除"},
        "title": "教材審查報告", "unit": "單元", "scope": "範圍", "core_ext": "核心＋延展", "core": "只限核心部分",
        "selected": "老師選定 {n} 項學習重點", "model": "模型",
        "pass": "**字形檢查：合格**（未發現簡體字）",
        "fail": "**字形檢查：不合格**　發現 {n} 個簡體字，涉及 {s} 句，發佈前必須修正",
        "unverified": "> 注意：此單元課程資料尚未經老師核對，結果僅供參考。",
        "coverage": "## 一、學習重點覆蓋", "col": "| 學習重點 | 狀態 | 依據片段 |",
        "findings": "## 二、問題與修改建議", "page": "第 {p} 頁", "whole": "全文", "unverified_quote": "　⚠ 引文未能在原文核實",
        "orig": "原文", "issue": "問題", "suggest": "建議", "patch": "補丁", "refs": "依據", "conf": "信心", "notes": "## 系統提示",
    },
    "en": {
        "category": {"simplified": "Simplified Chinese", "omission": "Missing content", "factual_error": "Content error",
                     "imprecise": "Imprecise", "terminology": "Terminology", "language": "Language", "safety": "Safety"},
        "severity": {"high": "high", "medium": "medium", "low": "low"},
        "status": {"covered": "Covered", "partial": "Partly covered", "missing": "Missing", "unassessed": "Not assessed"},
        "action": {"insert_after": "insert after", "replace": "replace", "delete": "delete"},
        "title": "Material review report", "unit": "Unit", "scope": "Scope", "core_ext": "core and extension", "core": "core only",
        "selected": "{n} learning points chosen by the teacher", "model": "Model",
        "pass": "**Script check: pass** (no simplified characters)",
        "fail": "**Script check: fail**: {n} simplified character(s) in {s} sentence(s); fix before publishing",
        "unverified": "> Note: this unit's curriculum data has not been checked by a teacher yet.",
        "coverage": "## 1. Learning point coverage", "col": "| Learning point | Status | Evidence |",
        "findings": "## 2. Issues and suggested changes", "page": "page {p}", "whole": "whole text", "unverified_quote": " ⚠ quote not found in the material",
        "orig": "Original", "issue": "Issue", "suggest": "Suggestion", "patch": "Patch", "refs": "Curriculum reference", "conf": "confidence", "notes": "## Notes",
    },
}


def render_markdown(r: ReviewReport) -> str:
    t = L.get(r.report_lang, L["zh"])
    s, sc = r.coverage_summary, r.script_check
    scope = (t["selected"].format(n=r.selected_objectives) if r.selected_objectives
             else t["core_ext"] if r.include_extension else t["core"])
    lines = [f"# {t['title']}: {r.unit_title}" if r.report_lang == "en" else f"# {t['title']}：{r.unit_title}", "",
             f"{t['unit']}: `{r.unit_id}`  {t['scope']}: {scope}  {t['model']}: `{r.model}`", "",
             t["pass"] if sc.passed else t["fail"].format(n=sc.simplified_chars, s=sc.sentences), "",
             t["coverage"], ""]
    if not r.unit_verified:
        lines += [t["unverified"], ""]
    lines += [t["col"], "|---|---|---|"]
    for c in r.coverage:
        lines.append(f"| `{c.objective_id}` {c.objective_text} | {t['status'][c.status.value]} | "
                     f"{', '.join(c.evidence_chunk_ids) or '—'} |")
    lines += ["", t["findings"], ""]
    for f in r.findings:
        loc = t["page"].format(p=f.page) if f.page else (f.chunk_id or t["whole"])
        flag = "" if f.quote_verified else t["unverified_quote"]
        lines += [f"### {f.finding_id} [{t['category'][f.category.value]} / {t['severity'][f.severity.value]}] {loc}{flag}", ""]
        if f.original_text:
            lines += [f"> {t['orig']}: {f.original_text}", ""]
        lines += [f"**{t['issue']}**: {f.issue}", "", f"**{t['suggest']}**: {f.suggestion}", ""]
        if f.patch:
            if f.patch.anchor_text:
                lines += [f"**{t['patch']}** ({t['action'][f.patch.action.value]}):", "", f"> {f.patch.anchor_text}", ""]
            if f.patch.new_text:
                lines += ["```text", f.patch.new_text, "```", ""]
        if f.curriculum_refs:
            lines += [f"{t['refs']}: {', '.join(f'`{i}`' for i in f.curriculum_refs)}  {t['conf']}: {f.confidence}", ""]
    if r.warnings:
        lines += [t["notes"], ""] + [f"- {w}" for w in r.warnings]
    return "\n".join(lines)
