from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app.models import Task, TaskStatus
from tests.test_tasks_security import make_user, task_payload


@pytest.mark.asyncio
async def test_task_list_is_visible_to_authenticated_users_and_paginated(api):
    client, _ = api
    author_id, author_headers = await make_user(client, "list_author")
    _, reader_headers = await make_user(client, "list_reader")
    created = []
    for priority in ("Low", "High", "Medium"):
        response = await client.post(
            "/tasks", json=task_payload(priority=priority), headers=author_headers
        )
        assert response.status_code == 201
        created.append(response.json())

    first = await client.get("/tasks?limit=2&offset=0", headers=reader_headers)
    second = await client.get("/tasks?limit=2&offset=2", headers=reader_headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["total"] == second.json()["total"] == 3
    assert [item["priority"] for item in first.json()["items"] + second.json()["items"]] == [
        "High", "Medium", "Low"
    ]
    assert first.json()["items"][0]["author"] == {
        "id": author_id, "username": "list_author"
    }
    assert first.json()["items"][0]["assignee"] is None
    assert len({item["id"] for item in first.json()["items"] + second.json()["items"]}) == 3
    assert (await client.get("/tasks?limit=101", headers=reader_headers)).status_code == 422
    assert (await client.get("/tasks/999999", headers=reader_headers)).status_code == 404


@pytest.mark.asyncio
async def test_update_rejects_protected_fields_and_invalid_deadline(api):
    client, _ = api
    author_id, headers = await make_user(client, "edit_author")
    assignee_id, _ = await make_user(client, "edit_assignee")
    task = (await client.post("/tasks", json=task_payload(), headers=headers)).json()
    path = f"/tasks/{task['id']}"

    for forbidden in ({"author_id": 100}, {"status": "Done"}, {"created_at": "2020-01-01"}):
        assert (await client.patch(path, json=forbidden, headers=headers)).status_code == 422
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert (await client.post("/tasks", json=task_payload(deadline=past), headers=headers)).status_code == 422
    assert (await client.patch(path, json={"deadline": past}, headers=headers)).status_code == 422

    changed = await client.patch(
        path, json={"title": "  Updated title  ", "assignee_id": assignee_id}, headers=headers
    )
    assert changed.status_code == 200
    assert changed.json()["title"] == "Updated title"
    assert changed.json()["author_id"] == author_id
    assert changed.json()["assignee"] == {"id": assignee_id, "username": "edit_assignee"}
    assert changed.json()["updated_at"] >= task["updated_at"]
    assert (await client.get(path, headers=headers)).json()["assignee_id"] == assignee_id


@pytest.mark.asyncio
async def test_status_graph_rejects_backwards_and_terminal_edits(api):
    client, _ = api
    _, headers = await make_user(client, "status_author")
    assignee_id, _ = await make_user(client, "status_worker")
    task = (
        await client.post(
            "/tasks", json=task_payload(assignee_id=assignee_id), headers=headers
        )
    ).json()
    path = f"/tasks/{task['id']}"

    assert (await client.patch(path + "/status", json={"status": "Done"}, headers=headers)).status_code == 409
    for target in ("In Progress", "Review"):
        assert (await client.patch(path + "/status", json={"status": target}, headers=headers)).status_code == 200
    assert (await client.patch(path + "/status", json={"status": "In Progress"}, headers=headers)).status_code == 409
    for assignee_change in ({"assignee_id": None}, {"assignee_id": assignee_id + 1}):
        assert (await client.patch(path, json=assignee_change, headers=headers)).status_code == 409
    assert (await client.get(path, headers=headers)).json()["status"] == "Review"
    assert (await client.patch(path + "/status", json={"status": "Done"}, headers=headers)).status_code == 200
    assert (await client.patch(path, json={"title": "No"}, headers=headers)).status_code == 409
    assert (await client.patch(path + "/status", json={"status": "Review"}, headers=headers)).status_code == 409
    assert (await client.delete(path, headers=headers)).status_code == 204
    assert (await client.get(path, headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_done_requires_assignee_and_unexpired_deadline(api):
    client, sessions = api
    _, headers = await make_user(client, "done_author")
    task = (await client.post("/tasks", json=task_payload(), headers=headers)).json()
    path = f"/tasks/{task['id']}"
    for target in ("In Progress", "Review"):
        assert (await client.patch(path + "/status", json={"status": target}, headers=headers)).status_code == 200
    assert (await client.patch(path + "/status", json={"status": "Done"}, headers=headers)).status_code == 409

    assignee_id, _ = await make_user(client, "done_worker")
    overdue = (datetime.now(timezone.utc) - timedelta(minutes=1))
    async with sessions() as db:
        await db.execute(
            update(Task).where(Task.id == task["id"]).values(assignee_id=assignee_id, deadline=overdue)
        )
        await db.commit()
    assert (await client.patch(path + "/status", json={"status": "Done"}, headers=headers)).status_code == 409
    assert (await client.get(path, headers=headers)).json()["status"] == "Review"


@pytest.mark.asyncio
async def test_cancel_and_deletion_rules(api):
    client, _ = api
    _, headers = await make_user(client, "cancel_author")
    backlog = (await client.post("/tasks", json=task_payload(), headers=headers)).json()
    backlog_path = f"/tasks/{backlog['id']}"
    assert (await client.delete(backlog_path, headers=headers)).status_code == 409
    assert (await client.patch(backlog_path + "/status", json={"status": "Cancelled"}, headers=headers)).status_code == 200
    assert (await client.patch(backlog_path, json={"title": "No"}, headers=headers)).status_code == 409
    assert (await client.patch(backlog_path + "/status", json={"status": "In Progress"}, headers=headers)).status_code == 409
    assert (await client.delete(backlog_path, headers=headers)).status_code == 204

    in_progress = (await client.post("/tasks", json=task_payload(), headers=headers)).json()
    path = f"/tasks/{in_progress['id']}"
    assert (await client.patch(path + "/status", json={"status": "In Progress"}, headers=headers)).status_code == 200
    assert (await client.delete(path, headers=headers)).status_code == 409
    assert (await client.patch(path + "/status", json={"status": "Cancelled"}, headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_completed_task_frees_capacity(api):
    client, sessions = api
    _, headers = await make_user(client, "capacity_author")
    assignee_id, _ = await make_user(client, "capacity_worker")
    tasks = []
    for _ in range(10):
        response = await client.post(
            "/tasks", json=task_payload(assignee_id=assignee_id), headers=headers
        )
        assert response.status_code == 201
        tasks.append(response.json())
    assert (await client.post("/tasks", json=task_payload(assignee_id=assignee_id), headers=headers)).status_code == 409
    async with sessions() as db:
        await db.execute(
            update(Task).where(Task.id == tasks[0]["id"]).values(status=TaskStatus.DONE)
        )
        await db.commit()
    assert (await client.post("/tasks", json=task_payload(assignee_id=assignee_id), headers=headers)).status_code == 201


@pytest.mark.asyncio
async def test_edit_cannot_assign_eleventh_active_task(api):
    client, _ = api
    _, headers = await make_user(client, "edit_capacity_author")
    assignee_id, _ = await make_user(client, "edit_capacity_worker")
    for _ in range(10):
        assert (
            await client.post(
                "/tasks", json=task_payload(assignee_id=assignee_id), headers=headers
            )
        ).status_code == 201
    unassigned = (await client.post("/tasks", json=task_payload(), headers=headers)).json()
    response = await client.patch(
        f"/tasks/{unassigned['id']}",
        json={"assignee_id": assignee_id},
        headers=headers,
    )
    assert response.status_code == 409
    assert (await client.get(f"/tasks/{unassigned['id']}", headers=headers)).json()["assignee_id"] is None


@pytest.mark.asyncio
async def test_comments_are_scoped_paginated_and_validated(api):
    client, _ = api
    _, owner_headers = await make_user(client, "comment_owner")
    commenter_id, commenter_headers = await make_user(client, "comment_writer")
    first_task = (await client.post("/tasks", json=task_payload(), headers=owner_headers)).json()
    second_task = (await client.post("/tasks", json=task_payload(), headers=owner_headers)).json()
    first_path = f"/tasks/{first_task['id']}/comments"
    second_path = f"/tasks/{second_task['id']}/comments"
    for text in ("One", "Two", "Three"):
        response = await client.post(first_path, json={"text": text}, headers=commenter_headers)
        assert response.status_code == 201
        assert response.json()["author_id"] == commenter_id
        assert response.json()["task_id"] == first_task["id"]
    assert (await client.post(first_path, json={"text": "  "}, headers=commenter_headers)).status_code == 422
    assert (await client.post("/tasks/999999/comments", json={"text": "No"}, headers=commenter_headers)).status_code == 404
    assert (await client.get("/tasks/999999/comments", headers=commenter_headers)).status_code == 404
    assert (await client.get(second_path, headers=commenter_headers)).json()["total"] == 0
    page1 = (await client.get(first_path + "?limit=2", headers=commenter_headers)).json()
    page2 = (await client.get(first_path + "?limit=2&offset=2", headers=commenter_headers)).json()
    assert page1["total"] == page2["total"] == 3
    assert [item["text"] for item in page1["items"] + page2["items"]] == ["One", "Two", "Three"]
    assert (await client.get(first_path + "?offset=-1", headers=commenter_headers)).status_code == 422
