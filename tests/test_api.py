"""平台整合測試：以離線示範模式啟動整個網站（真資料庫、真背景工作、真規則引擎）。"""
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import build_services, create_app
from app.services.patcher import apply_patches

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"
SAMPLE = (SAMPLES / "science_water_zh.txt").read_bytes()


def _settings(tmp_path, **kw):
    cur = tmp_path / "curriculum"
    shutil.copytree(ROOT / "data" / "curriculum", cur)
    return Settings(demo_mode=True, chroma_path=tmp_path / "chroma", db_path=tmp_path / "app.db",
                    curriculum_dir=cur, **kw)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(build_services(_settings(tmp_path)))) as c:
        yield c


def _wait(client, rid, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        row = client.get(f"/api/reviews/{rid}").json()
        if row["status"] in ("done", "failed"):
            return row
        time.sleep(0.2)
    raise AssertionError("審查逾時")


def test_site_and_config(client):
    assert "教材審查" in client.get("/").text
    assert client.get("/app.js").status_code == 200
    assert client.get("/api/config").json()["demo_mode"] is True


def test_full_review_flow(client):
    unit_id = "S1-SAMPLE-01"
    res = client.post("/api/reviews", files={"file": ("教材.txt", SAMPLE, "text/plain")},
                      data={"unit_id": unit_id, "teacher": "陳老師"})
    assert res.status_code == 202
    row = _wait(client, res.json()["id"])
    assert row["status"] == "done", row["error"]
    report = row["report"]

    # 簡體字必定觸發，並排在最前
    assert report["script_check"]["passed"] is False
    first = report["findings"][0]
    assert first["category"] == "simplified"
    # 示範模式下覆蓋判斷仍會產生結果
    assert {c["status"] for c in report["coverage"]} <= {"covered", "partial", "missing"}

    rid = row["id"]
    assert client.get(f"/api/reviews/{rid}/revised").status_code == 409     # 未接受任何建議
    assert client.put(f"/api/reviews/{rid}/feedback/{first['finding_id']}",
                      json={"decision": "accepted", "teacher": "陳老師"}).status_code == 200
    revised = client.get(f"/api/reviews/{rid}/revised")
    assert "水受熱後會變成水蒸氣" in revised.text and "受热" not in revised.text
    assert "attachment" in revised.headers["content-disposition"]

    md = client.get(f"/api/reviews/{rid}/markdown").text
    assert "字形檢查：不合格" in md
    assert client.get("/api/reviews").json()[0]["id"] == rid
    assert client.delete(f"/api/reviews/{rid}").status_code == 200
    assert client.get(f"/api/reviews/{rid}").status_code == 404


def test_upload_validation(client):
    unit_id = "S1-SAMPLE-01"
    bad = client.post("/api/reviews", files={"file": ("a.docx", b"x")}, data={"unit_id": unit_id})
    assert bad.status_code == 422
    missing = client.post("/api/reviews", files={"file": ("a.txt", b"x")}, data={"unit_id": "NOPE"})
    assert missing.status_code == 404


def test_unit_verification_persists(client, tmp_path):
    unit_id = "S1-SAMPLE-01"
    assert client.post(f"/api/units/{unit_id}/verify", json={"verified": True}).status_code == 422
    ok = client.post(f"/api/units/{unit_id}/verify",
                     json={"verified": True, "teacher": "李主任", "levels": ["S1", "S2"]})
    assert ok.json()["verified"] is True and ok.json()["levels"] == ["S1", "S2"]
    saved = (tmp_path / "curriculum" / "DEMO-water.json").read_text("utf-8")
    assert '"verified_by": "李主任"' in saved and '"S2"' in saved
    assert client.post(f"/api/units/{unit_id}/verify",
                       json={"verified": False, "levels": ["S7"]}).status_code == 422


def test_access_code(tmp_path):
    app = create_app(build_services(_settings(tmp_path, access_code="sci2026")))
    with TestClient(app) as c:
        assert c.get("/api/config").json()["access_required"] is True
        assert c.get("/api/units").status_code == 401
        assert c.post("/api/login", json={"code": "wrong"}).status_code == 401
        assert c.post("/api/login", json={"code": "sci2026"}).status_code == 200
        assert c.get("/api/units").status_code == 200


def test_patcher_reports_unapplied():
    findings = [
        {"finding_id": "F1", "patch": {"action": "replace", "anchor_text": "甲", "new_text": "乙"}},
        {"finding_id": "F2", "patch": {"action": "replace", "anchor_text": "不存在", "new_text": "x"}},
        {"finding_id": "F3", "patch": {"action": "insert_after", "anchor_text": "", "new_text": "補充"}},
    ]
    r = apply_patches("甲丙", findings)
    assert r.text.startswith("乙丙") and "補充" in r.text
    assert r.applied == ["F1", "F3"] and r.unapplied == ["F2"]


def test_subjects_cover_all_junior_secondary_levels(client):
    subjects = {s["code"]: s for s in client.get("/api/subjects").json()}
    assert {"SCI", "MATH", "CHI", "ENG", "CES", "HIST", "CHIST", "GEOG", "TE"} <= set(subjects)
    assert subjects["SCI"]["by_level"]["S1"]["units"] >= 2
    assert subjects["ENG"]["by_level"]["S2"]["units"] == 1
    assert [u["unit_id"] for u in client.get("/api/units", params={"subject": "MATH", "level": "S1"}).json()] \
        == ["DEMO-MATH-DIRECTED"]


def test_language_subject_requires_selected_learning_points(client):
    files = {"file": ("reading.txt", "A story.".encode(), "text/plain")}
    res = client.post("/api/reviews", files=files, data={"unit_id": "DEMO-ENG-NARRATIVE"})
    assert res.status_code == 422
    res = client.post("/api/reviews", files=files,
                      data={"unit_id": "DEMO-ENG-NARRATIVE", "objective_ids": "DEMO-ENG-NARRATIVE-99"})
    assert res.status_code == 422
    res = client.post("/api/reviews", files=files, data={
        "unit_id": "DEMO-ENG-NARRATIVE", "objective_ids": "DEMO-ENG-NARRATIVE-01,DEMO-ENG-NARRATIVE-04"})
    row = _wait(client, res.json()["id"])
    assert row["status"] == "done" and len(row["report"]["coverage"]) == 2


def test_english_material_with_english_report(client):
    data = (SAMPLES / "science_water_en.txt").read_bytes()
    res = client.post("/api/reviews", files={"file": ("water.txt", data, "text/plain")},
                      data={"unit_id": "DEMO-SCI-WATER-EN", "report_lang": "en"})
    report = _wait(client, res.json()["id"])["report"]
    assert report["material_language"] == "en" and report["report_lang"] == "en"
    issues = " ".join(f["issue"] for f in report["findings"])
    assert "vapor" in issues and "milliliters" in issues and "British spelling" in issues
    assert report["script_check"]["passed"] is True
    md = client.get(f"/api/reviews/{res.json()['id']}/markdown").text
    assert "Script check: pass" in md


def test_cross_language_warning(client):
    data = (SAMPLES / "science_water_en.txt").read_bytes()
    res = client.post("/api/reviews", files={"file": ("water.txt", data, "text/plain")},
                      data={"unit_id": "S1-SAMPLE-01"})
    warnings = " ".join(_wait(client, res.json()["id"])["report"]["warnings"])
    assert "跨語言" in warnings
