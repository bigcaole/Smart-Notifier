from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.schemas.task import TaskCreate


class TaskService:
    @staticmethod
    async def create_task(db: AsyncSession, payload: TaskCreate) -> Task:
        task = Task(
            content=payload.content,
            remarks=payload.remarks,
            is_recurring=payload.is_recurring,
            trigger_time=payload.trigger_time,
            cron_expr=payload.cron_expr,
            status="pending",
            snooze_count=0,
            chat_id=payload.chat_id,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def list_tasks_by_chat(db: AsyncSession, chat_id: str) -> list[Task]:
        result = await db.execute(select(Task).where(Task.chat_id == chat_id).order_by(Task.created_at.desc()))
        return list(result.scalars().all())

    @staticmethod
    async def list_tasks_by_chat_and_status(db: AsyncSession, chat_id: str, status: str | None) -> list[Task]:
        stmt = select(Task).where(Task.chat_id == chat_id)
        if status:
            stmt = stmt.where(Task.status == status)
        stmt = stmt.order_by(Task.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_pending_tasks(db: AsyncSession) -> list[Task]:
        result = await db.execute(select(Task).where(Task.status == "pending"))
        return list(result.scalars().all())

    @staticmethod
    async def get_task(db: AsyncSession, task_id: int) -> Task | None:
        return await db.get(Task, task_id)

    @staticmethod
    async def mark_done(db: AsyncSession, task: Task) -> Task:
        task.status = "completed"
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def update_status(db: AsyncSession, task: Task, status: str) -> Task:
        task.status = status
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def delete_task(db: AsyncSession, task: Task) -> None:
        await db.delete(task)
        await db.commit()

    @staticmethod
    async def snooze_task(db: AsyncSession, task: Task, minutes: int = 10) -> Task:
        task.snooze_count += 1
        if task.trigger_time is None:
            task.trigger_time = datetime.utcnow() + timedelta(minutes=minutes)
        else:
            task.trigger_time = max(task.trigger_time, datetime.utcnow()) + timedelta(minutes=minutes)
        task.status = "pending"
        await db.commit()
        await db.refresh(task)
        return task

    @staticmethod
    async def dump_all_tasks(db: AsyncSession) -> list[dict]:
        result = await db.execute(select(Task).order_by(Task.id.asc()))
        tasks = result.scalars().all()
        payload: list[dict] = []
        for t in tasks:
            payload.append(
                {
                    "id": t.id,
                    "content": t.content,
                    "remarks": t.remarks,
                    "is_recurring": t.is_recurring,
                    "trigger_time": t.trigger_time.isoformat() if t.trigger_time else None,
                    "cron_expr": t.cron_expr,
                    "status": t.status,
                    "snooze_count": t.snooze_count,
                    "chat_id": t.chat_id,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
            )
        return payload

    @staticmethod
    async def restore_from_dump(db: AsyncSession, tasks: list[dict]) -> tuple[int, int]:
        inserted = 0
        updated = 0
        for row in tasks:
            row_id = row.get("id")
            existing = await db.get(Task, int(row_id)) if row_id is not None else None

            attrs = {
                "content": row.get("content", ""),
                "remarks": row.get("remarks", ""),
                "is_recurring": bool(row.get("is_recurring", False)),
                "trigger_time": datetime.fromisoformat(row["trigger_time"]) if row.get("trigger_time") else None,
                "cron_expr": row.get("cron_expr"),
                "status": row.get("status", "pending"),
                "snooze_count": int(row.get("snooze_count", 0)),
                "chat_id": str(row.get("chat_id", "")),
                "created_at": datetime.fromisoformat(row["created_at"]) if row.get("created_at") else datetime.utcnow(),
            }

            if existing:
                for key, value in attrs.items():
                    setattr(existing, key, value)
                updated += 1
            else:
                item = Task(**attrs)
                if row_id is not None:
                    item.id = int(row_id)
                db.add(item)
                inserted += 1
        await db.commit()
        return inserted, updated
