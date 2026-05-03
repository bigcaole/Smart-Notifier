import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.schemas.task import TaskCreate
from app.services.scheduler_service import scheduler_service
from app.services.task_service import TaskService
from app.services.time_parser import build_recurring_expr, parse_user_datetime

STATE_TYPE, STATE_MODE, STATE_TIME, STATE_CONTENT, STATE_REMARKS = range(5)


def _main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📝 新建提醒", callback_data="menu_new")],
            [InlineKeyboardButton("📋 我的任务", callback_data="menu_list")],
            [InlineKeyboardButton("💾 备份与恢复", callback_data="menu_backup")],
        ]
    )


def _quick_time_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("10分钟后", callback_data="quick_10m"), InlineKeyboardButton("30分钟后", callback_data="quick_30m")],
            [InlineKeyboardButton("1小时后", callback_data="quick_1h"), InlineKeyboardButton("明早09:00", callback_data="quick_tomorrow9")],
        ]
    )


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "🤖 欢迎使用智能提醒系统\n点击按钮即可操作，随时 /cancel 取消当前步骤。"
    if update.message:
        await update.message.reply_text(text, reply_markup=_main_menu())
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=_main_menu())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_menu(update, context)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start 主菜单\n/new 新建\n/myid 查看Chat ID\n/ping 连通检查\n/cancel 取消当前流程"
    )


async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"你的 Chat ID: {update.effective_chat.id}")


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with AsyncSessionLocal() as db:
        tasks = await TaskService.list_tasks(db, chat_id=str(update.effective_chat.id))
    await update.message.reply_text(f"✅ 服务正常，你当前有 {len(tasks)} 条任务。")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("已取消当前操作。输入 /menu 可重新开始。")
    return ConversationHandler.END


async def start_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("单次提醒", callback_data="type_once"), InlineKeyboardButton("周期循环", callback_data="type_recurring")]]
    )
    if update.message:
        await update.message.reply_text("请选择提醒类型：", reply_markup=keyboard)
    else:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("请选择提醒类型：", reply_markup=keyboard)
    return STATE_TYPE


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "menu_new":
        return await start_new(update, context)
    if query.data == "menu_list":
        chat_id = str(query.message.chat_id)
        async with AsyncSessionLocal() as db:
            tasks = await TaskService.list_tasks(db, chat_id=chat_id)
        if not tasks:
            await query.edit_message_text("你还没有任务。")
            return ConversationHandler.END
        lines = [f"你的 Chat ID: {chat_id}"]
        for t in tasks[:20]:
            lines.append(f"#{t.id} [{t.status}] {t.content}")
        await query.edit_message_text("\n".join(lines))
        return ConversationHandler.END
    if query.data == "menu_backup":
        async with AsyncSessionLocal() as db:
            tasks = await TaskService.dump_all_tasks(db)
        p = Path("/tmp/backup.json")
        p.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        await query.message.reply_document(document=p.open("rb"), filename="backup.json", caption="这是当前数据备份文件")
        await query.edit_message_text("备份已发送。上传 backup.json 可恢复。")
        return ConversationHandler.END
    return ConversationHandler.END


async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    is_recurring = query.data == "type_recurring"
    context.user_data["is_recurring"] = is_recurring

    if not is_recurring:
        await query.edit_message_text(
            "请输入提醒时间（如：明天下午3点 / 2026-05-04 10:00）\n或点击快捷按钮：",
            reply_markup=_quick_time_keyboard(),
        )
        return STATE_TIME

    mode_keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("每周", callback_data="mode_weekly"), InlineKeyboardButton("每月", callback_data="mode_monthly")],
            [InlineKeyboardButton("每季度", callback_data="mode_quarterly"), InlineKeyboardButton("每年", callback_data="mode_yearly")],
            [InlineKeyboardButton("每隔N天", callback_data="mode_interval_days")],
        ]
    )
    await query.edit_message_text("请选择循环方式：", reply_markup=mode_keyboard)
    return STATE_MODE


async def choose_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    mode = query.data.replace("mode_", "")
    context.user_data["recurring_mode"] = mode

    tips = {
        "weekly": "每周格式：周一 09:30（或 1 09:30）",
        "monthly": "每月格式：15 09:30（每月15号）",
        "quarterly": "每季度格式：15 09:30（每季度首月的15号）",
        "yearly": "每年格式：10-01 09:30（每年10月1日）",
        "interval_days": "每隔N天格式：3 09:30（每隔3天）",
    }
    await query.edit_message_text(tips[mode])
    return STATE_TIME


async def quick_time_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    tz = ZoneInfo(settings.scheduler_timezone)
    now = datetime.now(tz)
    data = query.data
    if data == "quick_10m":
        dt = now + timedelta(minutes=10)
    elif data == "quick_30m":
        dt = now + timedelta(minutes=30)
    elif data == "quick_1h":
        dt = now + timedelta(hours=1)
    else:
        base = now + timedelta(days=1)
        dt = base.replace(hour=9, minute=0, second=0, microsecond=0)
    context.user_data["trigger_time"] = dt
    await query.edit_message_text(f"已选择：{dt.strftime('%Y-%m-%d %H:%M')}，请输入提醒内容")
    return STATE_CONTENT


async def input_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    is_recurring = bool(context.user_data.get("is_recurring"))
    try:
        if is_recurring:
            mode = context.user_data.get("recurring_mode")
            context.user_data["cron_expr"] = build_recurring_expr(mode, raw)
        else:
            dt = parse_user_datetime(raw, settings.scheduler_timezone)
            if dt <= datetime.now(ZoneInfo(settings.scheduler_timezone)):
                await update.message.reply_text("这个时间已经过去，请输入未来时间。")
                return STATE_TIME
            context.user_data["trigger_time"] = dt
    except Exception as exc:
        await update.message.reply_text(f"格式错误：{exc}")
        return STATE_TIME

    await update.message.reply_text("请输入提醒内容（如：给客户发周报）")
    return STATE_CONTENT


async def input_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["content"] = update.message.text.strip()
    await update.message.reply_text("请输入备注；如果没有，请回复：跳过")
    return STATE_REMARKS


async def input_remarks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    remarks = update.message.text.strip()
    if remarks == "跳过":
        remarks = ""
    payload = TaskCreate(
        content=context.user_data["content"],
        remarks=remarks,
        is_recurring=bool(context.user_data.get("is_recurring")),
        trigger_time=context.user_data.get("trigger_time"),
        cron_expr=context.user_data.get("cron_expr"),
        chat_id=str(update.message.chat_id),
    )
    async with AsyncSessionLocal() as db:
        task = await TaskService.create_task(db, payload)
    scheduler_service.schedule_task(task)
    context.user_data.clear()
    await update.message.reply_text(f"✅ 创建成功，任务ID: {task.id}")
    return ConversationHandler.END


async def reminder_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, task_id_text = query.data.split("_", 1)
    task_id = int(task_id_text)
    async with AsyncSessionLocal() as db:
        task = await TaskService.get_task(db, task_id)
        if not task:
            await query.edit_message_text("任务不存在或已删除。")
            return
        if action == "done":
            await TaskService.mark_done(db, task)
            scheduler_service.remove_task_job(task.id)
            await query.edit_message_text("✅ 该任务已确认完成")
            return
        if action == "snooze":
            await TaskService.snooze_task(db, task, minutes=10)
            scheduler_service.schedule_task(task)
            next_time = TaskService.to_local_display(task.trigger_time)
            t = next_time.strftime("%Y-%m-%d %H:%M") if next_time else "稍后"
            await query.edit_message_text(f"⏳ 已稍后提醒，下一次：{t}")


async def restore_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    if not doc or doc.file_name != "backup.json":
        return
    file = await doc.get_file()
    raw = await file.download_as_bytearray()
    try:
        data = json.loads(bytes(raw).decode("utf-8"))
        if not isinstance(data, list):
            raise ValueError("backup.json 内容格式错误")
    except Exception as exc:
        await update.message.reply_text(f"恢复失败：{exc}")
        return
    async with AsyncSessionLocal() as db:
        inserted, updated = await TaskService.restore_from_dump(db, data)
    await scheduler_service.reload_pending_tasks()
    await update.message.reply_text(f"✅ 恢复完成！新增 {inserted} 条，更新 {updated} 条。")


async def setup_bot_commands(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("start", "打开主菜单"),
            BotCommand("menu", "打开主菜单"),
            BotCommand("new", "新建提醒"),
            BotCommand("myid", "查看Chat ID"),
            BotCommand("ping", "检查连接"),
            BotCommand("help", "帮助"),
            BotCommand("cancel", "取消"),
        ]
    )


def build_application(token: str) -> Application:
    app = Application.builder().token(token).post_init(setup_bot_commands).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_router, pattern="^menu_(new|list|backup)$"), CommandHandler("new", start_new)],
        states={
            STATE_TYPE: [CallbackQueryHandler(choose_type, pattern="^type_(once|recurring)$")],
            STATE_MODE: [CallbackQueryHandler(choose_mode, pattern=r"^mode_(weekly|monthly|quarterly|yearly|interval_days)$")],
            STATE_TIME: [
                CallbackQueryHandler(quick_time_pick, pattern=r"^quick_(10m|30m|1h|tomorrow9)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, input_time),
            ],
            STATE_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_content)],
            STATE_REMARKS: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_remarks)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myid", myid_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(reminder_action, pattern=r"^(done|snooze)_\d+$"))
    app.add_handler(MessageHandler(filters.Document.ALL, restore_backup))
    return app
