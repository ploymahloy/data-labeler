"""Returns images with expired leases to their queues. Run as a Kubernetes CronJob.

python -m app.reaper
"""

import asyncio
import logging

from app.db import get_engine, get_sessionmaker
from app.services.workflow import reap_expired

logger = logging.getLogger("reaper")


async def run_once() -> int:
    async with get_sessionmaker()() as session, session.begin():
        return await reap_expired(session)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        count = await run_once()
        logger.info("reaped %d expired lease(s)", count)
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
