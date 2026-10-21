import io
import json
import urllib.error

from fastapi.testclient import TestClient

from db_demo import endpoint_docs, job_runner, main, row_counts


def _client(monkeypatch):
    monkeypatch.setattr(main.row_counts, "snapshot_once", lambda: 0)
    monkeypatch.setattr(main.row_counts, "get_growth_estimates", lambda: [])
    return TestClient(main.app)


def test_admin_posts_require_operator_credentials(monkeypatch):
    monkeypatch.setenv("DB_DEMO_OPERATOR_USERNAME", "operator")
    monkeypatch.setenv("DB_DEMO_OPERATOR_PASSWORD", "test-password")
    with _client(monkeypatch) as client:
        for path in (
            "/api/pop/snapshot",
            "/api/jobs/probe_all_throughput",
            "/api/jobs/setup_schedules",
            "/api/jobs/truncate_cron_jobs",
        ):
            assert client.post(path).status_code == 401


def test_admin_posts_fail_closed_without_configured_credentials(monkeypatch):
    monkeypatch.delenv("DB_DEMO_OPERATOR_USERNAME", raising=False)
    monkeypatch.delenv("DB_DEMO_OPERATOR_PASSWORD", raising=False)
    with _client(monkeypatch) as client:
        response = client.post(
            "/api/jobs/truncate_cron_jobs",
            auth=("operator", "test-password"),
        )
    assert response.status_code == 503


def test_admin_post_accepts_configured_operator(monkeypatch):
    monkeypatch.setenv("DB_DEMO_OPERATOR_USERNAME", "operator")
    monkeypatch.setenv("DB_DEMO_OPERATOR_PASSWORD", "test-password")
    monkeypatch.setattr(
        main.job_runner,
        "start_job",
        lambda name, **kwargs: {
            "id": 1,
            "name": name,
            "status": "running",
            "error": None,
            "started_at": 0,
            "finished_at": None,
            "logs": [],
        },
    )
    with _client(monkeypatch) as client:
        response = client.post(
            "/api/jobs/probe_all_throughput?restart=false",
            auth=("operator", "test-password"),
        )
    assert response.status_code == 200
    assert response.json()["job"]["name"] == "probe_all_throughput"


def test_dag_metadata_skips_database_setup(monkeypatch):
    calls = []

    class FakePipeline:
        _db_tool = object()

    def make_pipeline(setup_db=True):
        calls.append(setup_db)
        return FakePipeline()

    monkeypatch.setattr(main, "Pipeline", make_pipeline)
    monkeypatch.setattr(main.dag_builder, "build_dag", lambda _: {"nodes": [], "edges": []})
    monkeypatch.setattr(main.row_counts, "get_pop_row_counts", lambda _: {})
    with _client(monkeypatch) as client:
        response = client.get("/api/dag")
    assert response.status_code == 200
    assert calls == [False]


def test_openapi_fetch_failure_is_retried(monkeypatch):
    endpoint_docs._openapi_cache = None
    responses = [
        urllib.error.URLError("API unavailable"),
        io.BytesIO(json.dumps({"paths": {"/api/ready": {}}}).encode()),
    ]

    def fake_urlopen(*args, **kwargs):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(endpoint_docs.urllib.request, "urlopen", fake_urlopen)

    assert endpoint_docs._get_openapi_spec() == {"paths": {}}
    assert endpoint_docs._get_openapi_spec() == {"paths": {"/api/ready": {}}}


def test_snapshot_uses_actual_counts_and_escapes_identifiers():
    class FakeDB:
        def __init__(self):
            self.queries = []
            self.commands = []

        def execute_query(self, query, params=None):
            self.commands.append((query, params))

        def fetch_all(self, query, params=None):
            self.queries.append((query, params))
            if "SELECT c.relname" in query:
                return [("ordinary",), ('odd"name',)]
            if "COUNT(*)::BIGINT" in query:
                return [("ordinary", 3), ('odd"name', 4)]
            raise AssertionError(query)

    db = FakeDB()
    assert row_counts.snapshot_once(db) == 2
    count_query, params = db.queries[1]
    assert '"pop"."odd""name"' in count_query
    assert params == ("ordinary", 'odd"name')
    assert db.commands[-1][1] == ("ordinary", 3, 'odd"name', 4)


def test_job_output_is_captured_from_subprocess(monkeypatch, capsys):
    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    class FakeProcess:
        stdout = io.StringIO("pipeline output\n")

        def wait(self):
            return 0

    commands = []

    def fake_popen(command, **kwargs):
        commands.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(job_runner.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(job_runner.subprocess, "Popen", fake_popen)
    job_runner._current_job = None
    job = job_runner.start_job("probe_all_throughput", restart=False)

    assert job["status"] == "success"
    assert job["logs"] == ["pipeline output"]
    assert commands[0][0][-2:] == ["--restart", "false"]
    assert capsys.readouterr().out == ""
