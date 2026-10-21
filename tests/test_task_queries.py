from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.models import Task, TaskPriority, TaskStatus, User
from app.services.tasks import list_overdue_tasks, task_statistics
from tests.test_tasks_security import make_user, task_payload


@pytest.mark.asyncio
async def test_combined_search_filters_sort_and_second_page(api):
    client, sessions = api
    _, headers = await make_user(client, "query_author")
    assignee_id, _ = await make_user(client, "query_assignee")
    other_id, _ = await make_user(client, "query_other")
    anchor = datetime.now(timezone.utc) + timedelta(days=5)

    async def create(title, description, priority, assignee, days):
        response = await client.post(
            "/tasks",
            json=task_payload(
                title=title,
                description=description,
                priority=priority,
                assignee_id=assignee,
                deadline=(anchor + timedelta(days=days)).isoformat(),
            ),
            headers=headers,
        )
        assert response.status_code == 201
        return response.json()["id"]

    first_id = await create("Alpha launch", "First", "High", assignee_id, 1)
    second_id = await create("Second launch", "aLpHa in description", "High", assignee_id, 2)
    await create("Alpha low", "Wrong priority", "Low", assignee_id, 3)
    await create("Alpha other", "Wrong assignee", "High", other_id, 4)
    await create("No match", "Wrong search", "High", assignee_id, 5)
    async with sessions() as db:
        await db.execute(
            update(Task)
            .where(Task.id.in_([first_id, second_id]))
            .values(status=TaskStatus.IN_PROGRESS)
        )
        await db.commit()

    params = {
        "search": "ALPHA",
        "status": "In Progress",
        "priority": "High",
        "assignee_id": assignee_id,
        "deadline_from": anchor.isoformat(),
        "deadline_to": (anchor + timedelta(days=3)).isoformat(),
        "sort_by": "deadline",
        "sort_order": "desc",
        "limit": 1,
    }
    first_page = (await client.get("/tasks", params={**params, "offset": 0}, headers=headers)).json()
    second_page = (await client.get("/tasks", params={**params, "offset": 1}, headers=headers)).json()
    assert first_page["total"] == second_page["total"] == 2
    assert first_page["items"][0]["id"] == second_id
    assert second_page["items"][0]["id"] == first_id
    assert second_page["offset"] == 1

    assert (await client.get("/tasks", params={"sort_by": "unknown"}, headers=headers)).status_code == 422
    assert (await client.get("/tasks", params={"deadline_from": "2030-01-01T00:00:00"}, headers=headers)).status_code == 422
    assert (await client.get("/tasks", params={"deadline_from": (anchor + timedelta(days=2)).isoformat(), "deadline_to": anchor.isoformat()}, headers=headers)).status_code == 422


@pytest.mark.asyncio
async def test_default_and_explicit_sorting_and_literal_wildcards(api):
    client, sessions = api
    _, headers = await make_user(client, "sort_author")
    anchor = datetime.now(timezone.utc) + timedelta(days=8)
    records = []
    for title, priority, days in (
        ("Low", "Low", 1),
        ("High later", "High", 3),
        ("High sooner 100%", "High", 2),
        ("Medium", "Medium", 4),
    ):
        response = await client.post(
            "/tasks",
            json=task_payload(title=title, priority=priority, deadline=(anchor + timedelta(days=days)).isoformat()),
            headers=headers,
        )
        assert response.status_code == 201
        records.append(response.json())

    default = (await client.get("/tasks", headers=headers)).json()["items"]
    assert [task["title"] for task in default] == ["High sooner 100%", "High later", "Medium", "Low"]
    descending = (await client.get("/tasks", params={"sort_by": "priority", "sort_order": "desc"}, headers=headers)).json()["items"]
    assert [task["priority"] for task in descending] == ["Low", "Medium", "High", "High"]
    by_deadline = (await client.get("/tasks", params={"sort_by": "deadline", "sort_order": "desc"}, headers=headers)).json()["items"]
    assert [task["title"] for task in by_deadline] == ["Medium", "High later", "High sooner 100%", "Low"]
    literal = (await client.get("/tasks", params={"search": "%"}, headers=headers)).json()
    assert literal["total"] == 1
    assert literal["items"][0]["title"] == "High sooner 100%"

    async with sessions() as db:
        for index, task in enumerate(records):
            await db.execute(
                update(Task)
                .where(Task.id == task["id"])
                .values(created_at=anchor - timedelta(days=index + 1))
            )
        await db.commit()
    by_created = (await client.get("/tasks", params={"sort_by": "created_at", "sort_order": "asc"}, headers=headers)).json()["items"]
    assert [task["id"] for task in by_created] == [record["id"] for record in reversed(records)]


@pytest.mark.asyncio
async def test_overdue_and_statistics_share_visibility_and_time_rule(api):
    client, sessions = api
    author_id, author_headers = await make_user(client, "stats_author")
    _, reader_headers = await make_user(client, "stats_reader")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = (
        ("past backlog", TaskStatus.BACKLOG, TaskPriority.HIGH, now - timedelta(days=3)),
        ("past progress", TaskStatus.IN_PROGRESS, TaskPriority.MEDIUM, now - timedelta(days=2)),
        ("past review", TaskStatus.REVIEW, TaskPriority.LOW, now - timedelta(days=1)),
        ("past done", TaskStatus.DONE, TaskPriority.HIGH, now - timedelta(days=1)),
        ("past cancelled", TaskStatus.CANCELLED, TaskPriority.MEDIUM, now - timedelta(days=1)),
        ("future backlog", TaskStatus.BACKLOG, TaskPriority.LOW, now + timedelta(days=1)),
    )
    async with sessions() as db:
        db.add_all(
            Task(title=title, description="", status=state, priority=priority, author_id=author_id, deadline=deadline)
            for title, state, priority, deadline in rows
        )
        await db.commit()

    for headers in (author_headers, reader_headers):
        listing = await client.get("/tasks", headers=headers)
        overdue = await client.get("/tasks/overdue?limit=2&offset=1", headers=headers)
        stats = await client.get("/tasks/stats", headers=headers)
        assert listing.status_code == overdue.status_code == stats.status_code == 200
        assert listing.json()["total"] == 6
        assert overdue.json()["total"] == 3
        assert [item["title"] for item in overdue.json()["items"]] == ["past progress", "past review"]
        assert stats.json() == {
            "total": 6,
            "status_counts": {"Backlog": 2, "In Progress": 1, "Review": 1, "Done": 1, "Cancelled": 1},
            "priority_counts": {"High": 2, "Medium": 2, "Low": 2},
            "overdue_count": 3,
            "active_count": 4,
        }
    assert (await client.get("/tasks/overdue")).status_code == 401
    assert (await client.get("/tasks/stats")).status_code == 401

    # A fixed clock verifies the exact boundary; the live HTTP clock keeps moving.
    async with sessions() as db:
        db.add(Task(title="boundary", description="", author_id=author_id, deadline=now))
        await db.commit()
    async with sessions() as db:
        fixed_overdue, count = await list_overdue_tasks(db, 0, 20, now=now)
        fixed_stats = await task_statistics(db, now=now)
        assert count == 3
        assert all(task.title != "boundary" for task in fixed_overdue)
        assert fixed_stats.overdue_count == 3


@pytest.mark.asyncio
async def test_empty_statistics_include_all_enum_values(api):
    client, _ = api
    _, headers = await make_user(client, "empty_stats_reader")
    response = await client.get("/tasks/stats", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["status_counts"] == {state.value: 0 for state in TaskStatus}
    assert response.json()["priority_counts"] == {priority.value: 0 for priority in TaskPriority}
    assert response.json()["overdue_count"] == response.json()["active_count"] == 0


@pytest.mark.asyncio
async def test_equal_priority_and_deadline_use_id_for_stable_pagination(api):
    client, _ = api
    _, headers = await make_user(client, "stable_order_author")
    deadline = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    ids = []
    for title in ("First", "Second", "Third"):
        response = await client.post(
            "/tasks",
            json=task_payload(title=title, priority="High", deadline=deadline),
            headers=headers,
        )
        assert response.status_code == 201
        ids.append(response.json()["id"])
    pages = [
        (await client.get("/tasks", params={"limit": 1, "offset": offset}, headers=headers)).json()
        for offset in range(3)
    ]
    assert [page["items"][0]["id"] for page in pages] == ids
    assert all(page["total"] == 3 for page in pages)
