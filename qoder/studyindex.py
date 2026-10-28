"""课程笔记速查 CLI。

用法：
    python studyindex.py init                 初始化库与表
    python studyindex.py ingest notes         导入笔记目录
    python studyindex.py search 红黑树         关键词检索
    python studyindex.py build-site           生成静态站到 site/
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

import seekdb_store

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"


def slug(text: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text).strip("-")
    return cleaned or "note"


def page(title: str, body: str, extra_head: str = "") -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.7 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         max-width: 820px; margin: 0 auto; padding: 32px 20px; }}
  h1 {{ font-size: 28px; margin-bottom: 4px; }}
  .muted {{ color: #888; font-size: 14px; }}
  a {{ color: #1a73e8; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  input {{ width: 100%; padding: 12px 14px; font-size: 16px; border: 1px solid #ccc;
           border-radius: 8px; box-sizing: border-box; }}
  .item {{ padding: 12px 0; border-bottom: 1px solid #eee; }}
  pre {{ background: #f6f8fa; padding: 12px; border-radius: 8px; overflow-x: auto; }}
</style>
{extra_head}
</head>
<body>
{body}
</body>
</html>
"""


def cmd_init() -> None:
    seekdb_store.init_store()
    print("seekdb 库与表已就绪")


def cmd_ingest(directory: str) -> None:
    seekdb_store.init_store()
    count = seekdb_store.ingest(Path(directory))
    print(f"已导入 {count} 篇笔记到 seekdb")


def cmd_search(keyword: str) -> None:
    rows = seekdb_store.search(keyword)
    if not rows:
        print("没有命中")
        return
    for row in rows:
        snippet = re.sub(r"\s+", " ", row["content"])[:120]
        print(f"[{row['course']}] {row['title']}  命中 {row['hits']} 次")
        print(f"    {snippet}...")


def cmd_build_site() -> None:
    rows = seekdb_store.all_notes()
    if not rows:
        print("库里没有笔记，先 ingest")
        return
    SITE.mkdir(exist_ok=True)
    (SITE / "notes").mkdir(exist_ok=True)

    by_course: dict[str, list[dict]] = {}
    for row in rows:
        by_course.setdefault(row["course"], []).append(row)

    links = []
    for course, items in sorted(by_course.items()):
        entries = []
        for item in items:
            filename = f"{slug(course)}-{slug(item['title'])}.html"
            body = (
                f"<h1>{html.escape(item['title'])}</h1>"
                f"<p class='muted'>{html.escape(course)}</p>"
                f"<pre>{html.escape(item['content'])}</pre>"
                f"<p><a href='../index.html'>← 返回目录</a></p>"
            )
            (SITE / "notes" / filename).write_text(page(item["title"], body), encoding="utf-8")
            entries.append(
                f"<div class='item'><a href='notes/{filename}'>{html.escape(item['title'])}</a></div>"
            )
        links.append(
            f"<h2>{html.escape(course)}</h2>" + "".join(entries)
        )

    index_body = (
        "<h1>课程资料速查</h1>"
        "<p class='muted'>由 seekdb 检索索引生成的静态站</p>"
        "<p><a href='search.html'>🔍 站内搜索</a></p>"
        + "".join(links)
    )
    (SITE / "index.html").write_text(page("课程资料速查", index_body), encoding="utf-8")

    payload = [
        {
            "title": row["title"],
            "course": row["course"],
            "text": re.sub(r"\s+", " ", row["content"])[:4000],
        }
        for row in rows
    ]
    search_body = (
        "<h1>站内搜索</h1>"
        "<p class='muted'>输入关键词，实时过滤（索引来自 seekdb 导出）</p>"
        "<input id='q' placeholder='例如：红黑树、事务、索引' autofocus>"
        "<div id='out' style='margin-top:20px'></div>"
        "<script>\n"
        f"const DATA = {json.dumps(payload, ensure_ascii=False)};\n"
        "const q = document.getElementById('q');\n"
        "const out = document.getElementById('out');\n"
        "function render(){\n"
        "  const k = q.value.trim().toLowerCase();\n"
        "  if(!k){ out.innerHTML = '<p class=muted>共 ' + DATA.length + ' 篇笔记</p>'; return; }\n"
        "  const hits = DATA.map(d => {\n"
        "    const n = (d.text.toLowerCase().split(k).length - 1)\n"
        "            + (d.title.toLowerCase().includes(k) ? 3 : 0);\n"
        "    return {d, n};\n"
        "  }).filter(x => x.n > 0).sort((a,b) => b.n - a.n);\n"
        "  out.innerHTML = hits.length\n"
        "    ? hits.map(({d,n}) => `<div class=item><b>${d.title}</b>`\n"
        "        <span class=muted> · ${d.course} · 命中 ${n}</span>\n"
        "        <p class=muted>${d.text.slice(0,120)}…</p></div>`).join('')\n"
        "    : '<p class=muted>没有命中</p>';\n"
        "}\n"
        "q.addEventListener('input', render); render();\n"
        "</script>"
    )
    (SITE / "search.html").write_text(page("站内搜索", search_body), encoding="utf-8")
    print(f"静态站已生成：{SITE}（{len(rows)} 篇笔记）")


def main() -> None:
    parser = argparse.ArgumentParser(description="课程笔记速查")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    ingest_parser = sub.add_parser("ingest")
    ingest_parser.add_argument("directory", nargs="?", default="notes")
    search_parser = sub.add_parser("search")
    search_parser.add_argument("keyword")
    sub.add_parser("build-site")
    args = parser.parse_args()

    if args.command == "init":
        cmd_init()
    elif args.command == "ingest":
        cmd_ingest(args.directory)
    elif args.command == "search":
        cmd_search(args.keyword)
    else:
        cmd_build_site()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
