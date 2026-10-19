from datetime import date

import psycopg

import pipeline


def test_monthly_samples_respect_upstream_minimum_date(pg_dsn):
    schema = "foreign_ownership_boundary_test"
    connection = psycopg.connect(pg_dsn, autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            cursor.execute(f"CREATE SCHEMA {schema}")
            cursor.execute(
                f"CREATE TABLE {schema}.company_basic_info_list "
                "(stk_code TEXT, listing_date DATE)"
            )
            cursor.execute(
                f"INSERT INTO {schema}.company_basic_info_list "
                "VALUES ('TEST', DATE '2004-02-01')"
            )

            query = pipeline.Pipeline(setup_db=False)._get_view_create_sql(
                schema, "foreign_ownership_monthly_list.sql"
            )
            cursor.execute(query)
            cursor.execute(
                f"SELECT month_start_date FROM {schema}.foreign_ownership_monthly_list "
                "WHERE stk_code = 'TEST' ORDER BY month_start_date LIMIT 2"
            )

            assert cursor.fetchall() == [
                (date(2004, 2, 11),),
                (date(2004, 3, 5),),
            ]
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        connection.close()
