from datetime import datetime
from zoneinfo import ZoneInfo

import dateparser
from croniter import croniter


def parse_user_datetime(raw: str, timezone_name: str = "Asia/Shanghai") -> datetime:
    text = raw.strip()
    dt = dateparser.parse(
        text,
        languages=["zh", "en"],
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )
    if not dt:
        raise ValueError("无法识别该时间，请使用如 '明天下午3点' 或 '2026-05-04 10:00' 的格式")
    dt = dt.replace(second=0, microsecond=0)
    tz = ZoneInfo(timezone_name)
    return dt.replace(tzinfo=tz)


def parse_human_cron(raw: str) -> str:
    text = raw.strip()
    if text.startswith("每天"):
        hhmm = text.replace("每天", "").strip()
        hour, minute = hhmm.split(":")
        cron_text = f"{int(minute)} {int(hour)} * * *"
    elif text.startswith("每周"):
        # 例：每周1 10:30
        parts = text.replace("每周", "").strip().split()
        weekday = int(parts[0])
        hour, minute = parts[1].split(":")
        cron_text = f"{int(minute)} {int(hour)} * * {weekday}"
    else:
        cron_text = text

    if not croniter.is_valid(cron_text):
        raise ValueError("非法 Cron。请使用标准5段格式，例如：0 10 * * *")

    return cron_text
