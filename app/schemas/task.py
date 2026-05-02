from datetime import datetime

from pydantic import BaseModel


class TaskCreate(BaseModel):
    content: str
    remarks: str = ""
    is_recurring: bool = False
    trigger_time: datetime | None = None
    cron_expr: str | None = None
    chat_id: str


class TaskRead(BaseModel):
    id: int
    content: str
    remarks: str
    is_recurring: bool
    trigger_time: datetime | None
    cron_expr: str | None
    status: str
    snooze_count: int
    chat_id: str
    created_at: datetime

    class Config:
        from_attributes = True
