"""MiniMax 代码修改 Agent。"""

from __future__ import annotations

import os

from openai import OpenAI

BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-Text-01")


def fix_code(context: str) -> str:
    """调用 MiniMax 根据上下文生成修正后的完整 app/rules.py 文本。"""

    if not API_KEY:
        raise RuntimeError("缺少 MINIMAX_API_KEY 环境变量")

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    prompt = (
        "你是一名严谨的工程师。下面是规格、当前代码、测试和任务状态。\n"
        "请修复 app/rules.py 中的 is_adult 函数，使全部测试通过。\n"
        "只输出修正后的完整 app/rules.py 文件内容，不要任何解释、不要 markdown 代码块标记。\n\n"
        f"{context}"
    )
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return (response.choices[0].message.content or "").strip()
