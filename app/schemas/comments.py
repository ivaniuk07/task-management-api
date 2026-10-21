from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CommentCreate(BaseModel):
    text: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Comment must not be blank")
        return value


class CommentRead(BaseModel):
    id: int
    text: str
    task_id: int
    author_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CommentPage(BaseModel):
    items: list[CommentRead]
    total: int
    offset: int
    limit: int
