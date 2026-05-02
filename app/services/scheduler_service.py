from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.task import Task
from app.services.task_service import TaskService


class SchedulerService:
    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)
        self.bot_app: Application | None = None

    def bind_bot(self, bot_app: Application) -> None:
        self.bot_app = bot_app

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _job_id(self, task_id: int) -> str:
        return f"task_{task_id}"

    def remove_task_job(self, task_id: int) -> None:
        self.scheduler.remove_job(self._job_id(task_id)) if self.scheduler.get_job(self._job_id(task_id)) else None

    def schedule_task(self, task: Task) -> None:
        if task.status != "pending":
            return
        self.remove_task_job(task.id)

        if task.is_recurring and task.cron_expr:
            minute, hour, day, month, weekday = task.cron_expr.split()
            trigger = CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=weekday)
            self.scheduler.add_job(self.push_task_reminder, trigger=trigger, args=[task.id], id=self._job_id(task.id), replace_existing=True)
            return

        if task.trigger_time:
            trigger = DateTrigger(run_date=task.trigger_time)
            self.scheduler.add_job(self.push_task_reminder, trigger=trigger, args=[task.id], id=self._job_id(task.id), replace_existing=True)

    async def reload_pending_tasks(self) -> None:
        async with AsyncSessionLocal() as db:
            tasks = await TaskService.list_pending_tasks(db)
            for task in tasks:
                self.schedule_task(task)

    async def push_task_reminder(self, task_id: int) -> None:
        if self.bot_app is None:
            return

        async with AsyncSessionLocal() as db:
            task = await TaskService.get_task(db, task_id)
            if not task or task.status != "pending":
                return

            msg = f"⚠️ 提醒: {task.content}\n📝 备注: {task.remarks or '无'}"
            keyboard = InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton("✅ 已完成 (Done)", callback_data=f"done_{task.id}"),
                    InlineKeyboardButton("⏳ 稍后提醒 (Snooze)", callback_data=f"snooze_{task.id}"),
                ]]
            )
            await self.bot_app.bot.send_message(chat_id=task.chat_id, text=msg, reply_markup=keyboard)


scheduler_service = SchedulerService()
