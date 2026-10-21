import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Task, TaskStatus, User
from app.services.tasks import cancel_overdue_tasks


@pytest.mark.asyncio
async def test_worker_skips_task_locked_by_concurrent_status_change():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required")
    if not (make_url(database_url).database or "").endswith("_test"):
        pytest.fail("Race test must use an isolated _test database")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex[:12]
    owner_id = task_id = None
    now = datetime.now(timezone.utc)
    try:
        async with sessions() as db:
            owner = User(
                username=f"race_owner_{suffix}",
                email=f"race_owner_{suffix}@example.com",
                password_hash="test-only",
            )
            db.add(owner)
            await db.flush()
            owner_id = owner.id
            task = Task(
                title="Concurrent status change",
                description="",
                author_id=owner_id,
                status=TaskStatus.IN_PROGRESS,
                deadline=now - timedelta(minutes=1),
            )
            db.add(task)
            await db.flush()
            task_id = task.id
            await db.commit()

        async with sessions() as locker:
            locked = await locker.scalar(
                select(Task).where(Task.id == task_id).with_for_update()
            )
            assert locked is not None

            async def worker_scan():
                async with sessions() as db:
                    return await cancel_overdue_tasks(db, now=now)

            assert await asyncio.wait_for(worker_scan(), timeout=5) == 0
            locked.status = TaskStatus.CANCELLED
            await locker.commit()

        async with sessions() as db:
            assert await cancel_overdue_tasks(db, now=now) == 0
            stored = await db.get(Task, task_id)
            assert stored.status == TaskStatus.CANCELLED
    finally:
        if owner_id is not None:
            async with engine.begin() as connection:
                if task_id is not None:
                    await connection.execute(delete(Task).where(Task.id == task_id))
                await connection.execute(delete(User).where(User.id == owner_id))
        await engine.dispose()
