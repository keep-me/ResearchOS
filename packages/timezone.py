"""
用户时区工具 - 统一处理面向用户的日期/时间计算

数据库存储依然用 UTC（_utcnow），但所有"今天是哪天""按日期分组"等
面向用户的逻辑，使用本模块提供的函数，保证与用户本地时间一致。
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from packages.config import get_settings


def _user_tz() -> ZoneInfo:
    """获取用户时区对象"""
    return ZoneInfo(get_settings().user_timezone)


def user_now() -> datetime:
    """当前时刻（带用户时区信息）"""
    return datetime.now(_user_tz())


def user_today_start_utc() -> datetime:
    """用户时区的"今天 0:00"，转为 UTC naive datetime（与数据库 created_at 可比）"""
    tz = _user_tz()
    local_now = datetime.now(tz)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    # 转成 UTC，再 strip tzinfo 以匹配数据库中的 naive datetime
    utc_midnight = local_midnight.astimezone(UTC).replace(tzinfo=None)
    return utc_midnight


def user_date_str() -> str:
    """用户时区的今日日期字符串，如 '2026-03-01'"""
    return user_now().strftime("%Y-%m-%d")


def utc_offset_hours() -> float:
    """用户时区相对 UTC 的偏移小时数（如东八区返回 8.0）"""
    tz = _user_tz()
    offset = datetime.now(tz).utcoffset()
    if offset is None:
        return 0.0
    return offset.total_seconds() / 3600


def utc_naive_to_user_date(value: datetime) -> date:
    """Convert a UTC-naive or UTC-aware datetime to the user's local date."""

    tz = _user_tz()
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return dt.astimezone(tz).date()


def user_date_range_to_utc_bounds(
    start_date: date | None,
    end_date: date | None,
) -> tuple[datetime | None, datetime | None]:
    """
    将用户本地日期范围转换为 UTC naive datetime 边界。

    返回值语义：
    - start_utc: `>=`
    - end_utc: `<`（即 end_date 对应本地日期的次日零点）
    """
    tz = _user_tz()
    start_utc: datetime | None = None
    end_utc: datetime | None = None

    if start_date is not None:
        start_local = datetime(
            start_date.year,
            start_date.month,
            start_date.day,
            tzinfo=tz,
        )
        start_utc = start_local.astimezone(UTC).replace(tzinfo=None)

    if end_date is not None:
        end_local = datetime(
            end_date.year,
            end_date.month,
            end_date.day,
            tzinfo=tz,
        ) + timedelta(days=1)
        end_utc = end_local.astimezone(UTC).replace(tzinfo=None)

    return start_utc, end_utc
