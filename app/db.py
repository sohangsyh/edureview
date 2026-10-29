"""SQLite 儲存：審查記錄、老師回饋。MVP 用標準庫 sqlite3，日後可換 PostgreSQL。

私隱：只儲存教材的「擷取文字」（用於套用補丁），不儲存原始檔案。"""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    filename TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    unit_title TEXT NOT NULL,
    include_extension INTEGER NOT NULL,
    teacher TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    report_lang TEXT NOT NULL DEFAULT 'zh',
    status TEXT NOT NULL,          -- queued / running / done / failed
    stage TEXT NOT NULL DEFAULT '',
    progress REAL NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    material TEXT NOT NULL DEFAULT '',
    report TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
    review_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    decision TEXT NOT NULL,        -- accepted / rejected
    comment TEXT NOT NULL DEFAULT '',
    teacher TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (review_id, finding_id)
);
"""

_LIST_COLS = ("id, created_at, filename, unit_id, unit_title, include_extension, teacher, subject, report_lang, "
             "status, stage, progress, error")
_MIGRATIONS = [  # 舊資料庫補欄位
    "ALTER TABLE reviews ADD COLUMN subject TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE reviews ADD COLUMN report_lang TEXT NOT NULL DEFAULT 'zh'",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            for sql in _MIGRATIONS:
                try:
                    self._conn.execute(sql)
                except sqlite3.OperationalError:
                    pass  # 欄位已存在
            # 伺服器重啟時，未完成的審查無法續做
            self._conn.execute(
                "UPDATE reviews SET status='failed', error='伺服器曾重新啟動，審查已中斷，請重新提交。' "
                "WHERE status IN ('queued','running')")
            self._conn.commit()

    def _exec(self, sql: str, args=()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur

    def _query(self, sql: str, args=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    # ---- reviews ----
    def create_review(self, rid, filename, unit_id, unit_title, include_extension, teacher,
                      subject="", report_lang="zh"):
        self._exec("INSERT INTO reviews (id, created_at, filename, unit_id, unit_title, include_extension, "
                   "teacher, subject, report_lang, status, stage) VALUES (?,?,?,?,?,?,?,?,?, 'queued', 'queued')",
                   (rid, now(), filename, unit_id, unit_title, int(include_extension), teacher, subject, report_lang))

    def update_progress(self, rid, stage: str, progress: float):
        self._exec("UPDATE reviews SET status='running', stage=?, progress=? WHERE id=?",
                   (stage, round(progress, 3), rid))

    def set_material(self, rid, text: str):
        self._exec("UPDATE reviews SET material=? WHERE id=?", (text, rid))

    def finish(self, rid, report: dict):
        self._exec("UPDATE reviews SET status='done', stage='done', progress=1, report=? WHERE id=?",
                   (json.dumps(report, ensure_ascii=False), rid))

    def fail(self, rid, error: str):
        self._exec("UPDATE reviews SET status='failed', error=? WHERE id=?", (error, rid))

    def list_reviews(self, limit: int = 100) -> list[dict]:
        return self._query(f"SELECT {_LIST_COLS} FROM reviews ORDER BY created_at DESC LIMIT ?", (limit,))

    def get_review(self, rid) -> dict | None:
        rows = self._query("SELECT * FROM reviews WHERE id=?", (rid,))
        if not rows:
            return None
        row = rows[0]
        row["report"] = json.loads(row["report"]) if row["report"] else None
        return row

    def delete_review(self, rid) -> bool:
        self._exec("DELETE FROM feedback WHERE review_id=?", (rid,))
        return self._exec("DELETE FROM reviews WHERE id=?", (rid,)).rowcount > 0

    # ---- feedback ----
    def set_feedback(self, rid, fid, decision: str | None, comment: str, teacher: str):
        if decision is None:
            self._exec("DELETE FROM feedback WHERE review_id=? AND finding_id=?", (rid, fid))
        else:
            self._exec("INSERT INTO feedback VALUES (?,?,?,?,?,?) ON CONFLICT(review_id, finding_id) "
                       "DO UPDATE SET decision=excluded.decision, comment=excluded.comment, "
                       "teacher=excluded.teacher, updated_at=excluded.updated_at",
                       (rid, fid, decision, comment, teacher, now()))

    def get_feedback(self, rid) -> dict[str, dict]:
        return {r["finding_id"]: r for r in self._query("SELECT * FROM feedback WHERE review_id=?", (rid,))}

    def feedback_stats(self) -> list[dict]:
        """每類問題被老師接受／拒絕的次數，用於持續改善 prompt 及詞表。"""
        return self._query("SELECT decision, COUNT(*) AS n FROM feedback GROUP BY decision")
