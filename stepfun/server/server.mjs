// StepFun 迷你知识库后端：Node + seekdb-js（server 模式）
// 向量化在前端浏览器本地完成；混合检索由 seekdb DBMS_HYBRID_SEARCH 完成。
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { SeekdbClient } from "seekdb";

const PORT = Number(process.env.PORT || 8600);
const HOST = process.env.SEEKDB_HOST || "127.0.0.1";
const DBPORT = Number(process.env.SEEKDB_PORT || 2881);
const USER = process.env.SEEKDB_USER || "root";
const PASSWORD = process.env.SEEKDB_PASSWORD || "";
const DATABASE = process.env.SEEKDB_DATABASE || "sddemo";
const WEBROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..", "web");

const bootstrap = new SeekdbClient({ host: HOST, port: DBPORT, user: USER, password: PASSWORD });
await bootstrap.createDatabase(DATABASE).catch(() => {});
await bootstrap.close();
const client = new SeekdbClient({ host: HOST, port: DBPORT, user: USER, password: PASSWORD, database: DATABASE });
await client.execute(`CREATE TABLE IF NOT EXISTS docs (
  doc_id VARCHAR(64),
  session VARCHAR(64),
  title VARCHAR(200),
  content TEXT,
  vec VECTOR(384),
  FULLTEXT INDEX idx_content(content) WITH PARSER ik,
  FULLTEXT INDEX idx_title(title) WITH PARSER ik,
  VECTOR INDEX idx_vec(vec) WITH (distance=cosine, type=hnsw, lib=vsag)
)`);
console.log(`seekdb ${HOST}:${DBPORT}/${DATABASE} ready, table docs ok`);

function json(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { "Content-Type": "application/json; charset=utf-8" });
  res.end(body);
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const text = Buffer.concat(chunks).toString("utf-8");
  if (!text) return {};
  return JSON.parse(text);
}

// 校验查询串：query_string 语法里需要转义特殊字符
function safeQuery(q) {
  return String(q).replace(/["\\]/g, " ").trim();
}

function hybridParams(session, query, vec, topK) {
  const k = Math.min(Math.max(topK * 4, 20), 200);
  return JSON.stringify({
    query: {
      bool: {
        must: [
          { query_string: { fields: ["content^2", "title^1.5"], query: safeQuery(query) } },
          { term: { session } },
        ],
      },
    },
    knn: {
      field: "vec",
      k,
      query_vector: vec,
      filter: [{ term: { session } }],
    },
    rank: { rrf: { rank_window_size: 100, rank_constant: 60 } },
    _source: ["doc_id", "title", "content", "_keyword_score", "_semantic_score"],
  });
}

async function searchInSeekdb(session, query, vec, topK) {
  const started = performance.now();
  const params = hybridParams(session, query, vec, topK);
  const rows = await client.execute("SELECT DBMS_HYBRID_SEARCH.SEARCH('docs', ?) AS r", [params]);
  const elapsed = Math.round(performance.now() - started);
  const key = Object.keys(rows[0] || {})[0];
  const raw = rows && rows.length ? rows[0][key] : [];
  const all = typeof raw === "string" ? JSON.parse(raw ?? "[]") : Array.isArray(raw) ? raw : [];
  const list = all.slice(0, Math.min(topK, 50));
  return { list, elapsedMs: elapsed };
}

// 等待新导入文档可见（seekdb 索引异步刷新），最多 5s
async function waitVisible(session, docId, title, vec) {
  const started = Date.now();
  while (Date.now() - started < 5000) {
    const { list } = await searchInSeekdb(session, title.slice(0, 40), vec, 20);
    if (list.some((row) => row.doc_id === docId)) return Date.now() - started;
    await new Promise((r) => setTimeout(r, 300));
  }
  return -1;
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  const path = decodeURIComponent(url.pathname);
  try {
    if (path === "/api/health") return json(res, 200, { ok: true, database: DATABASE });

    if (path === "/api/ingest" && req.method === "POST") {
      const body = await readBody(req);
      const session = String(body.session || "default");
      const docs = Array.isArray(body.docs) ? body.docs : [];
      const started = performance.now();
      let count = 0;
      for (const doc of docs) {
        const vec = Array.isArray(doc.vec) ? doc.vec : doc.vec?.data || [];
        await client.execute("INSERT INTO docs VALUES (?, ?, ?, ?, ?)", [
          String(doc.id), session, String(doc.title || ""), String(doc.content || ""), JSON.stringify(vec),
        ]);
        count += 1;
      }
      const writeMs = Math.round(performance.now() - started);
      // 索引刷新可见性检查（对最后一条做验证）
      let refreshMs = 0;
      if (docs.length) {
        const last = docs[docs.length - 1];
        refreshMs = await waitVisible(session, String(last.id), String(last.title), (last.vec?.data) || last.vec);
      }
      return json(res, 200, { ok: true, count, writeMs, refreshMs });
    }

    if (path === "/api/search" && req.method === "POST") {
      const body = await readBody(req);
      const session = String(body.session || "default");
      const query = String(body.query || "");
      const vec = body.vec || [];
      const topK = Math.min(Math.max(Number(body.topK) || 10, 1), 50);
      const started = performance.now();
      const { list, elapsedMs } = await searchInSeekdb(session, query, vec, topK);
      const totalMs = Math.round(performance.now() - started);
      return json(res, 200, { ok: true, results: list, seekdbMs: elapsedMs, totalMs });
    }

    if (path === "/api/sessions" && req.method === "GET")
      return json(res, 200, {
        sessions: (await client.execute("SELECT DISTINCT session AS s FROM docs")).map((r) => r.s),
      });

    // 静态文件
    let root = WEBROOT;
    let rel = path === "/" ? "index.html" : path;
    if (rel.startsWith("/models/")) {
      root = join(fileURLToPath(new URL(".", import.meta.url)), "models");
      rel = rel.slice("/models/".length);
    }
    let file = normalize(join(root, rel));
    if (!file.startsWith(normalize(root))) return json(res, 403, { ok: false });
    const content = await readFile(file);
    const type = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".onnx": "application/octet-stream" }[extname(file)] || "application/octet-stream";
    res.writeHead(200, { "Content-Type": type });
    return res.end(content);
  } catch (err) {
    console.error("REQUEST_ERROR", path, String(err && err.message ? err.message : err).slice(0, 300));
    return json(res, 500, { ok: false, error: String(err && err.message ? err.message : err).slice(0, 300) });
  }
});

server.listen(PORT, "0.0.0.0", () => console.log(`StepFun KB server on http://127.0.0.1:${PORT}`));
