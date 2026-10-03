source .venv/bin/activate
pytest tests/test_sql_view_creation.py
pytest tests/test_sql_table_lineage_consistency.py
pytest tests/test_sql_column_lineage_coverage.py
./start-db.sh
RUN_POC_VIEW_SELECT_TESTS=1 pytest tests/test_sql_select_one_row.py
cd db_start
docker compose down
cd ..
