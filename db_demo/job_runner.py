"""Runs long-lived ``pipeline.py`` operations (probe/setup_schedules/truncate)
in a background thread, one at a time, with captured stdout logs so the
frontend can poll progress instead of blocking the request.
"""
from __future__ import annotations

import contextlib
import io
import threading
import time
from typing import Any, Callable, Dict, Optional

_lock = threading.Lock()
_current_job: Optional[Dict[str, Any]] = None
_job_counter = 0


class _LogCapture(io.TextIOBase):
    """Minimal stdout replacement that appends completed lines to a list."""

    def __init__(self, log_list: list):
        self._log_list = log_list
        self._buffer = ""

    def write(self, s: str) -> int:  # noqa: D102 - stdlib-compatible signature
        self._buffer += s
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._log_list.append(line)
        return len(s)

    def flush(self) -> None:
        return None


def start_job(name: str, target: Callable[..., None], *args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Start ``target(*args, **kwargs)`` in a background thread.

    Raises ``RuntimeError`` if another job is already running (only one
    pipeline operation is allowed at a time to avoid conflicting writes).
    """
    global _current_job, _job_counter
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
        capture = _LogCapture(job["logs"])
        try:
            with contextlib.redirect_stdout(capture):
                target(*args, **kwargs)
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
