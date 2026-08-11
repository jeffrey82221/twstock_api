import os
from typing import Any

import psycopg
from psycopg.rows import dict_row
from fastmcp import FastMCP

mcp = FastMCP("app-db")

DATABASE_URL = os.environ["DATABASE_URL"]


def connect():
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=1000000",
    )


@mcp.tool
def list_tables(schema: str = "public") -> list[dict[str, Any]]:
    """列出指定 schema 的資料表與 views。"""
    sql = """
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = %s
        ORDER BY table_name
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (schema,))
        return cur.fetchall()


@mcp.tool
def describe_table(table_name: str, schema: str = "public") -> list[dict[str, Any]]:
    """取得資料表欄位、型別與 nullable 資訊。"""
    sql = """
        SELECT
            column_name,
            data_type,
            is_nullable,
            column_default
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (schema, table_name))
        return cur.fetchall()


@mcp.tool
def select(sql: str) -> list[dict[str, Any]]:
    """執行唯讀 SELECT 或 WITH 查詢，最多回傳 1,000 筆。"""
    normalized = sql.lstrip().lower()

    if not (normalized.startswith("select") or normalized.startswith("with")):
        raise ValueError("Only SELECT or WITH queries are allowed.")

    if ";" in sql.rstrip().rstrip(";"):
        raise ValueError("Only one SQL statement is allowed.")

    wrapped_sql = f"SELECT * FROM ({sql.rstrip(';')}) AS mcp_query LIMIT 1000"

    with connect() as conn, conn.cursor() as cur:
        cur.execute(wrapped_sql)
        return cur.fetchall()


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8000,
    )