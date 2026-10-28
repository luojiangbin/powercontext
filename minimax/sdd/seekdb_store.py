"""seekdb 工程资料存取与检索。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pymysql

SEEKDB_HOST = os.environ.get("SEEKDB_HOST", "127.0.0.1")
SEEKDB_PORT = int(os.environ.get("SEEKDB_PORT", "2881"))
SEEKDB_USER = os.environ.get("SEEKDB_USER", "root")
SEEKDB_PASSWORD = os.environ.get("SEEKDB_PASSWORD", "")
DATABASE = "sdd"


@dataclass
class Doc:
    kind: str
    name: str
    content: str


def _connect(database: str | None = None) -> pymysql.Connection:
    return pymysql.connect(
        host=SEEKDB_HOST,
        port=SEEKDB_PORT,
        user=SEEKDB_USER,
        password=SEEKDB_PASSWORD,
        database=database,
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def init_database(spec_text: str, code_text: str, test_text: str) -> None:
    """建库建表并把规格/代码/测试资料写入 seekdb。"""

    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DATABASE}")
    with _connect(DATABASE) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS docs ("
                "id INT AUTO_INCREMENT PRIMARY KEY,"
                " kind VARCHAR(32), name VARCHAR(255), content TEXT)"
            )
            cursor.execute("DELETE FROM docs")
            rows = [
                ("spec", "R1 年龄判定", spec_text),
                ("code", "app/rules.py", code_text),
                ("test", "tests/test_rules.py", test_text),
            ]
            cursor.executemany(
                "INSERT INTO docs (kind, name, content) VALUES (%s, %s, %s)", rows
            )
    query("spec")  # 触发一次查询，验证连接与检索可用


def query(keyword: str) -> list[Doc]:
    """按关键词检索工程资料，结果用于构造上下文。"""

    with _connect(DATABASE) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT kind, name, content FROM docs "
                "WHERE name LIKE %s OR content LIKE %s ORDER BY id",
                (f"%{keyword}%", f"%{keyword}%"),
            )
            return [Doc(row["kind"], row["name"], row["content"]) for row in cursor.fetchall()]


def query_log() -> str:
    """返回一次检索记录，用于技术使用记录。"""

    hits = query("18")
    return "\n".join(f"- [{doc.kind}] {doc.name}" for doc in hits)
