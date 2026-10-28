# MiniMax × OceanBase：SDD 上下文管理系统

面向软件持续演进的规格驱动开发（SDD）上下文模块演示。

## 做了什么

一个 AI 开发助手在执行"修复 `is_adult` 年龄边界"任务时：

1. 从 **seekdb** 检索工程资料（规格 / 代码 / 测试）
2. 从 **PowerContext** 读取任务状态
3. 组装上下文，调用 **MiniMax** 生成修复
4. 应用修复并运行测试（改前失败 → 改后通过）
5. 把任务状态写回 **PowerContext**，重启后可读回

## 依赖与版本

- Python 3.11+
- seekdb 1.4.0（部署为服务，端口 2881）
- PowerContext 1.4.0（`powercontext server run`，端口 8000）
- MiniMax API（OpenAI 兼容端点）

```bash
pip install -r requirements.txt
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `MINIMAX_API_KEY` | 空 | 必填，MiniMax API Key |
| `MINIMAX_BASE_URL` | `https://api.minimaxi.com/v1` | OpenAI 兼容端点 |
| `MINIMAX_MODEL` | `MiniMax-Text-01` | 模型名 |
| `SEEKDB_HOST` / `PORT` / `USER` / `PASSWORD` | 127.0.0.1 / 2881 / root / 空 | seekdb 连接 |
| `POWERCONTEXT_URL` | `http://127.0.0.1:8000` | PowerContext 服务 |

## 运行步骤

```bash
# 1. 启动 seekdb（systemd）
sudo systemctl start seekdb

# 2. 启动 PowerContext
powercontext server run   # 另开一个终端

# 3. 初始化资料与状态
python run.py init

# 4. 执行一次开发任务（改前失败 -> MiniMax 修复 -> 改后通过）
MINIMAX_API_KEY=... python run.py run

# 5. 重启后读取状态
python run.py status
```

## 调用入口与输入输出

- `python run.py init`：初始化 seekdb 资料 + PowerContext 作用域与初始状态
- `python run.py run`：输入为任务关键词，输出为定位结果、代码差异、改前/改后测试 exit code
- `python run.py status`：输出 PowerContext 中保存的任务进度

## 已知限制

- 演示任务固定为"修复 is_adult 年龄边界"，规格更新见 `验证样例.md`
- MiniMax 输出会直接覆盖 `app/rules.py`，正式使用建议加 diff 审核步骤
