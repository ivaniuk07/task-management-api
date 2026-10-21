from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.security import JWT_ALGORITHM, verify_password
from app.models import User


REGISTRATION = {
    "username": "Sasha_01",
    "email": "Sasha_01@Example.com",
    "password": "Correct-Horse-91!",
}


@pytest.mark.asyncio
async def test_registration_stores_only_a_password_hash(api):
    client, sessions = api
    response = await client.post("/auth/register", json=REGISTRATION)

    assert response.status_code == 201
    assert set(response.json()) == {"id", "username", "email", "created_at"}
    assert response.json()["username"] == "sasha_01"
    assert response.json()["email"] == "sasha_01@example.com"

    async with sessions() as db:
        user = await db.scalar(select(User).where(User.id == response.json()["id"]))
        assert user is not None
        assert user.password_hash != REGISTRATION["password"]
        assert user.password_hash.startswith("$argon2")
        assert verify_password(REGISTRATION["password"], user.password_hash)


@pytest.mark.asyncio
async def test_duplicate_username_or_email_is_rejected_case_insensitively(api):
    client, _ = api
    assert (await client.post("/auth/register", json=REGISTRATION)).status_code == 201

    same_username = {**REGISTRATION, "username": "SASHA_01", "email": "new@example.com"}
    same_email = {**REGISTRATION, "username": "another", "email": "SASHA_01@example.COM"}
    assert (await client.post("/auth/register", json=same_username)).status_code == 409
    assert (await client.post("/auth/register", json=same_email)).status_code == 409


@pytest.mark.asyncio
async def test_login_and_current_user(api):
    client, _ = api
    await client.post("/auth/register", json=REGISTRATION)

    login = await client.post(
        "/auth/login",
        data={"username": "SASHA_01", "password": REGISTRATION["password"]},
    )
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"
    assert login.json()["access_token"]

    current_user = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"}
    )
    assert current_user.status_code == 200
    assert current_user.json()["username"] == "sasha_01"
    assert "password_hash" not in current_user.json()

    wrong_password = await client.post(
        "/auth/login", data={"username": "sasha_01", "password": "wrong"}
    )
    assert wrong_password.status_code == 401


@pytest.mark.asyncio
async def test_missing_invalid_expired_and_unknown_user_tokens_are_rejected(api):
    client, _ = api
    user = (await client.post("/auth/register", json=REGISTRATION)).json()

    expired = jwt.encode(
        {
            "sub": str(user["id"]),
            "type": "access",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        },
        settings.jwt_secret,
        algorithm=JWT_ALGORITHM,
    )
    unknown_user = jwt.encode(
        {
            "sub": "99999999",
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        settings.jwt_secret,
        algorithm=JWT_ALGORITHM,
    )

    assert (await client.get("/auth/me")).status_code == 401
    for token in ("invalid.token.value", expired, unknown_user):
        response = await client.get(
            "/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401
        assert settings.jwt_secret not in response.text
