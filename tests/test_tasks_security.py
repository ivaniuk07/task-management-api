from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import Task, TaskPriority, TaskStatus


def task_payload(**overrides):
    data = {
        "title": "Write API",
        "description": "Task with an authenticated author",
        "priority": "High",
        "deadline": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
    }
    return {**data, **overrides}


async def make_user(client, name):
    registration = await client.post(
        "/auth/register",
        json={
            "username": name,
            "email": f"{name}@example.com",
            "password": "Correct-Horse-91!",
        },
    )
    assert registration.status_code == 201
    login = await client.post(
        "/auth/login", data={"username": name, "password": "Correct-Horse-91!"}
    )
    assert login.status_code == 200
    return registration.json()["id"], {
        "Authorization": f"Bearer {login.json()['access_token']}"
    }


@pytest.mark.asyncio
async def test_every_current_task_write_requires_a_token(api):
    client, _ = api
    requests = [
        await client.post("/tasks", json=task_payload()),
        await client.get("/tasks"),
        await client.get("/tasks/1"),
        await client.patch("/tasks/1", json={"title": "Changed"}),
        await client.patch("/tasks/1/status", json={"status": "In Progress"}),
        await client.delete("/tasks/1"),
        await client.post("/tasks/1/comments", json={"text": "Hello"}),
        await client.get("/tasks/1/comments"),
    ]
    assert [response.status_code for response in requests] == [401] * 8


@pytest.mark.asyncio
async def test_task_uses_token_author_and_shared_status_priority_enums(api):
    client, sessions = api
    user_id, headers = await make_user(client, "task_author")

    spoofed = await client.post(
        "/tasks", json=task_payload(author_id=999), headers=headers
    )
    assert spoofed.status_code == 422
    assert (await client.post("/tasks", json=task_payload(priority="Urgent"), headers=headers)).status_code == 422

    created = await client.post("/tasks", json=task_payload(), headers=headers)
    assert created.status_code == 201
    assert created.json()["author_id"] == user_id
    assert created.json()["status"] == TaskStatus.BACKLOG.value
    assert created.json()["priority"] == TaskPriority.HIGH.value

    async with sessions() as db:
        stored = await db.scalar(select(Task).where(Task.id == created.json()["id"]))
        assert stored is not None
        assert stored.author_id == user_id
        assert stored.status is TaskStatus.BACKLOG
        assert stored.priority is TaskPriority.HIGH


@pytest.mark.asyncio
async def test_comments_use_token_author_and_other_users_cannot_edit_task(api):
    client, _ = api
    author_id, author_headers = await make_user(client, "author_one")
    commenter_id, commenter_headers = await make_user(client, "commenter_one")
    task = (await client.post("/tasks", json=task_payload(), headers=author_headers)).json()

    assert (
        await client.post(
            f"/tasks/{task['id']}/comments",
            json={"text": "Hello", "author_id": author_id},
            headers=commenter_headers,
        )
    ).status_code == 422
    comment = await client.post(
        f"/tasks/{task['id']}/comments",
        json={"text": "Hello"},
        headers=commenter_headers,
    )
    assert comment.status_code == 201
    assert comment.json()["author_id"] == commenter_id
    assert comment.json()["task_id"] == task["id"]

    assert (
        await client.patch(
            f"/tasks/{task['id']}", json={"title": "Hijacked"}, headers=commenter_headers
        )
    ).status_code == 403
    assert (
        await client.patch(
            f"/tasks/{task['id']}/status",
            json={"status": "In Progress"},
            headers=commenter_headers,
        )
    ).status_code == 403
    assert (await client.delete(f"/tasks/{task['id']}", headers=commenter_headers)).status_code == 403


@pytest.mark.asyncio
async def test_assignee_can_progress_status_but_only_author_can_cancel(api):
    client, _ = api
    _, author_headers = await make_user(client, "owner_two")
    assignee_id, assignee_headers = await make_user(client, "worker_two")
    task = (
        await client.post(
            "/tasks", json=task_payload(assignee_id=assignee_id), headers=author_headers
        )
    ).json()

    forbidden_cancel = await client.patch(
        f"/tasks/{task['id']}/status",
        json={"status": "Cancelled"},
        headers=assignee_headers,
    )
    assert forbidden_cancel.status_code == 403

    for new_status in ("In Progress", "Review", "Done"):
        response = await client.patch(
            f"/tasks/{task['id']}/status",
            json={"status": new_status},
            headers=assignee_headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == new_status

    assert (
        await client.patch(
            f"/tasks/{task['id']}", json={"title": "Edited"}, headers=author_headers
        )
    ).status_code == 409
    assert (await client.delete(f"/tasks/{task['id']}", headers=author_headers)).status_code == 204


@pytest.mark.asyncio
async def test_assignee_limit_rejects_eleventh_active_task(api):
    client, _ = api
    _, author_headers = await make_user(client, "owner_three")
    assignee_id, _ = await make_user(client, "worker_three")
    payload = task_payload(assignee_id=assignee_id)

    for _ in range(10):
        assert (await client.post("/tasks", json=payload, headers=author_headers)).status_code == 201
    assert (await client.post("/tasks", json=payload, headers=author_headers)).status_code == 409
