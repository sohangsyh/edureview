"""網站入口。

  uvicorn app.main:app --reload            # 正式模式（需 ANTHROPIC_API_KEY）
  DEMO_MODE=true uvicorn app.main:app      # 離線示範模式

網頁在 /，API 在 /api。審查以背景工作執行，前端輪詢進度。
"""
import asyncio
import hashlib
import secrets
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.db import Database
from app.schemas import ReviewReport
from app.services.curriculum_store import CurriculumStore, build_embedding_fn
from app.services.parser import ParseError
from app.services.patcher import apply_patches
from app.services.pipeline import ReviewInputError, ReviewPipeline
from app.services.renderer import render_markdown
from app.services.terminology import TerminologyChecker
from app.subjects import LEVELS, Subject, load_subjects

STATIC_DIR = Path(__file__).parent / "static"
ACCESS_COOKIE = "hkr_access"


@dataclass
class Services:
    settings: Settings
    store: CurriculumStore
    pipeline: ReviewPipeline
    db: Database
    subjects: dict[str, Subject]


def build_services(settings: Settings) -> Services:
    if settings.demo_mode:
        from app.services.offline import CharNgramEmbedding, OfflineReviewer
        embed_fn, reviewer = CharNgramEmbedding(), OfflineReviewer()
        # 示範模式的向量與正式模式不相容，分開存放
        settings = settings.model_copy(update={"chroma_path": settings.chroma_path.with_name("chroma-demo")})
    else:
        from app.services.reviewer import LLMReviewer
        embed_fn, reviewer = build_embedding_fn(settings.embedding_model), LLMReviewer(settings)
    store = CurriculumStore(settings, embed_fn)
    store.ensure_indexed()
    subjects = load_subjects(settings.subjects_path)
    terms = TerminologyChecker(settings.glossary_dir, store.official_texts())
    pipeline = ReviewPipeline(settings, store, reviewer, terms, embed_fn, subjects)
    return Services(settings, store, pipeline, Database(settings.db_path), subjects)


def _token(code: str) -> str:
    return hashlib.sha256(f"hkr:{code}".encode()).hexdigest()


def create_app(services: Services | None = None) -> FastAPI:
    tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.svc = services or build_services(get_settings())
        yield

    app = FastAPI(title="香港初中教材審查平台", lifespan=lifespan)

    def svc(request: Request) -> Services:
        return request.app.state.svc

    # ---------- 存取碼 ----------
    @app.middleware("http")
    async def access_guard(request: Request, call_next):
        s = request.app.state.svc.settings
        path = request.url.path
        open_paths = ("/api/config", "/api/login", "/health")
        if s.access_code and path.startswith("/api") and path not in open_paths:
            if not secrets.compare_digest(request.cookies.get(ACCESS_COOKIE, ""), _token(s.access_code)):
                return JSONResponse({"detail": "請先輸入存取碼"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/config")
    async def config(request: Request):
        s = svc(request).settings
        return {"demo_mode": s.demo_mode, "access_required": bool(s.access_code),
                "model": "示範模式（未使用 AI）" if s.demo_mode else s.llm_model,
                "max_upload_mb": s.max_upload_mb}

    class Login(BaseModel):
        code: str

    @app.post("/api/login")
    async def login(request: Request, body: Login):
        s = svc(request).settings
        if not s.access_code or not secrets.compare_digest(body.code, s.access_code):
            raise HTTPException(401, "存取碼不正確")
        resp = JSONResponse({"ok": True})
        resp.set_cookie(ACCESS_COOKIE, _token(s.access_code), httponly=True, samesite="lax",
                        max_age=60 * 60 * 24 * 30)
        return resp

    # ---------- 科目 ----------
    @app.get("/api/subjects")
    async def list_subjects(request: Request):
        services_ = svc(request)
        out = []
        for subj in services_.subjects.values():
            units = [u for u in services_.store.units.values() if u.subject == subj.code]
            by_level = {lv: {"units": sum(lv in u.levels for u in units),
                             "verified": sum(lv in u.levels and u.verified for u in units)} for lv in LEVELS}
            by_level["unassigned"] = {"units": sum(not u.levels for u in units),
                                      "verified": sum(not u.levels and u.verified for u in units)}
            out.append({**subj.model_dump(), "unit_count": len(units), "by_level": by_level})
        return out

    # ---------- 課程單元 ----------
    @app.get("/api/units")
    async def list_units(request: Request, subject: str | None = None, level: str | None = None):
        units = [u for u in svc(request).store.units.values()
                 if (not subject or u.subject == subject) and (not level or level in u.levels)]
        return [{"unit_id": u.unit_id, "subject": u.subject, "levels": u.levels, "language": u.language,
                 "curriculum_id": u.curriculum_id, "unit_no": u.unit_no,
                 "title": u.title, "verified": u.verified, "verified_by": u.verified_by,
                 "core_count": sum(o.tier == "core" for o in u.objectives),
                 "extension_count": sum(o.tier == "extension" for o in u.objectives),
                 "unlocated_count": sum(o.page is None for o in u.objectives),
                 "source": u.source, "source_url": u.source_url}
                for u in units]

    @app.get("/api/units/{unit_id}")
    async def get_unit(request: Request, unit_id: str):
        try:
            return svc(request).store.get_unit(unit_id)
        except KeyError:
            raise HTTPException(404, "找不到此單元")

    class VerifyBody(BaseModel):
        verified: bool
        teacher: str = ""
        levels: list[str] | None = None

    @app.post("/api/units/{unit_id}/verify")
    async def verify_unit(request: Request, unit_id: str, body: VerifyBody):
        store = svc(request).store
        try:
            unit = store.get_unit(unit_id)
        except KeyError:
            raise HTTPException(404, "找不到此單元")
        if body.verified and not body.teacher.strip():
            raise HTTPException(422, "請填寫核對老師姓名")
        if body.levels is not None:
            if any(lv not in LEVELS for lv in body.levels):
                raise HTTPException(422, "年級只可為 S1、S2、S3")
            unit.levels = sorted(set(body.levels))
        unit.verified, unit.verified_by = body.verified, body.teacher.strip() if body.verified else ""
        path = store.unit_path(unit_id)
        path.write_text(unit.model_dump_json(indent=2), "utf-8")
        return {"unit_id": unit_id, "verified": unit.verified, "verified_by": unit.verified_by, "levels": unit.levels}

    # ---------- 審查 ----------
    async def run_job(services_: Services, rid: str, filename: str, data: bytes, unit_id: str, ext: bool,
                      objective_ids: list[str] | None, report_lang: str):
        db = services_.db
        try:
            report = await services_.pipeline.run(
                filename, data, unit_id, ext, objective_ids, report_lang,
                progress=lambda stage, value: db.update_progress(rid, stage, value),
                on_text=lambda text: db.set_material(rid, text))
            db.finish(rid, report.model_dump(mode="json"))
        except (ParseError, ReviewInputError) as exc:
            db.fail(rid, str(exc))
        except Exception as exc:  # noqa: BLE001  背景工作必須把錯誤寫回，否則前端永遠等待
            db.fail(rid, f"審查失敗：{type(exc).__name__}：{exc}")

    @app.post("/api/reviews", status_code=202)
    async def create_review(request: Request, file: UploadFile = File(...), unit_id: str = Form(...),
                            include_extension: bool = Form(False), teacher: str = Form(""),
                            objective_ids: str = Form(""), report_lang: str = Form("zh")):
        services_ = svc(request)
        try:
            unit = services_.store.get_unit(unit_id)
        except KeyError:
            raise HTTPException(404, "找不到此單元")
        if report_lang not in ("zh", "en"):
            raise HTTPException(422, "report_lang 只可為 zh 或 en")
        ids = [i for i in objective_ids.split(",") if i] if objective_ids else []
        unknown = [i for i in ids if i not in {o.id for o in unit.objectives}]
        if unknown:
            raise HTTPException(422, f"不屬於此單元的學習重點：{', '.join(unknown)}")
        subject = services_.subjects.get(unit.subject)
        if subject and subject.mode == "skills" and not ids:
            raise HTTPException(422, "語文科須選擇這份教材針對的學習重點")
        name = file.filename or "教材"
        if not name.lower().endswith((".pdf", ".txt", ".md")):
            raise HTTPException(422, "只支援 PDF、TXT 或 MD 檔")
        data = await file.read()
        if len(data) > services_.settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"檔案超過 {services_.settings.max_upload_mb} MB")
        rid = uuid.uuid4().hex[:12]
        services_.db.create_review(rid, name, unit_id, unit.title, include_extension, teacher.strip(),
                                   unit.subject, report_lang)
        task = asyncio.create_task(run_job(services_, rid, name, data, unit_id, include_extension,
                                           ids or None, report_lang))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return {"id": rid}

    @app.get("/api/reviews")
    async def list_reviews(request: Request):
        return svc(request).db.list_reviews()

    def _get(request: Request, rid: str) -> dict:
        row = svc(request).db.get_review(rid)
        if not row:
            raise HTTPException(404, "找不到此審查記錄")
        return row

    @app.get("/api/reviews/{rid}")
    async def get_review(request: Request, rid: str):
        row = _get(request, rid)
        row.pop("material", None)
        row["feedback"] = svc(request).db.get_feedback(rid)
        return row

    @app.delete("/api/reviews/{rid}")
    async def delete_review(request: Request, rid: str):
        if not svc(request).db.delete_review(rid):
            raise HTTPException(404, "找不到此審查記錄")
        return {"ok": True}

    class FeedbackBody(BaseModel):
        decision: str | None   # accepted / rejected / None（取消）
        comment: str = ""
        teacher: str = ""

    @app.put("/api/reviews/{rid}/feedback/{fid}")
    async def put_feedback(request: Request, rid: str, fid: str, body: FeedbackBody):
        _get(request, rid)
        if body.decision not in ("accepted", "rejected", None):
            raise HTTPException(422, "decision 只可為 accepted、rejected 或 null")
        svc(request).db.set_feedback(rid, fid, body.decision, body.comment, body.teacher)
        return {"ok": True}

    def _attachment(text: str, filename: str) -> PlainTextResponse:
        return PlainTextResponse(text, media_type="text/plain; charset=utf-8", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})

    @app.get("/api/reviews/{rid}/markdown")
    async def review_markdown(request: Request, rid: str):
        row = _get(request, rid)
        if not row["report"]:
            raise HTTPException(409, "審查尚未完成")
        return _attachment(render_markdown(ReviewReport.model_validate(row["report"])),
                           f"審查報告_{Path(row['filename']).stem}.md")

    @app.get("/api/reviews/{rid}/revised")
    async def revised_text(request: Request, rid: str):
        """套用老師已接受的補丁，下載修訂版教材文字。"""
        row = _get(request, rid)
        if not row["report"]:
            raise HTTPException(409, "審查尚未完成")
        feedback = svc(request).db.get_feedback(rid)
        accepted = [f for f in row["report"]["findings"]
                    if feedback.get(f["finding_id"], {}).get("decision") == "accepted"]
        if not accepted:
            raise HTTPException(409, "尚未接受任何修改建議")
        result = apply_patches(row["material"], accepted)
        note = ""
        if result.unapplied:
            note = (f"\n\n【未能自動套用：{'、'.join(result.unapplied)}。原文位置已改變或找不到，"
                    f"請參考審查報告手動修改。】")
        return _attachment(result.text + note, f"修訂版_{Path(row['filename']).stem}.txt")

    # 網頁（放在最後，以免蓋過 /api）
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
