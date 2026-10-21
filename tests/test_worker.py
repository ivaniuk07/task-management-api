from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.models import Task, TaskStatus, User
from app.services.tasks import cancel_overdue_tasks


@pytest.mark.asyncio
async def test_worker_cancels_only_overdue_active_tasks_and_is_idempotent(api):
    _, sessions = api
    now = datetime.now(timezone.utc).replace(microsecond=0)
    task_ids = {}
    async with sessions() as db:
        user = User(
            username="worker_test_owner",
            email="worker_test_owner@example.com",
            password_hash="test-only",
        )
        db.add(user)
        await db.flush()
        for label, state, deadline in (
            ("backlog", TaskStatus.BACKLOG, now - timedelta(seconds=1)),
            ("progress", TaskStatus.IN_PROGRESS, now - timedelta(seconds=1)),
            ("review", TaskStatus.REVIEW, now - timedelta(seconds=1)),
            ("done", TaskStatus.DONE, now - timedelta(seconds=1)),
            ("cancelled", TaskStatus.CANCELLED, now - timedelta(seconds=1)),
            ("future", TaskStatus.BACKLOG, now + timedelta(seconds=1)),
            ("boundary", TaskStatus.BACKLOG, now),
        ):
            task = Task(
                title=label,
                description="",
                author_id=user.id,
                status=state,
                deadline=deadline,
            )
            db.add(task)
            await db.flush()
            task_ids[label] = task.id
        await db.commit()

    old_updated_at = now - timedelta(days=5)
    async with sessions() as db:
        await db.execute(
            update(Task)
            .where(Task.id.in_(task_ids.values()))
            .values(updated_at=old_updated_at)
        )
        await db.commit()

    async with sessions() as db:
        assert await cancel_overdue_tasks(db, now=now) == 3
        assert await cancel_overdue_tasks(db, now=now) == 0
        tasks = (await db.scalars(select(Task).where(Task.id.in_(task_ids.values())))).all()
        statuses = {task.title: task.status for task in tasks}
        assert all(statuses[name] == TaskStatus.CANCELLED for name in ("backlog", "progress", "review"))
        assert statuses["done"] == TaskStatus.DONE
        assert statuses["cancelled"] == TaskStatus.CANCELLED
        assert statuses["future"] == TaskStatus.BACKLOG
        assert statuses["boundary"] == TaskStatus.BACKLOG
        for task in tasks:
            if task.title in ("backlog", "progress", "review"):
                assert task.updated_at > old_updated_at
            else:
                assert task.updated_at == old_updated_at
