"use strict";
/* 瀏覽器版審查引擎：在沒有伺服器的網站（例如 GitHub Pages）上執行審查。
   每一部分對應 app/services/ 及 app/prompts/ 的 Python 程式；修改其中一邊時，另一邊要一起改。
   tests/test_static_engine.py 會以 Node 執行本檔，逐項核對兩邊的結果。 */
(function (root, factory) {
  const engine = factory();
  if (typeof module === "object" && module.exports) module.exports = engine;
  else root.HKREngine = engine;
})(typeof self !== "undefined" ? self : this, function () {
  const SETTINGS = {
    chunk_size: 700, chunk_overlap: 100, evidence_top_k: 3, curriculum_top_k: 4,
    accuracy_batch_size: 4, coverage_group_size: 8, min_confidence: 0.5,
    llm_max_tokens: 8000, llm_concurrency: 4,
  };
  const cps = (s) => Array.from(s);
  const cpLen = (s) => cps(s).length;
  const pyStrip = (s) => s.replace(/^\s+|\s+$/g, "");

  /* ---------- OpenCC（對應 opencc-python-reimplemented） ---------- */
  const OPENCC_SPLIT = /(\s+|-|,|\.|\?|!|\*|　|，|。|、|；|：|？|！|…|“|”|‘|’|『|』|「|」|﹁|﹂|—|－|（|）|《|》|〈|〉|～|．|／|＼|︒|︑|︔|︓|︿|﹀|︹|︺|︙|︐|［|﹇|］|﹈|︕|︖|︰|︳|︴|︽|︾|︵|︶|｛|︷|｝|︸|﹃|﹄|【|︻|】|︼)/;

  function parseDict(text) {
    const map = new Map();
    let max = 1, min = 1000;
    for (const raw of text.split("\n")) {
      const line = pyStrip(raw);
      if (!line) continue;
      const [key, value] = line.split("\t");
      map.set(key, value);
      const n = cpLen(key);
      if (n > max) max = n;
      if (n < min) min = n;
    }
    return { max, min, map };
  }

  function findMatch(arr, d, hint) {
    const n = arr.length;
    let len = Math.min(n, d.max);
    if (hint) len = Math.min(len, hint);
    for (; len >= d.min; len--) {
      for (let i = 0; i + len <= n; i++) {
        const value = d.map.get(arr.slice(i, i + len).join(""));
        if (value !== undefined) {
          return { value: value.split(" ")[0], left: i > 0 ? arr.slice(0, i).join("") : "",
                   right: i + len < n ? arr.slice(i + len).join("") : "", len };
        }
      }
    }
    return null;
  }

  function convertSegment(text, chain) {
    let current = text;
    for (const group of chain) {
      const rootNode = { value: current, hint: null, left: null, right: null };
      let working = [rootNode], unmatched = [];
      for (const d of group) {
        while (working.length) {
          const node = working.pop();
          const m = findMatch(cps(node.value), d, node.hint);
          if (m) {
            node.value = m.value;
            node.hint = null;
            if (m.left) { node.left = { value: m.left, hint: m.len, left: null, right: null }; working.push(node.left); }
            if (m.right) { node.right = { value: m.right, hint: m.len, left: null, right: null }; working.push(node.right); }
          } else {
            node.hint = null;
            unmatched.push(node);
          }
        }
        [working, unmatched] = [unmatched, working];
      }
      const out = [], stack = [];
      let node = rootNode;
      while (node || stack.length) {
        while (node) { stack.push(node); node = node.left; }
        node = stack.pop();
        out.push(node.value);
        node = node.right;
      }
      current = out.join("");
    }
    return current;
  }

  function makeConverter(chain) {
    return (text) => text.split(OPENCC_SPLIT).map((part, i) => (i % 2 ? part : convertSegment(part, chain))).join("");
  }

  /* ---------- 文件解析（parser.py） ---------- */
  const SENTENCE_END = "。！？；：」』）!?;:)";
  const CJK = "\\u3000-\\u303f\\u3400-\\u9fff\\uff00-\\uffef";
  const CJK_SPACES = new RegExp(`(?<=[${CJK}])[ \\t\\u00a0]+(?=[${CJK}])`, "g");
  const collapseCjkSpaces = (text) => text.replace(CJK_SPACES, "");

  function splitLines(text) {
    const lines = text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/);
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    return lines;
  }

  function joinBrokenLines(text) {
    const out = [];
    for (const raw of splitLines(text)) {
      const line = collapseCjkSpaces(pyStrip(raw));
      if (!line) { out.push("\n\n"); continue; }
      const last = out[out.length - 1];
      if (out.length && last !== "\n\n" && !SENTENCE_END.includes(last.slice(-1))) out[out.length - 1] += line;
      else out.push(line);
    }
    return pyStrip(out.join("\n").replace(/\n{3,}/g, "\n\n"));
  }

  function decodeText(bytes) {
    for (const enc of ["utf-8", "big5", "gb18030"]) {
      try { return new TextDecoder(enc, { fatal: true }).decode(bytes); } catch { /* 試下一種編碼 */ }
    }
    throw new Error("無法辨認文字檔編碼，請另存為 UTF-8");
  }

  const PDFJS = "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build/";
  async function parsePdf(bytes) {
    const pdfjs = await import(`${PDFJS}pdf.min.mjs`);
    pdfjs.GlobalWorkerOptions.workerSrc = `${PDFJS}pdf.worker.min.mjs`;
    let doc;
    try { doc = await pdfjs.getDocument({ data: bytes }).promise; } catch (exc) { throw new Error(`PDF 無法開啟：${exc.message}`); }
    const pages = [];
    for (let i = 1; i <= doc.numPages; i++) {
      const content = await (await doc.getPage(i)).getTextContent();
      const text = content.items.map((it) => (it.str || "") + (it.hasEOL ? "\n" : "")).join("");
      pages.push({ page_no: i, text: joinBrokenLines(text) });
    }
    const total = pages.reduce((n, p) => n + p.text.length, 0);
    if (pages.length && total < 30 * pages.length) throw new Error("PDF 幾乎沒有可擷取文字，可能是掃描檔；暫不支援 OCR");
    return pages.filter((p) => p.text);
  }

  async function parseDocument(filename, bytes) {
    const name = (filename || "").toLowerCase();
    if (name.endsWith(".pdf")) return parsePdf(bytes);
    if (name.endsWith(".txt") || name.endsWith(".md")) return [{ page_no: null, text: joinBrokenLines(decodeText(bytes)) }];
    throw new Error("只支援 PDF、TXT 或 MD 檔");
  }

  /* ---------- 切塊（chunker.py）及語言（language.py） ---------- */
  function chunkPages(pages, size = SETTINGS.chunk_size, overlap = SETTINGS.chunk_overlap) {
    const chunks = [];
    for (const page of pages) {
      const prefix = page.page_no != null ? `p${page.page_no}` : "t";
      let buf = "", n = 0;
      const emit = (text) => { n += 1; chunks.push({ chunk_id: `${prefix}-c${n}`, page: page.page_no, text: pyStrip(text) }); };
      for (const rawPara of page.text.split("\n\n")) {
        const para = pyStrip(rawPara);
        const sentences = para.split(/(?<=[。！？；!?;])/).filter((s) => pyStrip(s));
        for (const sent of sentences.length ? sentences : [para]) {
          if (buf && cpLen(buf) + cpLen(sent) > size) {
            emit(buf);
            buf = overlap ? cps(buf).slice(-overlap).join("") : "";
          }
          buf += sent;
        }
        buf += "\n";
      }
      if (pyStrip(buf)) emit(buf);
    }
    return chunks;
  }

  function detectLanguage(text) {
    const cjk = (text.match(/[\u3400-\u9fff]/g) || []).length;
    const latinWords = (text.match(/[A-Za-z]/g) || []).length / 5;
    if (cjk === 0 && latinWords === 0) return "zh";
    return cjk >= latinWords * 0.5 ? "zh" : "en";
  }

  /* ---------- 字形檢查（script_check.py） ---------- */
  function makeScriptChecker(opencc) {
    const simplifiedOnly = new Map(), dualUse = new Set();
    for (const line of opencc.STCharacters.split("\n")) {
      if (!line.includes("\t")) continue;
      const [key, rest] = [line.slice(0, line.indexOf("\t")), line.slice(line.indexOf("\t") + 1)];
      const cands = rest.split(/\s+/).filter(Boolean);
      if (cands.includes(key)) { if (cands.length > 1) dualUse.add(key); } else simplifiedOnly.set(key, cands[0]);
    }
    const dicts = Object.fromEntries(Object.entries(opencc).map(([k, v]) => [k, parseDict(v)]));
    const t2s = makeConverter([[dicts.TSPhrases, dicts.TSCharacters]]);
    const s2hk = makeConverter([[dicts.STPhrases, dicts.STCharacters], [dicts.HKVariants]]);
    const exceptions = new Set(["了"]);

    function scanSentence(sentence) {
      const chars = cps(sentence);
      const contextual = cps(s2hk(t2s(sentence)));
      const aligned = contextual.length === chars.length;
      const hits = [];
      chars.forEach((ch, i) => {
        if (simplifiedOnly.has(ch)) {
          const rep = aligned && contextual[i] !== ch ? contextual[i] : simplifiedOnly.get(ch);
          hits.push({ index: i, char: ch, replacement: rep, certain: true });
        } else if (dualUse.has(ch) && !exceptions.has(ch) && aligned && contextual[i] !== ch) {
          hits.push({ index: i, char: ch, replacement: contextual[i], certain: false });
        }
      });
      return hits;
    }

    function check(chunks, lang = "zh") {
      const findings = [], seen = new Set();
      let total = 0;
      for (const chunk of chunks) {
        for (const m of chunk.text.matchAll(/[^。！？；!?;\n]+[。！？；!?;]?/gu)) {
          const sentence = pyStrip(m[0]);
          if (!sentence || seen.has(sentence)) continue;
          const hits = scanSentence(sentence);
          if (!hits.length) continue;
          seen.add(sentence);
          total += hits.length;
          const certain = hits.some((h) => h.certain);
          const fixed = cps(sentence);
          for (const h of hits) fixed[h.index] = h.replacement;
          const pairs = [...new Set(hits.map((h) => `${h.char}→${h.replacement}`))].join("、");
          let issue, suggestion;
          if (lang === "en") {
            const label = certain ? "simplified Chinese characters"
              : "possible simplified characters (shared forms; check context, e.g. the surnames 范 and 余 are correct)";
            issue = `Found ${label}, ${hits.length} character(s): ${pairs}`;
            suggestion = "Hong Kong materials must use traditional characters. Replace the sentence with the patch and check the conversion.";
          } else {
            const label = certain ? "簡體字" : "疑似簡體字（簡繁共用字，請按語境確認，例如姓氏「范」「余」可保留）";
            issue = `發現${label}，共 ${hits.length} 字：${pairs}`;
            suggestion = "香港教材必須使用繁體字。請以補丁替換整句，並人工覆核轉換結果。";
          }
          findings.push(finding({
            category: "simplified", severity: certain ? "high" : "medium", chunk_id: chunk.chunk_id,
            original_text: sentence, issue, suggestion,
            patch: { action: "replace", anchor_text: sentence, new_text: fixed.join("") },
            confidence: certain ? 1.0 : 0.8,
          }));
        }
      }
      return [findings, { simplified_chars: total, sentences: findings.length, passed: total === 0 }];
    }
    return { check, scanSentence, t2s, s2hk };
  }

  function finding(f) {
    return { category: f.category, severity: f.severity, chunk_id: f.chunk_id ?? null, original_text: f.original_text ?? "",
             issue: f.issue, suggestion: f.suggestion, patch: f.patch ?? null, curriculum_refs: f.curriculum_refs ?? [],
             confidence: f.confidence };
  }

  /* ---------- 用語檢查（terminology.py） ---------- */
  const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const WORD = "[\\p{L}\\p{N}_]";   // Python \b 以 Unicode 字元判斷詞界，JavaScript 的 \b 只認 ASCII
  const wordBounded = (body, flags) => new RegExp(`(?<!${WORD})(?:${body})(?!${WORD})`, `u${flags}`);
  const isUpper = (s) => s === s.toUpperCase() && s !== s.toLowerCase();

  function makeTerminologyChecker(glossaries, officialTexts) {
    const entries = new Map();
    for (const list of glossaries) {
      for (const e of list) {
        const entry = { term: e.term, preferred: e.preferred, note: e.note ?? "",
                        context_sensitive: !!e.context_sensitive, lang: e.lang ?? "zh" };
        entries.set(entry.lang === "en" ? entry.term.toLowerCase() : entry.term, entry);
      }
    }
    const official = officialTexts.join("\n"), officialLower = official.toLowerCase();
    for (const e of entries.values()) {
      const hit = e.lang === "en" ? wordBounded(escapeRe(e.term), "").test(officialLower) : official.includes(e.term);
      if (!e.context_sensitive && hit) {
        e.context_sensitive = true;
        e.note = pyStrip(`${e.note}（此詞見於已載入的教育局課程文件，屬官方用語，請按語境判斷）`);
      }
    }
    const byLen = (lang) => [...entries.values()].filter((e) => e.lang === lang).map((e) => e.term)
      .sort((a, b) => cpLen(b) - cpLen(a));
    const zh = byLen("zh"), en = byLen("en");
    const zhPattern = zh.length ? new RegExp(zh.map(escapeRe).join("|"), "gu") : null;
    const enPattern = en.length ? wordBounded(en.map(escapeRe).join("|"), "gi") : null;
    const lookup = (m) => entries.get(m) || entries.get(m.toLowerCase());

    function windowAt(text, start, end, words) {
      const lineStart = text.lastIndexOf("\n", start - 1) + 1;
      let lineEnd = text.indexOf("\n", end);
      if (lineEnd === -1) lineEnd = text.length;
      let s = Math.max(lineStart, start - 12), e = Math.min(lineEnd, end + 12);
      if (words) {
        while (s > lineStart && !/\s/.test(text[s - 1])) s--;
        while (e < lineEnd && !/\s/.test(text[e])) e++;
      }
      const raw = text.slice(s, e), lead = raw.length - raw.replace(/^\s+/, "").length;
      return [pyStrip(raw), start - s - lead, end - s - lead];
    }

    function check(chunks) {
      const findings = [], hints = {}, firstHit = new Map(), counts = {};
      for (const chunk of chunks) {
        for (const pattern of [zhPattern, enPattern]) {
          if (!pattern) continue;
          for (const m of chunk.text.matchAll(pattern)) {
            const entry = lookup(m[0]);
            if (entry.context_sensitive) {
              (hints[chunk.chunk_id] ||= []).push({ chunk_id: chunk.chunk_id, term: entry.term, preferred: entry.preferred, note: entry.note });
              continue;
            }
            counts[entry.term] = (counts[entry.term] || 0) + 1;
            if (!firstHit.has(entry.term)) firstHit.set(entry.term, [chunk, m]);
          }
        }
      }
      for (const [term, [chunk, m]] of firstHit) {
        const entry = entries.get(term) || entries.get(term.toLowerCase());
        const preferred = entry.preferred.split("／")[0];
        const replacement = isUpper(m[0]) && m[0].length > 1 ? preferred.toUpperCase()
          : isUpper(m[0].slice(0, 1)) ? preferred.slice(0, 1).toUpperCase() + preferred.slice(1) : preferred;
        const [snippet, a, b] = windowAt(chunk.text, m.index, m.index + m[0].length, entry.lang === "en");
        let issue, suggestion;
        if (entry.lang === "en") {
          issue = `'${m[0]}' is not British spelling (${counts[term]} occurrence(s)). ${entry.note}`;
          suggestion = `Use '${entry.preferred}' and keep the spelling consistent throughout.`;
        } else {
          issue = pyStrip(`「${term}」並非香港常用詞（全文共 ${counts[term]} 處）。${entry.note}`);
          suggestion = `建議改用「${entry.preferred}」，並全文統一。`;
        }
        findings.push(finding({
          category: "terminology", severity: "low", chunk_id: chunk.chunk_id, original_text: snippet, issue, suggestion,
          patch: { action: "replace", anchor_text: snippet, new_text: snippet.slice(0, a) + replacement + snippet.slice(b) },
          confidence: 0.9,
        }));
      }
      return [findings, hints];
    }
    return { check };
  }

  /* ---------- 檢索：字元二元組相似度（瀏覽器版不載入 embedding 模型） ---------- */
  function bigrams(text) {
    const v = new Map();
    const a = cps(text);
    for (let i = 0; i < a.length - 1; i++) { const k = a[i] + a[i + 1]; v.set(k, (v.get(k) || 0) + 1); }
    let norm = 0;
    for (const x of v.values()) norm += x * x;
    return { v, norm: Math.sqrt(norm) || 1 };
  }
  function similarity(a, b) {
    const [small, big] = a.v.size < b.v.size ? [a, b] : [b, a];
    let dot = 0;
    for (const [k, x] of small.v) dot += x * (big.v.get(k) || 0);
    return dot / (a.norm * b.norm);
  }
  const topK = (query, items, k) => items.map((it) => [similarity(query, it.vec), it]).sort((x, y) => y[0] - x[0])
    .slice(0, k).map(([, it]) => it.item);

  function objectiveDocument(o) {
    const terms = o.key_terms.length ? `｜關鍵詞：${o.key_terms.join("、")}` : "";
    return `${o.subtopic ? `${o.subtopic}｜` : ""}${o.text}${terms}`;
  }

  function retrieve(unit, objectives, chunks, batches) {
    const indexed = chunks.map((c) => ({ item: c, vec: bigrams(c.text) }));
    const evidence = {};
    for (const o of objectives) {
      const keyword = chunks.filter((c) => o.key_terms.some((t) => c.text.includes(t))).slice(0, 2);
      const found = [...keyword, ...topK(bigrams(`${o.text} ${o.key_terms.join(" ")}`), indexed, SETTINGS.evidence_top_k)];
      evidence[o.id] = [...new Map(found.map((c) => [c.chunk_id, c])).values()].slice(0, SETTINGS.evidence_top_k + 2);
    }
    const unitObjs = unit.objectives.map((o) => ({ item: o, vec: bigrams(objectiveDocument(o)) }));
    const related = batches.map((batch) => {
      const objs = new Map();
      for (const c of batch) for (const o of topK(bigrams(c.text), unitObjs, SETTINGS.curriculum_top_k)) objs.set(o.id, o);
      return [...objs.values()];
    });
    return [evidence, related];
  }

  /* ---------- Prompt（prompts/profiles.py、prompts/templates.py） ---------- */
  const htmlEscape = (s, quote = true) => {
    let out = String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    if (quote) out = out.replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
    return out;
  };

  function buildSystemPrompt(prompts, profile, reportLang = "zh") {
    return prompts.system_template
      .replaceAll("{{SUBJECT_PROFILE}}", prompts.profiles[profile] ?? "")
      .replaceAll("{{OUTPUT_LANGUAGE}}", prompts.output_language[reportLang] ?? prompts.output_language.zh);
  }

  const chunkXml = (c) => `<chunk id="${c.chunk_id}"${c.page != null ? ` page="${c.page}"` : ""}>\n${htmlEscape(c.text, false)}\n</chunk>`;
  function objectiveXml(o) {
    let attrs = ` kind="${o.kind}" tier="${o.tier}"`;
    if (o.subtopic) attrs += ` subtopic="${htmlEscape(o.subtopic)}"`;
    if (o.skill) attrs += ' skill="true"';
    if (o.key_terms.length) attrs += ` key_terms="${o.key_terms.join("、")}"`;
    return `<objective id="${o.id}"${attrs}>${htmlEscape(o.text, false)}</objective>`;
  }

  const SKILLS_NOTE = `
0. 這是語文科。<curriculum> 中的學習重點是老師為這份教材選定的目標：判斷教材是否真正讓學生學習及練習該能力。
   只解釋知識而沒有練習，屬 partial。`;

  function buildCoveragePrompt(unit, pairs, mode = "content") {
    const curriculum = pairs.map(([o]) => objectiveXml(o)).join("\n");
    const material = pairs.map(([o, chunks]) => `<evidence objective_id="${o.id}">\n` + chunks.map(chunkXml).join("\n") + "\n</evidence>").join("\n");
    return `<task>coverage</task>
<unit id="${unit.unit_id}">${htmlEscape(unit.title)}</unit>

<curriculum>
${curriculum}
</curriculum>

<material>
${material}
</material>

<instructions>${mode === "skills" ? SKILLS_NOTE : ""}
1. <curriculum> 中每一個 objective 都必須輸出剛好一個 item，不可遺漏，不可新增。
2. <material> 中每組 <evidence> 是系統按語意為該學習重點檢索出的教材片段，可能不完整；若片段與學習重點無關，視作找不到。
3. status：covered = 有解釋且正確；partial = 只提及名詞或解釋不完整；missing = 找不到。
4. kind="key_experiment" 是教育局指定的重點實驗：教材須有該實驗的目的、步驟（或明確的實驗安排）及安全提示才算 covered；只在正文提及相關概念屬 partial。
5. skill="true" 是技能類學習成果：教材須有讓學生實際操作或練習的活動才算 covered。
6. partial 或 missing 必須附 patch：action 用 insert_after，anchor_text 選最適合插入位置的原文句子（逐字複製），沒有合適位置則留空字串。
7. 完成後呼叫 submit_coverage 工具。
</instructions>`;
  }

  function buildAccuracyPrompt(chunks, related, hints) {
    const curriculum = related.map(objectiveXml).join("\n") || "（無）";
    const material = chunks.map(chunkXml).join("\n");
    const hintXml = hints.map((h) => `<hint chunk_id="${h.chunk_id}" term="${h.term}" hk_preferred="${h.preferred}">${htmlEscape(h.note)}</hint>`).join("\n") || "（無）";
    return `<task>accuracy</task>

<curriculum>
${curriculum}
</curriculum>

<material>
${material}
</material>

<term_hints>
${hintXml}
</term_hints>

<instructions>
1. 逐段審查 factual_error、imprecise、terminology、language、safety 五類問題。不要報告 omission 及 simplified（另有流程處理）。
2. <curriculum> 只作參考，用於填寫 curriculum_refs；不是本任務要檢查的覆蓋清單。
3. 每項 finding 必須填寫 chunk_id。
4. <term_hints> 的詞語須按上下文判斷，只在確屬不當用法時才報告。其他明顯的非香港用語已由規則引擎處理，你不必重複報告「視頻」「信息」一類詞語。
5. 完成後呼叫 submit_findings 工具。
</instructions>`;
  }

  /* ---------- 審查者：關鍵詞比對（offline.py）或 Claude（reviewer.py） ---------- */
  const offlineReviewer = (reportLang) => ({
    async assessCoverage(system, prompt, pairs) {
      return { items: pairs.map(([o, chunks]) => {
        const text = chunks.map((c) => c.text).join("");
        const terms = o.key_terms || [];
        const found = terms.filter((t) => text.includes(t));
        const status = terms.length && found.length === terms.length ? "covered" : found.length ? "partial" : "missing";
        const rationale = reportLang === "en"
          ? `Demo mode, keyword match: ${found.length}/${terms.length} key terms found${found.length ? ` (${found.join(", ")})` : ""}.`
          : `示範模式以關鍵詞比對判斷：找到 ${found.length}／${terms.length} 個關鍵詞${found.length ? `（${found.join("、")}）` : ""}。`;
        return { objective_id: o.id, status, rationale, patch: null,
                 evidence_chunk_ids: chunks.filter((c) => found.some((t) => c.text.includes(t))).map((c) => c.chunk_id) };
      }) };
    },
    async reviewAccuracy() { return { findings: [] }; },
  });

  function validate(schema, value, path, errors) {
    if (schema.anyOf) {
      for (const option of schema.anyOf) {
        const errs = [];
        const out = validate(option, value, path, errs);
        if (!errs.length) return out;
      }
      errors.push(`${path}: 格式不符`);
      return value;
    }
    if (schema.enum && !schema.enum.includes(value)) { errors.push(`${path}: 只可為 ${schema.enum.join(" / ")}`); return value; }
    const fail = (msg) => { errors.push(`${path}: ${msg}`); return value; };
    switch (schema.type) {
      case "object": {
        if (value === null || typeof value !== "object" || Array.isArray(value)) return fail("須為物件");
        const out = { ...value };
        for (const [key, sub] of Object.entries(schema.properties || {})) {
          if (key in value) out[key] = validate(sub, value[key], `${path}.${key}`, errors);
          else if ("default" in sub) out[key] = JSON.parse(JSON.stringify(sub.default));
          else if ((schema.required || []).includes(key)) errors.push(`${path}.${key}: 必須填寫`);
        }
        return out;
      }
      case "array":
        if (!Array.isArray(value)) return fail("須為陣列");
        return value.map((x, i) => validate(schema.items || {}, x, `${path}[${i}]`, errors));
      case "string": return typeof value === "string" ? value : fail("須為文字");
      case "integer": return Number.isInteger(value) ? value : fail("須為整數");
      case "number":
        if (typeof value !== "number" || !Number.isFinite(value)) return fail("須為數字");
        if (schema.minimum != null && value < schema.minimum) return fail(`不可小於 ${schema.minimum}`);
        if (schema.maximum != null && value > schema.maximum) return fail(`不可大於 ${schema.maximum}`);
        return value;
      case "boolean": return typeof value === "boolean" ? value : fail("須為 true 或 false");
      case "null": return value === null ? value : fail("須為 null");
      default: return value;
    }
  }

  function claudeReviewer({ apiKey, model, schemas }) {
    let active = 0;
    const queue = [];
    const limit = async (fn) => {
      if (active >= SETTINGS.llm_concurrency) await new Promise((r) => queue.push(r));
      active++;
      try { return await fn(); } finally { active--; queue.shift()?.(); }
    };
    async function callTool(system, user, name, description, schema) {
      const tool = { name, description, input_schema: schema };
      let messages = [{ role: "user", content: user }];
      for (let attempt = 0; attempt < 2; attempt++) {
        const data = await limit(async () => {
          const res = await fetch("https://api.anthropic.com/v1/messages", {
            method: "POST",
            headers: { "content-type": "application/json", "x-api-key": apiKey, "anthropic-version": "2023-06-01",
                       "anthropic-dangerous-direct-browser-access": "true" },
            body: JSON.stringify({ model, max_tokens: SETTINGS.llm_max_tokens, tools: [tool],
                                   system: [{ type: "text", text: system, cache_control: { type: "ephemeral" } }],
                                   tool_choice: { type: "tool", name }, messages }),
          });
          const body = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(`Anthropic API ${res.status}：${body.error?.message || res.statusText}`);
          return body;
        });
        const block = (data.content || []).find((b) => b.type === "tool_use");
        if (!block) throw new Error(`模型未有呼叫 ${name}（stop_reason=${data.stop_reason}）`);
        const errors = [];
        const out = validate(schema, block.input, "input", errors);
        if (!errors.length) return out;
        messages = [...messages, { role: "assistant", content: data.content }, { role: "user", content: [{
          type: "tool_result", tool_use_id: block.id, is_error: true,
          content: `輸出不符合 schema，請修正後重新呼叫 ${name}：${errors.join("；")}` }] }];
      }
      throw new Error(`${name} 輸出兩次驗證失敗`);
    }
    return {
      assessCoverage: (system, prompt) => callTool(system, prompt, "submit_coverage", "提交課程重點覆蓋判斷結果", schemas.coverage),
      reviewAccuracy: (system, prompt) => callTool(system, prompt, "submit_findings", "提交教材審查問題清單", schemas.findings),
    };
  }

  /* ---------- 驗證與組裝（pipeline.py _assemble、validation.py） ---------- */
  const MSG = {
    zh: {
      unverified: "此單元的課程資料尚未經科任老師核對，遺漏判斷的依據僅供參考。",
      script_fail: "字形檢查不合格：發現 {n} 個簡體字（{s} 句），發佈前必須修正。",
      cov_fail: "覆蓋檢查有一組失敗：{e}", acc_fail: "準確性審查有一批失敗：{e}",
      fake_id: "已忽略模型虛構的課程 ID：{i}", unassessed: "模型未有回傳此項評估",
      missing: "課程重點「{t}」未見於教材。{r}", partial: "課程重點「{t}」只部分處理。{r}",
      with_patch: "建議按以下補丁補充內容。", no_patch: "請補充此課程重點的內容。",
      cross_lang: "教材為{m}，課程資料為{c}；系統已跨語言對照，請留意術語是否與課程文件對應。",
      lang_names: { zh: "中文", en: "英文" },
    },
    en: {
      unverified: "This unit's curriculum data has not yet been checked by a teacher; treat omission results as provisional.",
      script_fail: "Script check failed: {n} simplified Chinese character(s) in {s} sentence(s). Fix before publishing.",
      cov_fail: "A coverage check batch failed: {e}", acc_fail: "An accuracy review batch failed: {e}",
      fake_id: "Ignored a curriculum ID invented by the model: {i}", unassessed: "The model did not return an assessment for this item",
      missing: "Learning point \"{t}\" is not found in the material. {r}", partial: "Learning point \"{t}\" is only partly addressed. {r}",
      with_patch: "Add the content in the patch below.", no_patch: "Add content covering this learning point.",
      cross_lang: "The material is in {m} but the curriculum data is in {c}; matching was done across languages, so check that terms match the curriculum document.",
      lang_names: { zh: "Chinese", en: "English" },
    },
  };
  const fmt = (s, vars) => s.replace(/\{(\w+)\}/g, (_, k) => String(vars[k]));
  const normalizeForMatch = (text) => text.normalize("NFKC").split(/\s+/).join("");
  const quoteInText = (quote, normFull) => { const q = normalizeForMatch(quote); return !!q && normFull.includes(q); };
  const SEVERITY_ORDER = { high: 0, medium: 1, low: 2 };
  const errText = (e) => (e && e.message) || String(e);

  function assemble(ctx, unit, objectives, chunks, covResults, accResults, ruleFindings, scriptSummary, opts) {
    const msg = MSG[opts.reportLang] || MSG.zh;
    const warnings = [];
    if (!unit.verified) warnings.push(msg.unverified);
    if (!scriptSummary.passed) warnings.push(fmt(msg.script_fail, { n: scriptSummary.simplified_chars, s: scriptSummary.sentences }));
    if (opts.materialLang !== unit.language) {
      warnings.push(fmt(msg.cross_lang, { m: msg.lang_names[opts.materialLang], c: msg.lang_names[unit.language] }));
    }
    const chunkMap = new Map(chunks.map((c) => [c.chunk_id, c]));
    const normFull = normalizeForMatch(chunks.map((c) => c.text).join("\n"));
    const unitIds = new Set(objectives.map((o) => o.id));

    const coverage = new Map();
    for (const r of covResults) {
      if (r instanceof Error) { warnings.push(fmt(msg.cov_fail, { e: errText(r) })); continue; }
      for (const item of r.items) {
        if (!unitIds.has(item.objective_id)) { warnings.push(fmt(msg.fake_id, { i: item.objective_id })); continue; }
        coverage.set(item.objective_id, { ...item, evidence_chunk_ids: (item.evidence_chunk_ids || []).filter((i) => chunkMap.has(i)) });
      }
    }
    const covList = objectives.map((o) => {
      const item = coverage.get(o.id) || { objective_id: o.id, status: "unassessed", evidence_chunk_ids: [], rationale: msg.unassessed, patch: null };
      return { objective_id: item.objective_id, status: item.status, evidence_chunk_ids: item.evidence_chunk_ids,
               rationale: item.rationale, patch: item.patch ?? null, objective_text: o.text };
    });

    const raw = ruleFindings.map((f) => ["rule", f]);
    for (const cr of covList) {
      if (cr.status !== "missing" && cr.status !== "partial") continue;
      const missing = cr.status === "missing";
      raw.push(["llm", finding({
        category: "omission", severity: missing ? "high" : "medium",
        chunk_id: cr.evidence_chunk_ids[0] ?? null,
        issue: fmt(msg[missing ? "missing" : "partial"], { t: cr.objective_text, r: cr.rationale }),
        suggestion: cr.patch ? msg.with_patch : msg.no_patch,
        patch: cr.patch, curriculum_refs: [cr.objective_id], confidence: 0.8,
      })]);
    }
    for (const r of accResults) {
      if (r instanceof Error) { warnings.push(fmt(msg.acc_fail, { e: errText(r) })); continue; }
      for (const f of r.findings) if (f.category !== "omission" && f.category !== "simplified") raw.push(["llm", finding(f)]);
    }

    const final = [], seen = new Set();
    for (const [source, f] of raw) {
      f.curriculum_refs = f.curriculum_refs.filter((i) => ctx.objectiveIds.has(i));
      if (!chunkMap.has(f.chunk_id)) f.chunk_id = null;
      const quoteOk = !f.original_text || quoteInText(f.original_text, normFull);
      const anchorOk = !(f.patch && f.patch.anchor_text) || quoteInText(f.patch.anchor_text, normFull);
      const confidence = f.confidence * (quoteOk ? 1.0 : 0.6);
      if (source !== "rule" && confidence < SETTINGS.min_confidence) continue;
      const key = `${f.category}\u0000${normalizeForMatch(f.original_text)}`;
      if (f.original_text && seen.has(key)) continue;
      seen.add(key);
      final.push({ ...f, confidence: Math.round(confidence * 100) / 100, finding_id: "",
                   page: f.chunk_id ? chunkMap.get(f.chunk_id).page : null, source,
                   quote_verified: quoteOk, anchor_verified: anchorOk });
    }
    final.sort((a, b) => (a.category !== "simplified") - (b.category !== "simplified")
      || SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] || b.confidence - a.confidence);
    final.forEach((f, n) => { f.finding_id = `F${String(n + 1).padStart(3, "0")}`; });

    const summary = { covered: 0, partial: 0, missing: 0, unassessed: 0 };
    for (const cr of covList) summary[cr.status] += 1;
    return {
      unit_id: unit.unit_id, unit_title: unit.title, subject: unit.subject, material_language: opts.materialLang,
      report_lang: opts.reportLang, selected_objectives: opts.selected ? objectives.length : 0,
      curriculum_id: unit.curriculum_id, unit_verified: unit.verified, include_extension: opts.includeExtension,
      model: opts.model, embedding_model: opts.embeddingLabel, script_check: scriptSummary,
      coverage_summary: summary, coverage: covList, findings: final, warnings,
    };
  }

  /* ---------- 主流程（pipeline.py run） ---------- */
  function* batched(items, n) { for (let i = 0; i < items.length; i += n) yield items.slice(i, i + n); }

  async function runReview(ctx, { filename, bytes, unitId, includeExtension = false, objectiveIds = null, reportLang = "zh",
                                  apiKey = "", progress = () => {}, onText = () => {} }) {
    const unit = ctx.units.get(unitId);
    if (!unit) throw new Error("找不到此單元");
    const subject = ctx.subjects.get(unit.subject);
    const mode = subject ? subject.mode : "content";
    const system = buildSystemPrompt(ctx.prompts, subject ? subject.profile : "science", reportLang);
    let objectives;
    if (objectiveIds && objectiveIds.length) {
      const wanted = new Set(objectiveIds);
      objectives = unit.objectives.filter((o) => wanted.has(o.id));
    } else if (mode === "skills") {
      throw new Error("語文科須先選擇這份教材針對的學習重點");
    } else {
      objectives = unit.objectives.filter((o) => includeExtension || o.tier === "core");
    }
    if (!objectives.length) throw new Error("沒有可檢查的學習重點");

    progress("parse", 0.05);
    const pages = await parseDocument(filename, bytes);
    const fullText = pages.map((p) => p.text).join("\n\n");
    const materialLang = detectLanguage(fullText);
    onText(fullText);
    const chunks = chunkPages(pages);
    if (!chunks.length) throw new Error("教材沒有可審查的文字內容");

    progress("rules", 0.1);
    const [scriptFindings, scriptSummary] = ctx.script.check(chunks, reportLang);
    const [termFindings, hints] = ctx.terms.check(chunks);
    const batches = [...batched(chunks, SETTINGS.accuracy_batch_size)];
    progress("retrieve", 0.15);
    const [evidence, related] = retrieve(unit, objectives, chunks, batches);

    const reviewer = apiKey ? claudeReviewer({ apiKey, model: ctx.prompts.llm_model, schemas: ctx.schemas }) : offlineReviewer(reportLang);
    const covTasks = [...batched(objectives, SETTINGS.coverage_group_size)].map((g) => {
      const pairs = g.map((o) => [o, evidence[o.id]]);
      return () => reviewer.assessCoverage(system, buildCoveragePrompt(unit, pairs, mode), pairs);
    });
    const accTasks = batches.map((b, i) => () => reviewer.reviewAccuracy(
      system, buildAccuracyPrompt(b, related[i], b.flatMap((c) => hints[c.chunk_id] || [])), b));
    const total = covTasks.length + accTasks.length;
    let done = 0;
    progress(`ai:0/${total}`, 0.2);
    const results = await Promise.all([...covTasks, ...accTasks].map(async (task) => {
      try { return await task(); } catch (e) { return e instanceof Error ? e : new Error(String(e)); } finally {
        done += 1;
        progress(`ai:${done}/${total}`, 0.2 + 0.75 * done / total);
      }
    }));
    progress("assemble", 0.97);
    return assemble(ctx, unit, objectives, chunks, results.slice(0, covTasks.length), results.slice(covTasks.length),
      [...scriptFindings, ...termFindings], scriptSummary, {
        reportLang, materialLang, includeExtension, selected: !!(objectiveIds && objectiveIds.length),
        model: apiKey ? ctx.prompts.llm_model : "keyword-match", embeddingLabel: "char-bigram (browser)",
      });
  }

  /* ---------- 報告輸出（renderer.py）及補丁（patcher.py） ---------- */
  const RENDER = {
    zh: {
      category: { simplified: "簡體字", omission: "內容遺漏", factual_error: "內容錯誤", imprecise: "表述不精確",
                  terminology: "用語", language: "語文", safety: "安全" },
      severity: { high: "高", medium: "中", low: "低" },
      status: { covered: "已處理", partial: "部分處理", missing: "缺漏", unassessed: "未能評估" },
      action: { insert_after: "於下句之後插入", replace: "取代", delete: "刪除" },
      title: "教材審查報告", unit: "單元", scope: "範圍", core_ext: "核心＋延展", core: "只限核心部分",
      selected: "老師選定 {n} 項學習重點", model: "模型", embed: "檢索模型",
      pass: "**字形檢查：合格**（未發現簡體字）",
      fail: "**字形檢查：不合格**　發現 {n} 個簡體字，涉及 {s} 句，發佈前必須修正",
      unverified: "> 注意：此單元課程資料尚未經老師核對，結果僅供參考。",
      coverage: "## 一、學習重點覆蓋", col: "| 學習重點 | 狀態 | 依據片段 |",
      findings: "## 二、問題與修改建議", page: "第 {p} 頁", whole: "全文", unverified_quote: "　⚠ 引文未能在原文核實",
      orig: "原文", issue: "問題", suggest: "建議", patch: "補丁", refs: "依據", conf: "信心", notes: "## 系統提示",
    },
    en: {
      category: { simplified: "Simplified Chinese", omission: "Missing content", factual_error: "Content error",
                  imprecise: "Imprecise", terminology: "Terminology", language: "Language", safety: "Safety" },
      severity: { high: "high", medium: "medium", low: "low" },
      status: { covered: "Covered", partial: "Partly covered", missing: "Missing", unassessed: "Not assessed" },
      action: { insert_after: "insert after", replace: "replace", delete: "delete" },
      title: "Material review report", unit: "Unit", scope: "Scope", core_ext: "core and extension", core: "core only",
      selected: "{n} learning points chosen by the teacher", model: "Model", embed: "Retrieval model",
      pass: "**Script check: pass** (no simplified characters)",
      fail: "**Script check: fail**: {n} simplified character(s) in {s} sentence(s); fix before publishing",
      unverified: "> Note: this unit's curriculum data has not been checked by a teacher yet.",
      coverage: "## 1. Learning point coverage", col: "| Learning point | Status | Evidence |",
      findings: "## 2. Issues and suggested changes", page: "page {p}", whole: "whole text", unverified_quote: " ⚠ quote not found in the material",
      orig: "Original", issue: "Issue", suggest: "Suggestion", patch: "Patch", refs: "Curriculum reference", conf: "confidence", notes: "## Notes",
    },
  };
  const pyFloat = (x) => (Number.isInteger(x) ? x.toFixed(1) : String(x));

  function renderMarkdown(r) {
    const t = RENDER[r.report_lang] || RENDER.zh;
    const sc = r.script_check;
    const scope = r.selected_objectives ? fmt(t.selected, { n: r.selected_objectives }) : r.include_extension ? t.core_ext : t.core;
    const lines = [r.report_lang === "en" ? `# ${t.title}: ${r.unit_title}` : `# ${t.title}：${r.unit_title}`, "",
      `${t.unit}: \`${r.unit_id}\`  ${t.scope}: ${scope}  ${t.model}: \`${r.model}\`` + (r.embedding_model ? `  ${t.embed}: \`${r.embedding_model}\`` : ""), "",
      sc.passed ? t.pass : fmt(t.fail, { n: sc.simplified_chars, s: sc.sentences }), "", t.coverage, ""];
    if (!r.unit_verified) lines.push(t.unverified, "");
    lines.push(t.col, "|---|---|---|");
    for (const c of r.coverage) {
      lines.push(`| \`${c.objective_id}\` ${c.objective_text} | ${t.status[c.status]} | ${c.evidence_chunk_ids.join(", ") || "—"} |`);
    }
    lines.push("", t.findings, "");
    for (const f of r.findings) {
      const loc = f.page ? fmt(t.page, { p: f.page }) : (f.chunk_id || t.whole);
      lines.push(`### ${f.finding_id} [${t.category[f.category]} / ${t.severity[f.severity]}] ${loc}${f.quote_verified ? "" : t.unverified_quote}`, "");
      if (f.original_text) lines.push(`> ${t.orig}: ${f.original_text}`, "");
      lines.push(`**${t.issue}**: ${f.issue}`, "", `**${t.suggest}**: ${f.suggestion}`, "");
      if (f.patch) {
        if (f.patch.anchor_text) lines.push(`**${t.patch}** (${t.action[f.patch.action]}):`, "", `> ${f.patch.anchor_text}`, "");
        if (f.patch.new_text) lines.push("```text", f.patch.new_text, "```", "");
      }
      if (f.curriculum_refs.length) {
        lines.push(`${t.refs}: ${f.curriculum_refs.map((i) => `\`${i}\``).join(", ")}  ${t.conf}: ${pyFloat(f.confidence)}`, "");
      }
    }
    if (r.warnings.length) lines.push(t.notes, "", ...r.warnings.map((w) => `- ${w}`));
    return lines.join("\n");
  }

  function applyPatches(material, findings) {
    let text = material;
    const applied = [], unapplied = [], appendix = [];
    for (const f of findings) {
      const p = f.patch;
      if (!p) { unapplied.push(f.finding_id); continue; }
      const anchor = p.anchor_text || "", next = p.new_text || "";
      if (!anchor) {
        if (p.action === "insert_after" && next) { appendix.push(next); applied.push(f.finding_id); } else unapplied.push(f.finding_id);
        continue;
      }
      const pos = text.indexOf(anchor);
      if (pos < 0) { unapplied.push(f.finding_id); continue; }
      const end = pos + anchor.length;
      if (p.action === "replace") text = text.slice(0, pos) + next + text.slice(end);
      else if (p.action === "delete") text = text.slice(0, pos) + text.slice(end);
      else text = text.slice(0, end) + next + text.slice(end);
      applied.push(f.finding_id);
    }
    if (appendix.length) text += "\n\n【補充內容：以下為審查建議新增的課程內容，請移至合適位置】\n\n" + appendix.join("\n\n");
    return { text, applied, unapplied };
  }

  /* ---------- 資料載入 ---------- */
  function normalizeUnit(u) {
    return {
      subject: "", curriculum_id: "", unit_no: null, levels: [], language: "zh", source: "", source_url: "",
      verified: false, verified_by: "", ...u,
      objectives: u.objectives.map((o) => ({ subtopic: "", tier: "core", kind: "outcome", skill: false, page: null,
                                             key_terms: [], notes: "", ...o })),
    };
  }

  function createContext({ subjects, units, glossaries, opencc = null, prompts = null, schemas = null }) {
    const unitMap = new Map(units.map((u) => { const n = normalizeUnit(u); return [n.unit_id, n]; }));
    const all = [...unitMap.values()];
    const ctx = {
      subjects: new Map(subjects.map((s) => [s.code, { documents_page: "", documents: [], glossaries: [], ...s }])),
      units: unitMap,
      objectiveIds: new Set(all.flatMap((u) => u.objectives.map((o) => o.id))),
      officialTexts: [...all.flatMap((u) => u.objectives.map((o) => `${o.subtopic} ${o.text}`)), ...all.map((u) => u.title)],
      prompts, schemas,
    };
    ctx.terms = makeTerminologyChecker(glossaries, ctx.officialTexts);
    if (opencc) ctx.script = makeScriptChecker(opencc);
    return ctx;
  }

  return {
    SETTINGS, createContext, runReview, renderMarkdown, applyPatches, parseDocument, joinBrokenLines, collapseCjkSpaces,
    chunkPages, detectLanguage, buildSystemPrompt, buildCoveragePrompt, buildAccuracyPrompt, normalizeForMatch,
    makeScriptChecker, makeTerminologyChecker, validate,
  };
});
