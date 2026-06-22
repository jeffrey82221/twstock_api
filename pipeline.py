
import os
from sql_metadata import Parser
from typing import List, Dict
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
        self.dag = self._create_dag()
        self._db_tool = PostgreSQLTool()
    
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
                parser = Parser(sql)
                results[sql_path] = [t for t in parser.tables if t.startswith('poc.')]
        return results
    
    @property
    def view_create_sqls(self) -> Dict[str, str]:
        results = dict()
        for sql_path in self._sql_paths:
            with open(os.path.join(os.path.dirname(__file__), 'db', 'poc', sql_path), 'r') as f:
                sql = f.read()
                view_name = sql_path.split('.')[0]
                view_create_sql = f'''CREATE OR REPLACE VIEW poc.{view_name}
                AS 
                {sql}
                '''
                results[sql_path] = view_create_sql
        return results
    

    @property
    def ordered_sql_paths(self) -> List[str]:
        results = []
        dag_run(
            self.dag, processor=SequentialProcessor(), executor=ViewExecutor(results))
        return results
    

    def create_views(self):
        self._db_tool.execute_query('DROP SCHEMA IF EXISTS poc CASCADE;')
        self._db_tool.execute_query('CREATE SCHEMA poc;')
        for sql_path in self.ordered_sql_paths:
            print('Creating view for:', sql_path)
            create_sql = self.view_create_sqls[sql_path]
            print('Executing SQL:\n', create_sql)
            self._db_tool.execute_query(create_sql)