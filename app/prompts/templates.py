"""組裝 user message。用 XML 標籤清楚分隔「課程依據」與「被審查內容」。"""
from html import escape

from app.schemas import Chunk, Objective, Unit
from app.services.terminology import TermHint


def _chunk_xml(c: Chunk) -> str:
    page = f' page="{c.page}"' if c.page is not None else ""
    return f'<chunk id="{c.chunk_id}"{page}>\n{escape(c.text, quote=False)}\n</chunk>'


def _objective_xml(o: Objective) -> str:
    attrs = f' kind="{o.kind}" tier="{o.tier}"'
    if o.subtopic:
        attrs += f' subtopic="{escape(o.subtopic)}"'
    if o.skill:
        attrs += ' skill="true"'
    if o.key_terms:
        attrs += f' key_terms="{"、".join(o.key_terms)}"'
    return f'<objective id="{o.id}"{attrs}>{escape(o.text, quote=False)}</objective>'


_SKILLS_NOTE = """
0. 這是語文科。<curriculum> 中的學習重點是老師為這份教材選定的目標：判斷教材是否真正讓學生學習及練習該能力。
   只解釋知識而沒有練習，屬 partial。"""


def build_coverage_prompt(unit: Unit, pairs: list[tuple[Objective, list[Chunk]]], mode: str = "content") -> str:
    curriculum = "\n".join(_objective_xml(o) for o, _ in pairs)
    material = "\n".join(
        f'<evidence objective_id="{o.id}">\n' + "\n".join(_chunk_xml(c) for c in chunks) + "\n</evidence>"
        for o, chunks in pairs
    )
    return f"""<task>coverage</task>
<unit id="{unit.unit_id}">{escape(unit.title)}</unit>

<curriculum>
{curriculum}
</curriculum>

<material>
{material}
</material>

<instructions>{_SKILLS_NOTE if mode == "skills" else ""}
1. <curriculum> 中每一個 objective 都必須輸出剛好一個 item，不可遺漏，不可新增。
2. <material> 中每組 <evidence> 是系統按語意為該學習重點檢索出的教材片段，可能不完整；若片段與學習重點無關，視作找不到。
3. status：covered = 有解釋且正確；partial = 只提及名詞或解釋不完整；missing = 找不到。
4. kind="key_experiment" 是教育局指定的重點實驗：教材須有該實驗的目的、步驟（或明確的實驗安排）及安全提示才算 covered；只在正文提及相關概念屬 partial。
5. skill="true" 是技能類學習成果：教材須有讓學生實際操作或練習的活動才算 covered。
6. partial 或 missing 必須附 patch：action 用 insert_after，anchor_text 選最適合插入位置的原文句子（逐字複製），沒有合適位置則留空字串。
7. 完成後呼叫 submit_coverage 工具。
</instructions>"""


def build_accuracy_prompt(chunks: list[Chunk], related: list[Objective], hints: list[TermHint]) -> str:
    curriculum = "\n".join(_objective_xml(o) for o in related) or "（無）"
    material = "\n".join(_chunk_xml(c) for c in chunks)
    hint_xml = "\n".join(
        f'<hint chunk_id="{h.chunk_id}" term="{h.term}" hk_preferred="{h.preferred}">{escape(h.note)}</hint>'
        for h in hints
    ) or "（無）"
    return f"""<task>accuracy</task>

<curriculum>
{curriculum}
</curriculum>

<material>
{material}
</material>

<term_hints>
{hint_xml}
</term_hints>

<instructions>
1. 逐段審查 factual_error、imprecise、terminology、language、safety 五類問題。不要報告 omission 及 simplified（另有流程處理）。
2. <curriculum> 只作參考，用於填寫 curriculum_refs；不是本任務要檢查的覆蓋清單。
3. 每項 finding 必須填寫 chunk_id。
4. <term_hints> 的詞語須按上下文判斷，只在確屬不當用法時才報告。其他明顯的非香港用語已由規則引擎處理，你不必重複報告「視頻」「信息」一類詞語。
5. 完成後呼叫 submit_findings 工具。
</instructions>"""
