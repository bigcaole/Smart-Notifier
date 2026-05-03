from datetime import datetime

import dateparser
from croniter import croniter


WEEKDAY_MAP = {
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 0,
    "周一": 1,
    "周二": 2,
    "周三": 3,
    "周四": 4,
    "周五": 5,
    "周六": 6,
    "周日": 0,
    "周天": 0,
}


def parse_user_datetime(raw: str, timezone_name: str = "Asia/Shanghai") -> datetime:
    text = raw.strip()
    dt = dateparser.parse(
        text,
        languages=["zh", "en"],
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": timezone_name,
            "TO_TIMEZONE": timezone_name,
        },
    )
    if not dt:
        raise ValueError("无法识别该时间，请输入如 明天下午3点 / 2026-05-04 10:00")
    return dt.replace(second=0, microsecond=0)


def parse_human_cron(raw: str) -> str:
    text = raw.strip()
    if text.startswith("每天"):
        hhmm = text.replace("每天", "").strip()
        hour, minute = hhmm.split(":")
        cron_text = f"{int(minute)} {int(hour)} * * *"
    elif text.startswith("每周"):
        parts = text.replace("每周", "").strip().split()
        weekday = int(parts[0])
        hour, minute = parts[1].split(":")
        cron_text = f"{int(minute)} {int(hour)} * * {weekday}"
    else:
        cron_text = text

    if not croniter.is_valid(cron_text):
        raise ValueError("非法 Cron。请使用标准5段格式，例如：0 10 * * *")

    return cron_text


def build_recurring_expr(mode: str, raw: str) -> str:
    text = raw.strip()

    if mode == "weekly":
        # 例: 周一 09:30 或 1 09:30
        day_text, hhmm = text.split()
        weekday = WEEKDAY_MAP.get(day_text)
        if weekday is None:
            raise ValueError("每周格式：周一 09:30（或 1 09:30）")
        hh, mm = hhmm.split(":")
        return f"{int(mm)} {int(hh)} * * {weekday}"

    if mode == "monthly":
        # 例: 15 09:30
        day, hhmm = text.split()
        hh, mm = hhmm.split(":")
        d = int(day)
        if d < 1 or d > 31:
            raise ValueError("每月日期应在 1-31")
        return f"{int(mm)} {int(hh)} {d} * *"

    if mode == "quarterly":
        # 例: 15 09:30 -> 每季度首月(1/4/7/10)的15号
        day, hhmm = text.split()
        hh, mm = hhmm.split(":")
        d = int(day)
        if d < 1 or d > 31:
            raise ValueError("每季度日期应在 1-31")
        return f"{int(mm)} {int(hh)} {d} 1,4,7,10 *"

    if mode == "yearly":
        # 例: 10-01 09:30
        md, hhmm = text.split()
        month, day = md.split("-")
        hh, mm = hhmm.split(":")
        m = int(month)
        d = int(day)
        if m < 1 or m > 12 or d < 1 or d > 31:
            raise ValueError("每年格式：MM-DD HH:MM，例如 10-01 09:30")
        return f"{int(mm)} {int(hh)} {d} {m} *"

    if mode == "interval_days":
        # 例: 3 09:30
        n, hhmm = text.split()
        hh, mm = hhmm.split(":")
        days = int(n)
        if days < 1 or days > 365:
            raise ValueError("间隔天数范围：1-365")
        return f"every_ndays:{days}:{int(hh):02d}:{int(mm):02d}"

    raise ValueError("未知循环模式")
