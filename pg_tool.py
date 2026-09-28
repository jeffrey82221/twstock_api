import psycopg  # Or import psycopg2 as psycopg

class PostgreSQLTool:
    """A custom database helper tool based on psycopg."""
    
    def __init__(self):
        """Initialize the tool with a Data Source Name (DSN) connection string."""
        self._dsn = "postgresql://postgres:postgres@localhost:5432/app_db"
        self._conn = None

    def get_conn(self):
        """Property to create or retrieve an active database connection."""
        # Check if connection doesn't exist, is closed, or has broken status
        try:
            self._conn = psycopg.connect(self._dsn)
            return self._conn
        except BaseException as e:
            print(f"Failed to connect to the database: {e}")
            raise e

    def execute_query(self, query: str, params: tuple = None):
        """Execute a query (INSERT, UPDATE, DELETE) and commit changes."""
        try:
            conn = self.get_conn()
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                conn.commit()
        except BaseException as e:
            print(f"Database operation failed: {e}")
            raise e
        finally:
            if self._conn:
                self._conn.close()
                self._conn = None

    def fetch_all(self, query: str, params: tuple = None) -> list:
        """Execute a query and fetch all resulting records."""
        try:
            conn = self.get_conn()
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                return cursor.fetchall()
        except BaseException as e:
            print(f"Database fetch operation failed: {e}")
            raise e
        finally:
            if self._conn:
                self._conn.close()
                self._conn = None
            
    def get_dependent_views(self, view_name: str, schema: str = "poc") -> list:
        """Recursively find every view/materialized view that depends on
        (directly or transitively references) the given view or table.

        This walks PostgreSQL's own catalog dependency graph
        (`pg_depend` -> `pg_rewrite` -> `pg_class`) rather than parsing SQL
        text, following the approach described in
        https://www.cybertec-postgresql.com/en/tracking-view-dependencies-in-postgresql/
        A view never depends on the objects it selects from directly -- its
        `ON SELECT` rewrite rule (stored in `pg_rewrite`) does, so the chain
        to walk is: referenced relation -> `pg_depend` -> rule in
        `pg_rewrite` -> view in `pg_class`.

        Calling this once per view in the `poc` schema (or any other
        schema) builds a full downstream dependency map, which is useful to
        check what else would break/need rebuilding if a given `poc` view's
        definition changed.

        Args:
            view_name: name of the view or table to inspect. May be
                schema-qualified (e.g. "poc.ohlcv_daily") or bare (e.g.
                "ohlcv_daily"), in which case `schema` is used to qualify
                it.
            schema: schema used to qualify `view_name` when it isn't
                already schema-qualified (contains no "."). Defaults to
                "poc".

        Returns:
            A list of dicts, one per dependent view/materialized view,
            sorted by dependency level (ascending) then by view name:
            ``{"view": "<schema>.<name>", "is_materialized": bool,
            "level": int}``. ``level`` counts hops from `view_name`
            (1 = views/materialized views directly referencing it, 2 =
            views referencing those, ...). Each view is listed once, at
            the *deepest* level it is reachable at (mirrors the source
            article's `GROUP BY view ... max(level)`, needed because a view
            can depend on the target both directly and indirectly through
            another view). Returns an empty list for a leaf view with no
            dependents, or a view/table that does not exist raises the
            underlying database error (invalid `::regclass` cast).
        """
        qualified = view_name if "." in view_name else f"{schema}.{view_name}"
        query = """
            WITH RECURSIVE dependent_views AS (
                -- views/materialized views directly depending on the target
                SELECT DISTINCT v.oid::regclass AS view,
                       v.relkind = 'm' AS is_materialized,
                       1 AS level
                FROM pg_depend AS d
                JOIN pg_rewrite AS r ON r.oid = d.objid
                JOIN pg_class AS v ON v.oid = r.ev_class
                WHERE v.relkind IN ('v', 'm')
                  AND d.classid = 'pg_rewrite'::regclass
                  AND d.refclassid = 'pg_class'::regclass
                  AND d.deptype = 'n'
                  AND d.refobjid = %s::regclass

                UNION

                -- views/materialized views depending on those, recursively
                SELECT v.oid::regclass,
                       v.relkind = 'm',
                       dependent_views.level + 1
                FROM dependent_views
                JOIN pg_depend AS d ON d.refobjid = dependent_views.view
                JOIN pg_rewrite AS r ON r.oid = d.objid
                JOIN pg_class AS v ON v.oid = r.ev_class
                WHERE v.relkind IN ('v', 'm')
                  AND d.classid = 'pg_rewrite'::regclass
                  AND d.refclassid = 'pg_class'::regclass
                  AND d.deptype = 'n'
                  AND v.oid <> dependent_views.view  -- avoid infinite loops
            )
            SELECT view::text AS view,
                   bool_or(is_materialized) AS is_materialized,
                   max(level) AS level
            FROM dependent_views
            GROUP BY view
            ORDER BY max(level), view::text;
        """
        rows = self.fetch_all(query, (qualified,))
        return [
            {"view": row[0], "is_materialized": row[1], "level": row[2]}
            for row in rows
        ]

    def get_view_columns(self, view_name: str, schema: str = "poc") -> list:
        """Column names of a table or view, in schema-declaration order.

        Reads PostgreSQL's own catalog (`information_schema.columns`), so
        the result reflects the object's *actual* columns as it currently
        exists in the database -- independent of, and a ground truth for
        cross-checking against, any static SQL text parsing (e.g. by
        LineageX).

        Args:
            view_name: name of the view/table to inspect. May be
                schema-qualified (e.g. "poc.ohlcv_daily") or bare (e.g.
                "ohlcv_daily"), in which case `schema` is used to qualify
                it.
            schema: schema used to qualify `view_name` when it isn't
                already schema-qualified (contains no "."). Defaults to
                "poc".

        Returns:
            A list of column names (str), ordered by `ordinal_position`.
            Returns an empty list if the view/table does not exist or has
            no columns.
        """
        if "." in view_name:
            resolved_schema, name = view_name.split(".", 1)
        else:
            resolved_schema, name = schema, view_name
        query = """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position;
        """
        rows = self.fetch_all(query, (resolved_schema, name))
        return [row[0] for row in rows]

    def setup(self):
        """Set up the database with necessary extensions and schemas."""
        try:
            conn = self.get_conn()
            with conn.cursor() as cursor:
                with open('db/setting.sql', 'r') as f:
                    sql = f.read()
                    cursor.execute(sql)
            conn.commit()
        except BaseException as e:
            print(f"Database setup failed: {e}")
            raise e
        finally:
            if self._conn:
                self._conn.close()
                self._conn = None