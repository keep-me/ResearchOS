from __future__ import annotations

import types
from datetime import UTC, datetime

import pytest

from apps.worker import main as worker_main


class _FakeCronTrigger:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    @classmethod
    def from_crontab(cls, expr: str) -> _FakeCronTrigger:
        return cls(expr=expr)


class _FakeScheduler:
    def __init__(self, *, timezone: str):
        self.timezone = timezone
        self.jobs: list[dict[str, object]] = []
        self.started = False
        self.stopped = False

    def add_job(self, func, *, trigger, id: str, replace_existing: bool) -> None:
        self.jobs.append(
            {
                "func": func,
                "trigger": trigger,
                "id": id,
                "replace_existing": replace_existing,
            }
        )

    def shutdown(self, *, wait: bool = False) -> None:
        self.stopped = wait

    def start(self) -> None:
        self.started = True


def test_worker_registers_automatic_maintenance_and_brief_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {
        "idle_start": 0,
        "idle_stop": 0,
        "released": 0,
        "heartbeats": 0,
    }
    scheduler = _FakeScheduler(timezone="UTC")

    monkeypatch.setattr(worker_main, "_acquire_single_instance_lock", lambda: True)
    monkeypatch.setattr(
        worker_main,
        "_release_single_instance_lock",
        lambda: observed.__setitem__("released", int(observed["released"]) + 1),
    )
    monkeypatch.setattr(
        worker_main,
        "_write_heartbeat",
        lambda: observed.__setitem__("heartbeats", int(observed["heartbeats"]) + 1),
    )
    monkeypatch.setattr(
        worker_main,
        "start_idle_processor",
        lambda: observed.__setitem__("idle_start", int(observed["idle_start"]) + 1),
    )
    monkeypatch.setattr(
        worker_main,
        "stop_idle_processor",
        lambda: observed.__setitem__("idle_stop", int(observed["idle_stop"]) + 1),
    )
    monkeypatch.setattr(worker_main, "BlockingScheduler", lambda timezone: scheduler)
    monkeypatch.setattr(worker_main, "CronTrigger", _FakeCronTrigger)
    monkeypatch.setattr(worker_main.signal, "signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker_main,
        "get_settings",
        lambda: types.SimpleNamespace(
            daily_cron="0 4 * * *",
            weekly_cron="0 22 * * 0",
            user_timezone="Asia/Shanghai",
        ),
    )

    worker_main.run_worker()

    job_ids = {str(job["id"]) for job in scheduler.jobs}
    assert scheduler.timezone == "UTC"
    assert scheduler.started is True
    assert job_ids == {"topic_dispatch", "daily_brief", "weekly_graph"}
    assert observed["idle_start"] == 1
    assert observed["idle_stop"] == 1
    assert observed["heartbeats"] == 1
    assert observed["released"] == 1

    weekly_job = next(job for job in scheduler.jobs if job["id"] == "weekly_graph")
    daily_job = next(job for job in scheduler.jobs if job["id"] == "daily_brief")
    topic_job = next(job for job in scheduler.jobs if job["id"] == "topic_dispatch")

    assert getattr(weekly_job["trigger"], "kwargs", {}) == {"expr": "0 22 * * 0"}
    assert getattr(daily_job["trigger"], "kwargs", {}) == {"expr": "0 4 * * *"}
    assert getattr(topic_job["trigger"], "kwargs", {}) == {"minute": 0}


def test_cron_display_uses_configured_user_timezone() -> None:
    assert worker_main._cron_display("0 21 * * *", user_timezone="Asia/Shanghai") == (
        "UTC 21:00，Asia/Shanghai 05:00"
    )
    assert worker_main._cron_display("30 4 * * *", user_timezone="Asia/Shanghai") == (
        "UTC 04:30，Asia/Shanghai 12:30"
    )


def test_topic_due_for_dispatch_catches_missed_daily_slot() -> None:
    due_at = worker_main._topic_due_for_dispatch(
        freq="daily",
        time_utc=1,
        now=datetime(2026, 5, 3, 15, 30, tzinfo=UTC),
        last_run_at=datetime(2026, 4, 28, 1, 0, tzinfo=UTC),
    )

    assert due_at == datetime(2026, 5, 3, 1, 0, tzinfo=UTC)


def test_topic_due_for_dispatch_skips_when_latest_slot_already_recorded() -> None:
    due_at = worker_main._topic_due_for_dispatch(
        freq="daily",
        time_utc=1,
        now=datetime(2026, 5, 3, 15, 30, tzinfo=UTC),
        last_run_at=datetime(2026, 5, 3, 1, 5, tzinfo=UTC),
    )

    assert due_at is None


def test_latest_due_slot_handles_twice_daily_schedule() -> None:
    due_at = worker_main._latest_due_slot(
        "twice_daily",
        21,
        datetime(2026, 5, 3, 13, 30, tzinfo=UTC),
    )

    assert due_at == datetime(2026, 5, 3, 9, 0, tzinfo=UTC)
