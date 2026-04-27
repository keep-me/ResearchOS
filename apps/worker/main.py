"""
ResearchOS Worker - 智能定时任务调度（UTC 时间 + 闲时处理）
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from threading import Event, current_thread, main_thread
from typing import TextIO

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from packages.ai.ops.daily_runner import (
    run_daily_arxiv_trends,
    run_daily_brief,
    run_topic_ingest,
    run_weekly_graph_maintenance,
)
from packages.ai.ops.idle_processor import start_idle_processor, stop_idle_processor
from packages.config import get_settings
from packages.logging_setup import setup_logging
from packages.storage.bootstrap import bootstrap_worker_runtime
from packages.storage.db import session_scope
from packages.storage.repositories import TopicRepository

setup_logging()
logger = logging.getLogger(__name__)

settings = get_settings()
stop_event = Event()
_RETRY_MAX = settings.worker_retry_max
_RETRY_DELAY = settings.worker_retry_base_delay
_WORKER_RUNTIME_DIR = settings.pdf_storage_root.parent.resolve()
_HEALTH_FILE = _WORKER_RUNTIME_DIR / "worker_heartbeat"
_LOCK_FILE = _WORKER_RUNTIME_DIR / "worker.lock"
_LOCK_HANDLE: TextIO | None = None


def _acquire_single_instance_lock() -> bool:
    """同一份数据目录只允许一个 worker 调度器运行。"""
    global _LOCK_HANDLE

    if _LOCK_HANDLE is not None:
        return True

    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _LOCK_FILE.exists():
        _LOCK_FILE.write_text("0\n", encoding="utf-8")

    handle = _LOCK_FILE.open("r+", encoding="utf-8")
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False

    handle.seek(0)
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    _LOCK_HANDLE = handle
    return True


def _release_single_instance_lock() -> None:
    global _LOCK_HANDLE

    if _LOCK_HANDLE is None:
        return

    try:
        _LOCK_HANDLE.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(_LOCK_HANDLE.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(_LOCK_HANDLE.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        try:
            _LOCK_HANDLE.close()
        finally:
            _LOCK_HANDLE = None


atexit.register(_release_single_instance_lock)


def _write_heartbeat() -> None:
    """写入心跳文件供外部健康检查"""
    try:
        _HEALTH_FILE.write_text(str(time.time()))
    except OSError:
        pass


def _retry_with_backoff(fn, *args, max_retries: int = 3, base_delay: float = 5.0, **kwargs):
    """带指数退避的重试执行"""
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                "Attempt %d/%d failed: %s — retrying in %.0fs",
                attempt + 1,
                max_retries,
                e,
                delay,
            )
            time.sleep(delay)


def _should_run(freq: str, time_utc: int, hour: int, weekday: int) -> bool:
    """判断当前 UTC 小时是否匹配主题的调度规则"""
    if freq == "daily":
        return hour == time_utc
    if freq == "twice_daily":
        return hour == time_utc or hour == (time_utc + 12) % 24
    if freq == "weekdays":
        return hour == time_utc and weekday < 5
    if freq == "weekly":
        return hour == time_utc and weekday == 0
    return False


def _cron_display(expr: str, *, user_timezone: str) -> str:
    parts = str(expr or "").split()
    if len(parts) != 5:
        return f"UTC {expr}，{user_timezone} 计算中"
    minute, hour, _day, _month, weekday = parts
    if not minute.isdigit() or not hour.isdigit():
        return f"UTC {expr}，{user_timezone} 计算中"
    utc_dt = datetime(2026, 1, 4, int(hour), int(minute), tzinfo=timezone.utc)
    try:
        local_dt = utc_dt.astimezone(ZoneInfo(user_timezone))
    except Exception:
        local_dt = utc_dt.astimezone(ZoneInfo("Asia/Shanghai"))
        user_timezone = "Asia/Shanghai"
    weekday_label = ""
    if weekday not in {"*", "?"}:
        weekday_label = f"，cron weekday={weekday}"
    return f"UTC {int(hour):02d}:{int(minute):02d}{weekday_label}，{user_timezone} {local_dt:%H:%M}"


def topic_dispatch_job() -> None:
    """每小时执行：检查哪些主题需要在当前小时触发"""
    now = datetime.now(timezone.utc)
    hour = now.hour
    weekday = now.weekday()  # 0=Monday

    with session_scope() as session:
        topics = TopicRepository(session).list_topics(enabled_only=True, kind="subscription")
        candidates = []
        for t in topics:
            freq = getattr(t, "schedule_frequency", "daily")
            time_utc = getattr(t, "schedule_time_utc", 21)
            if _should_run(freq, time_utc, hour, weekday):
                candidates.append({"id": t.id, "name": t.name})

    if not candidates:
        logger.info(
            "topic_dispatch: UTC %02d, weekday %d — no topics scheduled",
            hour,
            weekday,
        )
        return

    logger.info(
        "topic_dispatch: triggering %d topic(s): %s",
        len(candidates),
        ", ".join(c["name"] for c in candidates),
    )
    for c in candidates:
        try:
            result = _retry_with_backoff(
                run_topic_ingest, c["id"], max_retries=_RETRY_MAX, base_delay=_RETRY_DELAY
            )
            logger.info(
                "topic %s done: inserted=%s, processed=%s",
                c["name"],
                result.get("inserted", 0) if result else 0,
                result.get("processed", 0) if result else 0,
            )
        except Exception:
            logger.exception("topic_dispatch failed for %s", c["name"])
    _write_heartbeat()


def brief_job() -> None:
    """每日简报任务，实际触发时间由 DAILY_CRON 配置控制。"""
    logger.info("📮 开始生成每日简报...")
    try:
        result = _retry_with_backoff(
            run_daily_brief, max_retries=_RETRY_MAX, base_delay=_RETRY_DELAY
        )
        logger.info(
            "✅ 每日简报生成完成：saved=%s, email_sent=%s",
            result.get("saved_path", "N/A") if result else "N/A",
            result.get("email_sent", False) if result else False,
        )
    except Exception:
        logger.exception("Daily brief job failed after retries")
    _write_heartbeat()


def dashboard_trend_job() -> None:
    """首页 arXiv 趋势预计算任务。"""
    logger.info("📈 开始预计算首页 arXiv 子域趋势...")
    try:
        result = _retry_with_backoff(
            run_daily_arxiv_trends,
            max_retries=_RETRY_MAX,
            base_delay=_RETRY_DELAY,
        )
        completed = [
            item
            for item in (result or {}).get("subdomains", [])
            if isinstance(item, dict) and str(item.get("status") or "") == "ok"
        ]
        logger.info("✅ 首页趋势预计算完成：%d 个子域", len(completed))
    except Exception:
        logger.exception("Dashboard trend job failed after retries")
    _write_heartbeat()


def weekly_graph_job() -> None:
    logger.info("Starting weekly graph job")
    try:
        _retry_with_backoff(
            run_weekly_graph_maintenance, max_retries=_RETRY_MAX, base_delay=_RETRY_DELAY
        )
    except Exception:
        logger.exception("Weekly graph job failed after retries")
    _write_heartbeat()


def run_worker() -> None:
    """
    Worker 主函数 - UTC 时间智能调度。具体 cron 以 Settings 为准，日志按 user_timezone 展示。
    """
    if not _acquire_single_instance_lock():
        logger.warning("检测到已有 worker 正在运行，当前实例退出：%s", _LOCK_FILE)
        return

    bootstrap_worker_runtime()

    scheduler = BlockingScheduler(timezone="UTC")

    settings = get_settings()

    # 每整点检查主题调度（UTC 时间）
    scheduler.add_job(
        topic_dispatch_job,
        trigger=CronTrigger(minute=0),
        id="topic_dispatch",
        replace_existing=True,
    )
    logger.info("✅ 已添加：主题分发任务（每小时整点，UTC）")

    dashboard_trend_cron = getattr(settings, "dashboard_trend_cron", None)
    daily_cron = getattr(settings, "daily_cron", "0 21 * * *")
    weekly_cron = getattr(settings, "weekly_cron", "0 22 * * 0")
    user_timezone = getattr(settings, "user_timezone", "Asia/Shanghai")

    if dashboard_trend_cron:
        trend_trigger = CronTrigger.from_crontab(dashboard_trend_cron)
        scheduler.add_job(
            dashboard_trend_job,
            trigger=trend_trigger,
            id="dashboard_trend",
            replace_existing=True,
        )
        logger.info(
            "✅ 已添加：首页趋势预计算任务（%s）",
            _cron_display(dashboard_trend_cron, user_timezone=user_timezone),
        )

    daily_trigger = CronTrigger.from_crontab(daily_cron)
    scheduler.add_job(
        brief_job,
        trigger=daily_trigger,
        id="daily_brief",
        replace_existing=True,
    )
    logger.info(
        "✅ 已添加：每日简报任务（%s）",
        _cron_display(daily_cron, user_timezone=user_timezone),
    )

    weekly_trigger = CronTrigger.from_crontab(weekly_cron)
    scheduler.add_job(
        weekly_graph_job,
        trigger=weekly_trigger,
        id="weekly_graph",
        replace_existing=True,
    )
    logger.info("✅ 已添加：每周图谱维护任务（%s）", _cron_display(weekly_cron, user_timezone=user_timezone))

    # 优雅关闭
    def _graceful_stop(*_: object) -> None:
        logger.info("收到终止信号，正在关闭...")
        stop_event.set()
        stop_idle_processor()  # 停止闲时处理器
        scheduler.shutdown(wait=False)
        logger.info("Worker 已关闭")
        _release_single_instance_lock()

    if current_thread() is main_thread():
        signal.signal(signal.SIGINT, _graceful_stop)
        signal.signal(signal.SIGTERM, _graceful_stop)
    else:
        logger.info("Worker 运行在线程中，跳过 signal 注册")

    # 写入初始心跳
    _write_heartbeat()

    # 启动闲时处理器
    logger.info("🤖 启动闲时自动处理器...")
    start_idle_processor()

    # 启动调度器
    logger.info("🚀 Worker 启动完成 - UTC 智能调度 + 闲时处理")
    logger.info("=" * 60)
    logger.info("调度时间表（UTC → %s）:", user_timezone)
    logger.info("  • 主题抓取：每小时整点 → 每小时整点")
    if dashboard_trend_cron:
        logger.info("  • 首页趋势：%s", _cron_display(dashboard_trend_cron, user_timezone=user_timezone))
    logger.info("  • 每日简报：%s", _cron_display(daily_cron, user_timezone=user_timezone))
    logger.info("  • 每周图谱：%s", _cron_display(weekly_cron, user_timezone=user_timezone))
    logger.info("  • 闲时处理：全天自动检测 → 全天自动检测")
    logger.info("=" * 60)

    try:
        scheduler.start()
    finally:
        stop_idle_processor()
        _release_single_instance_lock()


if __name__ == "__main__":
    run_worker()
