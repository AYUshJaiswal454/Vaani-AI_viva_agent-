"""Standalone background worker: `python -m app.worker`.

By default the web process also runs a worker (EMBEDDED_WORKER=true), which is right for a single small server.
To scale, run the web app with EMBEDDED_WORKER=false and start as many of these as you have spare CPU cores;
they share the database-backed queue (tasks are claimed atomically, highest priority first).
"""
from __future__ import annotations

import asyncio
import logging
import signal

from .config import get_settings
from .db import get_db
from .tasks import Worker, scheduler_loop

log = logging.getLogger("vaani.worker")


async def main() -> None:
    import app.pipelines  # noqa: F401  (registers task handlers)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    get_db()
    settings = get_settings()
    worker = Worker(concurrency=settings.worker_concurrency)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    tasks = [asyncio.create_task(worker.run()), asyncio.create_task(scheduler_loop(stop, worker.id))]
    await stop.wait()
    log.info("stopping worker %s", worker.id)
    await worker.stop()
    for t in tasks:
        t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
