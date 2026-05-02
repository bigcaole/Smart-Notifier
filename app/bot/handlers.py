import json
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.db.session import AsyncSessionLocal
from app.schemas.task import TaskCreate
from app.services.scheduler_service import scheduler_service
from app.services.task_service import TaskService
from app.services.time_parser import parse_human_cron, parse_user_datetime

STATE_TYPE, STATE_TIME, STATE_CONTENT, STATE_REMARKS = range(4)


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "🤖 欢迎使用智能提醒系统，请选择操作："
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📝 新建提醒", callback_data="menu_new")],
            [InlineKeyboardButton("📋 我的任务", callback_data="menu_list")],
            [InlineKeyboardButton("💾 备份与恢复", callback_data="menu_backup")],
        ]
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_menu(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("已取消当前操作。输入 /menu 可重新开始。")
    return ConversationHandler.END


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "menu_new":
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("单次提醒", callback_data="type_once"), InlineKeyboardButton("周期循环", callback_data="type_recurring")]]
        )
        await query.edit_message_text("请选择提醒类型：", reply_markup=keyboard)
        return STATE_TYPE

    if query.data == "menu_list":
        chat_id = str(query.message.chat_id)
        async with AsyncSessionLocal() as db:
            tasks = await TaskService.list_tasks_by_chat(db, chat_id)
        if not tasks:
            await query.edit_message_text("你还没有任务。输入 /menu 返回主菜单。")
            return ConversationHandler.END
        lines = []
        for t in tasks[:20]:
            lines.append(f"#{t.id} [{t.status}] {t.content}")
        await query.edit_message_text("\n".join(lines))
        return ConversationHandler.END

    if query.data == "menu_backup":
        chat_id = str(query.message.chat_id)
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
    prompt = "请输入 Cron（例如 每天 10:00 或 */5 * * * *）" if is_recurring else "请输入提醒时间（格式：YYYY-MM-DD HH:MM）"
    await query.edit_message_text(prompt)
    return STATE_TIME


async def input_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    is_recurring = bool(context.user_data.get("is_recurring"))
    try:
        if is_recurring:
            context.user_data["cron_expr"] = parse_human_cron(raw)
        else:
            context.user_data["trigger_time"] = parse_user_datetime(raw)
    except Exception as exc:
        await update.message.reply_text(f"格式错误：{exc}\n请重试。")
        return STATE_TIME

    await update.message.reply_text("请输入提醒的核心内容")
    return STATE_CONTENT


async def input_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["content"] = update.message.text.strip()
    await update.message.reply_text("请输入备注信息（如网址/密码），无备注请回复 跳过")
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


def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_router, pattern="^menu_(new|list|backup)$")],
        states={
            STATE_TYPE: [CallbackQueryHandler(choose_type, pattern="^type_(once|recurring)$")],
            STATE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_time)],
            STATE_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_content)],
            STATE_REMARKS: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_remarks)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(reminder_action, pattern=r"^(done|snooze)_\d+$"))
    app.add_handler(MessageHandler(filters.Document.ALL, restore_backup))

    return app
