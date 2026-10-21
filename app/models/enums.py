from enum import StrEnum


class TaskStatus(StrEnum):
    BACKLOG = "Backlog"
    IN_PROGRESS = "In Progress"
    REVIEW = "Review"
    DONE = "Done"
    CANCELLED = "Cancelled"


class TaskPriority(StrEnum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
