import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url

from app.db.session import get_db
from app.main import app


@pytest_asyncio.fixture
async def api() -> AsyncIterator[tuple[AsyncClient, async_sessionmaker]]:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated migrated PostgreSQL test database")

    database_name = make_url(database_url).database or ""
    if not database_name.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point to a database ending in _test")

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            sessions = async_sessionmaker(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )

            async def override_get_db():
                async with sessions() as session:
                    try:
                        yield session
                    except Exception:
                        await session.rollback()
                        raise

            app.dependency_overrides[get_db] = override_get_db
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    yield client, sessions
            finally:
                app.dependency_overrides.pop(get_db, None)
                await transaction.rollback()
    finally:
        await engine.dispose()
