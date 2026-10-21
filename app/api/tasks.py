from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import TaskPriority, TaskStatus, User
from app.schemas.comments import CommentCreate, CommentPage, CommentRead
from app.schemas.tasks import TaskCreate, TaskPage, TaskRead, TaskStats, TaskStatusUpdate, TaskUpdate
from app.services.tasks import (
    change_task_status,
    create_comment,
    create_task,
    delete_task,
    list_comments,
    list_overdue_tasks,
    list_tasks,
    require_task,
    task_statistics,
    update_task,
    TaskListFilters,
)


router = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=[Depends(get_current_user)])


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create(
    data: TaskCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_task(db, data, current_user)


@router.get("", response_model=TaskPage)
async def tasks(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=200),
    status: TaskStatus | None = None,
    priority: TaskPriority | None = None,
    assignee_id: int | None = Query(default=None, gt=0),
    deadline_from: datetime | None = None,
    deadline_to: datetime | None = None,
    sort_by: Literal["priority", "created_at", "deadline"] = "priority",
    sort_order: Literal["asc", "desc"] = "asc",
    db: AsyncSession = Depends(get_db),
):
    filters = TaskListFilters(
        search=search,
        status=status,
        priority=priority,
        assignee_id=assignee_id,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items, total = await list_tasks(db, offset, limit, filters)
    return TaskPage(items=items, total=total, offset=offset, limit=limit)


@router.get("/overdue", response_model=TaskPage)
async def overdue(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    items, total = await list_overdue_tasks(db, offset, limit)
    return TaskPage(items=items, total=total, offset=offset, limit=limit)


@router.get("/stats", response_model=TaskStats)
async def stats(db: AsyncSession = Depends(get_db)):
    return await task_statistics(db)


@router.get("/{task_id}", response_model=TaskRead)
async def detail(task_id: int, db: AsyncSession = Depends(get_db)):
    return await require_task(db, task_id)


@router.patch("/{task_id}", response_model=TaskRead)
async def update(
    task_id: int,
    data: TaskUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_task(db, task_id, data, current_user)


@router.patch("/{task_id}/status", response_model=TaskRead)
async def update_status(
    task_id: int,
    data: TaskStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await change_task_status(db, task_id, data, current_user)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await delete_task(db, task_id, current_user)


@router.post("/{task_id}/comments", response_model=CommentRead, status_code=201)
async def comment(
    task_id: int,
    data: CommentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_comment(db, task_id, data, current_user)


@router.get("/{task_id}/comments", response_model=CommentPage)
async def comments(
    task_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    items, total = await list_comments(db, task_id, offset, limit)
    return CommentPage(items=items, total=total, offset=offset, limit=limit)
