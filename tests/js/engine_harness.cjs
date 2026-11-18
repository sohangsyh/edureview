// 以 Node 執行 app/static/engine.js，供 tests/test_static_engine.py 與 Python 版本逐項比較。
// stdin：{"cases": [{"op": ..., ...}]}；stdout：每個 case 的結果（同樣次序）。
const fs = require("fs");
const path = require("path");
const E = require("../../app/static/engine.js");

const ROOT = path.join(__dirname, "..", "..");
const readJson = (p) => JSON.parse(fs.readFileSync(path.join(ROOT, p), "utf-8"));
const manifest = readJson("data/manifest.json");
const opencc = {};
for (const name of ["TSPhrases", "TSCharacters", "STPhrases", "STCharacters", "HKVariants"]) {
  opencc[name] = fs.readFileSync(path.join(ROOT, "app/static/vendor/opencc", `${name}.txt`), "utf-8");
}
const ctx = E.createContext({
  subjects: readJson("data/subjects.json").subjects,
  units: manifest.curriculum.map((f) => readJson(`data/curriculum/${f}`)),
  glossaries: manifest.glossary.map((f) => readJson(`data/glossary/${f}`)),
  opencc,
});
const objective = (id) => [...ctx.units.values()].flatMap((u) => u.objectives).find((o) => o.id === id);

// 測試用：「same」令所有片段與學習重點相似度為 1；「broken」模擬模型下載失敗
const FAKE_EMBEDDERS = {
  same: { label: "BAAI/bge-m3", embed: async (texts) => texts.map(() => [1, 0]) },
  broken: { label: "BAAI/bge-m3", embed: async () => { throw new Error("network down"); } },
};

async function run(c) {
  switch (c.op) {
    case "convert": return { t2s: ctx.script.t2s(c.text), s2hk: ctx.script.s2hk(c.text) };
    case "scan": return ctx.script.scanSentence(c.text);
    case "script_check": return ctx.script.check(c.chunks, c.lang);
    case "terms": return ctx.terms.check(c.chunks);
    case "join": return E.joinBrokenLines(c.text);
    case "chunk": return E.chunkPages(c.pages);
    case "lang": return E.detectLanguage(c.text);
    case "judge": {
      const chunks = c.chunks.map((text, i) => ({ chunk_id: `t-c${i + 1}`, page: null, text }));
      return E.judgeCoverage(objective(c.objective_id), chunks, c.lang, c.score == null ? null : { score: c.score, chunk: chunks[0] });
    }
    case "markdown": return E.renderMarkdown(c.report);
    case "patch": return E.applyPatches(c.material, c.findings);
    case "review":
      return E.runReview(ctx, { filename: c.filename, bytes: Buffer.from(c.text, "utf-8"), unitId: c.unit_id,
                               reportLang: c.report_lang, objectiveIds: c.objective_ids, includeExtension: c.include_extension,
                               embedder: c.embedder && FAKE_EMBEDDERS[c.embedder] });
    default: throw new Error(`unknown op ${c.op}`);
  }
}

let input = "";
process.stdin.on("data", (d) => { input += d; });
process.stdin.on("end", async () => {
  const out = [];
  for (const c of JSON.parse(input).cases) out.push(await run(c));
  process.stdout.write(JSON.stringify(out));
});
