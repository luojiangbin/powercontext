# 越聊越离谱的冒险故事 —— Dify + seekdb + PowerContext + MiniMax

一个「越聊越离谱」的冒险故事续写应用：玩家每轮说一句话，AI 顺着剧情脑洞大开地往下编，
并且**每一轮的新剧情都会写入 PowerContext 记忆**，下一轮自动读取全部历史剧情——记忆跨轮次、
跨会话（同一对话内）持续生效，实现真正"记得住剧情"的聊天机器人。

## 技术架构

```
玩家输入
   │
   ▼
Dify Chatflow（流程编排）
   ├─ 创建会话   HTTP → PowerContext POST /v1/scopes（按对话 idempotency）
   ├─ 解析会话ID code 节点（JSON 解析）
   ├─ 读取剧情   HTTP → PowerContext POST /v1/context/prepare（组装历史剧情）
   ├─ 解析剧情   code 节点
   ├─ 离谱续写   LLM → MiniMax minimax-text-01（OpenAI/Anthropic 兼容）
   ├─ 写回剧情   HTTP → PowerContext POST /v1/memory/remember（kind=story-state）
   └─ 输出续写
```

| 技术点 | 角色 |
|---|---|
| **Dify 1.18** | 应用编排：Chatflow 画布、8 个节点、模型管理与 API 发布 |
| **seekdb** | **Dify 的部署数据库**（DB_TYPE=seekdb，全部 172 张业务表 + 13 张插件表） |
| **PowerContext** | 剧情记忆：会话范围（scope）+ 原子记忆（story-state）+ 上下文组装（prepare） |
| **MiniMax** | 大模型：minimax-text-01，负责故事续写 |

## 目录

- `越聊越离谱的冒险故事.yml` —— 应用 DSL（Dify 官方导出格式，可直接导入）
- `deploy/` —— seekdb 适配补丁与 docker compose override（复现部署所需）
- `验证样例.md` —— 实际运行的两轮对话与记忆/数据库证据
- `README.md` —— 本文件
