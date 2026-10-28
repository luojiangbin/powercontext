# 端侧迷你知识库（StepFun × seekdb）

浏览器本地完成 embedding 推理，数据存入 seekdb，由 seekdb 完成向量+全文混合检索的迷你知识库应用。

## 快速开始

前置：Node.js 20+（后端）、Chrome/Edge 最新版（前端）、seekdb 1.4+（本机或远程）。

```bash
# 1. 启动 seekdb（本机已有服务则跳过）
seekdb --nodaemon --base-dir=/var/lib/seekdb2 --port=2881

# 2. 安装依赖并启动后端（托管前端页面 + 模型文件 + seekdb 数据层）
cd server
npm install
node server.mjs
# 默认监听 http://127.0.0.1:8600

# 3. 浏览器打开 http://127.0.0.1:8600
```

环境变量：`SEEKDB_HOST` / `SEEKDB_PORT`(2881) / `SEEKDB_USER`(root) / `SEEKDB_PASSWORD` / `PORT`(8600)。

## 使用流程

1. 顶部输入会话名（默认 default）
2. 在语料框输入文档（每行 `标题|||正文`）或选择 .txt/.md 文件
3. 点「导入并向量化」→ 浏览器本地编码 → 写入 seekdb → 索引刷新确认
4. 输入查询 → 点「混合检索」→ 展示排序结果（关键词分 / 语义分 / RRF 融合分）

## 模型

- `Xenova/all-MiniLM-L6-v2`（Apache-2.0），权重已内置在 `server/models/`（无需外网）
- 两种精度：`?dtype=q8`（INT8 量化，默认）/ `?dtype=fp32`（基线）
- 后端选择：优先 WebGPU；不支持或量化自检未通过时自动降级 WASM，界面明确提示
- `?force=wasm` 可强制降级路径（对照测试用）

## 质量评测

打开 `/eval.html?dtype=q8` 或 `?dtype=fp32`，对 12 篇文档 / 6 个查询计算 Recall@5 与 nDCG@5。

## 目录结构

```
server/server.mjs    seekdb-js 数据层 + HTTP API + 静态托管
server/models/       模型权重（int8 + fp32）
web/index.html       应用界面
web/app.js           端侧推理 + 流程编排
web/eval.html        量化质量评测
scripts/gen_corpus.py 测试语料生成
```
