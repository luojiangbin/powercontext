# 证据文件 ——「越聊越离谱的冒险故事」Dify Chatflow 应用

- 应用名：越聊越离谱的冒险故事
- 应用 ID：`6fa14d94-55fe-470a-b4c7-19615fdbf1df`
- 应用模式：advanced-chat（Chatflow）
- 验证时间：2026-10-29（Asia/Shanghai）；本文件由 DSH 自动化代理按任务书要求补录，所有数字均为 2026-10-29 22:40 前后实测
- 配套文件：`app.yml`（应用 DSL 导出）、`验证样例.md`、`技术说明.md`、`README.md`（同目录）

---

## 一、验证环境

| 组件 | 说明 |
|---|---|
| Dify 1.18.0 | http://127.0.0.1/（WSL 内 `docker compose --profile seekdb` 启动，全部容器健康；验证期间未对任何 docker 容器做改动） |
| seekdb | Dify 的部署数据库（`DB_TYPE=seekdb`），MySQL 协议，127.0.0.1:2881，库 `dify`（业务表）+ `dify_plugin`（插件表） |
| PowerContext 1.4.0 | http://127.0.0.1:8000（WSL 内运行，监听 0.0.0.0）；Dify 容器经 `http://host.docker.internal:8000` 访问 |
| MiniMax | `minimax-text-01`（llm），经官方插件 `langgenius/minimax@0.0.29` 接入，凭据已配置、校验有效 |

---

## 二、工作流结构（8 节点 / 7 边，串行链路）

以下节点清单读取自 seekdb `dify.workflows` 表 draft 行的 `graph` JSON，与画布一致：

| 节点 | 类型 | 作用 |
|---|---|---|
| node1 开始 | start | 接收玩家输入（sys.query） |
| node2 创建会话 | http-request | `POST http://host.docker.internal:8000/v1/scopes`，按对话 idempotency_key 幂等创建 scope |
| node3 解析会话ID | code | 从响应 JSON 提取 `scope_id` |
| node4 读取剧情 | http-request | `POST /v1/context/prepare`（scope_id + 本轮输入，max_bytes=2000），取回历史剧情 |
| node5 解析剧情 | code | 提取 prepare 返回的 `content` 剧情文本 |
| node6 离谱续写 | llm | `minimax-text-01`；「离谱冒险主持人」人设：只推进一步、保持离谱有趣、结尾给 2-3 个选项；系统提示词注入《当前剧情》，用户提示词注入本轮输入 |
| node7 写回剧情 | http-request | `POST /v1/memory/remember`（scope_id, kind=`story-state`, text=LLM 输出） |
| node8 结束 | end | 输出续写文本 |

边：node1→node2→node3→node4→node5→node6→node7→node8（共 7 条）。应用已发布，配置有 1 个 app API Key。

---

## 三、对话验证实录（同一会话两轮，跨轮记忆生效）

会话 ID：`849c062c-d21a-4270-819a-1bccd768f671`（2026-10-29 18:45，经 `/v1/chat-messages` blocking 模式调用）。
以下 query/answer 原文同步核对自 seekdb `dify.messages` 表两行消息（`85055948-de30-4206-b901-5f290e9f9bed`、`70506215-3560-4ea8-854d-546aa1e68291`），UTF-8 字节干净（首 12 字节 hex `E68891E59CA8E4B8...` = “我在一家…”）。

### 第 1 轮

**query：** 我在一家深夜拉面馆打工，今晚第一位客人居然是一只会说人话的橘猫。

**answer（minimax-text-01 生成）：**

> 橘猫优雅地跳上吧台，用爪子推了推墨镜，操着一口流利的京片子说道：“老板，来碗加辣加蛋的豚骨拉面，再给我来杯猫薄荷奶茶。”
>
> 你目瞪口呆地看着这只猫，心想这一定是梦。就在这时，橘猫从口袋里掏出一张百元大钞，甩在桌上：“别愣着啊，赶紧的，我赶时间。”
>
> 你机械地开始煮面，脑子飞速运转。这时，橘猫突然压低声音：“其实，我是来调查这家店的。你们老板最近是不是总在半夜进货？”
>
> 你刚想回答，突然听到后厨传来奇怪的声响，像是有什么东西在拖行。
>
> 选项：
> 1. 跟橘猫一起悄悄溜进后厨查看。
> 2. 假装没听见，继续给橘猫煮面。
> 3. 问橘猫是不是在开玩笑，想转移话题。

### 第 2 轮（验证跨轮记忆）

**query：** 我选 1，跟橘猫溜进后厨。

**answer：**

> 你深吸一口气，示意橘猫跟上，然后蹑手蹑脚地朝后厨走去。橘猫优雅地跳下吧台，悄无声息地跟在你身后。
>
> 推开后厨的门，眼前的景象让你倒吸一口凉气：一只巨大的机械章鱼正用触手卷着几袋面粉，旁边的老板正忙着给章鱼加油，嘴里还念叨着：“这次一定要赶上交货时间。”
>
> 橘猫低声说道：“看来我们找到线索了。这家店果然有问题！”
>
> 就在这时，机械章鱼突然停下动作，八只眼睛齐刷刷地转向你们。
>
> 选项：
> 1. 试图和机械章鱼谈判。
> 2. 转身逃跑，顺便报警。
> 3. 问老板这到底是怎么回事。

**跨轮记忆生效证明：** 第 2 轮 query 只有“选 1、跟橘猫溜进后厨”8 个字，而回答准确接续了第 1 轮的橘猫、拉面馆、后厨怪响等全部剧情要素，角色与设定一致。这些信息只能来自 PowerContext：第 1 轮 LLM 输出经 node7 写为 `story-state` 记忆，第 2 轮经 node2→node4（scopes→prepare）读回并注入提示词。

### 附：另一条独立会话（2026-10-29 21:30 二次实测，同样两轮、跨轮记忆生效）

会话 `a0451844-96af-4a4f-b159-492dc8c6a8b2`（新 scope，剧情不串线）：

- Q：深夜的实验室里，我养的仓鼠突然开始敲击键盘写代码。→ A：仓鼠小白写代码、召唤传送门、出现未来城市……（结尾 3 个选项）
- Q：我选 1，跟着小白穿过传送门。→ A：踏入未来城市、“新仓鼠纪元”机器人查证件、巨型机械仓鼠从天而降……（引用上轮全部要素，结尾 3 个选项）

---

## 四、PowerContext 记忆写入证据

### 会话 849c062c…（橘猫线）对应 scope

- scope_id：`scp_0881njy422p5werd1d7362vhnk`（title: dify-adventure）
- `POST /v1/atomic-memory/list {scope_id, kind:"story-state"}` 返回 **2 条** `state=active` 的 story-state 记忆，即两轮 LLM 输出的全文（与上文第 1、2 轮 answer 一字不差）：

| # | kind | state | text（全文，节选开头） |
|---|---|---|---|
| 1 | story-state | active | 橘猫优雅地跳上吧台，用爪子推了推墨镜，操着一口流利的京片子说道：“老板，来碗加辣加蛋的豚骨拉面……”（第 1 轮剧情全文） |
| 2 | story-state | active | 你深吸一口气，示意橘猫跟上，然后蹑手蹑脚地朝后厨走去。橘猫优雅地跳下吧台，悄无声息地跟在你身后……（第 2 轮剧情全文） |

字节级校验：记忆文本首字节 hex `e4bda0e6b7b1e590b8e4b880e58fa3e6b094…` = UTF-8 的“你深吸一口气，…”——存储内容为干净 UTF-8，无乱码。

### 会话 a0451844…（仓鼠线）对应 scope

- scope_id：`scp_77706b98jxt4bk30ab6hw5bk2c`，同样 2 条 story-state 记忆（仓鼠小白两轮剧情全文）。

即：**每一轮 LLM 输出都通过 HTTP 节点写回了 PowerContext，并在下一轮被 context/prepare 读出注入提示词**；scope 按对话幂等创建，记忆跨轮次、跨请求持久复用。

---

## 五、seekdb 数据证据（pymysql 直连实测）

连接方式（任务书给定）：`pymysql.connect(host="127.0.0.1", port=2881, user="root", password="difyai123456", database="dify", charset="utf8mb4")`

| 项目 | 实测值 |
|---|---|
| `dify` 库表数量 | **172 张**（`information_schema.tables`） |
| `dify_plugin` 库表数量 | **13 张** |
| `SELECT COUNT(*) FROM dify.apps` | **1**（本应用 `6fa14d94-55fe-470a-b4c7-19615fdbf1df`，name=越聊越离谱的冒险故事，mode=advanced-chat，status=normal，created_at 2026-10-29 18:03） |
| `SELECT COUNT(*) FROM dify.workflows` | **12**（11 个发布版本 + 1 个 draft；draft 行 `390e9a41-ae62-48ae-9370-72bc4613c889`） |
| `SELECT COUNT(*) FROM dify.workflow_runs` | **8**（`succeeded` 4 次 = 两条会话各 2 轮；`failed` 4 次为开发调试期记录，最终验证轮全部成功） |
| `SELECT COUNT(*) FROM dify.conversations` | **9**（其中两条为上述正式验证会话） |
| `SELECT COUNT(*) FROM dify.messages` | **11**（含 4 条正式验证消息：2 query + 2 answer × 2 会话；其余 7 条为早期调试消息） |
| `SELECT COUNT(*) FROM dify.provider_models` | **1**（`provider=minimax, model=minimax-text-01, model_type=llm, is_valid=1`） |
| 其它 | accounts=1, sites=1（title 已修正为“越聊越离谱的冒险故事”） |

正式验证消息在库中的落点（UTF-8 干净）：

| message id | conversation_id | 时间 | 内容 |
|---|---|---|---|
| `85055948-de30-4206-b901-5f290e9f9bed` | 849c062c-… | 18:45:35 | 橘猫线第 1 轮（query+answer） |
| `70506215-3560-4ea8-854d-546aa1e68291` | 849c062c-… | 18:45:58 | 橘猫线第 2 轮（query+answer） |
| `e6baddf5-b387-4510-a51c-e738e94c31ca` | a0451844-… | 21:30:43 | 仓鼠线第 1 轮（query+answer） |
| `91690456-7fab-4965-96f7-61f0b9dd8dbf` | a0451844-… | 21:30:57 | 仓鼠线第 2 轮（query+answer） |

---

## 六、DSL 导出

- 文件：`C:\Users\lenovo\Documents\deepseek-harness\default-workspace\dify-work\app.yml`（2736 字节，2026-10-29 22:47 重新导出）
- 导出方式：控制台 API `GET /console/api/apps/6fa14d94-55fe-470a-b4c7-19615fdbf1df/export`（默认 ifpkg，返回 `application/zip`）
- 格式：Dify 官方 DSL 包（zip 容器，`.yml` 后缀），内含：
  - `manifest.yaml`：`{ "version": "0.7.0", "kind": "app" }`
  - `app.yaml`（9611 字节）：`app.name=越聊越离谱的冒险故事`、`mode=advanced-chat`、依赖 `langgenius/minimax:0.0.29@1947c0bd…`、完整 8 节点 graph、3 个 PowerContext URL（`/v1/scopes`、`/v1/context/prepare`、`/v1/memory/remember`）、llm 节点 `name: minimax-text-01`、`provider: langgenius/minimax/minimax`
- zip 完整性经 `zipfile` 校验通过（namelist = manifest.yaml + app.yaml）

---

## 七、合规与安全声明

1. **未改动任何 docker 容器**，未改动 seekdb 的表结构（仅通过 Dify 控制台 API 修正了 `dify.apps.name` 与 `dify.sites.title` 两个文本字段：修复前两字段因早期客户端编码问题存为 10 个字面 `?`（hex `3F×10`），修复后为正确 UTF-8，字节级验证通过；不涉及表结构变更）。
2. 本文件及全部交付文件中**不包含 MiniMax API Key、不包含 Dify 管理员密码**。
3. PowerContext 服务重启过一次（1.4.0 默认拒绝非回环绑定，需环境变量 `POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true`，详见 `技术说明.md`），不影响 docker 容器。

---

## 八、过程中遇到的问题与解法（摘要）

| 问题 | 解法 |
|---|---|
| PowerContext 起在 0.0.0.0:8000 时被拒（"refusing to bind an unauthenticated Server to a non-loopback address"） | 设置 `POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true` 后正常监听 |
| seekdb 不支持 Dify 迁移脚本中的 MySQL `TEMPORARY TABLE` 语法，导致初始化中断 | 将迁移内临时表改为普通表并构建补丁镜像 `dify-api-seekdb:1.18.0`（见 `技术说明.md` / deploy/seekdb-patch） |
| 无外网环境下 Dify marketplace 不可达，MiniMax 供应商无法直接添加 | 从 `langgenius/dify-official-plugins` 取 minimax 官方插件源码，手工打包 `.difypkg` 经控制台 API 安装，`minimax-text-01` 校验有效 |
| Dify 1.18 空应用无 draft（GET workflows/draft 404），需从零构造 graph | 按 `SyncDraftWorkflowPayload` 规范自建 8 节点 graph 一次性同步成功 |
| 早期经 PowerShell 5.1 控制台 API 操作产生的少量中文文本在库中存为乱码（app 名、早期调试消息） | 验证轮改用 UTF-8 字节体调用后数据干净；本次补录时已把 app 名与站点标题修复为正确 UTF-8（字节级验证），正式验证的 4 条消息本就是干净 UTF-8 |
