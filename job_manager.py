from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from threading import Event, Lock, Thread
from typing import Any, Callable
import traceback

from operation_control import OperationCancelledError, check_cancelled


JobRunner = Callable[["JobContext"], Any]


@dataclass
class Job:
    job_id: int
    title: str
    kind: str
    runner: JobRunner
    status: str = "queued"
    progress_current: int = 0
    progress_total: int = 1
    message: str = ""
    logs: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_event: Event = field(default_factory=Event)
    result: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class JobContext:
    def __init__(self, manager: "JobManager", job_id: int):
        self._manager = manager
        self.job_id = job_id

    @property
    def cancel_event(self) -> Event:
        return self._manager._get_cancel_event(self.job_id)

    def check_cancelled(self):
        check_cancelled(self.cancel_event)

    def log(self, message: str):
        self._manager._append_log(self.job_id, message)

    def set_progress(self, current: int, total: int | None = None, message: str | None = None):
        self._manager._set_progress(self.job_id, current, total, message)

    def set_message(self, message: str):
        self._manager._set_message(self.job_id, message)


class JobManager:
    def __init__(self, max_concurrent_jobs: int = 2):
        self.max_concurrent_jobs = max(1, int(max_concurrent_jobs))
        self._lock = Lock()
        self._jobs: dict[int, Job] = {}
        self._queue: deque[int] = deque()
        self._running: set[int] = set()
        self._next_job_id = 1

    def submit(self, *, title: str, kind: str, runner: JobRunner, metadata: dict[str, Any] | None = None) -> int:
        with self._lock:
            job_id = self._next_job_id
            self._next_job_id += 1
            job = Job(
                job_id=job_id,
                title=title,
                kind=kind,
                runner=runner,
                metadata=dict(metadata or {}),
            )
            job.logs.append("Queued.")
            self._jobs[job_id] = job
            self._queue.append(job_id)
            self._maybe_start_jobs_locked()
            return job_id

    def cancel(self, job_id: int) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in {"completed", "failed", "cancelled"}:
                return False

            if job.status == "queued":
                try:
                    self._queue.remove(job_id)
                except ValueError:
                    pass
                job.status = "cancelled"
                job.finished_at = datetime.now()
                job.logs.append("Cancelled before start.")
                return True

            job.cancel_event.set()
            if job.status == "running":
                job.status = "cancelling"
                job.logs.append("Cancellation requested.")
            return True

    def has_active_jobs(self) -> bool:
        with self._lock:
            return any(job.status in {"queued", "running", "cancelling"} for job in self._jobs.values())

    def get_job_snapshot(self, job_id: int) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return self._snapshot_locked(job)

    def list_job_snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: job.job_id, reverse=True)
            return [self._snapshot_locked(job) for job in jobs]

    def _snapshot_locked(self, job: Job) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "title": job.title,
            "kind": job.kind,
            "status": job.status,
            "progress_current": job.progress_current,
            "progress_total": job.progress_total,
            "message": job.message,
            "logs": list(job.logs),
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "result": job.result,
            "error": job.error,
            "metadata": dict(job.metadata),
        }

    def _get_cancel_event(self, job_id: int) -> Event:
        with self._lock:
            return self._jobs[job_id].cancel_event

    def _append_log(self, job_id: int, message: str):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.logs.append(message)

    def _set_message(self, job_id: int, message: str):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.message = message

    def _set_progress(self, job_id: int, current: int, total: int | None, message: str | None):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.progress_current = max(0, int(current))
            if total is not None:
                job.progress_total = max(1, int(total))
            if message is not None:
                job.message = message

    def _maybe_start_jobs_locked(self):
        while self._queue and len(self._running) < self.max_concurrent_jobs:
            job_id = self._queue.popleft()
            job = self._jobs.get(job_id)
            if job is None or job.status != "queued":
                continue
            job.status = "running"
            job.started_at = datetime.now()
            job.logs.append("Started.")
            self._running.add(job_id)
            Thread(target=self._run_job, args=(job_id,), daemon=True).start()

    def _run_job(self, job_id: int):
        ctx = JobContext(self, job_id)
        try:
            with self._lock:
                job = self._jobs[job_id]
                runner = job.runner
            result = runner(ctx)
            with self._lock:
                job = self._jobs[job_id]
                if job.cancel_event.is_set():
                    job.status = "cancelled"
                    job.logs.append("Cancelled.")
                else:
                    job.status = "completed"
                    job.logs.append("Completed.")
                job.result = result
                job.finished_at = datetime.now()
        except OperationCancelledError as exc:
            with self._lock:
                job = self._jobs[job_id]
                job.status = "cancelled"
                job.error = str(exc)
                job.logs.append(str(exc))
                job.finished_at = datetime.now()
        except Exception as exc:
            with self._lock:
                job = self._jobs[job_id]
                job.status = "failed"
                job.error = str(exc)
                job.logs.append(f"ERROR: {exc}")
                job.logs.append(traceback.format_exc())
                job.finished_at = datetime.now()
        finally:
            with self._lock:
                self._running.discard(job_id)
                self._maybe_start_jobs_locked()
