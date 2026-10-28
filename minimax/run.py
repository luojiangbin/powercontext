"""SDD 上下文模块 CLI。

用法：
    python run.py init      初始化 seekdb 资料 + PowerContext 作用域 + 初始状态
    python run.py run       组装上下文 -> MiniMax 修复 -> 跑测试 -> 写回状态
    python run.py status    读取 PowerContext 中保存的任务状态
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import subprocess
import sys
from pathlib import Path

from sdd import context, minimax_agent, powercontext_state, seekdb_store

ROOT = Path(__file__).resolve().parent
SNAPSHOTS = ROOT / "snapshots"
LOGS = ROOT / "logs"


def _ensure_dirs() -> None:
    SNAPSHOTS.mkdir(exist_ok=True)
    LOGS.mkdir(exist_ok=True)


def run_tests() -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def cmd_init() -> None:
    _ensure_dirs()
    seekdb_store.init_database(
        context.read_file("spec.md"),
        context.read_file("app/rules.py"),
        context.read_file("tests/test_rules.py"),
    )
    scope_id = powercontext_state.ensure_scope()
    powercontext_state.write_state(
        scope_id, "任务开始：修复 is_adult 的年龄边界，使全部测试通过。"
    )
    print("seekdb 资料已初始化")
    print("PowerContext scope_id:", scope_id)
    print("初始状态已写入 PowerContext")


def cmd_run() -> None:
    _ensure_dirs()
    scope_id = powercontext_state.ensure_scope()
    before_code = context.read_file("app/rules.py")
    (SNAPSHOTS / "rules.before.py").write_text(before_code, encoding="utf-8")

    before_exit, before_out = run_tests()

    ctx = context.build_context(scope_id, "18")
    (LOGS / "context.txt").write_text(ctx, encoding="utf-8")

    fixed = minimax_agent.fix_code(ctx)
    (LOGS / "minimax_output.txt").write_text(fixed, encoding="utf-8")
    (ROOT / "app" / "rules.py").write_text(fixed, encoding="utf-8")
    (SNAPSHOTS / "rules.after.py").write_text(fixed, encoding="utf-8")

    after_exit, after_out = run_tests()

    diff = "".join(
        difflib.unified_diff(
            before_code.splitlines(keepends=True),
            fixed.splitlines(keepends=True),
            fromfile="app/rules.py(before)",
            tofile="app/rules.py(after)",
        )
    )
    (LOGS / "change.diff").write_text(diff, encoding="utf-8")

    state_text = (
        f"已完成：MiniMax 修复 is_adult；"
        f"改前测试 exit={before_exit}，改后 exit={after_exit}；"
        f"待办：无。"
    )
    powercontext_state.write_state(scope_id, state_text)

    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    log = (
        f"时间 {stamp}\n"
        f"MiniMax 模型 {minimax_agent.MODEL}\n"
        f"seekdb 检索记录：\n{seekdb_store.query_log()}\n"
        f"改前测试 exit={before_exit}\n{before_out}\n"
        f"改后测试 exit={after_exit}\n{after_out}\n"
    )
    (LOGS / "run.log").write_text(log, encoding="utf-8")

    print("=== 改前测试 ===")
    print(f"exit={before_exit}")
    print("=== 改后测试 ===")
    print(f"exit={after_exit}")
    print("=== 代码差异 ===")
    print(diff)
    print("状态已写入 PowerContext，日志见 logs/run.log，差异见 logs/change.diff")


def cmd_status() -> None:
    scope_id = powercontext_state.ensure_scope()
    print(powercontext_state.read_state(scope_id))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "run", "status"])
    args = parser.parse_args()
    {"init": cmd_init, "run": cmd_run, "status": cmd_status}[args.command]()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
