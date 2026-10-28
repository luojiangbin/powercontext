"""PowerContext 任务状态存取。"""

from __future__ import annotations

import json
import os
import urllib.request

POWERCONTEXT_URL = os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000").rstrip("/")
SCOPE_KEY = "minimax-sdd-demo"


def _post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{POWERCONTEXT_URL}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def ensure_scope() -> str:
    created = _post(
        "/v1/scopes",
        {"title": "SDD 上下文演示", "summary": "MiniMax 生态题", "idempotency_key": SCOPE_KEY},
    )
    return created["scope_id"]


def write_state(scope_id: str, text: str) -> None:
    _post(
        "/v1/memory/remember",
        {"scope_id": scope_id, "kind": "task-state", "text": text, "reason": "sdd-run"},
    )


def read_state(scope_id: str) -> str:
    """按 kind 列出该 scope 的全部 task-state 记忆，取最新一条正文。"""

    page = _post(
        "/v1/atomic-memory/list",
        {"scope_id": scope_id, "kind": "task-state", "limit": 100},
    )
    items = page.get("items") or []
    if not items:
        return ""
    texts = [item.get("text", "") for item in items if item.get("text")]
    return texts[-1] if texts else ""
