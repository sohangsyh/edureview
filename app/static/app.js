"use strict";
/* 教材審查平台前端（原生 JavaScript，無需建置）。
   路由：#/ 首頁、#/new?subject=&level= 審查、#/reviews、#/reviews/:id、#/units?subject=、#/units/:id */

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const LEVELS = ["S1", "S2", "S3"];

const state = { lang: "zh", config: null, subjects: null, pollTimer: null };
const main = $("#main");

/* ---------- 語言 ---------- */
function t(key, vars = {}) {
  const value = key.split(".").reduce((o, k) => (o == null ? o : o[k]), window.I18N[state.lang]);
  return String(value ?? key).replace(/\{(\w+)\}/g, (_, k) => (k in vars ? vars[k] : `{${k}}`));
}
// 標點按介面語言：英文介面不可出現全形標點
const P = () => (state.lang === "en" ? { comma: ", ", colon: ": ", stop: ". ", list: ", " }
                                     : { comma: "，", colon: "：", stop: "。", list: "、" });
const subjName = (s) => (s ? (state.lang === "en" ? s.name_en : s.name_zh) : "");
const subjectByCode = (code) => (state.subjects || []).find((s) => s.code === code);

function applyStatic() {
  document.documentElement.lang = state.lang === "en" ? "en" : "zh-HK";
  document.title = `${t("brand")}｜${t("brand_sub")}`;
  $$("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  $$("[data-i18n-placeholder]").forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  $$(".lang-switch button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.lang === state.lang));
  const bar = $("#demo-bar");
  bar.hidden = !state.config?.demo_mode;
  bar.textContent = t("demo_note");
}

function setLang(lang) {
  state.lang = lang;
  try { localStorage.setItem("hkr-lang", lang); } catch { /* 私隱模式 */ }
  applyStatic();
  route();
}

/* ---------- 共用 ---------- */
async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, { credentials: "same-origin", ...opts });
  if (res.status === 401 && path !== "/login") {
    showLogin();
    throw new Error(t("login_title"));
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch { /* 非 JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}
const jsonBody = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 2600);
}

const teacher = () => $("#teacher-name").value.trim();
const fmtDate = (iso) => new Date(iso).toLocaleString(state.lang === "en" ? "en-GB" : "zh-HK",
  { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const fmtSize = (bytes) => (bytes < 1048576 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`);

function showLogin() { const d = $("#login-dialog"); if (!d.open) d.showModal(); }

async function loadSubjects(force = false) {
  if (!state.subjects || force) state.subjects = await api("/subjects");
  return state.subjects;
}

function setView(html, nav) {
  main.innerHTML = html;
  $$("#nav a").forEach((a) => {
    if (a.dataset.nav === nav) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}
const errorView = (err, nav) => setView(`<div class="notice notice-error" role="alert">${esc(err.message)}</div>`, nav);

function stageText(stage) {
  const ai = /^ai:(\d+)\/(\d+)$/.exec(stage || "");
  if (ai) return t("stage_ai", { d: ai[1], t: ai[2] });
  const key = `stage_${stage}`;
  return t(key) === key ? stage : t(key);
}

/* ---------- 首頁 ---------- */
async function viewHome() {
  const [subjects, reviews] = await Promise.all([loadSubjects(true), api("/reviews")]);
  const cell = (s, lv) => {
    const c = s.by_level[lv];
    if (!c || !c.units) return `<td><span class="cell cell-none" aria-label="0">—</span></td>`;
    const href = `#/new?subject=${encodeURIComponent(s.code)}${lv === "unassigned" ? "" : `&level=${lv}`}`;
    const ok = c.verified === c.units;
    return `<td><a class="cell" href="${href}"><span><b>${c.units}</b>
      <span class="dot ${ok ? "ok" : ""}">${ok ? "✓ " : ""}${c.verified}/${c.units}</span></span></a></td>`;
  };
  const recent = reviews.slice(0, 5);

  setView(`
    <div class="hero">
      <div>
        <h1>${esc(t("hero_title"))}</h1>
        <p>${esc(t("hero_body"))}</p>
        <a class="btn btn-primary btn-big" href="#/new">${esc(t("hero_cta"))}</a>
      </div>
      <figure class="hero-sample" aria-hidden="true" style="margin:0">
        <div class="margin-note">${esc(t("sample_note"))}<span class="sev">${esc(t("sev.high"))}</span></div>
        <div class="correction"><del>${esc(t("sample_orig"))}</del><ins>${esc(t("sample_fix"))}</ins></div>
        <div class="stamp"><div><b>${esc(t("stamp_pass"))}</b><span>${esc(t("stamp_sub"))}</span></div></div>
      </figure>
    </div>

    <section>
      <h2>${esc(t("matrix_title"))}</h2>
      <p class="lead">${esc(t("matrix_body"))}</p>
      <div class="matrix-wrap"><table class="matrix">
        <thead><tr><th scope="col">${esc(t("subject"))}</th>${LEVELS.map((lv) => `<th scope="col" class="lv">${lv}</th>`).join("")}
          <th scope="col" class="lv" style="font:500 0.875rem var(--sans);color:var(--ink-soft)">${esc(t("unassigned"))}</th></tr></thead>
        <tbody>${subjects.map((s) => `<tr>
          <th scope="row">${esc(subjName(s))}<small class="meta">${esc(state.lang === "en" ? s.kla_en : s.kla_zh)}</small></th>
          ${[...LEVELS, "unassigned"].map((lv) => cell(s, lv)).join("")}</tr>`).join("")}
        </tbody></table></div>
    </section>

    <section>
      <div class="page-head" style="margin-bottom:12px"><h2>${esc(t("recent"))}</h2>
        ${reviews.length ? `<a href="#/reviews">${esc(t("see_all"))}</a>` : ""}</div>
      ${recent.length ? `<ul class="recent-list">${recent.map((r) => `<li>
          <a href="#/reviews/${esc(r.id)}">${esc(r.filename)}</a>
          <span class="meta">${esc(subjName(subjectByCode(r.subject)))}　${esc(r.unit_title)}</span>
          <span class="meta status status-${esc(r.status)}">${esc(t(`job_${r.status}`))}${P().comma}${esc(fmtDate(r.created_at))}</span></li>`).join("")}</ul>`
        : `<div class="empty"><p>${esc(t("no_reviews"))}</p></div>`}
    </section>`, "home");
}

/* ---------- 審查（三個步驟） ---------- */
async function viewNew(params) {
  const subjects = await loadSubjects();
  const form = { subject: params.get("subject") || "", level: params.get("level") || "", unit: null, file: null };

  setView(`
    <div class="page-head"><div><h1>${esc(t("new_title"))}</h1></div></div>
    <form id="review-form" novalidate>
      <ol class="steps">
        <li class="step" id="step-1">
          <h2>${esc(t("step1"))}</h2>
          <div class="choices" role="radiogroup" aria-label="${esc(t("subject"))}">
            ${subjects.map((s) => `<label class="choice"><input type="radio" name="subject" value="${esc(s.code)}" ${s.code === form.subject ? "checked" : ""}>
              <span>${esc(subjName(s))}<small>${esc(t("units_n", { n: s.unit_count }))}</small></span></label>`).join("")}
          </div>
          <div class="choices" role="radiogroup" aria-label="Level" style="margin-top:14px">
            ${["", ...LEVELS].map((lv) => `<label class="choice"><input type="radio" name="level" value="${lv}" ${lv === form.level ? "checked" : ""}>
              <span>${lv || esc(t("all_levels"))}</span></label>`).join("")}
          </div>
        </li>
        <li class="step" id="step-2"><h2>${esc(t("step2"))}</h2><div id="unit-area"></div></li>
        <li class="step" id="step-3">
          <h2>${esc(t("step3"))}</h2>
          <label class="drop" id="drop">
            <input type="file" id="file" accept=".pdf,.txt,.md">
            <strong id="drop-title">${esc(t("drop_title"))}</strong>
            <span class="meta" id="drop-meta">${esc(t("drop_meta", { mb: state.config.max_upload_mb }))}</span>
          </label>
          <div style="margin-top:18px">
            <span class="field-label">${esc(t("report_lang"))}</span>
            <div class="choices">
              ${["zh", "en"].map((l) => `<label class="choice"><input type="radio" name="report_lang" value="${l}" ${l === state.lang ? "checked" : ""}>
                <span>${l === "zh" ? "中文" : "English"}</span></label>`).join("")}
            </div>
            <p class="meta" style="margin:8px 0 0">${esc(t("report_lang_hint"))}</p>
          </div>
          <p class="form-error" id="form-error" role="alert"></p>
          <button class="btn btn-primary btn-big" type="submit">${esc(t("start"))}</button>
        </li>
      </ol>
    </form>`, "new");

  const formEl = $("#review-form");
  const markSteps = () => {
    $("#step-1").classList.toggle("is-done", !!form.subject);
    $("#step-2").classList.toggle("is-done", !!form.unit);
    $("#step-3").classList.toggle("is-done", !!form.file);
  };

  async function renderUnits() {
    const area = $("#unit-area");
    form.unit = null;
    markSteps();
    if (!form.subject) { area.innerHTML = `<p class="meta">${esc(t("need_subject"))}</p>`; return; }
    const q = new URLSearchParams({ subject: form.subject, ...(form.level ? { level: form.level } : {}) });
    const units = await api(`/units?${q}`);
    if (!units.length) {
      area.innerHTML = `<div class="empty"><p>${esc(t("no_units"))}</p><a class="btn" href="#/units?subject=${esc(form.subject)}">${esc(t("nav_units"))}</a></div>`;
      return;
    }
    area.innerHTML = `<div class="unit-list" role="radiogroup">${units.map((u) => `
      <label class="unit-card"><input type="radio" name="unit" value="${esc(u.unit_id)}">
        <span class="title">${esc(u.title)}</span>
        <span class="meta">
          <span>${u.levels.length ? u.levels.join(P().list) : esc(t("unassigned"))}</span>
          <span>${esc(t(u.language === "en" ? "lang_en" : "lang_zh"))}</span>
          <span>${esc(t("core_n", { n: u.core_count }))}${u.extension_count ? `${P().comma}${esc(t("ext_n", { n: u.extension_count }))}` : ""}</span>
          ${u.verified ? `<span class="tag tag-ok">${esc(t("verified_by", { n: u.verified_by }))}</span>` : `<span class="tag tag-warn">${esc(t("unverified"))}</span>`}
        </span></label>`).join("")}</div>
      <div id="points"></div>`;
    $$('input[name="unit"]', area).forEach((r) => r.addEventListener("change", () => selectUnit(r.value)));
  }

  async function selectUnit(unitId) {
    const unit = await api(`/units/${encodeURIComponent(unitId)}`);
    const subject = subjectByCode(unit.subject);
    const skills = subject?.mode === "skills";
    form.unit = unit;
    markSteps();
    const pointItem = (o) => `<li><label class="check"><input type="checkbox" name="point" value="${esc(o.id)}" ${!skills && o.tier === "core" ? "checked" : ""}>
      <span>${esc(o.text)}${o.tier === "extension" ? ` <span class="tag tag-ext">${esc(t("ext_tag"))}</span>` : ""}${o.subtopic ? ` <span class="tag">${esc(o.subtopic)}</span>` : ""}</span></label></li>`;
    const hasExt = unit.objectives.some((o) => o.tier === "extension");
    $("#points").innerHTML = `<div class="points">
      <span class="field-label">${esc(t("points_title"))}</span>
      ${skills ? `<div class="notice notice-info">${esc(t("skills_note"))}</div>
        <ul class="points-list">${unit.objectives.map(pointItem).join("")}</ul>`
      : `<div class="choices">
          <label class="choice"><input type="radio" name="scope" value="all" checked><span>${esc(t("points_all"))}</span></label>
          <label class="choice"><input type="radio" name="scope" value="some"><span>${esc(t("points_some"))}</span></label>
        </div>
        ${hasExt ? `<label class="check" id="ext-row" style="margin-top:12px"><input type="checkbox" id="ext"><span>${esc(t("include_ext"))}</span></label>` : ""}
        <ul class="points-list" id="point-list" hidden>${unit.objectives.map(pointItem).join("")}</ul>`}
    </div>`;
    $$('input[name="scope"]').forEach((r) => r.addEventListener("change", () => {
      const some = r.value === "some" && r.checked;
      $("#point-list").hidden = !some;
      if ($("#ext-row")) $("#ext-row").hidden = some;
    }));
  }

  $$('input[name="subject"]', formEl).forEach((r) => r.addEventListener("change", () => { form.subject = r.value; renderUnits(); }));
  $$('input[name="level"]', formEl).forEach((r) => r.addEventListener("change", () => { form.level = r.value; renderUnits(); }));
  renderUnits();

  const fileInput = $("#file"), drop = $("#drop");
  const showFile = () => {
    form.file = fileInput.files[0] || null;
    drop.classList.toggle("has-file", !!form.file);
    $("#drop-title").textContent = form.file ? form.file.name : t("drop_title");
    $("#drop-meta").textContent = form.file ? `${fmtSize(form.file.size)}${P().comma}${t("change_file")}` : t("drop_meta", { mb: state.config.max_upload_mb });
    markSteps();
  };
  fileInput.addEventListener("change", showFile);
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("is-over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove("is-over")));
  drop.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; showFile(); } });

  formEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#form-error");
    err.textContent = "";
    if (!form.subject) return (err.textContent = t("need_subject"));
    if (!form.unit) return (err.textContent = t("need_unit"));
    const skills = subjectByCode(form.unit.subject)?.mode === "skills";
    const scopeSome = $('input[name="scope"]:checked')?.value === "some";
    const points = $$('input[name="point"]:checked').map((c) => c.value);
    if ((skills || scopeSome) && !points.length) return (err.textContent = t("need_points"));
    if (!form.file) return (err.textContent = t("need_file"));
    if (form.file.size > state.config.max_upload_mb * 1048576) return (err.textContent = t("too_big", { mb: state.config.max_upload_mb }));

    const fd = new FormData();
    fd.append("file", form.file);
    fd.append("unit_id", form.unit.unit_id);
    fd.append("include_extension", !!$("#ext")?.checked);
    if (skills || scopeSome) fd.append("objective_ids", points.join(","));
    fd.append("report_lang", $('input[name="report_lang"]:checked').value);
    fd.append("teacher", teacher());
    const btn = e.submitter;
    btn.disabled = true;
    btn.textContent = t("uploading");
    try {
      const { id } = await api("/reviews", { method: "POST", body: fd });
      location.hash = `#/reviews/${id}`;
    } catch (ex) {
      err.textContent = ex.message;
      btn.disabled = false;
      btn.textContent = t("start");
    }
  });
}

/* ---------- 審查記錄 ---------- */
async function viewReviews() {
  await loadSubjects();
  const rows = await api("/reviews");
  setView(`
    <div class="page-head"><div><h1>${esc(t("history_title"))}</h1><p>${esc(t("history_body"))}</p></div>
      <a class="btn btn-primary" href="#/new">${esc(t("hero_cta"))}</a></div>
    ${rows.length ? `<div class="table-wrap"><table class="data">
      <thead><tr><th>${esc(t("col_date"))}</th><th>${esc(t("col_file"))}</th><th>${esc(t("col_subject"))}</th><th>${esc(t("col_unit"))}</th>
        <th>${esc(t("col_teacher"))}</th><th>${esc(t("col_status"))}</th></tr></thead>
      <tbody>${rows.map((r) => `<tr>
        <td class="num">${esc(fmtDate(r.created_at))}</td>
        <td><a href="#/reviews/${esc(r.id)}">${esc(r.filename)}</a></td>
        <td>${esc(subjName(subjectByCode(r.subject)) || "—")}</td>
        <td>${esc(r.unit_title)}</td>
        <td>${esc(r.teacher) || "—"}</td>
        <td class="status status-${esc(r.status)}">${esc(t(`job_${r.status}`))}</td></tr>`).join("")}
      </tbody></table></div>`
      : `<div class="empty"><p>${esc(t("no_reviews"))}</p><a class="btn btn-primary" href="#/new">${esc(t("hero_cta"))}</a></div>`}`, "reviews");
}

/* ---------- 報告 ---------- */
async function viewReview(id) {
  await loadSubjects();
  const row = await api(`/reviews/${id}`);
  if (row.status === "queued" || row.status === "running") return viewProgress(row);
  if (row.status === "failed") {
    return setView(`
      <div class="page-head"><div><h1>${esc(row.filename)}</h1><p>${esc(row.unit_title)}</p></div></div>
      <div class="notice notice-error" role="alert">${esc(row.error)}</div>
      <p style="margin-top:20px"><a class="btn btn-primary" href="#/new">${esc(t("resubmit"))}</a></p>`, "reviews");
  }
  renderReport(row);
}

function viewProgress(row) {
  setView(`
    <div class="page-head"><div><h1>${esc(row.filename)}</h1><p>${esc(row.unit_title)}</p></div></div>
    <div class="progress-wrap" aria-live="polite">
      <h2>${esc(t("progress_title"))}</h2>
      <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(row.progress * 100)}">
        <span style="width:${Math.max(4, row.progress * 100)}%"></span></div>
      <p class="meta">${esc(stageText(row.stage))}</p>
      <p class="meta" style="margin:0">${esc(t("progress_note"))}</p>
    </div>`, "reviews");
  state.pollTimer = setTimeout(() => viewReview(row.id).catch((e) => errorView(e, "reviews")), 1500);
}

function correctionHTML(f) {
  const p = f.patch;
  if (!p) return f.original_text ? `<div class="correction"><mark>${esc(f.original_text)}</mark></div>` : "";
  if (p.action === "insert_after") {
    return `<div class="correction">${p.anchor_text ? `<span class="anchor">…${esc(p.anchor_text)}</span>` : `<span class="anchor">${esc(t("new_para"))}</span>`}
      <ins class="insert">${esc(p.new_text)}</ins></div>`;
  }
  if (p.action === "delete") return `<div class="correction"><del>${esc(p.anchor_text)}</del></div>`;
  return `<div class="correction"><del>${esc(p.anchor_text || f.original_text)}</del><ins>${esc(p.new_text)}</ins></div>`;
}

function findingHTML(f, fb) {
  const decision = fb[f.finding_id]?.decision || "";
  const by = f.source === "rule" ? t("by_rule") : state.config.demo_mode ? t("by_demo") : t("by_ai");
  const where = [f.page ? t("page_n", { n: f.page }) : "", by].filter(Boolean).join(P().comma);
  const refs = (f.curriculum_refs || []).join(P().list);
  return `<article class="finding ${decision === "rejected" ? "is-rejected" : ""}" data-id="${esc(f.finding_id)}" data-cat="${esc(f.category)}">
    <div class="margin-note cat-${esc(f.category)}">${esc(t(`cat.${f.category}`))}<span class="sev">${esc(t(`sev.${f.severity}`))}</span></div>
    <div class="finding-body">
      <p class="finding-where">${esc(f.finding_id)}　${esc(where)}${f.quote_verified ? "" : `　<span class="warn-inline">${esc(t("quote_unverified"))}</span>`}</p>
      <p class="finding-issue">${esc(f.issue)}</p>
      ${correctionHTML(f)}
      <p class="finding-suggest">${esc(f.suggestion)}</p>
      <div class="finding-foot">
        <span class="refs">${refs ? `${esc(t("basis"))}${esc(refs)}` : ""}</span>
        ${f.patch?.new_text ? `<button class="btn btn-small copy" type="button">${esc(t("copy"))}</button>` : ""}
        <button class="btn btn-small decision" type="button" data-decision="accepted" aria-pressed="${decision === "accepted"}">${esc(t("accept"))}</button>
        <button class="btn btn-small decision" type="button" data-decision="rejected" aria-pressed="${decision === "rejected"}">${esc(t("reject"))}</button>
      </div>
    </div></article>`;
}

function renderReport(row) {
  const r = row.report, fb = row.feedback || {};
  const sc = r.script_check, cs = r.coverage_summary;
  const counts = r.findings.reduce((m, f) => ((m[f.category] = (m[f.category] || 0) + 1), m), {});
  const gaps = (cs.missing || 0) + (cs.partial || 0);
  const accepted = () => Object.values(fb).filter((x) => x.decision === "accepted").length;
  const scope = r.selected_objectives ? t("scope_sel", { n: r.selected_objectives }) : r.include_extension ? t("scope_ext") : t("scope_core");
  const subject = subjName(subjectByCode(r.subject));
  const cats = Object.keys(window.I18N.zh.cat).filter((k) => counts[k]);

  setView(`
    <div class="report">
      <div class="report-head">
        <h1>${esc(row.filename)}</h1>
        <p class="meta">${subject ? `${esc(subject)}${P().colon}` : ""}${esc(r.unit_title)}<br>
          ${esc(scope)}${P().stop}${esc(t("material_lang", { l: t(r.material_language === "en" ? "lang_en" : "lang_zh") }))}<br>
          ${esc(fmtDate(row.created_at))}${row.teacher ? `${P().comma}${esc(t("submitted_by", { n: row.teacher }))}` : ""}</p>
      </div>

      <div class="report-main">
        ${r.warnings.map((w) => `<div class="notice">${esc(w)}</div>`).join("")}
        <div class="tabs" role="tablist">
          <button type="button" role="tab" id="tab-f" aria-controls="panel-f" aria-selected="true">${esc(t("tab_findings"))}<span class="n">${r.findings.length}</span></button>
          <button type="button" role="tab" id="tab-c" aria-controls="panel-c" aria-selected="false">${esc(t("tab_coverage"))}<span class="n">${r.coverage.length}</span></button>
        </div>
        <div role="tabpanel" id="panel-f" aria-labelledby="tab-f">
          ${r.findings.length ? `
            <div class="filters" role="group">
              <button class="chip" type="button" data-filter="all" aria-pressed="true">${esc(t("filter_all"))}<span class="n">${r.findings.length}</span></button>
              ${cats.map((k) => `<button class="chip" type="button" data-filter="${k}" aria-pressed="false">${esc(t(`cat.${k}`))}<span class="n">${counts[k]}</span></button>`).join("")}
            </div>
            <div class="sheet" id="sheet">${r.findings.map((f) => findingHTML(f, fb)).join("")}</div>`
          : `<div class="empty"><p>${esc(t("no_findings"))}</p></div>`}
        </div>
        <div role="tabpanel" id="panel-c" aria-labelledby="tab-c" hidden>
          <ul class="checklist">${r.coverage.map((c) => `<li>
            <span class="mark ${esc(c.status)}" aria-label="${esc(t(`cov.${c.status}`))}">${{ covered: "✓", partial: "△", missing: "✗", unassessed: "？" }[c.status]}</span>
            <div>${esc(c.objective_text)} <span class="tag">${esc(c.objective_id)}</span>
              <p class="why">${esc(t(`cov.${c.status}`))}${P().stop}${esc(c.rationale)}</p></div></li>`).join("")}</ul>
        </div>
      </div>

      <aside class="report-aside">
        <div class="stamp ${sc.passed ? "" : "fail"}" role="img" aria-label="${esc(t("stamp_sub"))}: ${esc(t(sc.passed ? "stamp_pass" : "stamp_fail"))}">
          <div><b>${esc(t(sc.passed ? "stamp_pass" : "stamp_fail"))}</b><span>${esc(t("stamp_sub"))}</span></div></div>
        <ul class="tally">
          <li class="${sc.passed ? "" : "bad"}"><b>${sc.simplified_chars}</b>${esc(t("tally_simplified"))}</li>
          <li class="${gaps ? "bad" : ""}"><b>${gaps}</b>${esc(t("tally_gaps"))}</li>
          <li><b>${r.findings.length}</b>${esc(t("tally_findings"))}</li>
          <li><b id="accepted-n">${accepted()}</b>${esc(t("tally_accepted"))}</li>
        </ul>
        <div class="btn-col" style="display:grid;gap:8px">
          <a class="btn btn-primary" id="revised" href="/api/reviews/${esc(row.id)}/revised" download aria-disabled="${accepted() === 0}">${esc(t("dl_revised"))}</a>
          <a class="btn" href="/api/reviews/${esc(row.id)}/markdown" download>${esc(t("dl_report"))}</a>
        </div>
      </aside>
    </div>`, "reviews");

  $$('[role="tab"]').forEach((tab) => tab.addEventListener("click", () => {
    $$('[role="tab"]').forEach((x) => x.setAttribute("aria-selected", x === tab));
    $$('[role="tabpanel"]').forEach((p) => { p.hidden = p.id !== tab.getAttribute("aria-controls"); });
  }));
  $$(".chip").forEach((chip) => chip.addEventListener("click", () => {
    $$(".chip").forEach((c) => c.setAttribute("aria-pressed", c === chip));
    $$(".finding").forEach((el) => { el.hidden = chip.dataset.filter !== "all" && el.dataset.cat !== chip.dataset.filter; });
  }));

  $("#sheet")?.addEventListener("click", async (e) => {
    const card = e.target.closest(".finding");
    if (!card) return;
    const f = r.findings.find((x) => x.finding_id === card.dataset.id);
    if (e.target.closest(".copy")) {
      try { await navigator.clipboard.writeText(f.patch.new_text); toast(t("copied")); } catch (ex) { toast(ex.message); }
      return;
    }
    const btn = e.target.closest(".decision");
    if (!btn) return;
    const next = fb[f.finding_id]?.decision === btn.dataset.decision ? null : btn.dataset.decision;
    try {
      await api(`/reviews/${row.id}/feedback/${f.finding_id}`, jsonBody("PUT", { decision: next, teacher: teacher() }));
      if (next) fb[f.finding_id] = { decision: next }; else delete fb[f.finding_id];
      $$(".decision", card).forEach((b) => b.setAttribute("aria-pressed", b.dataset.decision === next));
      card.classList.toggle("is-rejected", next === "rejected");
      $("#accepted-n").textContent = accepted();
      $("#revised").setAttribute("aria-disabled", accepted() === 0);
      toast(t(next === "accepted" ? "accepted_toast" : next === "rejected" ? "rejected_toast" : "cleared_toast"));
    } catch (ex) { toast(ex.message); }
  });
}

/* ---------- 課程資料 ---------- */
async function viewUnits(params) {
  const subjects = await loadSubjects(true);
  const only = params.get("subject");
  const units = await api(`/units${only ? `?subject=${encodeURIComponent(only)}` : ""}`);
  const shown = subjects.filter((s) => !only || s.code === only);
  const docLinks = (d) => [
    d.pdf_zh && `<a href="${esc(d.pdf_zh)}" target="_blank" rel="noopener">PDF 中文</a>`,
    d.pdf_en && `<a href="${esc(d.pdf_en)}" target="_blank" rel="noopener">PDF English</a>`,
    d.notes_url && `<a href="${esc(d.notes_url)}" target="_blank" rel="noopener">+</a>`,
  ].filter(Boolean).join("　");

  setView(`
    <div class="page-head"><div><h1>${esc(t("units_title"))}</h1><p>${esc(t("units_body"))}</p></div></div>
    <div class="filters" role="group">
      <a class="chip" href="#/units" aria-pressed="${!only}">${esc(t("filter_all"))}</a>
      ${subjects.map((s) => `<a class="chip" href="#/units?subject=${esc(s.code)}" aria-pressed="${s.code === only}" style="text-decoration:none">${esc(subjName(s))}<span class="n">${s.unit_count}</span></a>`).join("")}
    </div>
    ${shown.map((s) => {
      const list = units.filter((u) => u.subject === s.code);
      return `<div class="subject-block">
        <h2>${esc(subjName(s))}<small class="meta">${esc(state.lang === "en" ? s.kla_en : s.kla_zh)}${s.mode === "skills" ? `${P().comma}${esc(t("mode_skills"))}` : ""}</small></h2>
        <ul class="doc-list">${s.documents.map((d) => `<li><strong>${esc(state.lang === "en" && d.title_en ? d.title_en : d.title_zh)}</strong>
          <span class="meta">${esc(d.status)}</span>　${docLinks(d)}</li>`).join("")}
          ${s.glossaries.map((g) => `<li>${esc(t("glossary"))}${P().colon}<a href="${esc(g.url)}" target="_blank" rel="noopener">${esc(g.title)}</a></li>`).join("")}
          ${s.documents_page ? `<li><a href="${esc(s.documents_page)}" target="_blank" rel="noopener">${esc(t("open_docs"))}</a></li>` : ""}
        </ul>
        ${list.length ? `<div class="table-wrap"><table class="data">
          <thead><tr><th>${esc(t("col_unit"))}</th><th>${esc(t("col_levels"))}</th><th>${esc(t("col_lang"))}</th>
            <th>${esc(t("col_core"))}</th><th>${esc(t("col_ext"))}</th><th>${esc(t("col_check"))}</th></tr></thead>
          <tbody>${list.map((u) => `<tr>
            <td><a href="#/units/${esc(u.unit_id)}">${esc(u.title)}</a>${u.unlocated_count ? `<br><span class="warn-inline">${esc(t("unlocated", { n: u.unlocated_count }))}</span>` : ""}</td>
            <td>${u.levels.length ? u.levels.join(P().list) : `<span class="meta">${esc(t("unassigned"))}</span>`}</td>
            <td>${esc(t(u.language === "en" ? "lang_en" : "lang_zh"))}</td>
            <td class="num">${u.core_count}</td><td class="num">${u.extension_count}</td>
            <td class="status ${u.verified ? "status-done" : "status-running"}">${u.verified ? esc(t("verified_by", { n: u.verified_by })) : esc(t("unverified"))}</td></tr>`).join("")}
          </tbody></table></div>`
        : `<div class="empty"><p>${esc(t("how_to_load"))}</p></div>`}
      </div>`;
    }).join("")}`, "units");
}

async function viewUnit(id) {
  const u = await api(`/units/${encodeURIComponent(id)}`);
  const kindLabel = (o) => (o.kind === "key_experiment" ? t("key_exp") : o.subtopic || t("outcome"));
  setView(`
    <div class="page-head"><div><h1>${esc(u.title)}</h1>
      <p>${esc(u.source)}${u.source_url ? `<br><a href="${esc(u.source_url)}" target="_blank" rel="noopener">${esc(t("unit_open_pdf"))}</a>` : ""}</p></div></div>
    <div class="notice ${u.verified ? "notice-info" : ""}">${u.verified ? esc(t("verified_note", { n: u.verified_by })) : esc(t("verify_hint"))}</div>
    <div class="table-wrap" style="margin-top:16px"><table class="data">
      <thead><tr><th>${esc(t("col_topic"))}</th><th>${esc(t("col_content"))}</th><th>${esc(t("col_page"))}</th></tr></thead>
      <tbody>${u.objectives.map((o) => `<tr>
        <td>${esc(kindLabel(o))}</td>
        <td>${esc(o.text)}${o.tier === "extension" ? ` <span class="tag tag-ext">${esc(t("ext_tag"))}</span>` : ""}${o.skill ? ` <span class="tag">${esc(t("skill_tag"))}</span>` : ""}
          ${o.notes ? `<br><span class="warn-inline">${esc(o.notes)}</span>` : ""}</td>
        <td class="num">${o.page ?? "—"}</td></tr>`).join("")}
      </tbody></table></div>
    <section style="margin-top:28px">
      <span class="field-label">${esc(t("levels_label"))}</span>
      <div class="level-boxes">${LEVELS.map((lv) => `<label class="check"><input type="checkbox" name="lv" value="${lv}" ${u.levels.includes(lv) ? "checked" : ""}><span>${lv}</span></label>`).join("")}</div>
      <div class="btn-row">
        ${u.verified ? `<button class="btn" id="unverify" type="button">${esc(t("revoke"))}</button>
                        <button class="btn" id="save" type="button">${esc(t("save_levels"))}</button>`
                     : `<button class="btn btn-primary" id="verify" type="button">${esc(t("confirm"))}</button>`}
      </div>
      <p class="form-error" id="verify-error" role="alert"></p>
    </section>`, "units");

  const submit = async (verified, msgKey) => {
    if (verified && !teacher()) {
      $("#verify-error").textContent = t("need_name");
      $("#teacher-name").focus();
      return;
    }
    try {
      const levels = $$('input[name="lv"]:checked').map((c) => c.value);
      await api(`/units/${encodeURIComponent(id)}/verify`, jsonBody("POST", { verified, teacher: teacher() || u.verified_by, levels }));
      state.subjects = null;
      toast(t(msgKey));
      viewUnit(id);
    } catch (ex) { $("#verify-error").textContent = ex.message; }
  };
  $("#verify")?.addEventListener("click", () => submit(true, "confirmed_toast"));
  $("#unverify")?.addEventListener("click", () => submit(false, "revoked_toast"));
  $("#save")?.addEventListener("click", () => submit(true, "saved_toast"));
}

/* ---------- 路由 ---------- */
async function route() {
  clearTimeout(state.pollTimer);
  const [path, query = ""] = location.hash.replace(/^#/, "").split("?");
  const [, view = "", id] = path.split("/");
  const params = new URLSearchParams(query);
  try {
    if (view === "new") await viewNew(params);
    else if (view === "reviews" && id) await viewReview(id);
    else if (view === "reviews") await viewReviews();
    else if (view === "units" && id) await viewUnit(decodeURIComponent(id));
    else if (view === "units") await viewUnits(params);
    else await viewHome();
    main.focus({ preventScroll: true });
  } catch (err) {
    errorView(err, view || "home");
  }
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  try {
    await api("/login", jsonBody("POST", { code: $("#login-code").value }));
    $("#login-dialog").close();
    state.subjects = null;
    route();
  } catch (err) { $("#login-error").textContent = err.message; }
});

(async function init() {
  try { state.lang = localStorage.getItem("hkr-lang") || "zh"; } catch { /* 預設中文 */ }
  try { $("#teacher-name").value = localStorage.getItem("hkr-teacher") || ""; } catch { /* 略過 */ }
  $("#teacher-name").addEventListener("change", () => { try { localStorage.setItem("hkr-teacher", teacher()); } catch { /* 略過 */ } });
  $$(".lang-switch button").forEach((b) => b.addEventListener("click", () => setLang(b.dataset.lang)));
  applyStatic();
  try {
    state.config = await api("/config");
  } catch (err) {
    return errorView(err, "home");
  }
  applyStatic();
  window.addEventListener("hashchange", route);
  route();
})();
