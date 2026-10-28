// 端侧迷你知识库前端：Transformers.js 本地 embedding + seekdb 混合检索
import { pipeline, env } from "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.3.3";
env.allowLocalModels = false;
// 模型默认从本服务器加载（服务器已内置 int8 与 fp32 两种权重）；
// 如需其他来源可用 ?remote=https://hf-mirror.com 覆盖
const remoteOverride = new URLSearchParams(location.search).get("remote");
env.remoteHost = remoteOverride || `${location.origin}/models/`;

const MODEL = "Xenova/all-MiniLM-L6-v2";
const urlParams = new URLSearchParams(location.search);
const DTYPE = urlParams.get("dtype") || "q8"; // q8=int8 量化; fp32=基线
const FORCE_DEVICE = urlParams.get("force"); // wasm 时强制降级路径，用于对照测试
const BATCH = 8;

const $ = (id) => document.getElementById(id);
const backendBadge = $("backend-badge");
const timingsBody = document.querySelector("#timings tbody");
let extractor = null;
let deviceUsed = "";

function log(stage, ms, note = "") {
  const tr = document.createElement("tr");
  tr.innerHTML = `<td>${stage}</td><td>${Math.round(ms)}</td><td>${note}</td>`;
  timingsBody.appendChild(tr);
}

function setBadge(device, note = "") {
  const cls = device === "webgpu" ? "webgpu" : device === "wasm" ? "wasm" : "err";
  backendBadge.innerHTML =
    `<span class="badge ${cls}">推理后端：${device.toUpperCase()}</span>` +
    (note ? `<span class="muted">${note}</span>` : "");
  deviceUsed = device;
}

function dot(a, b) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[i];
  return s;
}

// 量化推理质量自检：三组主题各两句，同主题对的平均相似度必须显著高于跨主题对
async function qualitySelfCheck() {
  const sents = [
    "监督学习是机器学习的一类方法", "机器学习包含监督学习与无监督学习",
    "数据库事务的隔离级别", "事务的 ACID 属性",
    "红黑树是自平衡二叉搜索树", "红黑树通过旋转保持平衡",
  ];
  const V = await embed(sents);
  let same = 0, cross = 0, nSame = 0, nCross = 0;
  for (let i = 0; i < V.length; i++) {
    for (let j = i + 1; j < V.length; j++) {
      const s = dot(V[i], V[j]);
      if (Math.floor(i / 2) === Math.floor(j / 2)) { same += s; nSame++; }
      else { cross += s; nCross++; }
    }
  }
  return same / nSame > cross / nCross + 0.05;
}

// 模型加载：优先 WebGPU，失败或量化自检不通过时自动降级 WASM
async function loadModel() {
  const t0 = performance.now();
  $("model-name").textContent = `模型 ${MODEL} (${DTYPE}) 加载中…`;
  const attempts = FORCE_DEVICE === "wasm" ? ["wasm"] : navigator.gpu ? ["webgpu", "wasm"] : ["wasm"];
  let lastErr = null;
  let fallbackNote = "";
  for (const device of attempts) {
    try {
      extractor = await pipeline("feature-extraction", MODEL, { dtype: DTYPE, device });
      if (device === "webgpu" && DTYPE === "q8") {
        const ok = await qualitySelfCheck();
        if (!ok) {
          fallbackNote = "WebGPU 下 INT8 推理自检未通过（本机 GPU 驱动问题），已自动降级 WASM";
          extractor = null;
          continue;
        }
      }
      log("模型加载", performance.now() - t0, `${DTYPE} / ${device}${fallbackNote ? "（自检降级）" : ""}`);
      setBadge(device, fallbackNote || (navigator.gpu ? "" : "当前浏览器无 WebGPU，已自动降级"));
      $("model-name").textContent = `模型 ${MODEL} (${DTYPE})`;
      return;
    } catch (e) {
      lastErr = e;
    }
  }
  setBadge("error", String(lastErr).slice(0, 120));
  throw lastErr;
}

async function embed(texts) {
  if (!extractor) await loadModel();
  const out = await extractor(texts, { pooling: "mean", normalize: true });
  return out.tolist(); // [n, 384]
}

// 解析语料：textarea 每行一篇 + 文件内容
function parseLine(line) {
  const idx = line.indexOf("|||");
  if (idx >= 0) return { title: line.slice(0, idx).trim() || "未命名", content: line.slice(idx + 3).trim() };
  const t = line.trim();
  if (!t) return null;
  return { title: t.slice(0, 30), content: t };
}

async function collectCorpus() {
  const docs = [];
  for (const line of $("corpus").value.split("\n")) {
    const d = parseLine(line);
    if (d && d.content) docs.push(d);
  }
  for (const file of $("files").files) {
    const text = await file.text();
    for (const line of text.split("\n")) {
      const d = parseLine(line);
      if (d && d.content) docs.push({ title: d.title || file.name, content: d.content });
    }
  }
  return docs;
}

$("import-btn").addEventListener("click", async () => {
  const btn = $("import-btn");
  btn.disabled = true;
  try {
    const session = $("session").value.trim() || "default";
    const docs = await collectCorpus();
    if (!docs.length) return alert("请先填写或选择语料");
    $("import-status").textContent = `共 ${docs.length} 篇，模型加载中…`;
    if (!extractor) await loadModel();

    let done = 0;
    const all = [];
    const t1 = performance.now();
    for (let i = 0; i < docs.length; i += BATCH) {
      const batch = docs.slice(i, i + BATCH);
      const vecs = await embed(batch.map((d) => d.content));
      batch.forEach((d, j) => all.push({ id: `${Date.now()}-${i + j}`, title: d.title, content: d.content, vec: vecs[j] }));
      done += batch.length;
      $("prog").style.width = `${Math.round((done / docs.length) * 80)}%`;
      $("import-status").textContent = `向量化 ${done}/${docs.length}…`;
    }
    const encMs = performance.now() - t1;
    log("文档向量化", encMs, `${docs.length} 篇 / 批 ${BATCH} / ${deviceUsed}`);

    $("prog").style.width = "90%";
    $("import-status").textContent = "写入 seekdb + 索引刷新…";
    const t2 = performance.now();
    const resp = await fetch("/api/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session, docs: all }),
    });
    const data = await resp.json();
    if (!data.ok) throw new Error(data.error || "ingest failed");
    $("prog").style.width = "100%";
    log("写入+索引刷新", performance.now() - t2, `writeMs=${data.writeMs} refreshMs=${data.refreshMs}`);
    $("import-status").textContent = `✅ 已导入 ${data.count} 篇（seekdb 写入 ${data.writeMs}ms，索引刷新可见 ${data.refreshMs}ms）`;
    log("端到端导入", encMs + data.writeMs + data.refreshMs, "向量化+写入+刷新");
  } catch (e) {
    $("import-status").textContent = "❌ " + String(e).slice(0, 160);
  } finally {
    btn.disabled = false;
  }
});

$("search-btn").addEventListener("click", async () => {
  const btn = $("search-btn");
  btn.disabled = true;
  try {
    const session = $("session").value.trim() || "default";
    const query = $("query").value.trim();
    if (!query) return;
    $("search-status").textContent = "查询向量化中…";
    const t1 = performance.now();
    if (!extractor) await loadModel();
    const [vec] = await embed([query]);
    const qEncMs = performance.now() - t1;
    log("查询向量化", qEncMs, `1 条 / ${deviceUsed}`);

    const t2 = performance.now();
    const resp = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session, query, vec, topK: 10 }),
    });
    const data = await resp.json();
    const total = performance.now() - t2;
    log("seekdb 混合检索", data.seekdbMs, "DBMS_HYBRID_SEARCH RRF");
    log("端到端查询", qEncMs + total, "向量化+检索+传输");

    if (!data.ok) throw new Error(data.error);
    $("search-status").textContent = `seekdb 检索 ${data.seekdbMs}ms，返回 ${data.results.length} 条`;
    $("results").innerHTML = data.results
      .map((r, i) => {
        const kw = r._keyword_score == null ? "—" : r._keyword_score.toFixed(2);
        const sem = r._semantic_score == null ? "—" : r._semantic_score.toFixed(3);
        const hy = r._score == null ? "—" : r._score.toFixed(3);
        return `<div class="result"><h4>${i + 1}. ${r.title} <span class="muted">(${r.doc_id})</span></h4>
        <div class="scores">关键词 <b>${kw}</b> · 语义 <b>${sem}</b> · RRF 融合 <b>${hy}</b></div>
        <p>${r.content.slice(0, 160)}${r.content.length > 160 ? "…" : ""}</p></div>`;
      })
      .join("") || "<p class='muted'>无结果</p>";
  } catch (e) {
    $("search-status").textContent = "❌ " + String(e).slice(0, 160);
  } finally {
    btn.disabled = false;
  }
});

$("clear-btn").addEventListener("click", () => (timingsBody.innerHTML = ""));

$("export-btn").addEventListener("click", () => {
  const rows = [...timingsBody.querySelectorAll("tr")].map((tr) => ({
    stage: tr.children[0].textContent,
    ms: Number(tr.children[1].textContent),
    note: tr.children[2].textContent,
  }));
  const report = {
    model: MODEL,
    dtype: DTYPE,
    backend: deviceUsed,
    batchSize: BATCH,
    userAgent: navigator.userAgent,
    navigatorGpu: Boolean(navigator.gpu),
    timings: rows,
  };
  $("report").textContent = JSON.stringify(report, null, 2);
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `stepfun-report-${DTYPE}-${deviceUsed}.json`;
  a.click();
});
