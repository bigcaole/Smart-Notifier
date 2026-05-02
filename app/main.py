import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse

from app.api.routes import router as api_router
from app.bot.handlers import build_application
from app.core.config import settings
from app.core.security import verify_basic_auth
from app.services.scheduler_service import scheduler_service

logger = logging.getLogger(__name__)
telegram_runner_task: asyncio.Task | None = None


async def run_telegram_polling() -> None:
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN 未配置，跳过 Bot 启动")
        return

    bot_app = build_application(settings.telegram_bot_token)
    scheduler_service.bind_bot(bot_app)

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(poll_interval=settings.telegram_poll_interval)

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global telegram_runner_task

    scheduler_service.start()
    await scheduler_service.reload_pending_tasks()
    telegram_runner_task = asyncio.create_task(run_telegram_polling())

    yield

    if telegram_runner_task:
        telegram_runner_task.cancel()
        with contextlib.suppress(Exception):
            await telegram_runner_task
    scheduler_service.shutdown()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(api_router)


@app.get("/", dependencies=[Depends(verify_basic_auth)])
async def web_index():
    return FileResponse("app/web/index.html")
