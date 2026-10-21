import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Task, TaskStatus, User
from app.schemas.tasks import TaskCreate
from app.services.tasks import create_task


@pytest.mark.asyncio
async def test_two_simultaneous_assignments_cannot_exceed_ten():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required")
    if not (make_url(database_url).database or "").endswith("_test"):
        pytest.fail("Concurrent test must use an isolated _test database")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex[:12]
    author_id = assignee_id = None
    deadline = datetime.now(timezone.utc) + timedelta(days=2)
    try:
        async with sessions() as db:
            author = User(
                username=f"author_{suffix}",
                email=f"author_{suffix}@example.com",
                password_hash="test-only",
            )
            assignee = User(
                username=f"worker_{suffix}",
                email=f"worker_{suffix}@example.com",
                password_hash="test-only",
            )
            db.add_all([author, assignee])
            await db.flush()
            author_id, assignee_id = author.id, assignee.id
            db.add_all(
                [
                    Task(
                        title=f"Existing task {number}",
                        description="",
                        author_id=author_id,
                        assignee_id=assignee_id,
                        deadline=deadline,
                        status=TaskStatus.BACKLOG,
                    )
                    for number in range(9)
                ]
            )
            await db.commit()

        async def assign_one():
            async with sessions() as db:
                author = await db.get(User, author_id)
                return await create_task(
                    db,
                    TaskCreate(
                        title="Concurrent assignment",
                        assignee_id=assignee_id,
                        deadline=deadline,
                    ),
                    author,
                )

        results = await asyncio.gather(assign_one(), assign_one(), return_exceptions=True)
        assert sum(isinstance(result, Task) for result in results) == 1
        rejections = [result for result in results if isinstance(result, HTTPException)]
        assert len(rejections) == 1
        assert rejections[0].status_code == 409
        async with sessions() as db:
            count = await db.scalar(
                select(func.count(Task.id)).where(
                    Task.assignee_id == assignee_id,
                    Task.status.in_(
                        (TaskStatus.BACKLOG, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW)
                    ),
                )
            )
            assert count == 10
    finally:
        if author_id is not None and assignee_id is not None:
            async with engine.begin() as connection:
                await connection.execute(delete(Task).where(Task.author_id == author_id))
                await connection.execute(
                    delete(User).where(User.id.in_([author_id, assignee_id]))
                )
        await engine.dispose()
