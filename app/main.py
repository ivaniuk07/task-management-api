from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import router as auth_router
from app.api.tasks import router as tasks_router
from app.db.session import get_db


app = FastAPI(title="Task Management API")
app.include_router(auth_router)
app.include_router(tasks_router)


@app.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    await db.execute(text("SELECT 1"))
    return {"status": "ok"}
