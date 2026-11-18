"use strict";
/* 網上靜態版（例如 GitHub Pages，沒有伺服器）：在瀏覽器內模擬 /api。
   課程資料直接讀取 repo 內的 data/；審查由 engine.js 執行；記錄存於此瀏覽器（localStorage）。 */
window.StaticApi = (() => {
  const E = window.HKREngine;
  const DATA = new URL("../../data/", document.baseURI);
  const VENDOR = new URL("vendor/", document.baseURI);
  const MAX_MB = 20;
  const REVIEWS_KEY = "hkr-static-reviews", UNITS_KEY = "hkr-static-units";
  const LEVELS = ["S1", "S2", "S3"];

  class ApiError extends Error {}
  const fail = (msg) => { throw new ApiError(msg); };

  async function fetchText(url) {
    const res = await fetch(url);
    if (!res.ok) throw new ApiError(`無法載入 ${url.pathname || url}（HTTP ${res.status}）`);
    return res.text();
  }
  const fetchJson = async (url) => JSON.parse(await fetchText(url));

  const load = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } };
  function save(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
  }

  /* ---------- 資料 ---------- */
  let ctxPromise = null, scriptPromise = null;
  function context() {
    ctxPromise ??= (async () => {
      const [subjects, manifest] = await Promise.all([
        fetchJson(new URL("subjects.json", DATA)), fetchJson(new URL("manifest.json", DATA))]);
      const [units, glossaries] = await Promise.all([
        Promise.all(manifest.curriculum.map((f) => fetchJson(new URL(`curriculum/${f}`, DATA)))),
        Promise.all(manifest.glossary.map((f) => fetchJson(new URL(`glossary/${f}`, DATA))))]);
      const ctx = E.createContext({ subjects: subjects.subjects, units, glossaries });
      // 老師在此瀏覽器核對過的年級及核對狀態
      for (const [id, o] of Object.entries(load(UNITS_KEY, {}))) {
        const u = ctx.units.get(id);
        if (u) Object.assign(u, o);
      }
      return ctx;
    })().catch((err) => { ctxPromise = null; throw err; });
    return ctxPromise;
  }
  // 簡繁字表約 1 MB，到開始審查時才載入
  function ensureScript(ctx) {
    scriptPromise ??= (async () => {
      const names = ["TSPhrases", "TSCharacters", "STPhrases", "STCharacters", "HKVariants"];
      const texts = await Promise.all(names.map((n) => fetchText(new URL(`opencc/${n}.txt`, VENDOR))));
      ctx.script = E.makeScriptChecker(Object.fromEntries(names.map((n, i) => [n, texts[i]])));
    })().catch((err) => { scriptPromise = null; throw err; });
    return scriptPromise;
  }

  /* ---------- BAAI/bge-m3（transformers.js，ONNX 8-bit 版約 570 MB，首次下載後由瀏覽器快取） ---------- */
  const TRANSFORMERS = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.7.5";
  const BGE_M3 = { repo: "Xenova/bge-m3", label: "BAAI/bge-m3" };
  let extractorPromise = null, onDownload = null;
  function loadExtractor() {
    extractorPromise ??= (async () => {
      const { pipeline, env } = await import(TRANSFORMERS);
      env.allowLocalModels = false;
      const files = {};
      return pipeline("feature-extraction", BGE_M3.repo, { dtype: "q8", progress_callback: (e) => {
        if (e.status !== "progress" || !e.total) return;
        files[e.file] = [e.loaded, e.total];
        const [loaded, total] = Object.values(files).reduce((a, [l, t]) => [a[0] + l, a[1] + t], [0, 0]);
        onDownload?.(Math.floor(loaded / total * 100));
      } });
    })().catch((err) => { extractorPromise = null; throw err; });
    return extractorPromise;
  }
  const bgeEmbedder = (progress) => ({
    label: BGE_M3.label,
    async embed(texts, onDone = () => {}) {
      onDownload = (p) => progress(`model:${p}`, 0.15);
      const extractor = await loadExtractor().finally(() => { onDownload = null; });
      const out = [];
      for (let i = 0; i < texts.length; i += 4) {
        const t = await extractor(texts.slice(i, i + 4), { pooling: "cls", normalize: true });
        out.push(...t.tolist());
        onDone(out.length);
      }
      return out;
    },
  });

  const unitOr404 = (ctx, id) => ctx.units.get(id) || fail("找不到此單元");

  /* ---------- 審查記錄 ---------- */
  const running = new Set();
  let reviews = load(REVIEWS_KEY, []);
  for (const r of reviews) {
    if (r.status === "queued" || r.status === "running") Object.assign(r, { status: "failed", error: "頁面曾重新載入，審查中斷。請重新提交。" });
  }
  function persist() {
    // 空間不足時，由最舊的記錄開始刪除
    const rows = [...reviews];
    while (!save(REVIEWS_KEY, rows) && rows.length > 1) rows.pop();
  }
  persist();
  const byId = (id) => reviews.find((r) => r.id === id) || fail("找不到此審查記錄");
  const LIST_COLS = ["id", "created_at", "filename", "unit_id", "unit_title", "include_extension", "teacher",
                     "subject", "report_lang", "status", "stage", "progress", "error"];

  async function createReview(fd) {
    const ctx = await context();
    const file = fd.get("file");
    if (!(file instanceof Blob)) fail("請選擇檔案");
    const unit = unitOr404(ctx, fd.get("unit_id") || "");
    const reportLang = fd.get("report_lang") || "zh";
    if (!["zh", "en"].includes(reportLang)) fail("report_lang 只可為 zh 或 en");
    const ids = String(fd.get("objective_ids") || "").split(",").filter(Boolean);
    const unknown = ids.filter((i) => !unit.objectives.some((o) => o.id === i));
    if (unknown.length) fail(`不屬於此單元的學習重點：${unknown.join(", ")}`);
    if (ctx.subjects.get(unit.subject)?.mode === "skills" && !ids.length) fail("語文科須選擇這份教材針對的學習重點");
    const name = file.name || "教材";
    if (!/\.(pdf|txt|md)$/i.test(name)) fail("只支援 PDF、TXT 或 MD 檔");
    if (file.size > MAX_MB * 1048576) fail(`檔案超過 ${MAX_MB} MB`);
    const includeExtension = fd.get("include_extension") === "true";

    const row = { id: Math.random().toString(16).slice(2, 14).padEnd(12, "0"), created_at: new Date().toISOString(),
                  filename: name, unit_id: unit.unit_id, unit_title: unit.title, include_extension: includeExtension,
                  teacher: String(fd.get("teacher") || "").trim(), subject: unit.subject, report_lang: reportLang,
                  status: "queued", stage: "queued", progress: 0, error: null, report: null, material: "", feedback: {} };
    reviews.unshift(row);
    running.add(row.id);
    persist();
    const bytes = new Uint8Array(await file.arrayBuffer());
    (async () => {
      try {
        await ensureScript(ctx);
        const progress = (stage, value) => { Object.assign(row, { status: "running", stage, progress: Math.round(value * 1000) / 1000 }); };
        row.report = await E.runReview(ctx, {
          filename: name, bytes, unitId: unit.unit_id, includeExtension, objectiveIds: ids.length ? ids : null,
          reportLang, embedder: bgeEmbedder(progress), progress, onText: (text) => { row.material = text; },
        });
        Object.assign(row, { status: "done", stage: "done", progress: 1 });
      } catch (err) {
        Object.assign(row, { status: "failed", error: err instanceof ApiError || /[\u4e00-\u9fff]/.test(err.message)
          ? err.message : `審查失敗：${err.name}：${err.message}` });
      } finally {
        running.delete(row.id);
        persist();
      }
    })();
    return { id: row.id };
  }

  function revised(row) {
    if (!row.report) fail("審查尚未完成");
    const accepted = row.report.findings.filter((f) => row.feedback[f.finding_id]?.decision === "accepted");
    if (!accepted.length) fail("尚未接受任何修改建議");
    const result = E.applyPatches(row.material, accepted);
    const note = result.unapplied.length
      ? `\n\n【未能自動套用：${result.unapplied.join("、")}。原文位置已改變或找不到，請參考審查報告手動修改。】` : "";
    return result.text + note;
  }
  const stem = (name) => name.replace(/\.[^.]*$/, "");

  /* ---------- 路由 ---------- */
  async function request(path, opts = {}) {
    const method = (opts.method || "GET").toUpperCase();
    const [p, query = ""] = path.split("?");
    const q = new URLSearchParams(query);
    const seg = p.split("/").filter(Boolean).map(decodeURIComponent);
    const body = typeof opts.body === "string" ? JSON.parse(opts.body) : opts.body;

    if (p === "/config") {
      return { static: true, demo_mode: false, access_required: false, max_upload_mb: MAX_MB,
               model: BGE_M3.label, embedding_models: [], embedding_default: "" };
    }
    const ctx = await context();
    const units = [...ctx.units.values()];
    if (p === "/subjects") {
      return [...ctx.subjects.values()].map((s) => {
        const list = units.filter((u) => u.subject === s.code);
        const count = (pred) => ({ units: list.filter(pred).length, verified: list.filter((u) => pred(u) && u.verified).length });
        const by_level = Object.fromEntries(LEVELS.map((lv) => [lv, count((u) => u.levels.includes(lv))]));
        by_level.unassigned = count((u) => !u.levels.length);
        return { ...s, unit_count: list.length, by_level };
      });
    }
    if (seg[0] === "units" && seg.length === 1) {
      return units.filter((u) => (!q.get("subject") || u.subject === q.get("subject")) && (!q.get("level") || u.levels.includes(q.get("level"))))
        .map((u) => ({ unit_id: u.unit_id, subject: u.subject, levels: u.levels, language: u.language,
                       curriculum_id: u.curriculum_id, unit_no: u.unit_no, title: u.title, verified: u.verified,
                       verified_by: u.verified_by, core_count: u.objectives.filter((o) => o.tier === "core").length,
                       extension_count: u.objectives.filter((o) => o.tier === "extension").length,
                       unlocated_count: u.objectives.filter((o) => o.page == null).length,
                       source: u.source, source_url: u.source_url }));
    }
    if (seg[0] === "units" && seg.length === 2) return unitOr404(ctx, seg[1]);
    if (seg[0] === "units" && seg[2] === "verify" && method === "POST") {
      const unit = unitOr404(ctx, seg[1]);
      const teacher = String(body.teacher || "").trim();
      if (body.verified && !teacher) fail("請填寫核對老師姓名");
      if (body.levels != null) {
        if (body.levels.some((lv) => !LEVELS.includes(lv))) fail("年級只可為 S1、S2、S3");
        unit.levels = [...new Set(body.levels)].sort();
      }
      Object.assign(unit, { verified: !!body.verified, verified_by: body.verified ? teacher : "" });
      const overrides = load(UNITS_KEY, {});
      overrides[unit.unit_id] = { levels: unit.levels, verified: unit.verified, verified_by: unit.verified_by };
      save(UNITS_KEY, overrides);
      return { unit_id: unit.unit_id, verified: unit.verified, verified_by: unit.verified_by, levels: unit.levels };
    }
    if (p === "/reviews" && method === "POST") return createReview(body);
    if (p === "/reviews") return reviews.map((r) => Object.fromEntries(LIST_COLS.map((k) => [k, r[k]])));
    if (seg[0] === "reviews" && seg.length === 2) {
      const row = byId(seg[1]);
      if (method === "DELETE") { reviews = reviews.filter((r) => r !== row); persist(); return { ok: true }; }
      const { material, ...rest } = row;
      return structuredClone(rest);
    }
    if (seg[0] === "reviews" && seg[2] === "feedback" && method === "PUT") {
      const row = byId(seg[1]);
      if (![null, "accepted", "rejected"].includes(body.decision ?? null)) fail("decision 只可為 accepted、rejected 或 null");
      if (body.decision) {
        row.feedback[seg[3]] = { review_id: row.id, finding_id: seg[3], decision: body.decision, comment: body.comment || "",
                                 teacher: body.teacher || "", updated_at: new Date().toISOString() };
      } else delete row.feedback[seg[3]];
      persist();
      return { ok: true };
    }
    fail(`HTTP 404：${method} ${p}`);
  }

  /** 下載連結（/reviews/:id/markdown 或 /revised）→ { filename, text } */
  async function download(path) {
    const [, , id, kind] = path.split("/");
    const row = byId(id);
    if (!row.report) fail("審查尚未完成");
    if (kind === "markdown") return { filename: `審查報告_${stem(row.filename)}.md`, text: E.renderMarkdown(row.report) };
    return { filename: `修訂版_${stem(row.filename)}.txt`, text: revised(row) };
  }

  return { request, download };
})();
