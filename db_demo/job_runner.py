"""Runs long-lived ``pipeline.py`` operations in isolated subprocesses."""
from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

_lock = threading.Lock()
_current_job: Optional[Dict[str, Any]] = None
_job_counter = 0
_REPO_ROOT = Path(__file__).resolve().parent.parent


def start_job(name: str, **kwargs: Any) -> Dict[str, Any]:
    """Start a supported pipeline operation in a subprocess, one at a time."""
    global _current_job, _job_counter
    if name not in {"probe_all_throughput", "setup_schedules", "truncate_cron_jobs"}:
        raise ValueError(f"unsupported job: {name}")
    if name != "probe_all_throughput" and kwargs:
        raise ValueError(f"unexpected arguments for job: {name}")
    restart = kwargs.get("restart", True)
    if name == "probe_all_throughput" and not isinstance(restart, bool):
        raise ValueError("restart must be a boolean")

    with _lock:
        if _current_job is not None and _current_job["status"] == "running":
            raise RuntimeError(f'a job is already running: {_current_job["name"]!r}')
        _job_counter += 1
        job: Dict[str, Any] = {
            "id": _job_counter,
            "name": name,
            "status": "running",
            "logs": [],
            "error": None,
            "started_at": time.time(),
            "finished_at": None,
        }
        _current_job = job

    def _runner() -> None:
        command = [sys.executable, "-m", "db_demo.job_runner", name]
        if name == "probe_all_throughput":
            command.extend(["--restart", str(restart).lower()])
        try:
            process = subprocess.Popen(
                command,
                cwd=str(_REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                job["logs"].append(line.rstrip("\n"))
            return_code = process.wait()
            if return_code:
                job["error"] = f"Pipeline operation exited with status {return_code}"
                job["logs"].append(job["error"])
                job["status"] = "error"
            else:
                job["status"] = "success"
        except Exception as e:  # noqa: BLE001 - surface any failure to the UI
            job["logs"].append(f"ERROR: {e}")
            job["status"] = "error"
            job["error"] = str(e)
        finally:
            job["finished_at"] = time.time()

    threading.Thread(target=_runner, daemon=True, name=f"db_demo-job-{name}").start()
    return job


def get_current_job() -> Optional[Dict[str, Any]]:
    return _current_job


def _run_pipeline_operation(name: str, restart: bool) -> None:
    from pipeline import Pipeline

    pipeline = Pipeline()
    if name == "probe_all_throughput":
        pipeline.probe_all_throughput(restart=restart)
    elif name == "setup_schedules":
        pipeline.setup_schedules()
    elif name == "truncate_cron_jobs":
        pipeline.truncate_cron_jobs()
    else:
        raise ValueError(f"unsupported job: {name}")


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("name", choices=("probe_all_throughput", "setup_schedules", "truncate_cron_jobs"))
    parser.add_argument("--restart", choices=("true", "false"), default="true")
    args = parser.parse_args()
    _run_pipeline_operation(args.name, args.restart == "true")


if __name__ == "__main__":
    _main()
