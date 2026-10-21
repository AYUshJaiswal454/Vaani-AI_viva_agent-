"""DB-backed background task queue and periodic scheduler.

Any number of worker processes can run `Worker.run()`; a task is claimed with a single conditional UPDATE so
two workers can never take the same task. Scoring, audio finalisation and speech pre-synthesis never run inside a
live viva connection, so a burst of 150 vivas ending at once only lengthens the scoring queue, never the examiner's
reply time.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import socket
import traceback
from typing import Any, Awaitable, Callable

from starlette.concurrency import run_in_threadpool

from .db import get_db, iso_in, now_iso

log = logging.getLogger("vaani.tasks")

Handler = Callable[[dict], Any] | Callable[[dict], Awaitable[Any]]
HANDLERS: dict[str, Handler] = {}
MAX_ATTEMPTS = 3


def handler(kind: str):
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn
    return deco


# Finishing a viva's audio and scoring it come first; bulk work (bank generation, consistency runs) yields.
PRIORITY = {"finalize_session": 10, "score_session": 9, "presynth_viva": 6, "generate_bank": 5, "rescore_session": 4,
            "sweep_sessions": 3, "consistency_run": 2, "cleanup": 0}


def enqueue(kind: str, payload: dict | None = None, user_id: int | None = None, dedupe_key: str | None = None,
            delay_s: int = 0) -> int:
    db = get_db()
    if dedupe_key:
        existing = db.fetchone(
            "SELECT id FROM tasks WHERE dedupe_key = ? AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
            (dedupe_key,),
        )
        if existing:
            return int(existing["id"])
    return db.insert("tasks", {
        "kind": kind,
        "priority": PRIORITY.get(kind, 3),
        "user_id": user_id,
        "payload": payload or {},
        "status": "queued",
        "dedupe_key": dedupe_key,
        "run_after": iso_in(seconds=delay_s) if delay_s else now_iso(),
        "created_at": now_iso(),
    })


def claim(worker_id: str) -> dict | None:
    db = get_db()
    for _ in range(5):
        row = db.fetchone(
            "SELECT id FROM tasks WHERE status = 'queued' AND (run_after IS NULL OR run_after <= ?) "
            "ORDER BY priority DESC, id LIMIT 1",
            (now_iso(),),
        )
        if not row:
            return None
        cur = db.execute(
            "UPDATE tasks SET status = 'running', worker = ?, started_at = ?, attempts = attempts + 1 "
            "WHERE id = ? AND status = 'queued'",
            (worker_id, now_iso(), row["id"]),
        )
        if cur.rowcount == 1:
            return db.fetchone("SELECT * FROM tasks WHERE id = ?", (row["id"],))
    return None


def set_progress(task_id: int, progress: int, message: str = "") -> None:
    get_db().execute("UPDATE tasks SET progress = ?, message = ? WHERE id = ?", (int(progress), message[:300], task_id))


def complete(task_id: int, result: Any = None) -> None:
    get_db().update("tasks", task_id, {
        "status": "done", "progress": 100, "result": result if result is not None else {}, "finished_at": now_iso(),
    })


def fail(task: dict, error: str) -> None:
    db = get_db()
    if task["attempts"] < MAX_ATTEMPTS and "PERMANENT:" not in error:
        backoff = 5 * (2 ** (task["attempts"] - 1))
        db.update("tasks", task["id"], {"status": "queued", "error": error[-2000:], "run_after": iso_in(seconds=backoff)})
    else:
        db.update("tasks", task["id"], {
            "status": "failed", "error": error.replace("PERMANENT:", "").strip()[-2000:], "finished_at": now_iso(),
        })


def recover_stale(max_age_min: int = 15) -> int:
    """Re-queue tasks whose worker died mid-run."""
    cur = get_db().execute(
        "UPDATE tasks SET status = 'queued', worker = NULL WHERE status = 'running' AND started_at < ?",
        (iso_in(minutes=-max_age_min),),
    )
    return cur.rowcount


async def run_task(task: dict) -> None:
    fn = HANDLERS.get(task["kind"])
    if fn is None:
        fail({**task, "attempts": MAX_ATTEMPTS}, f"PERMANENT: no handler for {task['kind']}")
        return
    try:
        if inspect.iscoroutinefunction(fn):
            result = await fn(task)
        else:
            result = await run_in_threadpool(fn, task)
        complete(task["id"], result)
    except Exception as exc:  # noqa: BLE001 - every failure must be recorded
        log.warning("Task %s (%s) failed: %s", task["id"], task["kind"], exc)
        fail(task, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}")


class Worker:
    def __init__(self, concurrency: int = 4, poll_s: float = 0.5) -> None:
        self.id = f"{socket.gethostname()}:{os.getpid()}"
        self.concurrency = concurrency
        self.poll_s = poll_s
        self._stop = asyncio.Event()
        self._sem = asyncio.Semaphore(concurrency)
        self._running: set[asyncio.Task] = set()

    async def run(self) -> None:
        await run_in_threadpool(recover_stale)
        log.info("Worker %s started (concurrency=%s)", self.id, self.concurrency)
        while not self._stop.is_set():
            await self._sem.acquire()
            task = await run_in_threadpool(claim, self.id)
            if task is None:
                self._sem.release()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_s)
                except asyncio.TimeoutError:
                    pass
                continue
            job = asyncio.create_task(self._run_one(task))
            self._running.add(job)
            job.add_done_callback(self._running.discard)

    async def _run_one(self, task: dict) -> None:
        try:
            await run_task(task)
        finally:
            self._sem.release()

    async def stop(self) -> None:
        self._stop.set()
        if self._running:
            await asyncio.wait(self._running, timeout=20)


# ----------------------------------------------------------------------------- scheduler
SCHEDULES: dict[str, tuple[int, str, dict]] = {}  # name -> (interval_s, task_kind, payload)


def schedule(name: str, every_s: int, kind: str, payload: dict | None = None) -> None:
    SCHEDULES[name] = (every_s, kind, payload or {})


def tick_schedules(worker_id: str) -> list[str]:
    """Enqueue any periodic task that is due. Safe across processes (conditional UPDATE)."""
    db = get_db()
    fired = []
    for name, (every_s, kind, payload) in SCHEDULES.items():
        db.insert_ignore("schedules", {"name": name, "next_run_at": now_iso()})
        row = db.fetchone("SELECT * FROM schedules WHERE name = ?", (name,))
        if row and row["next_run_at"] <= now_iso():
            cur = db.execute(
                "UPDATE schedules SET next_run_at = ?, last_run_at = ?, locked_by = ? WHERE name = ? AND next_run_at = ?",
                (iso_in(seconds=every_s), now_iso(), worker_id, name, row["next_run_at"]),
            )
            if cur.rowcount == 1:
                enqueue(kind, payload, dedupe_key=f"schedule:{name}")
                fired.append(name)
    return fired


async def scheduler_loop(stop: asyncio.Event, worker_id: str, every_s: float = 30) -> None:
    while not stop.is_set():
        try:
            await run_in_threadpool(tick_schedules, worker_id)
        except Exception:  # pragma: no cover
            log.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=every_s)
        except asyncio.TimeoutError:
            pass


async def drain(max_tasks: int = 1000, worker_id: str = "inline") -> int:
    """Run queued tasks inline until the queue is empty (used by tests and scripts)."""
    count = 0
    while count < max_tasks:
        task = claim(worker_id)
        if task is None:
            break
        await run_task(task)
        count += 1
    return count
