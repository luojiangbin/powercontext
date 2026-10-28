"""SDD 上下文组装。"""

from __future__ import annotations

from pathlib import Path

from . import powercontext_state, seekdb_store

ROOT = Path(__file__).resolve().parent.parent


def read_file(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def build_context(scope_id: str, keyword: str) -> str:
    """把规格、代码、测试、seekdb 检索结果与 PowerContext 状态拼成上下文。"""

    spec = read_file("spec.md")
    code = read_file("app/rules.py")
    test = read_file("tests/test_rules.py")
    docs = seekdb_store.query(keyword)
    state = powercontext_state.read_state(scope_id)

    parts = [
        "## 规格",
        spec,
        "## 当前代码 app/rules.py",
        code,
        "## 测试 tests/test_rules.py",
        test,
        "## seekdb 检索到的相关资料",
        "\n".join(f"- [{doc.kind}] {doc.name}\n{doc.content}" for doc in docs) or "（无）",
        "## PowerContext 任务状态",
        state or "（空）",
    ]
    return "\n\n".join(parts)


def locate(keyword: str) -> str:
    """返回上下文模块的定位输出（规格/代码/测试/状态来源）。"""

    docs = seekdb_store.query(keyword)
    return (
        "适用规格条目：R1 年龄判定（spec.md）\n"
        "需修改代码：app/rules.py 的 is_adult 函数\n"
        "验证测试：tests/test_rules.py\n"
        f"seekdb 命中：{[doc.name for doc in docs]}"
    )
