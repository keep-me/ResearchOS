"""Topic repository extracted from the monolithic repository module."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from packages.storage.models import TopicSubscription

logger = logging.getLogger(__name__)
_UNSET = object()


class TopicRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _normalize_external_filter_values(
        *,
        venue_tier: str,
        venue_type: str,
        venue_names: list[str] | None,
        from_year: int | None,
    ) -> tuple[str, str, list[str], int | None]:
        normalized_tier = str(venue_tier or "all").strip().lower() or "all"
        if normalized_tier not in {"all", "ccf_a"}:
            normalized_tier = "all"

        normalized_type = str(venue_type or "all").strip().lower() or "all"
        if normalized_type not in {"all", "conference", "journal"}:
            normalized_type = "all"

        normalized_names = [
            str(item).strip()
            for item in (venue_names or [])
            if str(item).strip()
        ]

        normalized_year: int | None = None
        if from_year is not None:
            try:
                normalized_year = max(1900, min(int(from_year), 2100))
            except (TypeError, ValueError):
                normalized_year = None

        return normalized_tier, normalized_type, normalized_names, normalized_year

    @staticmethod
    def _normalize_date_filter_values(
        *,
        enable_date_filter: bool,
        date_filter_days: int,
        date_filter_start: date | None,
        date_filter_end: date | None,
    ) -> tuple[int, date | None, date | None]:
        normalized_days = max(1, date_filter_days)
        if not enable_date_filter:
            return normalized_days, None, None
        if date_filter_start is not None and date_filter_end is not None:
            if date_filter_start > date_filter_end:
                raise ValueError("date_filter_start cannot be after date_filter_end")
            normalized_days = max(1, (date_filter_end - date_filter_start).days + 1)
        return normalized_days, date_filter_start, date_filter_end

    def list_topics(
        self,
        enabled_only: bool = False,
        kind: str | None = None,
    ) -> list[TopicSubscription]:
        q = select(TopicSubscription).order_by(TopicSubscription.created_at.desc())
        if enabled_only:
            q = q.where(TopicSubscription.enabled.is_(True))
        if kind:
            q = q.where(TopicSubscription.kind == kind)
        try:
            return list(self.session.execute(q).scalars())
        except OperationalError as exc:
            # Backward compatibility: old sqlite schemas may miss new topic columns.
            if (
                "topic_subscriptions.enable_date_filter" in str(exc)
                or "topic_subscriptions.kind" in str(exc)
                or "topic_subscriptions.sort_by" in str(exc)
                or "topic_subscriptions.source" in str(exc)
                or "topic_subscriptions.search_field" in str(exc)
                or "topic_subscriptions.priority_mode" in str(exc)
                or "topic_subscriptions.venue_tier" in str(exc)
                or "topic_subscriptions.venue_type" in str(exc)
                or "topic_subscriptions.venue_names" in str(exc)
                or "topic_subscriptions.from_year" in str(exc)
                or "topic_subscriptions.default_folder_id" in str(exc)
                or "topic_subscriptions.date_filter_start" in str(exc)
                or "topic_subscriptions.date_filter_end" in str(exc)
                or "topic_subscriptions.last_run_at" in str(exc)
                or "topic_subscriptions.last_run_status" in str(exc)
                or "topic_subscriptions.last_run_count" in str(exc)
                or "topic_subscriptions.last_run_error" in str(exc)
            ):
                logger.warning(
                    "topic_subscriptions schema is outdated; "
                    "returning empty topic list as fallback"
                )
                return []
            raise

    def get_by_name(self, name: str) -> TopicSubscription | None:
        q = select(TopicSubscription).where(TopicSubscription.name == name)
        return self.session.execute(q).scalar_one_or_none()

    def get_by_id(self, topic_id: str) -> TopicSubscription | None:
        return self.session.get(TopicSubscription, topic_id)

    def upsert_topic(
        self,
        *,
        name: str,
        kind: str = "subscription",
        query: str = "",
        sort_by: str = "submittedDate",
        source: str = "arxiv",
        search_field: str = "all",
        priority_mode: str = "time",
        venue_tier: str = "all",
        venue_type: str = "all",
        venue_names: list[str] | None = None,
        from_year: int | None = None,
        default_folder_id: str | None = None,
        enabled: bool = True,
        max_results_per_run: int = 20,
        retry_limit: int = 2,
        schedule_frequency: str = "daily",
        schedule_time_utc: int = 21,
        enable_date_filter: bool = False,
        date_filter_days: int = 7,
        date_filter_start: date | None = None,
        date_filter_end: date | None = None,

    ) -> TopicSubscription:
        normalized_tier, normalized_type, normalized_names, normalized_year = self._normalize_external_filter_values(
            venue_tier=venue_tier,
            venue_type=venue_type,
            venue_names=venue_names,
            from_year=from_year,
        )
        normalized_days, normalized_start, normalized_end = self._normalize_date_filter_values(
            enable_date_filter=enable_date_filter,
            date_filter_days=date_filter_days,
            date_filter_start=date_filter_start,
            date_filter_end=date_filter_end,
        )
        found = self.get_by_name(name)
        if found:
            found.kind = kind or "subscription"
            found.query = query
            found.sort_by = sort_by or "submittedDate"
            found.source = source or "arxiv"
            found.search_field = search_field or "all"
            found.priority_mode = priority_mode or "time"
            found.venue_tier = normalized_tier
            found.venue_type = normalized_type
            found.venue_names_json = normalized_names
            found.from_year = normalized_year
            found.default_folder_id = default_folder_id
            found.enabled = enabled
            found.max_results_per_run = max(max_results_per_run, 1)
            found.retry_limit = max(retry_limit, 0)
            found.schedule_frequency = schedule_frequency
            found.schedule_time_utc = max(0, min(23, schedule_time_utc))
            found.enable_date_filter = enable_date_filter
            found.date_filter_days = normalized_days
            found.date_filter_start = normalized_start
            found.date_filter_end = normalized_end
            found.updated_at = datetime.now(UTC)
            self.session.flush()
            return found
        topic = TopicSubscription(
            name=name,
            kind=kind or "subscription",
            query=query,
            sort_by=sort_by or "submittedDate",
            source=source or "arxiv",
            search_field=search_field or "all",
            priority_mode=priority_mode or "time",
            venue_tier=normalized_tier,
            venue_type=normalized_type,
            venue_names_json=normalized_names,
            from_year=normalized_year,
            default_folder_id=default_folder_id,
            enabled=enabled,
            max_results_per_run=max(max_results_per_run, 1),
            retry_limit=max(retry_limit, 0),
            schedule_frequency=schedule_frequency,
            schedule_time_utc=max(0, min(23, schedule_time_utc)),
            enable_date_filter=enable_date_filter,
            date_filter_days=normalized_days,
            date_filter_start=normalized_start,
            date_filter_end=normalized_end,
        )
        self.session.add(topic)
        self.session.flush()
        return topic

    def update_topic(
        self,
        topic_id: str,
        *,
        name: str | None = None,
        kind: str | None = None,
        query: str | None = None,
        sort_by: str | None = None,
        source: str | None = None,
        search_field: str | None = None,
        priority_mode: str | None = None,
        venue_tier: str | None = None,
        venue_type: str | None = None,
        venue_names: list[str] | None | object = _UNSET,
        from_year: int | None | object = _UNSET,
        default_folder_id: str | None | object = _UNSET,
        enabled: bool | None = None,
        max_results_per_run: int | None = None,
        retry_limit: int | None = None,
        schedule_frequency: str | None = None,
        enable_date_filter: bool | None = None,
        date_filter_days: int | None = None,
        date_filter_start: date | None = None,
        date_filter_end: date | None = None,
        schedule_time_utc: int | None = None,
    ) -> TopicSubscription:
        topic = self.session.get(TopicSubscription, topic_id)
        if topic is None:
            raise ValueError(f"topic {topic_id} not found")
        if name is not None:
            topic.name = name.strip()
        if kind is not None:
            topic.kind = kind.strip() or "subscription"
        if query is not None:
            topic.query = query
        if sort_by is not None:
            topic.sort_by = sort_by.strip() or "submittedDate"
        if source is not None:
            topic.source = source.strip() or "arxiv"
        if search_field is not None:
            topic.search_field = search_field.strip() or "all"
        if priority_mode is not None:
            topic.priority_mode = priority_mode.strip() or "time"
        if (
            venue_tier is not None
            or venue_type is not None
            or venue_names is not _UNSET
            or from_year is not _UNSET
        ):
            normalized_tier, normalized_type, normalized_names, normalized_year = self._normalize_external_filter_values(
                venue_tier=venue_tier or topic.venue_tier,
                venue_type=venue_type or topic.venue_type,
                venue_names=topic.venue_names_json if venue_names is _UNSET else venue_names,
                from_year=topic.from_year if from_year is _UNSET else from_year,
            )
            topic.venue_tier = normalized_tier
            topic.venue_type = normalized_type
            topic.venue_names_json = normalized_names
            topic.from_year = normalized_year
        if default_folder_id is not _UNSET:
            topic.default_folder_id = default_folder_id or None
        if enabled is not None:
            topic.enabled = enabled
        if max_results_per_run is not None:
            topic.max_results_per_run = max(max_results_per_run, 1)
        if retry_limit is not None:
            topic.retry_limit = max(retry_limit, 0)
        if schedule_frequency is not None:
            topic.schedule_frequency = schedule_frequency
        if schedule_time_utc is not None:
            topic.schedule_time_utc = max(0, min(23, schedule_time_utc))
        if enable_date_filter is not None:
            topic.enable_date_filter = enable_date_filter
        if (
            date_filter_days is not None
            or date_filter_start is not None
            or date_filter_end is not None
        ):
            normalized_days, normalized_start, normalized_end = self._normalize_date_filter_values(
                enable_date_filter=topic.enable_date_filter,
                date_filter_days=date_filter_days or topic.date_filter_days,
                date_filter_start=date_filter_start,
                date_filter_end=date_filter_end,
            )
            topic.date_filter_days = normalized_days
            topic.date_filter_start = normalized_start
            topic.date_filter_end = normalized_end
        elif enable_date_filter is False:
            topic.date_filter_start = None
            topic.date_filter_end = None
        topic.updated_at = datetime.now(UTC)
        self.session.flush()
        return topic

    def delete_topic(self, topic_id: str) -> None:
        topic = self.session.get(TopicSubscription, topic_id)
        if topic is not None:
            self.session.delete(topic)
