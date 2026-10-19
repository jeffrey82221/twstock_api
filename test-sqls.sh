rm -rf .venv/
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-py311.txt
pytest tests/test_sql_view_creation.py
pytest tests/test_sql_table_lineage_consistency.py
pytest tests/test_sql_column_lineage_coverage.py
RUN_POC_VIEW_SELECT_TESTS=1 pytest tests/test_sql_select_one_row.py -vv
