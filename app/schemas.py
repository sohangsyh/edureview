"""資料模型。LLM 輸出模型（Finding / CoverageItem）同時用作 tool input_schema，
因此 Field description 會直接被模型看到，請用清晰中文撰寫。"""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------- 內部資料 ----------
class Chunk(BaseModel):
    chunk_id: str
    page: int | None = None
    text: str


class Objective(BaseModel):
    """一項課程要求。對應官方文件「學生應能」欄的一項學習成果，或一項「重點實驗」。"""
    id: str
    text: str = Field(description="逐字摘自官方文件")
    subtopic: str = Field(default="", description="例如「5.4 地球資源」")
    tier: Literal["core", "extension"] = "core"      # 官方文件以藍色斜體標示延展部分
    kind: Literal["outcome", "key_experiment"] = "outcome"
    skill: bool = False                              # 官方文件以 (S) 標示的技能類學習成果
    page: int | None = None                          # 官方 PDF 頁碼，作引文依據
    key_terms: list[str] = []
    notes: str = ""


class Unit(BaseModel):
    unit_id: str
    subject: str = Field(default="", description="科目代碼，對應 data/subjects.json，例如 SCI")
    curriculum_id: str = Field(default="", description="例如 SCI-S13-2025")
    unit_no: int | None = None
    title: str
    levels: list[Literal["S1", "S2", "S3"]] = []    # 課程文件建議或老師設定的年級；空白 = 未指定
    language: Literal["zh", "en"] = "zh"             # 課程資料的語言（中文版或英文版課程文件）
    source: str = ""
    source_url: str = ""
    verified: bool = False       # 必須由科任老師逐項核對後才設為 true
    verified_by: str = ""
    objectives: list[Objective]


# ---------- LLM 輸出 ----------
class Category(str, Enum):
    simplified = "simplified"        # 系統規則專用：簡體字
    omission = "omission"
    factual_error = "factual_error"
    imprecise = "imprecise"
    terminology = "terminology"
    language = "language"
    safety = "safety"


class Severity(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class PatchAction(str, Enum):
    insert_after = "insert_after"
    replace = "replace"
    delete = "delete"


class Patch(BaseModel):
    action: PatchAction = Field(description="insert_after：在 anchor_text 之後插入；replace：以 new_text 取代 anchor_text；delete：刪除 anchor_text")
    anchor_text: str = Field(description="逐字摘自教材的原文（含標點）。insert_after 時為插入位置前的句子；找不到合適位置可留空字串")
    new_text: str = Field(description="可直接貼入教材的香港繁體中文文字；delete 時留空字串")


class Finding(BaseModel):
    category: Category
    severity: Severity
    chunk_id: str | None = Field(default=None, description="問題所在的教材片段 ID")
    original_text: str = Field(default="", description="逐字複製的問題原文；omission 可留空")
    issue: str = Field(description="問題說明：錯在哪裏、為何會誤導中一學生")
    suggestion: str = Field(description="修改建議（給老師看的說明）")
    patch: Patch | None = None
    curriculum_refs: list[str] = Field(default=[], description="相關課程重點 ID，只可使用 <curriculum> 中出現的 ID")
    confidence: float = Field(ge=0, le=1, description="你對此問題成立的把握，0 至 1")


class FindingsOutput(BaseModel):
    findings: list[Finding]


class CoverageStatus(str, Enum):
    covered = "covered"
    partial = "partial"
    missing = "missing"
    unassessed = "unassessed"   # 系統用：模型漏答時填入，模型不應使用


class CoverageItem(BaseModel):
    objective_id: str
    status: CoverageStatus = Field(description="covered / partial / missing（不要使用 unassessed）")
    evidence_chunk_ids: list[str] = Field(default=[], description="支持判斷的教材片段 ID")
    rationale: str = Field(description="判斷理由，一至兩句")
    patch: Patch | None = Field(default=None, description="status 為 partial 或 missing 時必須提供補丁")


class CoverageOutput(BaseModel):
    items: list[CoverageItem]


# ---------- API 回應 ----------
class CoverageResult(CoverageItem):
    objective_text: str


class LocatedFinding(Finding):
    finding_id: str
    page: int | None = None
    source: str = Field(description="llm 或 rule（規則引擎）")
    quote_verified: bool = Field(description="original_text 是否能在教材中逐字找到")
    anchor_verified: bool = Field(description="patch.anchor_text 是否能在教材中逐字找到")


class ScriptCheckSummary(BaseModel):
    simplified_chars: int
    sentences: int
    passed: bool


class ReviewReport(BaseModel):
    unit_id: str
    unit_title: str
    subject: str = ""
    material_language: Literal["zh", "en"] = "zh"
    report_lang: Literal["zh", "en"] = "zh"
    selected_objectives: int = 0
    curriculum_id: str = ""
    unit_verified: bool = False
    include_extension: bool = False
    model: str
    script_check: ScriptCheckSummary
    coverage_summary: dict[str, int]
    coverage: list[CoverageResult]
    findings: list[LocatedFinding]
    warnings: list[str] = []
