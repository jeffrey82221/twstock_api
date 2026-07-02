
import os
from jinja2 import Template
from sql_metadata import Parser
from typing import List, Dict, Optional
from paradag import DAG, dag_run, SequentialProcessor
from pg_tool import PostgreSQLTool
import time
class ViewExecutor:
    def __init__(self, results: List[str]):
        self.results = results

    def param(self, vertex):
        # You can map vertex to any param object; here we just echo its name
        return vertex
    
    def execute(self, param):
        self.results.append(param)

class Pipeline:
    def __init__(self):
        self._sql_paths = list(filter(lambda x: x.endswith('.sql'), 
                            os.listdir(os.path.join(os.path.dirname(__file__), 'db', 'poc'))   ))
        
        self.poc_tables = ['poc.' + s.split('.sql')[0] for s in self._sql_paths]
        self.dag = self._create_dag()
        self._db_tool = PostgreSQLTool()
        self._db_tool.setup()  # Ensure the database is set up with necessary extensions and schemas
    
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
        sqls = list(self.dag.all_starts())
        sqls.extend([p for p in self._sql_paths if p.endswith('_list.sql')])
        results = [p.split('.sql')[0] for p in sqls]
        return list(set(results))

    def create_seed_tables(self):
        for table in self.seed_tables:
            print('Creating seed table for:', table)
            create_sql = f'CREATE TABLE IF NOT EXISTS pop.{table} AS SELECT * FROM poc.{table} LIMIT 0;'
            print('Executing SQL:\n', create_sql)
            self._db_tool.execute_query(create_sql)
            self._set_seed_table_primary_key('pop', table)

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

        columns = [
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

    def create_mat_views(self, recreate: bool = False):
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
        self._insert_few_rows_to_seed_tables(row_cnt=1)

    @staticmethod
    def _period_minutes_to_cron(period_minutes: int) -> str:
        """Translate a minute-period into a 5-field cron expression.

        Supported:
          - 1..59 minutes   -> '*/N * * * *'
          - >=60 minutes, evenly dividing 60*24 (hour-aligned periods
            of 60, 120, 180, ..., 720, 1440) -> '0 */H * * *' or daily
          - Exactly 1440    -> '0 0 * * *' (daily at midnight)

        Anything else raises ValueError instead of silently producing a
        cron that drifts (e.g. period=90 has no clean 5-field form).
        """
        if not isinstance(period_minutes, int) or period_minutes <= 0:
            raise ValueError(
                f'period_minutes must be a positive int, got {period_minutes!r}'
            )
        if period_minutes < 60:
            if 60 % period_minutes != 0 and period_minutes not in range(1, 60):
                raise ValueError(
                    f'period_minutes={period_minutes} cannot be expressed cleanly; '
                    'use a divisor of 60 (1,2,3,4,5,6,10,12,15,20,30) or any 1..59 '
                    'value via */N semantics.'
                )
            return f'*/{period_minutes} * * * *'
        if period_minutes == 1440:
            return '0 0 * * *'
        if period_minutes % 60 != 0:
            raise ValueError(
                f'period_minutes={period_minutes} must be <60 or a multiple of 60'
            )
        hours = period_minutes // 60
        if 24 % hours != 0:
            raise ValueError(
                f'period_minutes={period_minutes} ({hours}h) does not evenly divide a day; '
                'use 60, 120, 180, 240, 360, 480, 720, or 1440.'
            )
        return f'0 */{hours} * * *'

    def schedule_seed_table_refresh(
        self,
        table: str,
        period_minutes: int,
        row_cnt: int = 1,
        job_name: Optional[str] = None,
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
        period_minutes:
            Refresh cadence in minutes. See _period_minutes_to_cron for
            allowed values.
        row_cnt:
            How many new rows to insert per tick. Maps to SQL LIMIT.
        job_name:
            Optional pg_cron job name. Defaults to
            'seed_refresh_<table>' so subsequent calls with the same
            table act as an update (alter_job) instead of creating a
            duplicate schedule.

        Returns the resolved job_name.

        Requires pg_cron to be installed and bound to the current DB
        via cron.database_name (see db/enable_pg_cron.sh).
        """
        if table not in self.seed_tables:
            raise ValueError(
                f'{table!r} is not a known seed table. '
                f'Known: {sorted(self.seed_tables)}'
            )
        if not isinstance(row_cnt, int) or row_cnt <= 0:
            raise ValueError(f'row_cnt must be a positive int, got {row_cnt!r}')

        schedule = self._period_minutes_to_cron(period_minutes)
        if job_name is None:
            job_name = f'seed_refresh_{table}'

        # Use the exact same INSERT shape as _insert_few_rows_to_seed_tables.
        # Single-line SQL keeps it readable in cron.job.
        command = (
            f'INSERT INTO pop.{table} '
            f'SELECT * FROM hidden.{table} '
            f'EXCEPT SELECT * FROM pop.{table} '
            f'LIMIT {row_cnt};'
        )

        # "Upsert" the job: alter if it exists, otherwise schedule.
        existing = self._db_tool.fetch_all(
            'SELECT jobid FROM cron.job WHERE jobname = %s',
            (job_name,),
        )
        if existing:
            self._db_tool.execute_query('SELECT cron.unschedule(%s);', (job_name,))
        
        print(
            f'Creating pg_cron job {job_name!r}: '
            f'schedule={schedule!r}, command={command!r}'
        )
        self._db_tool.execute_query(
            'SELECT cron.schedule(%s, %s, %s);',
            (job_name, schedule, command),
        )

        return job_name

    def _insert_few_rows_to_seed_tables(self, row_cnt: int = 1, sleep_time: int = 0):
        """
        從 poc views 取一筆資料，插入到 seed tables
        """
        print('seed tables:', self.seed_tables)
        for sql_path in self.ordered_sql_paths:
            table = sql_path.split('.')[0]
            if table in self.seed_tables:
                time.sleep(sleep_time)
                insert_sql = f"""
                INSERT INTO pop.{table}
                SELECT * FROM hidden.{table} 
                EXCEPT SELECT * FROM pop.{table}
                LIMIT {row_cnt};
                """
                print('Inserting one row into seed table:', table)
                print('Executing SQL:\n', insert_sql)
                try:
                    self._db_tool.execute_query(insert_sql)
                except Exception as e:
                    print(f"[_insert_few_rows_to_seed_tables] Failed to insert into seed table {table}: {e}")
                    raise e