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
from app.services.time_parser import parse_human_cron, parse_user_datetime

STATE_TYPE, STATE_TIME, STATE_CONTENT, STATE_REMARKS = range(4)


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
            [
                InlineKeyboardButton("10分钟后", callback_data="quick_10m"),
                InlineKeyboardButton("30分钟后", callback_data="quick_30m"),
            ],
            [
                InlineKeyboardButton("1小时后", callback_data="quick_1h"),
                InlineKeyboardButton("今晚21:00", callback_data="quick_tonight9"),
            ],
            [InlineKeyboardButton("明早09:00", callback_data="quick_tomorrow9")],
        ]
    )


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🤖 欢迎使用智能提醒系统\n"
        "\n"
        "推荐流程：先点【📝 新建提醒】\n"
        "随时可以发送 /cancel 退出当前步骤。"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=_main_menu())
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=_main_menu())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_menu(update, context)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "可用指令：\n"
        "/start - 打开主菜单\n"
        "/menu - 打开主菜单\n"
        "/new - 直接开始新建提醒\n"
        "/myid - 显示你的 Chat ID（Web 查询会用到）\n"
        "/ping - 检查机器人与数据库连通\n"
        "/cancel - 取消当前输入流程"
    )


async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"你的 Chat ID 是：`{update.effective_chat.id}`", parse_mode="Markdown")


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    async with AsyncSessionLocal() as db:
        tasks = await TaskService.list_tasks(db, chat_id=chat_id)
    await update.message.reply_text(f"✅ 连接正常。你当前共有 {len(tasks)} 条任务。")


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
    elif update.callback_query:
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
            await query.edit_message_text("你还没有任务。输入 /menu 返回主菜单。")
            return ConversationHandler.END
        lines = [f"你的 Chat ID: {chat_id}"]
        for t in tasks[:20]:
            lines.append(f"#{t.id} [{t.status}] {t.content}")
        await query.edit_message_text("\n".join(lines))
        return ConversationHandler.END

    if query.data == "menu_backup":
        async with AsyncSessionLocal() as db:
            tasks = await TaskService.dump_all_tasks(db)
        backup_path = Path("/tmp/backup.json")
        backup_path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        await query.message.reply_document(document=backup_path.open("rb"), filename="backup.json", caption="这是当前数据备份文件。")
        await query.edit_message_text("已生成备份并发送。你可以上传 backup.json 进行恢复。")
        return ConversationHandler.END

    return ConversationHandler.END


async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    is_recurring = query.data == "type_recurring"
    context.user_data["is_recurring"] = is_recurring

    if is_recurring:
        await query.edit_message_text(
            "请输入周期规则（支持两种）：\n"
            "1) 自然写法：每天 10:00 / 每周1 09:30\n"
            "2) 标准 Cron：0 10 * * *"
        )
    else:
        await query.edit_message_text(
            "请输入提醒时间。\n"
            "你可以直接输入：明天下午3点、下周五早上9点、2026-05-04 10:00\n"
            "也可以点下面快捷按钮：",
            reply_markup=_quick_time_keyboard(),
        )
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
    elif data == "quick_tonight9":
        dt = now.replace(hour=21, minute=0, second=0, microsecond=0)
        if dt <= now:
            dt = dt + timedelta(days=1)
    elif data == "quick_tomorrow9":
        base = now + timedelta(days=1)
        dt = base.replace(hour=9, minute=0, second=0, microsecond=0)
    else:
        await query.edit_message_text("快捷时间无效，请重新输入。")
        return STATE_TIME

    context.user_data["trigger_time"] = dt
    await query.edit_message_text(f"已选择提醒时间：{dt.strftime('%Y-%m-%d %H:%M')}\n\n请输入提醒的核心内容。")
    return STATE_CONTENT


async def input_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    is_recurring = bool(context.user_data.get("is_recurring"))
    try:
        if is_recurring:
            context.user_data["cron_expr"] = parse_human_cron(raw)
        else:
            dt = parse_user_datetime(raw, settings.scheduler_timezone)
            now = datetime.now(ZoneInfo(settings.scheduler_timezone))
            if dt <= now:
                await update.message.reply_text("这个时间已经过去了，请输入未来时间。")
                return STATE_TIME
            context.user_data["trigger_time"] = dt
    except Exception as exc:
        await update.message.reply_text(f"格式错误：{exc}\n请重试。")
        return STATE_TIME

    await update.message.reply_text("请输入提醒的核心内容（例如：给客户发周报）")
    return STATE_CONTENT


async def input_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["content"] = update.message.text.strip()
    await update.message.reply_text("请输入备注（如网址/账号）。没有就回复：跳过")
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
    await update.message.reply_text(
        f"✅ 创建成功，任务ID: {task.id}\n"
        f"如需在 Web 查看该任务，请使用 Chat ID：{update.message.chat_id}"
    )
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
            next_time = task.trigger_time.strftime("%Y-%m-%d %H:%M") if task.trigger_time else "稍后"
            await query.edit_message_text(f"⏳ 已稍后提醒，下一次提醒时间：{next_time}")


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
    await update.message.reply_text(f"✅ 恢复完成！新增 {inserted} 条，更新 {updated} 条记录。")


async def setup_bot_commands(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("start", "打开主菜单"),
            BotCommand("menu", "打开主菜单"),
            BotCommand("new", "新建提醒"),
            BotCommand("myid", "查看我的Chat ID"),
            BotCommand("ping", "检查连接状态"),
            BotCommand("cancel", "取消当前操作"),
            BotCommand("help", "查看帮助"),
        ]
    )


def build_application(token: str) -> Application:
    app = Application.builder().token(token).post_init(setup_bot_commands).build()

    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(menu_router, pattern="^menu_(new|list|backup)$"),
            CommandHandler("new", start_new),
        ],
        states={
            STATE_TYPE: [CallbackQueryHandler(choose_type, pattern="^type_(once|recurring)$")],
            STATE_TIME: [
                CallbackQueryHandler(quick_time_pick, pattern=r"^quick_(10m|30m|1h|tonight9|tomorrow9)$"),
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
