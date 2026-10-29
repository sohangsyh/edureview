"""科目登記（data/subjects.json）。"""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

LEVELS = ["S1", "S2", "S3"]


class SubjectDoc(BaseModel):
    curriculum_id: str
    title_zh: str
    title_en: str = ""
    status: str = ""
    pdf_zh: str | None = None
    pdf_en: str | None = None
    notes_url: str | None = None
    unit_heading_regex: dict[str, str] | None = None


class Glossary(BaseModel):
    title: str
    url: str


class Subject(BaseModel):
    code: str
    kla_zh: str
    kla_en: str
    name_zh: str
    name_en: str
    mode: Literal["content", "skills"]
    profile: str
    documents_page: str = ""
    documents: list[SubjectDoc] = []
    glossaries: list[Glossary] = []


def load_subjects(path: Path) -> dict[str, Subject]:
    raw = json.loads(Path(path).read_text("utf-8"))
    return {s["code"]: Subject.model_validate(s) for s in raw["subjects"]}


def find_document(subjects: dict[str, Subject], curriculum_id: str) -> tuple[Subject, SubjectDoc] | None:
    for s in subjects.values():
        for d in s.documents:
            if d.curriculum_id == curriculum_id:
                return s, d
    return None
