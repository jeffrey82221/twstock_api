"""Row-count snapshotting + daily-growth estimation for the ``pop`` schema.

State lives in a dedicated ``demo`` schema (separate from poc/pop/hidden) so it
never interferes with ``Pipeline``'s ``DROP SCHEMA ... CASCADE`` resets.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pg_tool import PostgreSQLTool

SNAPSHOT_TABLE = "demo.row_count_history"


def ensure_schema(db: PostgreSQLTool) -> None:
    db.execute_query("CREATE SCHEMA IF NOT EXISTS demo;")
    db.execute_query(
        f"""
        CREATE TABLE IF NOT EXISTS {SNAPSHOT_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            table_name TEXT NOT NULL,
            row_count BIGINT NOT NULL
        );
        """
    )
    db.execute_query(
        "CREATE INDEX IF NOT EXISTS row_count_history_table_ts_idx "
        f"ON {SNAPSHOT_TABLE} (table_name, snapshot_at);"
    )


def get_pop_row_counts(db: Optional[PostgreSQLTool] = None) -> Dict[str, int]:
    """Estimated row counts (``pg_class.reltuples``) for every table in ``pop``.

    Uses planner statistics instead of ``COUNT(*)`` so this is cheap enough to
    call on every dashboard refresh without scanning the (potentially large)
    materialized views.
    """
    db = db or PostgreSQLTool()
    rows = db.fetch_all(
        """
        SELECT c.relname, c.reltuples::BIGINT
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'pop' AND c.relkind IN ('r', 'm')
        ORDER BY c.relname;
        """
    )
    return {name: max(int(count), 0) for name, count in rows}


def snapshot_once(db: Optional[PostgreSQLTool] = None) -> int:
    """Insert one row-count snapshot for every ``pop`` table. Returns rows inserted."""
    db = db or PostgreSQLTool()
    ensure_schema(db)
    counts = get_pop_row_counts(db)
    if not counts:
        return 0
    values_sql = ", ".join("(%s, %s)" for _ in counts)
    params: List = []
    for name, count in counts.items():
        params.extend([name, count])
    db.execute_query(
        f"INSERT INTO {SNAPSHOT_TABLE} (table_name, row_count) VALUES {values_sql};",
        tuple(params),
    )
    return len(counts)


def get_growth_estimates(db: Optional[PostgreSQLTool] = None) -> List[dict]:
    """Estimate each table's daily row growth from its earliest vs. latest snapshot."""
    db = db or PostgreSQLTool()
    ensure_schema(db)
    rows = db.fetch_all(
        f"""
        WITH ranked AS (
            SELECT table_name, snapshot_at, row_count,
                   ROW_NUMBER() OVER (PARTITION BY table_name ORDER BY snapshot_at ASC) AS rn_asc,
                   ROW_NUMBER() OVER (PARTITION BY table_name ORDER BY snapshot_at DESC) AS rn_desc
            FROM {SNAPSHOT_TABLE}
        )
        SELECT table_name,
               MAX(CASE WHEN rn_asc = 1 THEN row_count END) AS first_count,
               MAX(CASE WHEN rn_asc = 1 THEN snapshot_at END) AS first_ts,
               MAX(CASE WHEN rn_desc = 1 THEN row_count END) AS last_count,
               MAX(CASE WHEN rn_desc = 1 THEN snapshot_at END) AS last_ts,
               COUNT(*) AS sample_count
        FROM ranked
        GROUP BY table_name
        ORDER BY table_name;
        """
    )
    results = []
    for table_name, first_count, first_ts, last_count, last_ts, sample_count in rows:
        hours = (last_ts - first_ts).total_seconds() / 3600.0 if first_ts and last_ts else 0.0
        daily_growth = None
        if sample_count >= 2 and hours > 0:
            daily_growth = (last_count - first_count) / hours * 24.0
        results.append(
            {
                "table_name": table_name,
                "first_count": first_count,
                "last_count": last_count,
                "sample_count": sample_count,
                "hours_observed": round(hours, 2),
                "estimated_daily_growth": round(daily_growth, 1) if daily_growth is not None else None,
            }
        )
    return results
