import pytest
from psycopg import OperationalError

import pipeline


def test_pipeline_setup_errors_propagate(monkeypatch):
    error = OperationalError("database unavailable")

    def fail_setup(self):
        raise error

    monkeypatch.setattr(pipeline.PostgreSQLTool, "setup", fail_setup)

    with pytest.raises(OperationalError) as raised:
        pipeline.Pipeline()

    assert raised.value is error


def test_pipeline_can_skip_database_setup(monkeypatch):
    def fail_setup(self):
        pytest.fail("database setup should be skipped")

    monkeypatch.setattr(pipeline.PostgreSQLTool, "setup", fail_setup)

    pipeline.Pipeline(setup_db=False)
