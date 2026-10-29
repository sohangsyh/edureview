"""用語詞表檢查（中文：香港用語；英文：英式拼寫）。

- 不受語境影響的詞（如「視頻」「color」）→ 直接產生 finding。
- 受語境影響的詞（如「質量」「meter」）→ 交給 LLM 判斷。
- 官方用語保護：凡在已載入的教育局課程文件中出現的詞，自動降為「語境詞」。
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.schemas import Category, Chunk, Finding, Patch, PatchAction, Severity


@dataclass
class GlossaryEntry:
    term: str
    preferred: str
    note: str = ""
    context_sensitive: bool = False
    lang: str = "zh"


@dataclass
class TermHint:
    chunk_id: str
    term: str
    preferred: str
    note: str


def _window(text: str, start: int, end: int, pad: int = 12, words: bool = False) -> tuple[str, int, int]:
    """取匹配位置前後的上下文作為補丁錨點（不跨行）。英文會擴展到完整的詞。"""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end == -1 else line_end
    s, e = max(line_start, start - pad), min(line_end, end + pad)
    if words:
        while s > line_start and not text[s - 1].isspace():
            s -= 1
        while e < line_end and not text[e].isspace():
            e += 1
    return text[s:e].strip(), start - s - (len(text[s:e]) - len(text[s:e].lstrip())), end - s - (len(text[s:e]) - len(text[s:e].lstrip()))


def _match_case(original: str, replacement: str) -> str:
    if original.isupper() and len(original) > 1:
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


class TerminologyChecker:
    def __init__(self, glossary: Path, official_texts: Iterable[str] = ()):
        """glossary 可以是一個 JSON 檔或一個資料夾（載入其中所有 *.json）。"""
        glossary = Path(glossary)
        files = sorted(glossary.glob("*.json")) if glossary.is_dir() else [glossary]
        self.entries: dict[str, GlossaryEntry] = {}
        for f in files:
            for e in json.loads(f.read_text("utf-8")):
                entry = GlossaryEntry(**e)
                self.entries[entry.term.lower() if entry.lang == "en" else entry.term] = entry

        official = "\n".join(official_texts)
        official_lower = official.lower()
        self.protected: list[str] = []
        for e in self.entries.values():
            hit = (re.search(rf"\b{re.escape(e.term)}\b", official_lower) if e.lang == "en"
                   else e.term in official)
            if not e.context_sensitive and hit:
                e.context_sensitive = True
                e.note = f"{e.note}（此詞見於已載入的教育局課程文件，屬官方用語，請按語境判斷）".strip()
                self.protected.append(e.term)

        zh_terms = sorted((e.term for e in self.entries.values() if e.lang == "zh"), key=len, reverse=True)
        en_terms = sorted((e.term for e in self.entries.values() if e.lang == "en"), key=len, reverse=True)
        self.zh_pattern = re.compile("|".join(map(re.escape, zh_terms))) if zh_terms else None
        self.en_pattern = (re.compile(r"\b(?:" + "|".join(map(re.escape, en_terms)) + r")\b", re.IGNORECASE)
                           if en_terms else None)

    def _lookup(self, matched: str) -> GlossaryEntry:
        return self.entries.get(matched) or self.entries[matched.lower()]

    def check(self, chunks: list[Chunk]) -> tuple[list[Finding], dict[str, list[TermHint]]]:
        findings: list[Finding] = []
        hints: dict[str, list[TermHint]] = {}
        first_hit: dict[str, tuple[Chunk, re.Match]] = {}
        counts: dict[str, int] = {}

        for chunk in chunks:
            for pattern in (self.zh_pattern, self.en_pattern):
                if not pattern:
                    continue
                for m in pattern.finditer(chunk.text):
                    entry = self._lookup(m.group())
                    if entry.context_sensitive:
                        hints.setdefault(chunk.chunk_id, []).append(
                            TermHint(chunk.chunk_id, entry.term, entry.preferred, entry.note))
                        continue
                    counts[entry.term] = counts.get(entry.term, 0) + 1
                    first_hit.setdefault(entry.term, (chunk, m))

        for term, (chunk, m) in first_hit.items():
            entry = self.entries.get(term) or self.entries[term.lower()]
            replacement = _match_case(m.group(), entry.preferred.split("／")[0])
            snippet, a, b = _window(chunk.text, m.start(), m.end(), words=entry.lang == "en")
            if entry.lang == "en":
                issue = f"'{m.group()}' is not British spelling ({counts[term]} occurrence(s)). {entry.note}"
                suggestion = f"Use '{entry.preferred}' and keep the spelling consistent throughout."
            else:
                issue = f"「{term}」並非香港常用詞（全文共 {counts[term]} 處）。{entry.note}".strip()
                suggestion = f"建議改用「{entry.preferred}」，並全文統一。"
            findings.append(Finding(
                category=Category.terminology, severity=Severity.low, chunk_id=chunk.chunk_id,
                original_text=snippet, issue=issue, suggestion=suggestion,
                patch=Patch(action=PatchAction.replace, anchor_text=snippet,
                            new_text=snippet[:a] + replacement + snippet[b:]),
                confidence=0.9,
            ))
        return findings, hints
