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

(
    STATE_TYPE,
    STATE_MODE,
    STATE_TIME,
    STATE_CONTENT,
    STATE_REMARKS,
    STATE_EDIT_FIELD,
    STATE_EDIT_VALUE,
) = range(7)


def _main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📝 新建提醒", callback_data="menu_new")],
            [InlineKeyboardButton("📋 我的任务", callback_data="menu_list")],
            [InlineKeyboardButton("🧹 消息清理", callback_data="menu_clean")],
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


def _task_actions(task_id: int, completed: bool) -> InlineKeyboardMarkup:
    done_btn = InlineKeyboardButton("✅ 完成", callback_data=f"done_{task_id}")
    snooze_btn = InlineKeyboardButton("⏳ 稍后", callback_data=f"snooze_{task_id}")
    edit_btn = InlineKeyboardButton("✏️ 修改", callback_data=f"task_edit_{task_id}")
    del_btn = InlineKeyboardButton("🗑️ 删除", callback_data=f"task_del_{task_id}")
    if completed:
        return InlineKeyboardMarkup([[edit_btn, del_btn]])
    return InlineKeyboardMarkup([[done_btn, snooze_btn], [edit_btn, del_btn]])


def _cleanup_ttl(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> int:
    cfg = context.bot_data.setdefault("cleanup_ttl", {})
    return int(cfg.get(str(chat_id), 60))


def _set_cleanup_ttl(context: ContextTypes.DEFAULT_TYPE, chat_id: int, ttl: int) -> None:
    cfg = context.bot_data.setdefault("cleanup_ttl", {})
    cfg[str(chat_id)] = ttl


async def _delete_later(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, ttl: int) -> None:
    if ttl <= 0:
        return
    await asyncio.sleep(ttl)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def _send_temp(chat_id: int, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    ttl = _cleanup_ttl(context, chat_id)
    context.application.create_task(_delete_later(context, chat_id, msg.message_id, ttl))


async def _track_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        ttl = _cleanup_ttl(context, update.effective_chat.id)
        context.application.create_task(_delete_later(context, update.effective_chat.id, update.message.message_id, ttl))


def _fmt_task(task) -> str:
    local_time = TaskService.to_local_display(task.trigger_time)
    t = local_time.strftime("%Y-%m-%d %H:%M") if local_time else "-"
    rtype = "周期" if task.is_recurring else "单次"
    cron = task.cron_expr or "-"
    return (
        f"ID: {task.id}\n"
        f"ChatID: {task.chat_id}\n"
        f"内容: {task.content}\n"
        f"提醒时间: {t}\n"
        f"循环规则: {cron}\n"
        f"提醒类型: {rtype}\n"
        f"状态: {task.status}"
    )


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "🤖 欢迎使用智能提醒系统\n请选择操作："
    if update.message:
        await _send_temp(update.effective_chat.id, context, text, _main_menu())
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=_main_menu())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_menu(update, context)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _track_user_message(update, context)
    await _send_temp(
        update.effective_chat.id,
        context,
        "左侧命令菜单可直接点选：/start /new /myid /ping /cancel。\n新建与修改都支持按钮化流程。",
    )


async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _track_user_message(update, context)
    await _send_temp(update.effective_chat.id, context, f"你的 Chat ID: {update.effective_chat.id}")


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _track_user_message(update, context)
    async with AsyncSessionLocal() as db:
        tasks = await TaskService.list_tasks(db, chat_id=str(update.effective_chat.id))
    await _send_temp(update.effective_chat.id, context, f"✅ 服务正常，你当前有 {len(tasks)} 条任务。")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _track_user_message(update, context)
    context.user_data.clear()
    await _send_temp(update.effective_chat.id, context, "已取消当前操作。")
    return ConversationHandler.END


async def start_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("单次提醒", callback_data="type_once"), InlineKeyboardButton("周期循环", callback_data="type_recurring")]]
    )
    if update.message:
        await _send_temp(update.effective_chat.id, context, "请选择提醒类型：", keyboard)
    else:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("请选择提醒类型：", reply_markup=keyboard)
    return STATE_TYPE


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat_id)

    if query.data == "menu_new":
        return await start_new(update, context)

    if query.data == "menu_list":
        async with AsyncSessionLocal() as db:
            tasks = await TaskService.list_tasks(db, chat_id=chat_id)
        if not tasks:
            await query.edit_message_text("你还没有任务。")
            return ConversationHandler.END
        await query.edit_message_text(f"共 {len(tasks)} 条任务，详情如下：")
        for t in tasks[:20]:
            await context.bot.send_message(chat_id=int(chat_id), text=_fmt_task(t), reply_markup=_task_actions(t.id, t.status == "completed"))
        return ConversationHandler.END

    if query.data == "menu_clean":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("30秒", callback_data="clean_set_30"), InlineKeyboardButton("60秒", callback_data="clean_set_60")],
                [InlineKeyboardButton("5分钟", callback_data="clean_set_300"), InlineKeyboardButton("关闭自动清理", callback_data="clean_set_0")],
            ]
        )
        await query.edit_message_text("请选择自动清理时长（提醒消息不会自动清理）：", reply_markup=kb)
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


async def cleanup_setter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    ttl = int(query.data.split("_")[-1])
    _set_cleanup_ttl(context, query.message.chat_id, ttl)
    if ttl == 0:
        await query.edit_message_text("已关闭自动清理。")
    else:
        await query.edit_message_text(f"已设置自动清理：{ttl} 秒")


async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    is_recurring = query.data == "type_recurring"
    context.user_data["is_recurring"] = is_recurring

    if not is_recurring:
        await query.edit_message_text("请输入提醒时间（如：明天下午3点 / 2026-05-04 10:00）\n或点快捷按钮：", reply_markup=_quick_time_keyboard())
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
        "weekly": "输入：周一 09:30（或 1 09:30）",
        "monthly": "输入：15 09:30（每月15号）",
        "quarterly": "输入：15 09:30（每季度首月15号）",
        "yearly": "输入：10-01 09:30",
        "interval_days": "输入：3 09:30（每隔3天）",
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
    await _track_user_message(update, context)
    raw = update.message.text.strip()
    is_recurring = bool(context.user_data.get("is_recurring"))
    try:
        if is_recurring:
            mode = context.user_data.get("recurring_mode")
            context.user_data["cron_expr"] = build_recurring_expr(mode, raw)
        else:
            dt = parse_user_datetime(raw, settings.scheduler_timezone)
            if dt <= datetime.now(ZoneInfo(settings.scheduler_timezone)):
                await _send_temp(update.effective_chat.id, context, "这个时间已经过去，请输入未来时间。")
                return STATE_TIME
            context.user_data["trigger_time"] = dt
    except Exception as exc:
        await _send_temp(update.effective_chat.id, context, f"格式错误：{exc}")
        return STATE_TIME

    await _send_temp(update.effective_chat.id, context, "请输入提醒内容（如：给客户发周报）")
    return STATE_CONTENT


async def input_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _track_user_message(update, context)
    context.user_data["content"] = update.message.text.strip()
    await _send_temp(update.effective_chat.id, context, "请输入备注；如果没有，请回复：跳过")
    return STATE_REMARKS


async def input_remarks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _track_user_message(update, context)
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
    await _send_temp(update.effective_chat.id, context, f"✅ 创建成功\n{_fmt_task(task)}")
    return ConversationHandler.END


async def start_edit_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[-1])
    context.user_data["edit_task_id"] = task_id
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("内容", callback_data="editf_content"), InlineKeyboardButton("备注", callback_data="editf_remarks")],
            [InlineKeyboardButton("提醒时间", callback_data="editf_time"), InlineKeyboardButton("状态", callback_data="editf_status")],
            [InlineKeyboardButton("循环规则", callback_data="editf_cron")],
        ]
    )
    await query.edit_message_text(f"修改任务 #{task_id}：请选择要修改的字段", reply_markup=kb)
    return STATE_EDIT_FIELD


async def choose_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    field = query.data.replace("editf_", "")
    context.user_data["edit_field"] = field
    tips = {
        "content": "请输入新的内容",
        "remarks": "请输入新的备注",
        "time": "请输入新的提醒时间（如 2026-05-04 10:00）",
        "status": "请输入状态：pending 或 completed",
        "cron": "请输入新的循环规则（Cron或 every_ndays:3:09:30）",
    }
    await query.edit_message_text(tips[field])
    return STATE_EDIT_VALUE


async def apply_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _track_user_message(update, context)
    task_id = context.user_data.get("edit_task_id")
    field = context.user_data.get("edit_field")
    raw = update.message.text.strip()
    if not task_id or not field:
        await _send_temp(update.effective_chat.id, context, "编辑状态丢失，请重新进入任务列表。")
        return ConversationHandler.END

    kwargs = {}
    try:
        if field == "content":
            kwargs["content"] = raw
        elif field == "remarks":
            kwargs["remarks"] = raw
        elif field == "time":
            kwargs["trigger_time"] = parse_user_datetime(raw, settings.scheduler_timezone)
        elif field == "status":
            if raw not in {"pending", "completed"}:
                raise ValueError("状态只能是 pending 或 completed")
            kwargs["status"] = raw
        elif field == "cron":
            kwargs["cron_expr"] = raw
    except Exception as exc:
        await _send_temp(update.effective_chat.id, context, f"输入无效：{exc}")
        return STATE_EDIT_VALUE

    async with AsyncSessionLocal() as db:
        task = await TaskService.get_task(db, int(task_id))
        if not task:
            await _send_temp(update.effective_chat.id, context, "任务不存在")
            return ConversationHandler.END
        updated = await TaskService.update_task_fields(db, task, **kwargs)

    if updated.status == "pending":
        scheduler_service.schedule_task(updated)
    else:
        scheduler_service.remove_task_job(updated.id)

    await _send_temp(update.effective_chat.id, context, f"✅ 任务已更新\n{_fmt_task(updated)}")
    context.user_data.pop("edit_task_id", None)
    context.user_data.pop("edit_field", None)
    return ConversationHandler.END


async def task_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[-1])
    async with AsyncSessionLocal() as db:
        task = await TaskService.get_task(db, task_id)
        if not task:
            await query.edit_message_text("任务不存在")
            return
        await TaskService.delete_task(db, task)
    scheduler_service.remove_task_job(task_id)
    await query.edit_message_text(f"🗑️ 任务 #{task_id} 已删除")


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
    await _track_user_message(update, context)
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
        await _send_temp(update.effective_chat.id, context, f"恢复失败：{exc}")
        return
    async with AsyncSessionLocal() as db:
        inserted, updated = await TaskService.restore_from_dump(db, data)
    await scheduler_service.reload_pending_tasks()
    await _send_temp(update.effective_chat.id, context, f"✅ 恢复完成！新增 {inserted} 条，更新 {updated} 条。")


async def setup_bot_commands(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("start", "打开主菜单"),
            BotCommand("new", "新建提醒"),
            BotCommand("myid", "查看Chat ID"),
            BotCommand("ping", "检查连接"),
            BotCommand("cancel", "取消"),
            BotCommand("help", "帮助"),
        ]
    )


def build_application(token: str) -> Application:
    app = Application.builder().token(token).post_init(setup_bot_commands).build()

    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(menu_router, pattern="^menu_(new|list|backup|clean)$"),
            CommandHandler("new", start_new),
            CallbackQueryHandler(start_edit_task, pattern=r"^task_edit_\d+$"),
        ],
        states={
            STATE_TYPE: [CallbackQueryHandler(choose_type, pattern="^type_(once|recurring)$")],
            STATE_MODE: [CallbackQueryHandler(choose_mode, pattern=r"^mode_(weekly|monthly|quarterly|yearly|interval_days)$")],
            STATE_TIME: [
                CallbackQueryHandler(quick_time_pick, pattern=r"^quick_(10m|30m|1h|tomorrow9)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, input_time),
            ],
            STATE_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_content)],
            STATE_REMARKS: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_remarks)],
            STATE_EDIT_FIELD: [CallbackQueryHandler(choose_edit_field, pattern=r"^editf_(content|remarks|time|status|cron)$")],
            STATE_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, apply_edit_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myid", myid_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(cleanup_setter, pattern=r"^clean_set_(0|30|60|300)$"))
    app.add_handler(CallbackQueryHandler(task_delete, pattern=r"^task_del_\d+$"))
    app.add_handler(CallbackQueryHandler(reminder_action, pattern=r"^(done|snooze)_\d+$"))
    app.add_handler(MessageHandler(filters.Document.ALL, restore_backup))
    return app


import asyncio
