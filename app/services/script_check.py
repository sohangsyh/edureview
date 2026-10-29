"""字形檢查（硬性規則）：香港教材不可出現簡體字。

- 不經 LLM、不受信心門檻過濾、結果可重現。
- 依 OpenCC 字表 STCharacters.txt 自動分類，不靠人手白名單：
  * 純簡體字：繁體候選不包括自己（热→熱、发→發／髮）→ 一律觸發，嚴重度 high
  * 簡繁共用字：繁體候選包括自己（后→後／后、干→幹／乾／干、里→裏／里）
    → 以「先轉簡體、再按詞組轉港繁」判斷語境；若語境要求轉換即觸發。
       同句有純簡體字 → high；整句是繁體 → medium「疑似，請確認」（例如姓氏「范」「余」）
"""
import re
from dataclasses import dataclass
from pathlib import Path

from app.schemas import Category, Chunk, Finding, Patch, PatchAction, ScriptCheckSummary, Severity

try:
    import opencc
    from opencc import OpenCC
except ImportError as exc:  # 字形檢查是硬性要求，缺少依賴時拒絕啟動，而不是靜默略過
    raise RuntimeError("缺少 opencc-python-reimplemented，簡體字檢查無法運作") from exc


def _load_tables() -> tuple[dict[str, str], set[str]]:
    path = Path(opencc.__file__).parent / "dictionary" / "STCharacters.txt"
    simplified_only: dict[str, str] = {}
    dual_use: set[str] = set()
    for line in path.read_text("utf-8").splitlines():
        if "\t" not in line:
            continue
        key, cands = line.split("\t", 1)
        cands = cands.split()
        if key in cands:
            if len(cands) > 1:
                dual_use.add(key)
        else:
            simplified_only[key] = cands[0]
    return simplified_only, dual_use


SIMPLIFIED_ONLY, DUAL_USE = _load_tables()
# 共用字的例外：轉換結果在香港同樣通行，不算簡體（了解／瞭解）
_DUAL_USE_EXCEPTIONS = {"了"}
_T2S, _S2HK = OpenCC("t2s"), OpenCC("s2hk")
_SENTENCE = re.compile(r"[^。！？；!?;\n]+[。！？；!?;]?")


@dataclass
class ScriptHit:
    index: int
    char: str
    replacement: str
    certain: bool  # True = 純簡體字


def scan_sentence(sentence: str) -> list[ScriptHit]:
    contextual = _S2HK.convert(_T2S.convert(sentence))
    aligned = len(contextual) == len(sentence)
    hits: list[ScriptHit] = []
    for i, ch in enumerate(sentence):
        if ch in SIMPLIFIED_ONLY:
            rep = contextual[i] if aligned and contextual[i] != ch else SIMPLIFIED_ONLY[ch]
            hits.append(ScriptHit(i, ch, rep, True))
        elif ch in DUAL_USE and ch not in _DUAL_USE_EXCEPTIONS and aligned and contextual[i] != ch:
            hits.append(ScriptHit(i, ch, contextual[i], False))
    return hits


class ScriptChecker:
    def check(self, chunks: list[Chunk], lang: str = "zh") -> tuple[list[Finding], ScriptCheckSummary]:
        findings: list[Finding] = []
        seen: set[str] = set()   # 切塊有重疊，同一句只報一次
        total_chars = 0
        for chunk in chunks:
            for m in _SENTENCE.finditer(chunk.text):
                sentence = m.group().strip()
                if not sentence or sentence in seen:
                    continue
                hits = scan_sentence(sentence)
                if not hits:
                    continue
                seen.add(sentence)
                total_chars += len(hits)
                sentence_has_certain = any(h.certain for h in hits)
                fixed = list(sentence)
                for h in hits:
                    fixed[h.index] = h.replacement
                pairs = "、".join(dict.fromkeys(f"{h.char}→{h.replacement}" for h in hits))
                if lang == "en":
                    label = ("simplified Chinese characters" if sentence_has_certain else
                             "possible simplified characters (shared forms; check context, e.g. the surnames 范 and 余 are correct)")
                    issue = f"Found {label}, {len(hits)} character(s): {pairs}"
                    suggestion = "Hong Kong materials must use traditional characters. Replace the sentence with the patch and check the conversion."
                else:
                    label = "簡體字" if sentence_has_certain else "疑似簡體字（簡繁共用字，請按語境確認，例如姓氏「范」「余」可保留）"
                    issue = f"發現{label}，共 {len(hits)} 字：{pairs}"
                    suggestion = "香港教材必須使用繁體字。請以補丁替換整句，並人工覆核轉換結果。"
                findings.append(Finding(
                    category=Category.simplified,
                    severity=Severity.high if sentence_has_certain else Severity.medium,
                    chunk_id=chunk.chunk_id,
                    original_text=sentence,
                    issue=issue,
                    suggestion=suggestion,
                    patch=Patch(action=PatchAction.replace, anchor_text=sentence, new_text="".join(fixed)),
                    confidence=1.0 if sentence_has_certain else 0.8,
                ))
        summary = ScriptCheckSummary(simplified_chars=total_chars, sentences=len(findings),
                                     passed=total_chars == 0)
        return findings, summary
