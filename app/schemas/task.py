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
    reminder_type: str | None = None
    rule_text: str | None = None
    next_run_time: datetime | None = None

    class Config:
        from_attributes = True


class TaskUpdate(BaseModel):
    content: str | None = None
    remarks: str | None = None
    trigger_time: datetime | None = None
    cron_expr: str | None = None
    status: str | None = None
