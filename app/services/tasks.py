from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Comment, Task, TaskPriority, TaskStatus, User
from app.schemas.comments import CommentCreate
from app.schemas.tasks import TaskCreate, TaskStats, TaskStatusUpdate, TaskUpdate


ACTIVE_STATUSES = (TaskStatus.BACKLOG, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW)
ALLOWED_TRANSITIONS = {
    TaskStatus.BACKLOG: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.REVIEW, TaskStatus.CANCELLED},
    TaskStatus.REVIEW: {TaskStatus.DONE},
    TaskStatus.DONE: set(),
    TaskStatus.CANCELLED: set(),
}


@dataclass(frozen=True)
class TaskListFilters:
    search: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    assignee_id: int | None = None
    deadline_from: datetime | None = None
    deadline_to: datetime | None = None
    sort_by: Literal["priority", "created_at", "deadline"] = "priority"
    sort_order: Literal["asc", "desc"] = "asc"


def overdue_condition(now: datetime):
    return (Task.deadline < now, Task.status.in_(ACTIVE_STATUSES))


def require_future_deadline(deadline: datetime) -> None:
    if deadline <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Deadline must be in the future",
        )


def apply_status_transition(
    task: Task, new_status: TaskStatus, *, now: datetime, automatic: bool = False
) -> None:
    if automatic:
        if new_status != TaskStatus.CANCELLED or task.status not in ACTIVE_STATUSES or task.deadline >= now:
            raise HTTPException(status_code=409, detail="Automatic cancellation is not allowed")
    elif new_status not in ALLOWED_TRANSITIONS[task.status]:
        raise HTTPException(status_code=409, detail="Status transition is not allowed")

    if new_status == TaskStatus.DONE:
        if task.assignee_id is None:
            raise HTTPException(status_code=409, detail="Done requires an assignee")
        if task.deadline <= now:
            raise HTTPException(status_code=409, detail="Overdue task cannot be Done")
    task.status = new_status


async def require_task(db: AsyncSession, task_id: int, *, lock: bool = False) -> Task:
    query = (
        select(Task)
        .options(selectinload(Task.author), selectinload(Task.assignee))
        .where(Task.id == task_id)
    )
    if lock:
        query = query.with_for_update()
    task = await db.scalar(query)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


async def list_tasks(
    db: AsyncSession, offset: int, limit: int, filters: TaskListFilters
) -> tuple[list[Task], int]:
    conditions = []
    if filters.search:
        # Escape SQL wildcard characters so the query searches for literal text.
        term = filters.search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        if term:
            pattern = f"%{term}%"
            conditions.append(
                or_(Task.title.ilike(pattern, escape="\\"), Task.description.ilike(pattern, escape="\\"))
            )
    if filters.status is not None:
        conditions.append(Task.status == filters.status)
    if filters.priority is not None:
        conditions.append(Task.priority == filters.priority)
    if filters.assignee_id is not None:
        conditions.append(Task.assignee_id == filters.assignee_id)
    for name, value in (("deadline_from", filters.deadline_from), ("deadline_to", filters.deadline_to)):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise HTTPException(status_code=422, detail=f"{name} must include a timezone offset")
    if filters.deadline_from is not None and filters.deadline_to is not None:
        if filters.deadline_from > filters.deadline_to:
            raise HTTPException(status_code=422, detail="deadline_from must not exceed deadline_to")
    if filters.deadline_from is not None:
        conditions.append(Task.deadline >= filters.deadline_from)
    if filters.deadline_to is not None:
        conditions.append(Task.deadline <= filters.deadline_to)

    # A unique ID breaks ties, so pagination stays deterministic.
    priority_order = case(
        (Task.priority == TaskPriority.HIGH, 0),
        (Task.priority == TaskPriority.MEDIUM, 1),
        else_=2,
    )
    field = priority_order if filters.sort_by == "priority" else getattr(Task, filters.sort_by)
    first_order = field.desc() if filters.sort_order == "desc" else field.asc()
    order = (first_order, Task.deadline.asc(), Task.id.asc()) if filters.sort_by == "priority" else (first_order, Task.id.asc())
    total = await db.scalar(select(func.count(Task.id)).where(*conditions))
    result = await db.scalars(
        select(Task)
        .options(selectinload(Task.author), selectinload(Task.assignee))
        .where(*conditions)
        .order_by(*order)
        .offset(offset)
        .limit(limit)
    )
    return list(result), total or 0


async def list_overdue_tasks(
    db: AsyncSession, offset: int, limit: int, *, now: datetime | None = None
) -> tuple[list[Task], int]:
    conditions = overdue_condition(now or datetime.now(timezone.utc))
    total = await db.scalar(select(func.count(Task.id)).where(*conditions))
    result = await db.scalars(
        select(Task)
        .options(selectinload(Task.author), selectinload(Task.assignee))
        .where(*conditions)
        .order_by(Task.deadline, Task.id)
        .offset(offset)
        .limit(limit)
    )
    return list(result), total or 0


async def task_statistics(db: AsyncSession, *, now: datetime | None = None) -> TaskStats:
    now = now or datetime.now(timezone.utc)
    statuses = list(TaskStatus)
    priorities = list(TaskPriority)
    counters = [func.count(Task.id)]
    counters.extend(func.count(Task.id).filter(Task.status == state) for state in statuses)
    counters.extend(func.count(Task.id).filter(Task.priority == value) for value in priorities)
    counters.append(func.count(Task.id).filter(*overdue_condition(now)))
    counters.append(func.count(Task.id).filter(Task.status.in_(ACTIVE_STATUSES)))
    row = (await db.execute(select(*counters))).one()
    return TaskStats(
        total=row[0],
        status_counts={state.value: row[index + 1] for index, state in enumerate(statuses)},
        priority_counts={value.value: row[index + 1 + len(statuses)] for index, value in enumerate(priorities)},
        overdue_count=row[-2],
        active_count=row[-1],
    )


async def require_assignee_capacity(db: AsyncSession, assignee_id: int) -> None:
    # The user-row lock serializes assignments to the same user.
    assignee = await db.scalar(
        select(User).where(User.id == assignee_id).with_for_update()
    )
    if assignee is None:
        raise HTTPException(status_code=422, detail="Assignee does not exist")

    active_count = await db.scalar(
        select(func.count(Task.id)).where(
            Task.assignee_id == assignee_id,
            Task.status.in_(ACTIVE_STATUSES),
        )
    )
    if active_count >= 10:
        raise HTTPException(status_code=409, detail="Assignee already has 10 active tasks")


async def create_task(db: AsyncSession, data: TaskCreate, author: User) -> Task:
    require_future_deadline(data.deadline)
    if data.assignee_id is not None:
        await require_assignee_capacity(db, data.assignee_id)

    task = Task(**data.model_dump(), author_id=author.id)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    await db.refresh(task, attribute_names=["author", "assignee"])
    return task


async def update_task(
    db: AsyncSession, task_id: int, data: TaskUpdate, current_user: User
) -> Task:
    task = await require_task(db, task_id, lock=True)
    if task.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the author can edit this task")
    if task.status in (TaskStatus.DONE, TaskStatus.CANCELLED):
        raise HTTPException(status_code=409, detail="Completed tasks cannot be edited")

    changes = data.model_dump(exclude_unset=True)
    if "deadline" in changes:
        require_future_deadline(changes["deadline"])
    if "assignee_id" in changes and changes["assignee_id"] != task.assignee_id:
        if task.status == TaskStatus.REVIEW:
            raise HTTPException(status_code=409, detail="Assignee is locked in Review")
        if changes["assignee_id"] is not None:
            await require_assignee_capacity(db, changes["assignee_id"])

    for name, value in changes.items():
        setattr(task, name, value)
    await db.commit()
    await db.refresh(task)
    await db.refresh(task, attribute_names=["author", "assignee"])
    return task


async def change_task_status(
    db: AsyncSession, task_id: int, data: TaskStatusUpdate, current_user: User
) -> Task:
    task = await require_task(db, task_id, lock=True)
    if current_user.id not in (task.author_id, task.assignee_id):
        raise HTTPException(status_code=403, detail="Not allowed to change this status")
    if data.status == TaskStatus.CANCELLED and current_user.id != task.author_id:
        raise HTTPException(status_code=403, detail="Only the author can cancel a task")
    apply_status_transition(task, data.status, now=datetime.now(timezone.utc))
    await db.commit()
    await db.refresh(task)
    await db.refresh(task, attribute_names=["author", "assignee"])
    return task


async def delete_task(db: AsyncSession, task_id: int, current_user: User) -> None:
    task = await require_task(db, task_id, lock=True)
    if task.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the author can delete this task")
    if task.status not in (TaskStatus.DONE, TaskStatus.CANCELLED):
        raise HTTPException(status_code=409, detail="Task must be Done or Cancelled")
    await db.delete(task)
    await db.commit()


async def create_comment(
    db: AsyncSession, task_id: int, data: CommentCreate, current_user: User
) -> Comment:
    await require_task(db, task_id)
    comment = Comment(text=data.text, task_id=task_id, author_id=current_user.id)
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return comment


async def list_comments(
    db: AsyncSession, task_id: int, offset: int, limit: int
) -> tuple[list[Comment], int]:
    await require_task(db, task_id)
    total = await db.scalar(
        select(func.count(Comment.id)).where(Comment.task_id == task_id)
    )
    result = await db.scalars(
        select(Comment)
        .where(Comment.task_id == task_id)
        .order_by(Comment.created_at, Comment.id)
        .offset(offset)
        .limit(limit)
    )
    return list(result), total or 0


async def cancel_overdue_tasks(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Cancel overdue active tasks in locked batches; safe to run repeatedly."""
    now = now or datetime.now(timezone.utc)
    cancelled = 0
    while True:
        result = await db.scalars(
            select(Task)
            .where(*overdue_condition(now))
            .order_by(Task.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        batch = list(result)
        if not batch:
            break
        for task in batch:
            apply_status_transition(task, TaskStatus.CANCELLED, now=now, automatic=True)
        await db.commit()
        cancelled += len(batch)
    return cancelled
