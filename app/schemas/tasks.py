from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import TaskPriority, TaskStatus


def validate_deadline(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Deadline must include a timezone offset")
    return value


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    priority: TaskPriority = TaskPriority.MEDIUM
    assignee_id: int | None = Field(default=None, gt=0)
    deadline: datetime

    model_config = ConfigDict(extra="forbid")

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value

    @field_validator("deadline")
    @classmethod
    def deadline_has_timezone(cls, value: datetime) -> datetime:
        return validate_deadline(value)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    priority: TaskPriority | None = None
    assignee_id: int | None = Field(default=None, gt=0)
    deadline: datetime | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value

    @field_validator("deadline")
    @classmethod
    def deadline_has_timezone(cls, value: datetime | None) -> datetime | None:
        return validate_deadline(value) if value is not None else None

    @model_validator(mode="after")
    def required_fields_cannot_be_cleared(self):
        for name in ("title", "description", "priority", "deadline"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class TaskStatusUpdate(BaseModel):
    status: TaskStatus

    model_config = ConfigDict(extra="forbid")


class UserBrief(BaseModel):
    id: int
    username: str

    model_config = ConfigDict(from_attributes=True)


class TaskRead(BaseModel):
    id: int
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    author_id: int
    assignee_id: int | None
    author: UserBrief
    assignee: UserBrief | None
    deadline: datetime
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskPage(BaseModel):
    items: list[TaskRead]
    total: int
    offset: int
    limit: int


class TaskStats(BaseModel):
    total: int
    status_counts: dict[str, int]
    priority_counts: dict[str, int]
    overdue_count: int
    active_count: int
