
import os
from jinja2 import Template
from sql_metadata import Parser
from typing import List, Dict, Optional
from paradag import DAG, dag_run, SequentialProcessor
from pg_tool import PostgreSQLTool

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
    
    @property
    def seed_tables(self) -> List[str]:
        """
        Returns a list of seed tables that are 
        aim to be populated step-by-step. 
        """
        sqls = list(self.dag.all_starts())
        sqls.extend([p for p in self._sql_paths if p.endswith('_list.sql')])
        results = [p.split('.sql')[0] for p in sqls]
        return results

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

    def create_mat_views(self):
        """
        從 seed tables 往後接 materialized view 
        (建立 materialized view ，如果已經存在就建立到 hidden schema)

        作法：
        """
        self._db_tool.execute_query('DROP SCHEMA IF EXISTS pop CASCADE;')
        self._db_tool.execute_query('CREATE SCHEMA IF NOT EXISTS pop;')
        self._db_tool.execute_query('DROP SCHEMA IF EXISTS hidden CASCADE;')
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

    def _insert_few_rows_to_seed_tables(self, row_cnt: int = 1):
        """
        從 poc views 取一筆資料，插入到 seed tables
        """
        for table in self.seed_tables:
            insert_sql = f"""
            INSERT INTO pop.{table}
            SELECT * FROM poc.{table} LIMIT {row_cnt};
            """
            print('Inserting one row into seed table:', table)
            print('Executing SQL:\n', insert_sql)
            self._db_tool.execute_query(insert_sql)