"""PodOS durable worker: monitor scheduler + outbox consumer.

    python -m podos_worker [--interval 2] [--once]

Runs independently of any browser session. Safe to run several replicas: rows
are claimed with FOR UPDATE SKIP LOCKED under per-pod transactions and leases.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket

from podos_api import background
from podos_api.services import Services
from podos_api.settings import get_settings

log = logging.getLogger("podos.worker")


async def run(interval: float, once: bool) -> None:
    svc = Services(get_settings())
    await svc.open()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    log.info("worker %s started", worker_id)
    try:
        while True:
            try:
                stats = await background.tick(svc, worker_id)
                if any(stats.values()):
                    log.info("tick %s", stats)
            except Exception:
                log.exception("tick failed")
            if once:
                return
            await asyncio.sleep(interval)
    finally:
        await svc.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run(a.interval, a.once))


if __name__ == "__main__":
    main()
