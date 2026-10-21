from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.models import Task, TaskStatus
from tests.test_tasks_security import make_user, task_payload


@pytest.mark.asyncio
async def test_every_status_transition_matches_the_agreed_table_and_database(api):
    client, sessions = api
    author_id, headers = await make_user(client, "transition_matrix_author")
    allowed = {
        TaskStatus.BACKLOG: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
        TaskStatus.IN_PROGRESS: {TaskStatus.REVIEW, TaskStatus.CANCELLED},
        TaskStatus.REVIEW: {TaskStatus.DONE},
        TaskStatus.DONE: set(),
        TaskStatus.CANCELLED: set(),
    }
    for initial in TaskStatus:
        for target in TaskStatus:
            created = await client.post("/tasks", json=task_payload(), headers=headers)
            assert created.status_code == 201
            task_id = created.json()["id"]
            async with sessions() as db:
                await db.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(
                        status=initial,
                        assignee_id=author_id if target == TaskStatus.DONE else None,
                    )
                )
                await db.commit()

            response = await client.patch(
                f"/tasks/{task_id}/status",
                json={"status": target.value},
                headers=headers,
            )
            expected = target if target in allowed[initial] else initial
            assert response.status_code == (200 if target in allowed[initial] else 409), (
                initial, target, response.text
            )
            async with sessions() as db:
                stored = await db.scalar(select(Task.status).where(Task.id == task_id))
                assert stored == expected, (initial, target)


@pytest.mark.asyncio
async def test_terminal_assignee_and_review_deletion_are_rejected(api):
    client, _ = api
    _, headers = await make_user(client, "terminal_author")
    assignee_id, _ = await make_user(client, "terminal_worker")
    task = (
        await client.post(
            "/tasks", json=task_payload(assignee_id=assignee_id), headers=headers
        )
    ).json()
    path = f"/tasks/{task['id']}"
    for status in ("In Progress", "Review"):
        assert (
            await client.patch(path + "/status", json={"status": status}, headers=headers)
        ).status_code == 200
    assert (await client.delete(path, headers=headers)).status_code == 409
    assert (
        await client.patch(path + "/status", json={"status": "Done"}, headers=headers)
    ).status_code == 200
    assert (
        await client.patch(path, json={"assignee_id": None}, headers=headers)
    ).status_code == 409


@pytest.mark.asyncio
async def test_cancelled_task_frees_assignee_capacity(api):
    client, _ = api
    _, headers = await make_user(client, "cancel_capacity_author")
    assignee_id, _ = await make_user(client, "cancel_capacity_worker")
    task_ids = []
    for _ in range(10):
        response = await client.post(
            "/tasks", json=task_payload(assignee_id=assignee_id), headers=headers
        )
        assert response.status_code == 201
        task_ids.append(response.json()["id"])
    assert (
        await client.post(
            "/tasks", json=task_payload(assignee_id=assignee_id), headers=headers
        )
    ).status_code == 409
    assert (
        await client.patch(
            f"/tasks/{task_ids[0]}/status",
            json={"status": "Cancelled"},
            headers=headers,
        )
    ).status_code == 200
    assert (
        await client.post(
            "/tasks", json=task_payload(assignee_id=assignee_id), headers=headers
        )
    ).status_code == 201


@pytest.mark.asyncio
async def test_full_api_journey_from_registration_to_statistics(api):
    client, _ = api
    owner_id, owner_headers = await make_user(client, "journey_owner")
    worker_id, worker_headers = await make_user(client, "journey_worker")
    created = await client.post(
        "/tasks",
        json={
            "title": "Deliver first feature",
            "description": "End-to-end acceptance test",
            "priority": "High",
            "deadline": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        },
        headers=owner_headers,
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    assert created.json()["author_id"] == owner_id
    assert created.json()["assignee_id"] is None

    assigned = await client.patch(
        f"/tasks/{task_id}", json={"assignee_id": worker_id}, headers=owner_headers
    )
    assert assigned.status_code == 200
    for status in ("In Progress", "Review", "Done"):
        response = await client.patch(
            f"/tasks/{task_id}/status",
            json={"status": status},
            headers=worker_headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == status

    commented = await client.post(
        f"/tasks/{task_id}/comments",
        json={"text": "  Ready for review  "},
        headers=worker_headers,
    )
    assert commented.status_code == 201
    assert commented.json()["author_id"] == worker_id
    assert commented.json()["text"] == "Ready for review"

    listed = await client.get("/tasks", params={"search": "deliver"}, headers=owner_headers)
    comments = await client.get(f"/tasks/{task_id}/comments", headers=owner_headers)
    stats = await client.get("/tasks/stats", headers=owner_headers)
    assert listed.status_code == comments.status_code == stats.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == task_id
    assert comments.json()["total"] == 1
    assert stats.json()["total"] == 1
    assert stats.json()["status_counts"]["Done"] == 1
    assert stats.json()["active_count"] == 0
    assert (await client.delete(f"/tasks/{task_id}", headers=owner_headers)).status_code == 204
    assert (await client.get("/tasks/stats", headers=owner_headers)).json()["total"] == 0
