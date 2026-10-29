"""審查流程總指揮：

  解析 → 切塊 → 規則引擎(簡體字【硬性】+ 用語) ───────────────┐
              └→ 教材臨時索引 ─┬→ [覆蓋檢查] 課程重點 → 檢索教材證據 → LLM ─┤
                               └→ [準確性]  教材片段 → 檢索相關課程重點 → LLM ┤
                                                                              ↓
                                           驗證引文 / ID → 去重 → 排序 → 報告
"""
import asyncio
from collections.abc import Callable
from itertools import islice

from app.config import Settings
from app.prompts.profiles import build_system_prompt
from app.prompts.templates import build_accuracy_prompt, build_coverage_prompt
from app.schemas import (Category, Chunk, CoverageItem, CoverageResult, CoverageStatus,
                         Finding, LocatedFinding, Objective, ReviewReport, Severity, Unit)
from app.services.chunker import chunk_pages
from app.services.curriculum_store import CurriculumStore, MaterialIndex
from app.services.language import detect_language
from app.services.parser import ParseError, parse_document
from app.services.reviewer import LLMReviewer
from app.services.script_check import ScriptChecker
from app.services.terminology import TerminologyChecker
from app.subjects import Subject
from app.services.validation import normalize_for_match, quote_in_text

_SEVERITY_ORDER = {Severity.high: 0, Severity.medium: 1, Severity.low: 2}


class ReviewInputError(Exception):
    """提交內容不完整（例如語文科未選學習重點）。"""


_MSG = {
    "zh": {
        "unverified": "此單元的課程資料尚未經科任老師核對，遺漏判斷的依據僅供參考。",
        "script_fail": "字形檢查不合格：發現 {n} 個簡體字（{s} 句），發佈前必須修正。",
        "cov_fail": "覆蓋檢查有一組失敗：{e}",
        "acc_fail": "準確性審查有一批失敗：{e}",
        "fake_id": "已忽略模型虛構的課程 ID：{i}",
        "unassessed": "模型未有回傳此項評估",
        "missing": "課程重點「{t}」未見於教材。{r}",
        "partial": "課程重點「{t}」只部分處理。{r}",
        "with_patch": "建議按以下補丁補充內容。",
        "no_patch": "請補充此課程重點的內容。",
        "cross_lang": "教材為{m}，課程資料為{c}；系統已跨語言對照，請留意術語是否與課程文件對應。",
        "lang_names": {"zh": "中文", "en": "英文"},
    },
    "en": {
        "unverified": "This unit's curriculum data has not yet been checked by a teacher; treat omission results as provisional.",
        "script_fail": "Script check failed: {n} simplified Chinese character(s) in {s} sentence(s). Fix before publishing.",
        "cov_fail": "A coverage check batch failed: {e}",
        "acc_fail": "An accuracy review batch failed: {e}",
        "fake_id": "Ignored a curriculum ID invented by the model: {i}",
        "unassessed": "The model did not return an assessment for this item",
        "missing": "Learning point \"{t}\" is not found in the material. {r}",
        "partial": "Learning point \"{t}\" is only partly addressed. {r}",
        "with_patch": "Add the content in the patch below.",
        "no_patch": "Add content covering this learning point.",
        "cross_lang": "The material is in {m} but the curriculum data is in {c}; matching was done across languages, so check that terms match the curriculum document.",
        "lang_names": {"zh": "Chinese", "en": "English"},
    },
}


def _batched(items, n):
    it = iter(items)
    while batch := list(islice(it, n)):
        yield batch


class ReviewPipeline:
    def __init__(self, settings: Settings, store: CurriculumStore, reviewer: LLMReviewer,
                 terms: TerminologyChecker, embed_fn, subjects: dict[str, Subject] | None = None):
        self.subjects = subjects or {}
        self.script = ScriptChecker()
        self.s = settings
        self.store = store
        self.reviewer = reviewer
        self.terms = terms
        self.embed_fn = embed_fn

    # ---------- 檢索（同步，放到 thread 執行） ----------
    def _retrieve(self, unit: Unit, objectives: list[Objective], chunks: list[Chunk],
                  batches: list[list[Chunk]]):
        index = MaterialIndex(self.embed_fn, chunks)
        try:
            # 反向檢索：以「課程重點」查「教材」，找不到證據 = 可能遺漏
            evidence: dict[str, list[Chunk]] = {}
            for o in objectives:
                found = index.keyword_hits(o.key_terms) + index.search(
                    f"{o.text} {' '.join(o.key_terms)}", self.s.evidence_top_k)
                evidence[o.id] = list({c.chunk_id: c for c in found}.values())[: self.s.evidence_top_k + 2]
        finally:
            index.close()

        # 正向檢索：以「教材」查「課程重點」，作為準確性審查的參考依據
        related: list[list[Objective]] = []
        for batch in batches:
            objs: dict[str, Objective] = {}
            for c in batch:
                for o in self.store.search(c.text, self.s.curriculum_top_k, unit_id=unit.unit_id):
                    objs[o.id] = o
            related.append(list(objs.values()))
        return evidence, related

    # ---------- 主流程 ----------
    async def run(self, filename: str, data: bytes, unit_id: str, include_extension: bool = False,
                  objective_ids: list[str] | None = None, report_lang: str = "zh",
                  progress: Callable[[str, float], None] | None = None,
                  on_text: Callable[[str], None] | None = None) -> ReviewReport:
        report = progress or (lambda stage, value: None)
        unit = self.store.get_unit(unit_id)
        subject = self.subjects.get(unit.subject)
        mode = subject.mode if subject else "content"
        system = build_system_prompt(subject.profile if subject else "science", report_lang)
        if objective_ids:
            # 老師自選學習重點（語文科必須自選；其他科可選擇只檢查部分重點）
            wanted = set(objective_ids)
            objectives = [o for o in unit.objectives if o.id in wanted]
        elif mode == "skills":
            raise ReviewInputError("語文科須先選擇這份教材針對的學習重點")
        else:
            # 官方要求必須教授核心部分；延展部分按校本需要選擇性檢查
            objectives = [o for o in unit.objectives if include_extension or o.tier == "core"]
        if not objectives:
            raise ReviewInputError("沒有可檢查的學習重點")
        report("parse", 0.05)
        pages = await asyncio.to_thread(parse_document, filename, data)
        full_text = "\n\n".join(p.text for p in pages)
        material_lang = detect_language(full_text)
        if on_text:
            on_text(full_text)
        chunks = chunk_pages(pages, self.s.chunk_size, self.s.chunk_overlap)
        if not chunks:
            raise ParseError("教材沒有可審查的文字內容")

        report("rules", 0.1)
        script_findings, script_summary = self.script.check(chunks, report_lang)
        term_findings, hints = self.terms.check(chunks)
        rule_findings = script_findings + term_findings
        batches = list(_batched(chunks, self.s.accuracy_batch_size))
        report("retrieve", 0.15)
        evidence, related = await asyncio.to_thread(self._retrieve, unit, objectives, chunks, batches)

        cov_groups = list(_batched(objectives, self.s.coverage_group_size))
        cov_coros = []
        for g in cov_groups:
            pairs = [(o, evidence[o.id]) for o in g]
            cov_coros.append(self.reviewer.assess_coverage(
                system, build_coverage_prompt(unit, pairs, mode), pairs))
        acc_coros = [self.reviewer.review_accuracy(system, build_accuracy_prompt(
            b, related[i], [h for c in b for h in hints.get(c.chunk_id, [])]), b)
            for i, b in enumerate(batches)]

        total, done = len(cov_coros) + len(acc_coros), 0
        report(f"ai:0/{total}", 0.2)

        async def tracked(coro):
            nonlocal done
            try:
                return await coro
            finally:
                done += 1
                report(f"ai:{done}/{total}", 0.2 + 0.75 * done / total)

        results = await asyncio.gather(*(tracked(c) for c in cov_coros + acc_coros),
                                       return_exceptions=True)
        report("assemble", 0.97)
        return self._assemble(unit, objectives, chunks, results[: len(cov_coros)],
                              results[len(cov_coros):], rule_findings, script_summary,
                              include_extension, material_lang, report_lang,
                              selected=bool(objective_ids))

    # ---------- 驗證與組裝 ----------
    def _assemble(self, unit, objectives, chunks, cov_results, acc_results, rule_findings,
                  script_summary, include_extension, material_lang="zh", report_lang="zh",
                  selected=False) -> ReviewReport:
        msg = _MSG.get(report_lang, _MSG["zh"])
        warnings: list[str] = []
        if not unit.verified:
            warnings.append(msg["unverified"])
        if not script_summary.passed:
            warnings.append(msg["script_fail"].format(n=script_summary.simplified_chars,
                                                      s=script_summary.sentences))
        if material_lang != unit.language:
            names = msg["lang_names"]
            warnings.append(msg["cross_lang"].format(m=names[material_lang], c=names[unit.language]))
        chunk_map = {c.chunk_id: c for c in chunks}
        norm_full = normalize_for_match("\n".join(c.text for c in chunks))
        unit_obj_ids = {o.id for o in objectives}

        # 1) 覆蓋結果：只接受本單元的 ID；模型漏答的補上 unassessed
        coverage: dict[str, CoverageItem] = {}
        for r in cov_results:
            if isinstance(r, Exception):
                warnings.append(msg["cov_fail"].format(e=r))
                continue
            for item in r.items:
                if item.objective_id not in unit_obj_ids:
                    warnings.append(msg["fake_id"].format(i=item.objective_id))
                    continue
                item.evidence_chunk_ids = [i for i in item.evidence_chunk_ids if i in chunk_map]
                coverage[item.objective_id] = item
        cov_list = []
        for o in objectives:
            item = coverage.get(o.id) or CoverageItem(
                objective_id=o.id, status=CoverageStatus.unassessed, rationale=msg["unassessed"])
            cov_list.append(CoverageResult(**item.model_dump(), objective_text=o.text))

        # 2) 收集所有 finding
        raw: list[tuple[str, Finding]] = [("rule", f) for f in rule_findings]
        for cr in cov_list:
            if cr.status in (CoverageStatus.missing, CoverageStatus.partial):
                missing = cr.status == CoverageStatus.missing
                raw.append(("llm", Finding(
                    category=Category.omission,
                    severity=Severity.high if missing else Severity.medium,
                    chunk_id=cr.evidence_chunk_ids[0] if cr.evidence_chunk_ids else None,
                    issue=msg["missing" if missing else "partial"].format(t=cr.objective_text, r=cr.rationale),
                    suggestion=msg["with_patch"] if cr.patch else msg["no_patch"],
                    patch=cr.patch, curriculum_refs=[cr.objective_id], confidence=0.8)))
        for r in acc_results:
            if isinstance(r, Exception):
                warnings.append(msg["acc_fail"].format(e=r))
                continue
            # omission 由覆蓋流程負責；simplified 只由規則引擎判斷
            raw.extend(("llm", f) for f in r.findings
                       if f.category not in (Category.omission, Category.simplified))

        # 3) 驗證、降級、去重
        final: list[LocatedFinding] = []
        seen: set[tuple] = set()
        for source, f in raw:
            f.curriculum_refs = [i for i in f.curriculum_refs if i in self.store.objectives]
            if f.chunk_id not in chunk_map:
                f.chunk_id = None
            quote_ok = not f.original_text or quote_in_text(f.original_text, norm_full)
            anchor_ok = not (f.patch and f.patch.anchor_text) or quote_in_text(f.patch.anchor_text, norm_full)
            confidence = f.confidence * (1.0 if quote_ok else 0.6)
            # 規則引擎結果是確定性的，不受信心門檻過濾（簡體字必須觸發）
            if source != "rule" and confidence < self.s.min_confidence:
                continue
            key = (f.category, normalize_for_match(f.original_text))
            if f.original_text and key in seen:
                continue
            seen.add(key)
            final.append(LocatedFinding(
                **{**f.model_dump(), "confidence": round(confidence, 2)},
                finding_id="", page=chunk_map[f.chunk_id].page if f.chunk_id else None,
                source=source, quote_verified=quote_ok, anchor_verified=anchor_ok))

        # 簡體字永遠排最前：屬發佈前必須修正的硬性問題
        final.sort(key=lambda x: (x.category != Category.simplified,
                                  _SEVERITY_ORDER[x.severity], -x.confidence))
        for n, f in enumerate(final, 1):
            f.finding_id = f"F{n:03d}"

        summary = {s.value: 0 for s in CoverageStatus}
        for cr in cov_list:
            summary[cr.status.value] += 1

        return ReviewReport(unit_id=unit.unit_id, unit_title=unit.title, subject=unit.subject,
                            material_language=material_lang, report_lang=report_lang,
                            selected_objectives=len(objectives) if selected else 0,
                            curriculum_id=unit.curriculum_id, unit_verified=unit.verified,
                            include_extension=include_extension, model=self.s.llm_model,
                            script_check=script_summary, coverage_summary=summary,
                            coverage=cov_list, findings=final, warnings=warnings)
