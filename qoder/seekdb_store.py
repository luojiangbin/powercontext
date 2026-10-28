"""课程笔记速查 —— seekdb 存储与全文检索。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pymysql

DATABASE = "studyindex"
TABLE = "notes"


def _connect(with_db: bool = True) -> pymysql.Connection:
    return pymysql.connect(
        host=os.environ.get("SEEKDB_HOST", "127.0.0.1"),
        port=int(os.environ.get("SEEKDB_PORT", "2881")),
        user=os.environ.get("SEEKDB_USER", "root"),
        password=os.environ.get("SEEKDB_PASSWORD", ""),
        database=DATABASE if with_db else None,
        autocommit=True,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


@dataclass
class Note:
    title: str
    course: str
    content: str


def init_store() -> None:
    with _connect(False) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DATABASE}")
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS {TABLE} ("
                "id INT AUTO_INCREMENT PRIMARY KEY,"
                " title VARCHAR(200) NOT NULL,"
                " course VARCHAR(100) NOT NULL,"
                " content LONGTEXT NOT NULL,"
                " updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )


def reset_store() -> None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"TRUNCATE TABLE {TABLE}")


def ingest(directory: Path) -> int:
    """把目录下的 .md/.txt 笔记写入 seekdb，按文件名去重覆盖。"""

    count = 0
    for path in sorted(directory.rglob("*")):
        if path.suffix.lower() not in {".md", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        title = path.stem
        course = path.parent.name if path.parent != directory else "未分类"
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {TABLE} WHERE title=%s AND course=%s", (title, course))
                cursor.execute(
                    f"INSERT INTO {TABLE} (title, course, content) VALUES (%s, %s, %s)",
                    (title, course, text),
                )
        count += 1
    return count


def search(keyword: str, limit: int = 20, course: str | None = None) -> list[dict]:
    """按标题或正文做关键词检索，命中数高的排前面；传入 course 时只查该课程。"""

    pattern = f"%{keyword}%"
    sql = (
        f"SELECT title, course, content,"
        f" (CHAR_LENGTH(content) - CHAR_LENGTH(REPLACE(content, %s, ''))) / CHAR_LENGTH(%s) AS hits"
        f" FROM {TABLE}"
        " WHERE (title LIKE %s OR content LIKE %s)"
    )
    params: list = [keyword, keyword, pattern, pattern]
    if course:
        sql += " AND course = %s"
        params.append(course)
    sql += " ORDER BY hits DESC, title"
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())


def all_notes() -> list[dict]:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT title, course, content FROM {TABLE} ORDER BY course, title")
            return list(cursor.fetchall())
