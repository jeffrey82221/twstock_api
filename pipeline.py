
import os
import re
from jinja2 import Template
from sql_metadata import Parser
from typing import Any, List, Dict, Optional
from paradag import DAG, dag_run, SequentialProcessor
from pg_tool import PostgreSQLTool
from psycopg import OperationalError
import json
import time

SLEEP_FOR_DB_RECOVER = 5
class ViewExecutor:
    def __init__(self, results: List[str]):
        self.results = results

    def param(self, vertex):
        # You can map vertex to any param object; here we just echo its name
        return vertex
    
    def execute(self, param):
        self.results.append(param)

class Pipeline:
    def __init__(self, setup_db: bool = True, in_memory_db: bool = False):
        """``in_memory_db=True`` switches the dependent DB to a throw-away
        pgserver-extend instance (bundles the ``http`` extension) instead of
        ``localhost:5432``; call ``close()`` to stop it."""
        self._pg_server = None
        self._pg_dir = None
        self._sql_paths = list(filter(lambda x: x.endswith('.sql'), 
                            os.listdir(os.path.join(os.path.dirname(__file__), 'db', 'poc'))   ))
        
        self.poc_tables = ['poc.' + s.split('.sql')[0] for s in self._sql_paths]
        self.dag = self._create_dag()
        self._db_tool = PostgreSQLTool()
        if in_memory_db:
            self.use_in_memory_db()
        elif setup_db:
            self._db_tool.setup()  # Ensure the database is set up with necessary extensions and schemas
        
    def use_in_memory_db(self) -> str:
        """Start a pgserver-extend DB, apply ``db/setting.sql`` (minus the
        pg_ivm/pg_cron parts it cannot provide) and point the pipeline at it.
        Returns the DSN."""
        import re
        import tempfile
        import pgserver
        import psycopg

        if self._pg_server is not None:
            return self._db_tool._dsn
        self._pg_dir = tempfile.mkdtemp(prefix='twstock_pgserver_')
        self._pg_server = pgserver.get_server(self._pg_dir)
        self._pg_server.psql('CREATE DATABASE app_db;')
        dsn = self._pg_server.get_uri(database='app_db')
        with open(os.path.join(os.path.dirname(__file__), 'db', 'setting.sql'), 'r', encoding='utf-8') as f:
            setting_sql = f.read()
        for pattern in (
            r"CREATE EXTENSION IF NOT EXISTS\s+pg_ivm\s*;",
            r"CREATE EXTENSION IF NOT EXISTS\s+pg_cron\s*;",
            r"CREATE\s+or\s+REPLACE\s+VIEW\s+public\.(job_run_details|job)\b.*?"
            r"GRANT SELECT ON public\.\1 TO mcp_reader;",
        ):
            setting_sql = re.sub(pattern, '', setting_sql, flags=re.IGNORECASE | re.DOTALL)
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(setting_sql)
        self._db_tool._dsn = dsn
        return dsn

    def close(self):
        """Stop the in-memory DB started by ``use_in_memory_db``."""
        import shutil
        if self._pg_server is not None:
            self._pg_server.cleanup()
            shutil.rmtree(self._pg_dir, ignore_errors=True)
            self._pg_server = self._pg_dir = None

    def _poc_select_sql(self, view: str, limit: int, where: Optional[str]) -> str:
        view = view[:-len('.sql')] if view.endswith('.sql') else view
        view = view.split('.')[-1]
        if view + '.sql' not in self._sql_paths:
            raise ValueError(f'Unknown poc view: {view}')
        sql = f'SELECT * FROM poc.{view}'
        if where:
            sql += f' WHERE {where}'
        return sql + f' LIMIT {int(limit)}'

    def select_poc_view(self, view: str, limit: int = 10, where: Optional[str] = None,
                        timeout_seconds: Optional[int] = None) -> List[Dict]:
        """``SELECT * FROM poc.<view> [WHERE ..] LIMIT n``; returns rows as dicts."""
        sql = self._poc_select_sql(view, limit, where)
        with self._db_tool.get_conn() as conn:
            with conn.cursor() as cur:
                if timeout_seconds:
                    cur.execute(f"SET statement_timeout = '{int(timeout_seconds)}s'")
                cur.execute(sql)
                columns = [d.name for d in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]

    def explain_select_poc_view(self, view: str, limit: int = 1, where: Optional[str] = None,
                                analyze: bool = False, timeout_seconds: Optional[int] = None) -> str:
        """Query plan (``EXPLAIN (VERBOSE)``) of the select in ``select_poc_view``.
        ``analyze=True`` also executes it (use ``timeout_seconds`` to bound it)."""
        sql = self._poc_select_sql(view, limit, where)
        options = 'ANALYZE, VERBOSE, BUFFERS' if analyze else 'VERBOSE'
        with self._db_tool.get_conn() as conn:
            with conn.cursor() as cur:
                if timeout_seconds:
                    cur.execute(f"SET statement_timeout = '{int(timeout_seconds)}s'")
                cur.execute(f'EXPLAIN ({options}) {sql}')
                return '\n'.join(row[0] for row in cur.fetchall())

    def _create_dag(self):
        dag = DAG()
        for sql_path in self._sql_paths:
            print('Adding vertex for:', sql_path)
            dag.add_vertex(sql_path)
        for sql_path, src_tables in self.src_tables.items():
            for src_table in src_tables:
                src, dest = src_table.split('.')[-1] + '.sql', sql_path
                print('Adding edge from', src, 'to', dest)
                dag.add_edge(src, dest)
        return dag
    
    @property
    def src_tables(self) -> Dict[str, str]:
        results = dict()
        for sql_path in self._sql_paths:
            with open(os.path.join(os.path.dirname(__file__), 'db', 'poc', sql_path), 'r') as f:
                sql = f.read()
                sql = Template(sql).render(schema='poc')
                print('Parsing SQL for:', sql_path)
                print('SQL content:\n', sql)
                parser = Parser(sql)
                results[sql_path] = [t for t in parser.tables if t.startswith('poc.') and t in self.poc_tables]
        return results
    
    @property
    def view_create_sqls(self) -> Dict[str, str]:
        results = dict()
        for sql_path in self._sql_paths:
            view_create_sql = self._get_view_create_sql('poc', sql_path)
            results[sql_path] = view_create_sql
        return results    

    def _get_view_create_sql(self, schema: str, sql_path: str, target_schema: Optional[str]=None) -> str:
        if target_schema is None:
            target_schema = schema
        with open(os.path.join(os.path.dirname(__file__), 'db', 'poc', sql_path), 'r') as f:
            sql = f.read()
            sql = Template(sql).render(schema=schema)
            view_name = sql_path.split('.')[0]
            view_create_sql = f'''CREATE OR REPLACE VIEW {target_schema}.{view_name}
            AS 
            {sql}
            '''
        return view_create_sql
    
    @property
    def ordered_sql_paths(self) -> List[str]:
        results = []
        dag_run(
            self.dag, processor=SequentialProcessor(), executor=ViewExecutor(results))
        return results    

    def create_views(self):
        create_sqls = self.view_create_sqls
        self._db_tool.execute_query('DROP SCHEMA IF EXISTS poc CASCADE;')
        self._db_tool.execute_query('CREATE SCHEMA poc;')
        for sql_path in self.ordered_sql_paths:
            print('Creating view for:', sql_path)
            create_sql = create_sqls[sql_path]
            print('Executing SQL:\n', create_sql)
            self._db_tool.execute_query(create_sql)
    
    def check_view_run_speed(self, timeout_seconds: int = 10):
        """
        Check the execution speed of each view in the DAG.
        Using limit 1 to avoid fetching too many rows, 
        just to check the execution time.
        """
        from psycopg.errors import QueryCanceled, InvalidParameterValue
        sqls_in_concern = []
        for sql_path in self.ordered_sql_paths:
            try:
                table = sql_path.split('.')[0]
                check_sql = f'SELECT * FROM poc.{table} LIMIT 1'
                self._db_tool.execute_query(f"""
                SET statement_timeout = '{timeout_seconds}s';
                {check_sql};
                RESET statement_timeout;
                """)
                print('[SUCCESS] execution of view', table)
            except BaseException as e:
                print('[ERROR] Execution of view', table, 'took too long and was canceled.')
                sqls_in_concern.append((sql_path, e))
        return sqls_in_concern
    
    @property
    def seed_tables(self) -> List[str]:
        """
        Returns a list of seed tables that are 
        aim to be populated step-by-step. 
        """
        sqls = []
        sqls.extend([p for p in self.ordered_sql_paths if p.endswith('_list.sql')])
        results = [p.split('.sql')[0] for p in sqls]
        return list(dict.fromkeys(results))

    def create_seed_tables(self):
        for table in self.seed_tables:
            print('Creating seed table for:', table)
            create_sql = f'CREATE TABLE IF NOT EXISTS pop.{table} AS SELECT * FROM poc.{table} LIMIT 0;'
            print('Executing SQL:\n', create_sql)
            self._db_tool.execute_query(create_sql)
            self._set_seed_table_primary_key('pop', table)

    def _get_table_columns(self, schema: str, table: str) -> List[str]:
        """Return column names of ``schema.table`` in ordinal order.

        Reads information_schema.columns; returns [] if the table does
        not exist yet (caller is responsible for handling that case).
        """
        return [
            row[0]
            for row in self._db_tool.fetch_all(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                (schema, table),
            )
        ]

    @staticmethod
    def _sql_literal(value: Any) -> str:
        """Render a Python scalar as an untyped SQL literal (``'...'``).

        Untyped literals let Postgres coerce to the column's own type
        (text/int/date/numeric), so ``"stock_id" = '2330'`` and
        ``"year" = '2024'`` both work and stay pushable into the view.
        Single quotes are doubled (safe with standard_conforming_strings,
        the Postgres default).
        """
        if value is None:
            raise ValueError('Filter value must not be None (seed rows are never NULL).')
        if isinstance(value, bool):
            text = 'true' if value else 'false'
        elif isinstance(value, (int, float, str)):
            text = str(value)
        else:
            raise ValueError(f'Unsupported filter value type: {type(value).__name__}')
        if '\x00' in text:
            raise ValueError('Filter value must not contain NUL characters.')
        return "'" + text.replace("'", "''") + "'"

    def _build_seed_filter_clause(self, columns: List[str], filters: Optional[Dict[str, Any]]) -> str:
        """``AND "col" = 'v'`` / ``AND "col" IN ('a','b')`` for each filter.

        Column names are only accepted if they are real columns of
        pop.<table> (== hidden.<table>), so no identifier is ever taken
        verbatim from the caller.
        """
        if not filters:
            return ''
        parts = []
        for col, value in filters.items():
            if col not in columns:
                raise ValueError(f'Unknown filter column {col!r}. Columns: {columns}')
            if isinstance(value, (list, tuple, set)):
                values = list(value)
                if not values:
                    raise ValueError(f'Filter list for {col!r} is empty.')
                parts.append(f'"{col}" IN ({", ".join(self._sql_literal(v) for v in values)})')
            else:
                parts.append(f'"{col}" = {self._sql_literal(value)}')
        return ' AND ' + ' AND '.join(parts)

    _ORDER_TERM_RE = re.compile(r'^\s*(\S+?)(?:\s+(asc|desc))?\s*$', re.IGNORECASE)
    _ORDERABLE_TIME_TYPES = ('date', 'timestamp without time zone', 'timestamp with time zone')
    _ORDERABLE_TIME_NAMES = ('year', 'ym', 'quarter', 'month')

    def _default_seed_order_by(self, table: str) -> List[str]:
        """Newest-first default: the first date/timestamp column of
        pop.<table>, else a column named year/ym/quarter/month, as DESC.
        Returns [] when the table has no time-like column."""
        rows = self._db_tool.fetch_all(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'pop' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        for name, dtype in rows:
            if dtype in self._ORDERABLE_TIME_TYPES:
                return [f'{name} DESC']
        for name, _ in rows:
            if name in self._ORDERABLE_TIME_NAMES:
                return [f'{name} DESC']
        return []

    def _build_seed_order_clause(self, columns: List[str], order_by: Optional[List[str]]) -> str:
        """`` ORDER BY "c1" DESC, "c2" ASC`` from ``["c1", "c2 asc"]``.

        A bare column means DESC (newest first). Columns must be real
        columns of pop.<table>; only ASC/DESC are accepted as modifiers.
        """
        if not order_by:
            return ''
        terms = []
        for term in order_by:
            m = self._ORDER_TERM_RE.match(term or '')
            if not m:
                raise ValueError(f'Bad order_by term {term!r}; use "column" or "column ASC|DESC".')
            col, direction = m.group(1), (m.group(2) or 'DESC').upper()
            if col not in columns:
                raise ValueError(f'Unknown order_by column {col!r}. Columns: {columns}')
            terms.append(f'"{col}" {direction}')
        return ' ORDER BY ' + ', '.join(terms)

    def _build_seed_insert_sql(
        self,
        table: str,
        row_cnt: int = 1,
        table_reindex: bool = False,
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[List[str]] = None,
    ) -> List[str]:
        """Build the seed-refresh SQL for ``pop.<table>``.

        Parameters
        ----------
        table:
            Seed table target, e.g. ``company_list``.
        row_cnt:
            How many new rows to insert per tick.
        table_reindex:
            When True, prepend ``REINDEX TABLE CONCURRENTLY pop.<table>;``
            before the INSERT. Defaults to False to avoid unnecessary index
            maintenance during routine probes and scheduled refreshes.
        filters:
            Optional ``{column: value}`` restricting which hidden rows are
            picked, e.g. ``{"stock_id": "2330"}`` or
            ``{"stock_id": ["2330", "2317"]}`` (IN). Lets callers prioritise
            the rows most relevant to the current analysis.
        order_by:
            Optional ``["column", "column ASC|DESC", ...]`` deciding which
            rows the LIMIT keeps; a bare column sorts DESC (newest first).
            None/[] keeps the original unordered pick (cheapest; used by cron).

        This SQL is shared by both the bootstrap one-shot path and the
        pg_cron scheduled refresh path so all callers stay in sync.
        """
        columns = self._get_table_columns('pop', table)
        if not columns:
            raise ValueError(
                f'Cannot build seed INSERT for {table!r}: pop.{table} has no columns '
                '(was create_seed_tables run?).'
            )
        not_null_clause = ' AND '.join(f'"{c}" IS NOT NULL' for c in columns)
        not_null_clause += self._build_seed_filter_clause(columns, filters)
        insert_sql = (
            f'INSERT INTO pop.{table} '
            f'SELECT * FROM hidden.{table} '
            f'WHERE {not_null_clause} '
            f'EXCEPT SELECT * FROM pop.{table}'
            f'{self._build_seed_order_clause(columns, order_by)} '
            f'LIMIT {row_cnt};'
        )
        if table_reindex:
            return [
                f'REINDEX TABLE CONCURRENTLY pop.{table}; ',
                insert_sql,
            ]
        return [insert_sql]

    def _set_seed_table_primary_key(self, schema: str, table: str) -> None:
        """Promote every column of a freshly created seed table to PRIMARY KEY.

        A composite PK over all columns is the simplest way to make a
        seed table behave like a set (no duplicates) without having to
        know the natural key in advance. We:
          1. Skip if the table already has a primary key (idempotent reruns).
          2. SET NOT NULL on every column -- PRIMARY KEY requires it.
          3. ADD CONSTRAINT <table>_pkey PRIMARY KEY (col1, col2, ...).
        """
        pk_exists = self._db_tool.fetch_all(
            """
            SELECT 1
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE c.contype = 'p'
              AND n.nspname = %s
              AND t.relname = %s
            """,
            (schema, table),
        )
        if pk_exists:
            print(f'Primary key already exists on {schema}.{table}, skipping.')
            return

        columns = self._get_table_columns(schema, table)
        if not columns:
            print(f'No columns found for {schema}.{table}, skipping PK.')
            return

        for col in columns:
            not_null_sql = (
                f'ALTER TABLE {schema}.{table} '
                f'ALTER COLUMN "{col}" SET NOT NULL;'
            )
            print('Executing SQL:\n', not_null_sql)
            self._db_tool.execute_query(not_null_sql)

        col_list = ', '.join(f'"{c}"' for c in columns)
        pk_sql = (
            f'ALTER TABLE {schema}.{table} '
            f'ADD CONSTRAINT {table}_pkey PRIMARY KEY ({col_list});'
        )
        print('Executing SQL:\n', pk_sql)
        self._db_tool.execute_query(pk_sql)


    def _get_matview_create_sqls(self, schema: str, sql_path: str, target_schema: Optional[str]=None) -> str:
        sql = self._get_matview_select_sqls(schema, sql_path)
        view_name = sql_path.split('.')[0]
        if target_schema is None:
            target_schema = schema
        view_create_sql = f"""SELECT pgivm.create_immv('{target_schema}.{view_name}',
            $sql$
            {sql}
            $sql$
        );
        """
        return view_create_sql
    
    def _get_matview_select_sqls(self, schema: str, sql_path: str) -> str:
        with open(os.path.join(os.path.dirname(__file__), 'db', 'poc', sql_path), 'r') as f:
            sql = Template(f.read()).render(schema=schema)
            sql = sql.strip().removesuffix(';')
        upstream_sql_paths = self.dag.predecessors(sql_path)
        for upstream_sql_path in upstream_sql_paths:
            upstream_view_name = upstream_sql_path.split('.')[0]
            if upstream_view_name not in self.seed_tables:
                upstream_sql = self._get_matview_select_sqls(schema, upstream_sql_path)
                upstream_sql = upstream_sql.strip().removesuffix(';')
                sql = sql.replace(f'{schema}.{upstream_view_name}', f'({upstream_sql})')
                print('Replacing', f'{schema}.{upstream_view_name}', 'with subquery for:', upstream_sql_path)
        return sql
    
    def table_exists(self, schema: str, table_name: str) -> bool:
        check_sql = f"""
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables 
            WHERE table_schema = '{schema}' 
            AND table_name = '{table_name}'
        );
        """
        result = self._db_tool.fetch_all(check_sql)
        return result[0][0] # Assuming the result is a list of tuples

    def create_mat_views(self, recreate: bool = False, test_insert: bool=False):
        """
        從 seed tables 往後接 materialized view 
        (建立 materialized view ，如果已經存在就建立到 hidden schema)

        作法：
        """
        if recreate:
            self._db_tool.execute_query('DROP SCHEMA IF EXISTS pop CASCADE;')
            self._db_tool.execute_query('DROP SCHEMA IF EXISTS hidden CASCADE;')
        self._db_tool.execute_query('CREATE SCHEMA IF NOT EXISTS pop;')    
        self._db_tool.execute_query('CREATE SCHEMA IF NOT EXISTS hidden;')
        self.create_seed_tables()
        for sql_path in self.ordered_sql_paths:
            if not self.table_exists('pop', sql_path.split('.')[0]):
                sql = self._get_matview_create_sqls('pop', sql_path)
                print('=================================')
                print('Creating materialized view for:', sql_path)
                print('Executing SQL:\n', sql)
                self._db_tool.execute_query(sql)
                print('==========SUCCESS==================')
            elif sql_path.endswith('_list.sql'):
                sql = self._get_view_create_sql('pop', sql_path, target_schema='hidden')
                print('=================================')
                print('Creating view for:', sql_path)
                print('Executing SQL:\n', sql)
                self._db_tool.execute_query(sql)
                print('==========SUCCESS==================')
        if test_insert:
            self._insert_few_rows_to_seed_tables(row_cnt=1)

    # ------------------------------------------------------------------
    # Targeted pop updates: only touch objects whose SQL changed
    # ------------------------------------------------------------------

    def _render_sql(self, schema: str, sql_path: str) -> str:
        with open(os.path.join(os.path.dirname(__file__), 'db', 'poc', sql_path), 'r') as f:
            return Template(f.read()).render(schema=schema).strip().removesuffix(';')

    def _expected_pop_definitions(self) -> List[Dict[str, str]]:
        """What every pop object should be, in DAG order.

        Seed tables (``*_list``) are plain tables whose *definition* lives in
        ``hidden.<name>``; every other SQL is an IMMV ``pop.<name>`` whose
        query inlines its non-seed upstream (see _get_matview_select_sqls).
        """
        seeds = set(self.seed_tables)
        out = []
        for sql_path in self.ordered_sql_paths:
            name = sql_path[:-len('.sql')]
            if name in seeds:
                out.append({'name': name, 'kind': 'seed', 'sql_path': sql_path,
                            'sql': self._render_sql('pop', sql_path)})
            else:
                sql = self._get_matview_select_sqls('pop', sql_path).strip().removesuffix(';')
                out.append({'name': name, 'kind': 'immv', 'sql_path': sql_path, 'sql': sql})
        return out

    @staticmethod
    def _normalize_viewdef(sql: Optional[str]) -> Optional[str]:
        if sql is None:
            return None
        return ' '.join(sql.split()).rstrip(';').strip()

    def pop_definition_drift(self, names: Optional[List[str]] = None) -> List[Dict[str, object]]:
        """Compare pop objects in the database with the current db/poc SQL.

        Read-only. Both sides are normalised by Postgres: the expected SQL is
        turned into a temporary view (rolled back) and read back with
        pg_get_viewdef; the current side is pg_get_viewdef(hidden.<seed>) or
        pgivm.get_immv_def(pop.<immv>).

        status per object:
          ok            definition matches
          changed       definition differs -> update_mat_views would rebuild it
          missing       pop table / hidden view / IMMV does not exist
          invalid_sql   the expected SQL does not compile against pop (error in
                        'error'); usually an upstream that must be updated first
        """
        expected = self._expected_pop_definitions()
        if names is not None:
            known = {e['name'] for e in expected}
            unknown = sorted(set(names) - known)
            if unknown:
                raise ValueError(f'Unknown pop objects: {unknown}')
            expected = [e for e in expected if e['name'] in set(names)]

        results: List[Dict[str, object]] = []
        conn = self._db_tool.get_conn()
        try:
            with conn.cursor() as cur:
                for e in expected:
                    name, kind = e['name'], e['kind']
                    row: Dict[str, object] = {'name': name, 'kind': kind, 'status': 'ok', 'error': None}
                    if kind == 'seed':
                        cur.execute('SELECT to_regclass(%s) IS NOT NULL, to_regclass(%s) IS NOT NULL',
                                    (f'pop.{name}', f'hidden.{name}'))
                        table_ok, view_ok = cur.fetchone()
                        row['table_exists'], row['hidden_view_exists'] = table_ok, view_ok
                        current = None
                        if view_ok:
                            cur.execute('SELECT pg_get_viewdef(%s::regclass)', (f'hidden.{name}',))
                            current = cur.fetchone()[0]
                        exists = table_ok and view_ok
                    else:
                        cur.execute(
                            'SELECT to_regclass(%s) IS NOT NULL, '
                            'EXISTS (SELECT 1 FROM pgivm.pg_ivm_immv WHERE immvrelid = to_regclass(%s))',
                            (f'pop.{name}', f'pop.{name}'))
                        table_ok, is_immv = cur.fetchone()
                        row['table_exists'], row['is_immv'] = table_ok, is_immv
                        current = None
                        if is_immv:
                            cur.execute('SELECT pgivm.get_immv_def(%s::regclass)', (f'pop.{name}',))
                            current = cur.fetchone()[0]
                        exists = is_immv

                    cur.execute('SAVEPOINT drift_chk')
                    try:
                        cur.execute(f'CREATE TEMP VIEW _pop_drift_chk AS {e["sql"]}')
                        cur.execute("SELECT pg_get_viewdef('_pop_drift_chk'::regclass)")
                        want = cur.fetchone()[0]
                    except Exception as exc:  # noqa: BLE001 - reported per object
                        want = None
                        row['status'] = 'invalid_sql'
                        row['error'] = str(exc).strip().splitlines()[0]
                    finally:
                        cur.execute('ROLLBACK TO SAVEPOINT drift_chk')

                    if row['status'] != 'invalid_sql':
                        if not exists:
                            row['status'] = 'missing'
                        elif self._normalize_viewdef(current) != self._normalize_viewdef(want):
                            row['status'] = 'changed'
                    results.append(row)
        finally:
            conn.rollback()
            conn.close()
        return results

    def _create_one_seed_table(self, table: str) -> None:
        """Empty pop.<table> shaped like poc.<table> (poc must be current).

        LIKE copies the column list without running the poc query; CREATE
        TABLE AS ... LIMIT 0 would still let the planner pre-evaluate the
        IMMUTABLE http_get_content() on constant URLs (an API call).
        """
        self._db_tool.execute_query(
            f'CREATE TABLE IF NOT EXISTS pop.{table} (LIKE poc.{table});')
        self._set_seed_table_primary_key('pop', table)

    def _hidden_views_depending_on(self, table: str) -> List[str]:
        rows = self._db_tool.fetch_all(
            """
            SELECT DISTINCT v.relname
            FROM pg_depend d
            JOIN pg_rewrite r ON r.oid = d.objid
            JOIN pg_class v ON v.oid = r.ev_class
            JOIN pg_namespace n ON n.oid = v.relnamespace
            WHERE d.refobjid = to_regclass(%s) AND n.nspname = 'hidden' AND v.relkind = 'v'
            """,
            (f'pop.{table}',),
        )
        return [r[0] for r in rows]

    def update_mat_views(
        self,
        names: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> List[Dict[str, object]]:
        """Bring only the drifted pop objects in line with db/poc SQL.

        Unlike ``create_mat_views(recreate=True)`` this never drops the pop
        schema or a seed table, so crawled data survives (rule 18):

        * seed (``*_list``): ``CREATE OR REPLACE VIEW hidden.<name>``; if
          ``pop.<name>`` is missing it is created empty. A seed whose columns
          changed is refused (CREATE OR REPLACE VIEW cannot change columns and
          the seed data would have to be dropped) -- handle that explicitly.
        * IMMV: ``DROP TABLE pop.<name> CASCADE`` + ``pgivm.create_immv``,
          which repopulates it by re-running the query over the current seed
          rows (one API call per row, served from job_control.http_cache when
          fresh). ``hidden`` views dropped by the cascade are recreated.
          (pgivm.restore_immv(replace => true) is not used: it adds a second
          set of IVM triggers without removing the old ones, so every later
          seed insert is applied twice.)

        Run ``create_views()`` first so poc reflects the same SQL.

        names:   restrict to these objects (default: every drifted object).
        dry_run: only return the plan.
        Returns one dict per object with the action taken / planned.
        """
        drift = self.pop_definition_drift(names)
        by_name = {e['name']: e for e in self._expected_pop_definitions()}
        plan: List[Dict[str, object]] = []
        for d in drift:
            if d['status'] == 'ok':
                continue
            if d['status'] == 'invalid_sql':
                plan.append({**d, 'action': 'blocked'})
                continue
            if d['kind'] == 'seed':
                action = 'create_seed_table+hidden_view' if not d['table_exists'] else 'replace_hidden_view'
            else:
                action = 'create_immv' if not d['table_exists'] else 'recreate_immv'
            plan.append({**d, 'action': action})

        if dry_run:
            for p in plan:
                if p['action'] == 'recreate_immv':
                    p['seed_upstreams'] = self.get_upstream_seed_tables(p['name'])
            return plan

        for p in plan:
            name, action = p['name'], p['action']
            if action == 'blocked':
                # An upstream created earlier in this loop (e.g. a new seed this
                # SQL now reads) may have made it compile: re-check once.
                (again,) = self.pop_definition_drift([name])
                if again['status'] in ('changed', 'missing'):
                    p.update(again)
                    if p['kind'] == 'seed':
                        action = 'create_seed_table+hidden_view' if not again['table_exists'] else 'replace_hidden_view'
                    else:
                        action = 'create_immv' if not again['table_exists'] else 'recreate_immv'
                    p['action'] = action
                else:
                    p['result'] = f"skipped: expected SQL does not compile ({again['error']})"
                    continue
            try:
                if p['kind'] == 'seed':
                    if action == 'create_seed_table+hidden_view':
                        self._create_one_seed_table(name)
                    self._db_tool.execute_query(
                        self._get_view_create_sql('pop', by_name[name]['sql_path'], target_schema='hidden'))
                    p['result'] = 'ok'
                else:
                    dependents = self._hidden_views_depending_on(name)
                    if action == 'recreate_immv':
                        self._db_tool.execute_query(f'DROP TABLE IF EXISTS pop.{name} CASCADE;')
                    self._db_tool.execute_query(
                        self._get_matview_create_sqls('pop', by_name[name]['sql_path']))
                    for dep in dependents:
                        self._db_tool.execute_query(
                            self._get_view_create_sql('pop', by_name[dep]['sql_path'], target_schema='hidden'))
                    p['recreated_hidden_views'] = dependents
                    p['result'] = 'ok'
            except Exception as exc:  # noqa: BLE001 - keep going, report per object
                msg = str(exc).strip().splitlines()[0]
                if p['kind'] == 'seed' and 'cannot' in msg and 'column' in msg:
                    msg += (' -- the seed columns changed; pop data would have to be dropped '
                            '(rule 18). Rebuild this seed explicitly.')
                p['result'] = f'error: {msg}'
        return plan

    @staticmethod
    def _period_seconds_to_schedule(period_seconds: int) -> str:
        """Translate a second-period into a pg_cron schedule string.

        pg_cron >= 1.5 accepts three schedule forms; this helper picks
        the most natural one for the given period:

          * 1..59 seconds  -> ``'N seconds'`` interval syntax (native
            sub-minute support introduced in pg_cron 1.5).
          * 60..3599 seconds, when the period divides 3600 evenly and
            maps cleanly to a minute count -> ``'*/M * * * *'``
            5-field cron.
          * >= 3600 seconds -> hour-aligned 5-field cron
            (``'0 */H * * *'``) when it evenly divides a day, or the
            daily midnight form for 86400s.

        Anything else raises ValueError instead of silently producing a
        schedule that drifts (e.g. period=90s has no clean 5-field form).
        """
        if not isinstance(period_seconds, int) or period_seconds <= 0:
            raise ValueError(
                f'period_seconds must be a positive int, got {period_seconds!r}'
            )
        # Sub-minute: pg_cron 1.5 native 'N seconds' syntax.
        if period_seconds < 60:
            return f'{period_seconds} seconds'
        # Convert to minutes for the >= 60s branch; must land on an
        # exact minute boundary or the 5-field cron would drift.
        if period_seconds % 60 != 0:
            raise ValueError(
                f'period_seconds={period_seconds} is >=60 but not a whole minute; '
                'use 1..59 for sub-minute or a multiple of 60.'
            )
        period_minutes = period_seconds // 60
        if period_minutes < 60:
            if 60 % period_minutes != 0:
                raise ValueError(
                    f'period_seconds={period_seconds} ({period_minutes}m) cannot be '
                    'expressed cleanly; use a divisor of 60 minutes (1,2,3,4,5,6,'
                    '10,12,15,20,30) or any 1..59 minute value.'
                )
            return f'*/{period_minutes} * * * *'
        if period_minutes == 1440:
            return '0 0 * * *'
        if period_minutes % 60 != 0:
            raise ValueError(
                f'period_seconds={period_seconds} ({period_minutes}m) must be <60m '
                'or a multiple of 60m.'
            )
        hours = period_minutes // 60
        if 24 % hours != 0:
            raise ValueError(
                f'period_seconds={period_seconds} ({hours}h) does not evenly divide '
                'a day; use 60, 120, 180, 240, 360, 480, 720, or 1440 minutes.'
            )
        return f'0 */{hours} * * *'

    @classmethod
    def _period_minutes_to_cron(cls, period_minutes: int) -> str:
        """Deprecated alias -- forwards to :meth:`_period_seconds_to_schedule`.

        Kept so any external callers or older tests that still pass
        ``period_minutes=`` continue to work during the transition.
        Delete after downstream callers migrate.
        """
        return cls._period_seconds_to_schedule(period_minutes * 60)
    
    def setup_schedules(
        self,
        period_seconds: Optional[int] = None,
        row_cnt: int = 1,
        config_path: str = 'throughput_config.json',
        profile: str = 'max_throughput',
        period_minutes: Optional[int] = None,
        table_reindex: bool = False,
        restart: bool = False,
    ):
        """Set up pg_cron jobs for all seed tables.

        Parameters
        ----------
        restart:
            When True, delete all existing cron jobs via
            :meth:`truncate_cron_jobs` before recreating the refreshed
            schedules. Defaults to False so existing jobs are kept unless
            a full reset is explicitly requested.

        Each job inserts new rows from ``hidden.<table>`` into
        ``pop.<table>`` every ``period_seconds`` seconds. If a
        ``throughput_config.json`` produced by
        :meth:`probe_all_throughput` exists, its per-table
        ``(period_seconds, row_cnt)`` are used instead of the fixed
        arguments -- the ``profile`` argument selects which optimisation
        target to apply (``max_throughput`` / ``min_period`` /
        ``max_batch``).

        Fallback order for the (period, row_cnt) of each seed table:

          1. ``throughput_config.json`` (produced by
             :meth:`probe_all_throughput`) -- per-table
             (period_seconds, row_cnt) under ``profile``.
          2. ``batch_size.json`` (legacy, produced by
             :meth:`probe_all`) -- per-table row_cnt only, combined
             with the caller-supplied ``period_seconds``.
          3. The caller-supplied defaults ``period_seconds`` and
             ``row_cnt``.

        The deprecated ``period_minutes`` kwarg is still accepted for
        one release to keep older call sites working; it is converted
        to ``period_seconds`` transparently.
        """
        # --- Back-compat: period_minutes -> period_seconds ---
        if period_minutes is not None and period_seconds is not None:
            raise ValueError(
                'Pass either period_seconds or period_minutes, not both.'
            )
        if period_minutes is not None:
            period_seconds = period_minutes * 60
        if period_seconds is None:
            period_seconds = 60  # default: once per minute

        if restart:
            self.truncate_cron_jobs()

        # --- Load throughput config produced by probe_all_throughput ---
        throughput_cfg: Dict[str, Dict[str, int]] = {}
        if os.path.exists(config_path):
            with open(config_path, 'r') as file:
                raw = json.load(file)
            # Expected shape:
            # {'<table>': {'max_throughput': {'period_seconds': N, 'row_cnt': M}, ...}}
            throughput_cfg = raw

        # --- Load legacy batch_size.json (row_cnt only) ---
        legacy_batch: Optional[Dict[str, int]] = None
        if os.path.exists('batch_size.json'):
            with open('batch_size.json', 'r') as file:
                legacy_batch = json.load(file)

        for table in self.seed_tables:
            resolved_period = period_seconds
            resolved_row_cnt = row_cnt

            if table in throughput_cfg and profile in throughput_cfg[table]:
                entry = throughput_cfg[table][profile]
                resolved_period = entry['period_seconds']
                resolved_row_cnt = entry['row_cnt']
                print(
                    f'[setup_schedules] {table}: using throughput_config '
                    f'profile={profile!r} -> period={resolved_period}s, '
                    f'row_cnt={resolved_row_cnt}'
                )
            elif legacy_batch is not None and table in legacy_batch:
                resolved_row_cnt = legacy_batch[table]
                print(
                    f'[setup_schedules] {table}: using batch_size.json '
                    f'-> period={resolved_period}s (caller default), '
                    f'row_cnt={resolved_row_cnt}'
                )
            else:
                print(
                    f'[setup_schedules] {table}: no config; using caller defaults '
                    f'period={resolved_period}s, row_cnt={resolved_row_cnt}'
                )

            self.schedule_seed_table_refresh(
                table=table,
                period_seconds=resolved_period,
                row_cnt=resolved_row_cnt,
                table_reindex=table_reindex,
            )

    def truncate_cron_jobs(self) -> None:
        """Stop every pg_cron job by unscheduling it and truncating cron.job.

        This is useful when you want to fully disable the seed refresh jobs
        without removing the cron extension itself.
        """
        job_rows = self._db_tool.fetch_all('SELECT jobid FROM cron.job;')
        for (jobid,) in job_rows:
            try:
                self._db_tool.execute_query('SELECT cron.unschedule(%s);', (jobid,))
                print(f'[truncate_cron_jobs] unscheduled cron job {jobid}')
            except Exception as exc:  # pragma: no cover - defensive logging branch
                print(f'[truncate_cron_jobs] failed to unschedule cron job {jobid}: {exc}')

        self._db_tool.execute_query('TRUNCATE TABLE cron.job;')
        print('[truncate_cron_jobs] truncated cron.job; all pg_cron jobs are stopped.')

    def schedule_seed_table_refresh(
        self,
        table: str,
        period_seconds: Optional[int] = None,
        row_cnt: int = 1,
        job_name: Optional[str] = None,
        period_minutes: Optional[int] = None,
        table_reindex: bool = False,
    ) -> str:
        """Schedule (or update) a pg_cron job that periodically inserts new
        rows from hidden.<table> into pop.<table>.

        The SQL executed on each tick is identical in shape to one
        iteration of _insert_few_rows_to_seed_tables (minus the
        cross-table sleep), so the data is materialised the same way.

        Parameters
        ----------
        table:
            Seed table name (must be present in self.seed_tables).
        period_seconds:
            Refresh cadence in seconds. See
            :meth:`_period_seconds_to_schedule` for allowed values.
            Sub-minute periods (1..59s) use pg_cron 1.5's native
            'N seconds' interval syntax.
        row_cnt:
            How many new rows to insert per tick. Maps to SQL LIMIT.
        job_name:
            Optional pg_cron job name. Defaults to
            'seed_refresh_<table>' so subsequent calls with the same
            table act as an update (alter_job) instead of creating a
            duplicate schedule.
        period_minutes:
            Deprecated alias -- if provided, is converted to
            period_seconds. Passing both raises ValueError.

        Returns the resolved job_name.

        Requires pg_cron to be installed and bound to the current DB
        via cron.database_name (see db/enable_pg_cron.sh).
        """
        if period_minutes is not None and period_seconds is not None:
            raise ValueError(
                'Pass either period_seconds or period_minutes, not both.'
            )
        if period_minutes is not None:
            period_seconds = period_minutes * 60
        if period_seconds is None:
            raise ValueError(
                'period_seconds is required (or use deprecated period_minutes).'
            )

        if table not in self.seed_tables:
            raise ValueError(
                f'{table!r} is not a known seed table. '
                f'Known: {sorted(self.seed_tables)}'
            )
        if not isinstance(row_cnt, int) or row_cnt <= 0:
            raise ValueError(f'row_cnt must be a positive int, got {row_cnt!r}')

        schedule = self._period_seconds_to_schedule(period_seconds)
        if job_name is None:
            job_name = f'seed_refresh_{table}'

        # Use the exact same INSERT shape as _insert_few_rows_to_seed_tables
        # via the shared _build_seed_insert_sql helper (keeps the null-row
        # filter in one place). Baked into cron.job at schedule time.
        commands = self._build_seed_insert_sql(
            table,
            row_cnt=row_cnt,
            table_reindex=table_reindex,
        )
        for i, sub_command in enumerate(commands):
            # "Upsert" the job: alter if it exists, otherwise schedule.
            subjob_name = job_name + str(i)
            existing = self._db_tool.fetch_all(
                'SELECT jobid FROM cron.job WHERE jobname = %s',
                (subjob_name, ),
            )
            if existing:
                self._db_tool.execute_query('SELECT cron.unschedule(%s);', (subjob_name,))
            
            print(
                f'Creating pg_cron job {subjob_name!r}: '
                f'schedule={schedule!r}, command={sub_command!r}'
            )
            self._db_tool.execute_query(
                'SELECT cron.schedule(%s, %s, %s);',
                (subjob_name, schedule, sub_command),
            )

        return job_name

    def _insert_few_rows_to_seed_tables(
        self,
        row_cnt: int = 1,
        sleep_time: int = 0,
        table_reindex: bool = False,
    ):
        """
        從 poc views 取一筆資料，插入到 seed tables
        """
        print('seed tables:', self.seed_tables)
        for sql_path in self.ordered_sql_paths:
            table = sql_path.split('.')[0]
            if table in self.seed_tables:
                time.sleep(sleep_time)
                statements = self._build_seed_insert_sql(
                    table,
                    row_cnt=row_cnt,
                    table_reindex=table_reindex,
                )
                insert_sql = statements[-1]
                print('Inserting one row into seed table:', table)
                print('Executing SQL:\n', insert_sql)
                try:
                    if len(statements) > 1:
                        self._db_tool.execute_query(statements[0])
                    self._db_tool.execute_query(insert_sql)
                except Exception as e:
                    print(f"[_insert_few_rows_to_seed_tables] Failed to insert into seed table {table}: {e}")
                    raise e

    # ------------------------------------------------------------------
    # Capacity probing: find the largest safe LIMIT for seed refresh tick
    # ------------------------------------------------------------------

    def _run_seed_insert_trial(
        self,
        table: str,
        limit_count: int,
        per_insert_timeout_sec: int = 50,
        sleep_between_sec: int = 5,
        trials: int = 3,
        table_reindex: bool = False,
    ) -> bool:
        """Run one capacity trial at a given ``limit_count``.

        A single trial:

        1. ``TRUNCATE pop.<table>`` so every trial starts from empty and
           has to actually copy ``limit_count`` fresh rows -- otherwise
           later trials would benefit from the anti-join short-circuit
           and skew the result.
        2. Run ``_build_seed_insert_sql(table, row_cnt=limit_count)``
           ``trials`` times (default 3), sleeping ``sleep_between_sec``
           between runs. Each insert is wrapped in
           ``SET LOCAL statement_timeout`` so any single run exceeding
           ``per_insert_timeout_sec`` is treated as a failure.
        3. After each insert, ``SELECT count(*) FROM pop.<table>`` must
           equal ``i * limit_count`` (accumulated because we do not
           truncate between the ``trials`` runs -- only at trial start).

        Returns True only if every one of the ``trials`` runs completes
        under the timeout AND actually inserts ``limit_count`` new rows.
        """
        print(
            f'[probe {limit_count}] trial limit={limit_count} table={table} '
            f'(runs={trials}, per_insert_timeout={per_insert_timeout_sec}s, '
            f'sleep={sleep_between_sec}s)'
        )
        # Fair-start: empty the seed so every run inserts fresh rows.
        # self._db_tool.execute_query(f'TRUNCATE TABLE pop.{table};')

        statements = self._build_seed_insert_sql(
            table,
            row_cnt=limit_count,
            table_reindex=table_reindex,
        )
        reindex_sql = statements[0] if len(statements) > 1 else None
        insert_sql = statements[-1]
        print(f'[probe {limit_count}]   insert SQL:\n{insert_sql}')
        start_cnt = self._db_tool.fetch_all(
                f'SELECT count(*) FROM pop.{table};'
            )[0][0]
        for i in range(1, trials + 1):
            if i > 1:
                time.sleep(sleep_between_sec)
            if reindex_sql is not None:
                reindex_wrapped = (
                    f"SET statement_timeout = '{per_insert_timeout_sec}s'; "
                    f'{reindex_sql} '
                    f'RESET statement_timeout;'
                )
            else:
                reindex_wrapped = None
            insert_wrapped = (
                f"SET statement_timeout = '{per_insert_timeout_sec}s'; "
                f'{insert_sql} '
                f'RESET statement_timeout;'
            )
            t0 = time.monotonic()
            try:
                if reindex_wrapped is not None:
                    self._db_tool.execute_query(reindex_wrapped)
                self._db_tool.execute_query(insert_wrapped)
            except OperationalError as e:
                elapsed = time.monotonic() - t0
                print(
                    f'[probe {limit_count}]   run {i}/{trials} FAILED after {elapsed:.1f}s '
                    f'(OperationalError): {e}'
                )
                time.sleep(SLEEP_FOR_DB_RECOVER)
                return False
            except BaseException as e:
                elapsed = time.monotonic() - t0
                print(
                    f'[probe {limit_count}]   run {i}/{trials} FAILED after {elapsed:.1f}s '
                    f'(likely statement_timeout {per_insert_timeout_sec}s hit or '
                    f'other error): {e}'
                )
                return False
            elapsed = time.monotonic() - t0

            actual = self._db_tool.fetch_all(
                f'SELECT count(*) FROM pop.{table};'
            )[0][0]
            expected = i * limit_count
            increase = actual - start_cnt
            if increase < expected:
                print(
                    f'[probe {limit_count}]   run {i}/{trials} FAILED: expected {expected} '
                    f'new rows, but only {increase} were inserted (total rows={actual})'
                )
                return False
            print(
                f'[probe {limit_count}]   run {i}/{trials} ok in {elapsed:.1f}s '
                f'(total rows={actual})'
            )
        return True

    def probe_seed_insert_limit(
        self,
        table: str,
        per_insert_timeout_sec: int = 50,
        sleep_between_sec: int = 5,
        trials: int = 3,
        max_limit: Optional[int] = None,
        table_reindex: bool = False,
    ) -> int:
        """Find the largest ``row_cnt`` at which the seed-refresh insert
        for ``pop.<table>`` is *stably* fast enough.

        "Stable" means all ``trials`` (default 3) consecutive inserts,
        each starting from a truncated seed and separated by
        ``sleep_between_sec`` seconds, complete within
        ``per_insert_timeout_sec`` seconds AND insert exactly
        ``limit_count`` fresh rows.

        Strategy
        --------
        1. **Exponential probe** for the upper bound: start at
           ``low=1``, double until a trial fails. If ``max_limit`` is
           given, the probe stops there and returns immediately if it
           still succeeds (caller-supplied ceiling).
        2. **Binary search** in ``[last_success, first_failure]`` to
           pinpoint the largest safe limit.

        Returns the largest limit_count that passed all ``trials``
        consecutive runs. Returns ``0`` if even ``limit_count=1`` fails
        (i.e. the underlying INSERT is not viable at all right now).

        The probe uses :meth:`_build_seed_insert_sql`, so it exercises
        the exact SQL that ``schedule_seed_table_refresh`` bakes into
        ``cron.job.command`` -- including ``REINDEX`` and the
        ``IS NOT NULL`` filter. The returned number is therefore a
        realistic upper bound for ``row_cnt`` in
        :meth:`schedule_seed_table_refresh`.

        Notes
        -----
        * The seed table is left truncated when the probe returns. Run
          ``_insert_few_rows_to_seed_tables`` or the pg_cron job to
          re-populate it if needed.
        * The probe only writes to ``pop.<table>``; other pop tables
          are untouched.
        * Result is not persisted -- caller decides what to do with it.
        """
        if table not in self.seed_tables:
            raise ValueError(
                f'{table!r} is not a seed table. Known seed tables: '
                f'{self.seed_tables}'
            )

        print(
            f'[probe] === probing safe insert limit for pop.{table} ===\n'
            f'[probe] per_insert_timeout={per_insert_timeout_sec}s '
            f'sleep_between={sleep_between_sec}s trials={trials} '
            f'max_limit={max_limit}'
        )

        # --- 1. Exponential probe to find an upper bound that fails ---
        low_success = 0            # largest known passing limit
        high_failure: Optional[int] = None  # smallest known failing limit
        candidate = 1
        while True:
            if max_limit is not None and candidate > max_limit:
                candidate = max_limit
            ok = self._run_seed_insert_trial(
                table,
                limit_count=candidate,
                per_insert_timeout_sec=per_insert_timeout_sec,
                sleep_between_sec=sleep_between_sec,
                trials=trials,
                table_reindex=table_reindex,
            )
            if ok:
                low_success = candidate
                print(f'[probe] exp-up: {candidate} PASS')
                if max_limit is not None and candidate >= max_limit:
                    print(
                        f'[probe] hit caller max_limit={max_limit}; '
                        'returning without narrowing further'
                    )
                    return low_success
                candidate *= 2
            else:
                high_failure = candidate
                print(f'[probe] exp-up: {candidate} FAIL -> upper bound found')
                break

        if low_success == 0:
            print('[probe] even limit=1 failed; returning 0')
            return 0

        # --- 2. Binary search in (low_success, high_failure) ---
        print(
            f'[probe] entering binary search in ({low_success}, {high_failure})'
        )
        while high_failure - low_success > 1:
            mid = (low_success + high_failure) // 2
            ok = self._run_seed_insert_trial(
                table,
                limit_count=mid,
                per_insert_timeout_sec=per_insert_timeout_sec,
                sleep_between_sec=sleep_between_sec,
                trials=trials,
                table_reindex=table_reindex,
            )
            if ok:
                low_success = mid
                print(f'[probe] bsearch: {mid} PASS -> low_success={mid}')
            else:
                high_failure = mid
                print(f'[probe] bsearch: {mid} FAIL -> high_failure={mid}')

        print(
            f'[probe] === done: safe limit for pop.{table} = {low_success} ==='
        )
        return low_success

    def get_upstream_seed_tables(self, table: str) -> List[str]:
        """Return a list of upstream seed tables for the given table."""
        upstream_seed_tables = []
        upstream_sql_paths = list(self.dag.predecessors(table + '.sql'))
        for upstream_sql_path in upstream_sql_paths:
            upstream_table = upstream_sql_path.split('.')[0]
            if upstream_table.endswith('_list'):
                upstream_seed_tables.append(upstream_table)
            else:
                upstream_seed_tables.extend(self.get_upstream_seed_tables(upstream_table))
        assert all(t.endswith('_list') for t in upstream_seed_tables), 'All upstream seed tables should end with "_list"'
        return upstream_seed_tables
    
    def probe_all(
        self,
        per_insert_timeout_sec: int = 50,
        sleep_between_sec: int = 5,
        trials: int = 3,
        max_limit: Optional[int] = None,
        table_reindex: bool = False,
    ) -> Dict[str, int]:
        """Probe all seed tables and return a dict of {table: safe_limit}."""
        results = {}
        if os.path.exists("batch_size.json"):
            with open("batch_size.json", "r") as file:
                results = json.load(file)
        seed_tables = list(self.seed_tables)
        i = 0
        while i < len(seed_tables):
            table = seed_tables[i]
            if table in results:
                print(f'[probe_all] skipping seed table no.{i+1}: {table} (already probed)')
                i += 1
                continue
            safe_limit = 0
            attempts = 0
            while safe_limit == 0:
                try:
                    safe_limit = self.probe_seed_insert_limit(
                        table=table,
                        per_insert_timeout_sec=per_insert_timeout_sec,
                        sleep_between_sec=sleep_between_sec,
                        trials=trials,
                        max_limit=max_limit,
                        table_reindex=table_reindex,
                    )
                except OperationalError as e:
                    print(f'[probe_all] ERROR: {table} failed to probe due to OperationalError: {e}. Retrying...')
                    time.sleep(sleep_between_sec * 10)  # wait longer before retrying
                    safe_limit = 0
                results[table] = safe_limit
                time.sleep(sleep_between_sec)  # avoid hammering the DB too quickly
                attempts += 1
                if attempts >= 1 and safe_limit == 0:
                    print(f'[probe_all] WARNING: {table} failed to find a safe limit after 3 attempts; moving on.')
                    for upstream_table in self.get_upstream_seed_tables(table):
                        self._run_seed_insert_trial(
                            upstream_table,
                            results[upstream_table],
                            per_insert_timeout_sec=per_insert_timeout_sec,
                            sleep_between_sec=sleep_between_sec,
                            trials=3,
                            table_reindex=table_reindex,
                        )
                    continue
                elif attempts >= 3 and safe_limit == 0:
                    print(f'[probe_all] ERROR: {table} failed to find a safe limit after 3 attempts; skipping.')
                    break
            i += 1
            with open("batch_size.json", "w") as file:
                json.dump(results, file, indent=4)
    # ------------------------------------------------------------------
    # Throughput probing: sweep (period_seconds x row_cnt) grid to find
    # optimal (row_cnt, period_seconds) combinations per seed table.
    # ------------------------------------------------------------------

    # pg_cron 1.5 accepts 'N seconds' schedules for 1..59; anything >=60
    # must be a whole-minute divisor of 60m (or an hour multiple that
    # divides 24h). This grid is the union of "sub-minute native" and
    # "clean minute buckets"; see _period_seconds_to_schedule for the
    # exact validation logic.
    THROUGHPUT_PERIOD_GRID_SECONDS: List[int] = [
        1, 2, 3, 5, 10, 15, 20, 30, 60, 120
    ]

    def probe_seed_insert_throughput(
        self,
        table: str,
        period_grid: Optional[List[int]] = None,
        trials: int = 3,
        sleep_between_sec: int = 1,
        max_row_cnt: Optional[int] = None,
        table_reindex: bool = False,
    ) -> Dict[str, Dict[str, int]]:
        """Sweep the (period_seconds x row_cnt) grid to find the maximum
        stable insert rate for ``pop.<table>``.

        For each candidate ``period_seconds`` in ``period_grid`` this
        method uses :meth:`_run_seed_insert_trial` to binary-search the
        largest ``row_cnt`` whose ``trials`` consecutive inserts each
        finish under a ``statement_timeout`` equal to ``period_seconds``
        minus a 1-second safety margin (or the full period when it is
        <=1s). That timeout is the natural throttle -- any run slower
        than the requested cadence is treated as a failure, so the probe
        never blocks longer than the cadence itself.

        The full sweep records ``(period_seconds, row_cnt)`` and the
        derived ``rows_per_second`` for every period where at least
        row_cnt=1 is stable. From that record three profiles are
        derived so ``setup_schedules`` can choose a policy without
        rerunning the probe:

          * ``max_throughput`` -- highest rows_per_second overall.
          * ``min_period``     -- smallest period_seconds that is
            stable at row_cnt>=1.
          * ``max_batch``      -- largest row_cnt (regardless of
            period), useful for off-peak bulk loads.

        Returns a dict of the shape::

            {
              'max_throughput': {'period_seconds': N, 'row_cnt': M,
                                 'rows_per_second': float},
              'min_period':     {'period_seconds': N, 'row_cnt': M,
                                 'rows_per_second': float},
              'max_batch':      {'period_seconds': N, 'row_cnt': M,
                                 'rows_per_second': float},
              'sweep': [{'period_seconds': N, 'row_cnt': M,
                         'rows_per_second': float}, ...],
            }

        Every period whose SQL schedule string is rejected by
        :meth:`_period_seconds_to_schedule` is skipped (so callers can
        pass any grid without pre-filtering).

        Notes
        -----
        * ``max_row_cnt`` caps the binary search on very cheap seeds
          (e.g. small dimension lists) so the probe does not chase
          runaway 2^N growth on a period where LIMIT 1e6 might still
          "pass" in the timeout budget.
        * The seed table is repeatedly appended to during the sweep;
          call :meth:`_insert_few_rows_to_seed_tables` afterward if
          you need a clean state before scheduling cron jobs.
        """
        if table not in self.seed_tables:
            raise ValueError(
                f'{table!r} is not a seed table. Known: {self.seed_tables}'
            )
        if period_grid is None:
            period_grid = list(self.THROUGHPUT_PERIOD_GRID_SECONDS)

        sweep: List[Dict[str, float]] = []

        for period_seconds in period_grid:
            # Skip periods that pg_cron cannot schedule cleanly.
            try:
                schedule_str = self._period_seconds_to_schedule(period_seconds)
            except ValueError as e:
                print(
                    f'[throughput] {table} period={period_seconds}s: SKIP '
                    f'({e})'
                )
                continue

            # Timeout budget: at sub-minute periods we lop 1 second off
            # so pg_cron never sees a run overlap; at 1s we cannot
            # subtract anything, so we accept a "borderline" bound and
            # rely on trials>1 to catch consistent overruns.
            timeout_sec = period_seconds - 1 if period_seconds > 1 else 1

            print(
                f'\n[throughput] === {table} period={period_seconds}s '
                f'(schedule={schedule_str!r}, timeout={timeout_sec}s) ==='
            )

            # Binary-probe row_cnt at this period.
            # Reuses _run_seed_insert_trial's contract: ``trials``
            # consecutive successful runs within the timeout.
            best_row_cnt = 0
            # Exponential upper bound.
            candidate = 1
            upper_fail: Optional[int] = None
            while True:
                if max_row_cnt is not None and candidate > max_row_cnt:
                    candidate = max_row_cnt
                ok = self._run_seed_insert_trial(
                    table,
                    limit_count=candidate,
                    per_insert_timeout_sec=timeout_sec,
                    sleep_between_sec=sleep_between_sec,
                    trials=trials,
                    table_reindex=table_reindex,
                )
                if ok:
                    best_row_cnt = candidate
                    print(
                        f'[throughput]   {table} p={period_seconds}s '
                        f'row_cnt={candidate} PASS'
                    )
                    if max_row_cnt is not None and candidate >= max_row_cnt:
                        break
                    candidate *= 2
                else:
                    upper_fail = candidate
                    print(
                        f'[throughput]   {table} p={period_seconds}s '
                        f'row_cnt={candidate} FAIL'
                    )
                    break

            # If even row_cnt=1 failed, this period is not usable.
            if best_row_cnt == 0:
                print(
                    f'[throughput] {table} p={period_seconds}s not viable '
                    '(row_cnt=1 fails within timeout budget); skipping'
                )
                continue

            # Binary refine between best_row_cnt and upper_fail.
            if upper_fail is not None:
                low, high = best_row_cnt, upper_fail
                while high - low > 1:
                    mid = (low + high) // 2
                    ok = self._run_seed_insert_trial(
                        table,
                        limit_count=mid,
                        per_insert_timeout_sec=timeout_sec,
                        sleep_between_sec=sleep_between_sec,
                        trials=trials,
                        table_reindex=table_reindex,
                    )
                    if ok:
                        low = mid
                        print(
                            f'[throughput]   {table} p={period_seconds}s '
                            f'bsearch row_cnt={mid} PASS'
                        )
                    else:
                        high = mid
                        print(
                            f'[throughput]   {table} p={period_seconds}s '
                            f'bsearch row_cnt={mid} FAIL'
                        )
                best_row_cnt = low

            rows_per_second = best_row_cnt / float(period_seconds)
            sweep.append({
                'period_seconds': period_seconds,
                'row_cnt': best_row_cnt,
                'rows_per_second': rows_per_second,
            })
            print(
                f'[throughput] {table} p={period_seconds}s -> row_cnt='
                f'{best_row_cnt}, rows_per_second={rows_per_second:.2f}'
            )

        if not sweep:
            print(
                f'[throughput] {table}: NO viable (period, row_cnt) '
                'combination found across the grid'
            )
            return {'sweep': []}

        # Derive the three profiles from the sweep record.
        max_throughput = max(sweep, key=lambda r: r['rows_per_second'])
        # min_period: smallest period_seconds that is viable at all.
        min_period = min(sweep, key=lambda r: r['period_seconds'])
        max_batch = max(sweep, key=lambda r: r['row_cnt'])

        result = {
            'max_throughput': max_throughput,
            'min_period': min_period,
            'max_batch': max_batch,
            'sweep': sweep,
        }
        print(
            f'\n[throughput] === {table} summary ===\n'
            f'  max_throughput: {max_throughput}\n'
            f'  min_period:     {min_period}\n'
            f'  max_batch:      {max_batch}'
        )
        return result

    def probe_all_throughput(
        self,
        period_grid: Optional[List[int]] = None,
        trials: int = 3,
        sleep_between_sec: int = 1,
        max_row_cnt: Optional[int] = None,
        config_path: str = 'throughput_config.json',
        table_reindex: bool = False,
        restart: bool = False,
    ) -> Dict[str, Dict[str, Dict[str, int]]]:
        """Probe every seed table and persist per-table
        (period_seconds, row_cnt) profiles to ``config_path``.

        The output file has the shape consumed by
        :meth:`setup_schedules` when ``profile='max_throughput'`` (or
        another of the three profiles) is requested:

            {
              '<seed_table>': {
                'max_throughput': {'period_seconds': N, 'row_cnt': M,
                                   'rows_per_second': float},
                'min_period':     {...},
                'max_batch':      {...},
                'sweep':          [ ... ],
              },
              ...
            }

        Existing entries in the config file are preserved so this
        method is safe to resume (progress is written to disk after
        every table completes).
        """
        results: Dict[str, Dict[str, Dict[str, int]]] = {}
        if restart and os.path.exists(config_path):
            os.remove(config_path)
            print(f'[probe_all_throughput] restart=True: removed existing config at {config_path!r}')
        if os.path.exists(config_path):
            with open(config_path, 'r') as file:
                results = json.load(file)

        for table in self.seed_tables:
            if table in results and 'max_throughput' in results[table]:
                print(f'[probe_all_throughput] skipping {table} (already probed)')
                continue
            try:
                results[table] = self.probe_seed_insert_throughput(
                    table=table,
                    period_grid=period_grid,
                    trials=trials,
                    sleep_between_sec=sleep_between_sec,
                    max_row_cnt=max_row_cnt,
                    table_reindex=table_reindex,
                )
            except OperationalError as e:
                print(
                    f'[probe_all_throughput] {table} OperationalError: {e}; '
                    f'sleeping {SLEEP_FOR_DB_RECOVER}s and skipping'
                )
                time.sleep(SLEEP_FOR_DB_RECOVER)
                continue
            # Persist after every completed table so long sweeps are
            # resumable without recomputing finished seeds.
            with open(config_path, 'w') as file:
                json.dump(results, file, indent=4)
            print(f'[probe_all_throughput] persisted {config_path} '
                  f'after finishing {table}')

        return results

    def prune_and_report_seed_cron_jobs(self, lookback_minutes: int = 60) -> Dict[str, Dict[str, object]]:
        """Scan pg_cron run history for every ``pop.<seed_table>`` refresh
        job and take action based on the last ``lookback_minutes`` window.

        The per-seed query aggregates ``insert_size`` (parsed out of
        ``return_message`` for successful ``INSERT ...`` runs) into a
        single SUM, restricted to ``status='succeeded'``. Three cases:

        * ``SUM > 0``  -- job is actively producing inserts; kept, and the
          last N-minute insert count plus a linear 24h projection
          (``sum * (24 * 60 / lookback_minutes)``) is logged.
        * ``SUM = 0``  -- every succeeded run in the window inserted zero
          rows; the corresponding ``cron.job`` entry is unscheduled and
          a removal line is logged.
        * ``SUM IS NULL`` -- no succeeded rows (or no rows at all) in the
          window. A warning is emitted; **no** action is taken.

        Parameters
        ----------
        lookback_minutes:
            Time window (minutes) applied to ``cron.job_run_details`` via
            ``start_time > NOW() - make_interval(mins => <lookback_minutes>)``.
            Defaults to 60 to match the original probe SQL.

        Returns
        -------
        Dict keyed by seed table name with per-seed stats::

            {
                '<seed_table>': {
                    'insert_sum': int | None,   # None when SUM is NULL
                    'projected_24h': int | None,
                    'removed': bool,
                    'action': 'kept' | 'removed' | 'no_data' | 'no_job'
                              | 'fetch_error' | 'unschedule_error',
                },
                ...
            }
        """
        # Aggregate query: single row, single column SUM(insert_size).
        # Parametrised on the command LIKE pattern and the lookback window.
        # (`%%` is required because psycopg treats a single `%` as a
        # parameter placeholder marker.)
        query = """
            SELECT SUM(insert_size)
            FROM (
                SELECT
                    CASE WHEN return_message LIKE 'INSERT%%'
                         THEN split_part(return_message, ' ', -1)::integer
                         ELSE NULL::integer END AS insert_size,
                    status,
                    EXTRACT(SECOND FROM end_time - start_time) AS run_seconds
                FROM cron.job_run_details
                WHERE command LIKE %s
                  AND start_time > NOW() - make_interval(mins => %s)
                ORDER BY start_time DESC
            ) t
            WHERE status = 'succeeded'
        """

        stats: Dict[str, Dict[str, object]] = {}
        projection_factor = (24 * 60) / float(lookback_minutes)

        for table in self.seed_tables:
            like_pattern = f'%pop.{table}%'
            try:
                rows = self._db_tool.fetch_all(query, (like_pattern, lookback_minutes))
            except Exception as e:
                print(
                    f'[prune_and_report_seed_cron_jobs] {table!r} '
                    f'fetch failed: {e}; skipping'
                )
                stats[table] = {
                    'insert_sum': None,
                    'projected_24h': None,
                    'removed': False,
                    'action': 'fetch_error',
                }
                continue

            # SUM aggregate always returns exactly one row.
            insert_sum = rows[0][0] if rows else None

            if insert_sum is None:
                print(
                    f'[prune_and_report_seed_cron_jobs] WARNING {table!r}: '
                    f'no succeeded runs with parseable insert_size in last '
                    f'{lookback_minutes}min (SUM is NULL); no action taken'
                )
                stats[table] = {
                    'insert_sum': None,
                    'projected_24h': None,
                    'removed': False,
                    'action': 'no_data',
                }
                continue

            insert_sum_int = int(insert_sum)
            projected_24h = int(round(insert_sum_int * projection_factor))

            if insert_sum_int == 0:
                job_name = f'seed_refresh_{table}'
                try:
                    existing = self._db_tool.fetch_all(
                        'SELECT jobid FROM cron.job WHERE jobname = %s',
                        (job_name,),
                    )
                    if existing:
                        for i in [0, 1]:
                            self._db_tool.execute_query(
                                'SELECT cron.unschedule(%s);', (job_name + str(i),)
                            )
                            print(
                                f'[prune_and_report_seed_cron_jobs] REMOVED cron '
                                f'job {job_name!r} {i} for seed table pop.{table} '
                                f'(SUM(insert_size)=0 across succeeded runs in '
                                f'last {lookback_minutes}min)'
                            )
                        stats[table] = {
                            'insert_sum': 0,
                            'projected_24h': 0,
                            'removed': True,
                            'action': 'removed',
                        }
                    else:
                        print(
                            f'[prune_and_report_seed_cron_jobs] {table!r}: '
                            f'SUM(insert_size)=0 but cron.job entry '
                            f'{job_name!r} not found (already unscheduled?)'
                        )
                        stats[table] = {
                            'insert_sum': 0,
                            'projected_24h': 0,
                            'removed': False,
                            'action': 'no_job',
                        }
                except Exception as e:
                    print(
                        f'[prune_and_report_seed_cron_jobs] failed to '
                        f'unschedule {job_name!r}: {e}'
                    )
                    stats[table] = {
                        'insert_sum': 0,
                        'projected_24h': 0,
                        'removed': False,
                        'action': 'unschedule_error',
                    }
            else:
                print(
                    f'[prune_and_report_seed_cron_jobs] {table!r}: kept '
                    f'(SUM(insert_size)={insert_sum_int}); '
                    f'last {lookback_minutes}min INSERT sum = {insert_sum_int}, '
                    f'projected 24h insert count ~ {projected_24h}'
                )
                stats[table] = {
                    'insert_sum': insert_sum_int,
                    'projected_24h': projected_24h,
                    'removed': False,
                    'action': 'kept',
                }

        return stats
