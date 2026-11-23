import pytest

import pipeline


@pytest.fixture
def p(monkeypatch):
    obj = pipeline.Pipeline.__new__(pipeline.Pipeline)
    monkeypatch.setattr(obj, "_get_table_columns", lambda schema, table: ["stock_id", "year"], raising=False)
    return obj


def test_no_filters_keeps_original_sql(p):
    (sql,) = p._build_seed_insert_sql("company_list", row_cnt=3)
    assert sql == (
        'INSERT INTO pop.company_list SELECT * FROM hidden.company_list '
        'WHERE "stock_id" IS NOT NULL AND "year" IS NOT NULL '
        'EXCEPT SELECT * FROM pop.company_list LIMIT 3;'
    )


def test_equality_and_in_filters(p):
    (sql,) = p._build_seed_insert_sql(
        "company_list", row_cnt=5, filters={"stock_id": ["2330", "2317"], "year": 2024})
    assert """AND "stock_id" IN ('2330', '2317') AND "year" = '2024' EXCEPT""" in sql
    assert sql.endswith("LIMIT 5;")


def test_quotes_are_escaped(p):
    (sql,) = p._build_seed_insert_sql("company_list", filters={"stock_id": "x' OR '1'='1"})
    assert """"stock_id" = 'x'' OR ''1''=''1'""" in sql


@pytest.mark.parametrize("filters", [
    {'stock_id" = 1; --': "a"},   # unknown column (identifier injection)
    {"stock_id": None},
    {"stock_id": []},
    {"stock_id": {"a": 1}},
])
def test_bad_filters_rejected(p, filters):
    with pytest.raises(ValueError):
        p._build_seed_insert_sql("company_list", filters=filters)


def test_order_by_defaults_to_desc_and_precedes_limit(p):
    (sql,) = p._build_seed_insert_sql(
        "company_list", row_cnt=2, filters={"stock_id": "2330"}, order_by=["year", "stock_id asc"])
    assert sql.endswith('EXCEPT SELECT * FROM pop.company_list ORDER BY "year" DESC, "stock_id" ASC LIMIT 2;')


@pytest.mark.parametrize("order_by", [["nope"], ["year; drop table x"], ["year sideways"], [""]])
def test_bad_order_by_rejected(p, order_by):
    with pytest.raises(ValueError):
        p._build_seed_insert_sql("company_list", order_by=order_by)


def test_default_order_by_picks_first_date_column(p, monkeypatch):
    class FakeDB:
        def __init__(self, rows):
            self.rows = rows

        def fetch_all(self, sql, params):
            return self.rows

    p._db_tool = FakeDB([("stk_code", "text"), ("month_start_date", "date"), ("listing_date", "date")])
    assert p._default_seed_order_by("ohlcv_monthly_list") == ["month_start_date DESC"]
    p._db_tool = FakeDB([("stk_code", "text"), ("year", "integer")])
    assert p._default_seed_order_by("financial_analysis_year_list") == ["year DESC"]
    p._db_tool = FakeDB([("stk_code", "text"), ("company_name", "text")])
    assert p._default_seed_order_by("company_list") == []
