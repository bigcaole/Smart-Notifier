from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(String(255), nullable=False)
    remarks: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_recurring: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trigger_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    cron_expr: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    snooze_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, default=datetime.utcnow)
