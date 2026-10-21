from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.models import Comment, Task, TaskPriority, TaskStatus, User


@pytest.mark.asyncio
async def test_task_and_comment_relationships_on_migrated_schema(api):
    _, sessions = api
    async with sessions() as db:
        author = User(
            username="author", email="author@example.com", password_hash="test-hash"
        )
        db.add(author)
        await db.flush()
        task = Task(
            title="Write tests",
            description="Check the ORM relationships",
            author_id=author.id,
            deadline=datetime.now(timezone.utc) + timedelta(days=1),
        )
        db.add(task)
        await db.flush()
        db.add(Comment(text="First comment", task_id=task.id, author_id=author.id))
        await db.commit()

    async with sessions() as db:
        loaded = await db.scalar(
            select(Task)
            .where(Task.id == task.id)
            .options(selectinload(Task.author), selectinload(Task.comments))
        )
        assert loaded is not None
        assert loaded.author.username == "author"
        assert loaded.assignee_id is None
        assert loaded.status == TaskStatus.BACKLOG
        assert loaded.priority == TaskPriority.MEDIUM
        assert [comment.text for comment in loaded.comments] == ["First comment"]


@pytest.mark.asyncio
async def test_database_constraints_reject_bad_status_and_duplicate_email(api):
    _, sessions = api
    async with sessions() as db:
        author = User(username="owner", email="owner@example.com", password_hash="hash")
        db.add(author)
        await db.commit()

        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.execute(
                    text(
                        "INSERT INTO tasks (title, description, status, priority, "
                        "author_id, deadline) VALUES "
                        "('Invalid status', '', 'Unknown', 'High', :author_id, now())"
                    ),
                    {"author_id": author.id},
                )

        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                db.add(
                    User(
                        username="other",
                        email="OWNER@example.com",
                        password_hash="hash",
                    )
                )
                await db.flush()
