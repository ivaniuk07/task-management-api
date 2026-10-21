import asyncio
import logging

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.tasks import cancel_overdue_tasks


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    while True:
        try:
            async with SessionLocal() as db:
                count = await cancel_overdue_tasks(db)
                if count:
                    logger.info("Cancelled %s overdue tasks", count)
        except Exception:
            logger.exception("Overdue task check failed")
        await asyncio.sleep(settings.worker_interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
