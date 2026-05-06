"""Paper repository extracted from the monolithic repository module."""

from __future__ import annotations

import heapq
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import Integer, Select, Text, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from packages.domain.enums import ReadStatus
from packages.domain.math_utils import cosine_distance as _cosine_distance
from packages.domain.schemas import PaperCreate
from packages.storage.models import Paper, PaperTopic, TopicSubscription
from packages.storage.json_schema import with_schema_version


def _created_sort_key(value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


class PaperRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _base_arxiv_id(arxiv_id: str | None) -> str:
        value = str(arxiv_id or "").strip()
        if not value:
            return ""
        return re.sub(r"v\d+$", "", value, flags=re.IGNORECASE).strip()

    @staticmethod
    def _normalize_keywords(keywords: list[str] | None) -> list[str]:
        if not keywords:
            return []
        seen: set[str] = set()
        normalized: list[str] = []
        for keyword in keywords:
            value = str(keyword).strip().lower()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return normalized

    def _build_list_queries(
        self,
        *,
        folder: str | None = None,
        topic_id: str | None = None,
        status: str | None = None,
        date_str: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        search: str | None = None,
        keywords: list[str] | None = None,
    ) -> tuple[Select, Select]:
        filters = []
        need_join_topic = False

        if search:
            like_pat = f"%{search}%"
            filters.append(
                Paper.title.ilike(like_pat)
                | Paper.abstract.ilike(like_pat)
                | Paper.arxiv_id.ilike(like_pat)
            )

        if folder == "favorites":
            filters.append(Paper.favorited == True)  # noqa: E712
        elif folder == "recent":
            since = datetime.now(UTC) - timedelta(days=7)
            filters.append(Paper.created_at >= since)
        elif folder == "unclassified":
            subq = (
                select(PaperTopic.paper_id)
                .join(TopicSubscription, TopicSubscription.id == PaperTopic.topic_id)
                .where(TopicSubscription.kind == "folder")
                .distinct()
            )
            filters.append(Paper.id.notin_(subq))
        elif topic_id:
            need_join_topic = True
            filters.append(PaperTopic.topic_id == topic_id)

        if status and status in ("unread", "skimmed", "deep_read"):
            filters.append(Paper.read_status == ReadStatus(status))

        if date_str:
            try:
                d = date.fromisoformat(date_str)
                from packages.timezone import user_date_range_to_utc_bounds

                day_start, day_end = user_date_range_to_utc_bounds(d, d)
                if day_start is not None:
                    filters.append(Paper.created_at >= day_start)
                if day_end is not None:
                    filters.append(Paper.created_at < day_end)
            except ValueError:
                pass
        else:
            from packages.timezone import user_date_range_to_utc_bounds

            parsed_from: date | None = None
            parsed_to: date | None = None
            try:
                parsed_from = date.fromisoformat(date_from) if date_from else None
            except ValueError:
                parsed_from = None
            try:
                parsed_to = date.fromisoformat(date_to) if date_to else None
            except ValueError:
                parsed_to = None

            start_bound, end_bound = user_date_range_to_utc_bounds(parsed_from, parsed_to)
            if start_bound is not None:
                filters.append(Paper.created_at >= start_bound)
            if end_bound is not None:
                filters.append(Paper.created_at < end_bound)

        selected_keywords = self._normalize_keywords(keywords)
        if selected_keywords:
            metadata_text = func.lower(cast(Paper.metadata_json, Text))
            filters.append(
                and_(*[metadata_text.like(f'%"{keyword}"%') for keyword in selected_keywords])
            )

        base_q = select(Paper)
        count_q = select(func.count()).select_from(Paper)
        if need_join_topic:
            base_q = base_q.join(PaperTopic, Paper.id == PaperTopic.paper_id)
            count_q = count_q.join(PaperTopic, Paper.id == PaperTopic.paper_id)

        for query_filter in filters:
            base_q = base_q.where(query_filter)
            count_q = count_q.where(query_filter)

        return base_q, count_q

    def upsert_paper(self, data: PaperCreate) -> Paper:
        q = select(Paper).where(Paper.arxiv_id == data.arxiv_id)
        existing = self.session.execute(q).scalar_one_or_none()
        if existing:
            existing.title = data.title
            existing.abstract = data.abstract
            existing.publication_date = data.publication_date
            existing.metadata_json = with_schema_version(data.metadata)
            existing.updated_at = datetime.now(UTC)
            self.session.flush()
            return existing

        paper = Paper(
            arxiv_id=data.arxiv_id,
            title=data.title,
            abstract=data.abstract,
            publication_date=data.publication_date,
            metadata_json=with_schema_version(data.metadata),
        )
        self.session.add(paper)
        self.session.flush()
        return paper

    def list_latest(self, limit: int = 20) -> list[Paper]:
        q: Select[tuple[Paper]] = select(Paper).order_by(Paper.created_at.desc()).limit(limit)
        return list(self.session.execute(q).scalars())

    def list_all(self, limit: int = 10000) -> list[Paper]:
        return self.list_latest(limit=limit)

    def list_by_ids(self, paper_ids: list[str]) -> list[Paper]:
        if not paper_ids:
            return []
        q = select(Paper).where(Paper.id.in_(paper_ids))
        return list(self.session.execute(q).scalars())

    def list_existing_arxiv_ids(self, arxiv_ids: list[str]) -> set[str]:
        """批量检查哪些 arxiv_id 已存在，返回已存在的 ID 集合"""
        if not arxiv_ids:
            return set()
        q = select(Paper.arxiv_id).where(Paper.arxiv_id.in_(arxiv_ids))
        return set(self.session.execute(q).scalars())

    def list_existing_arxiv_base_ids(self, arxiv_ids: list[str]) -> set[str]:
        """按 arXiv base id（忽略版本号）检查哪些论文已存在。"""
        normalized_ids = [self._base_arxiv_id(arxiv_id) for arxiv_id in arxiv_ids]
        base_ids = [arxiv_id for arxiv_id in normalized_ids if arxiv_id]
        if not base_ids:
            return set()

        clauses = [
            or_(
                Paper.arxiv_id == base_id,
                Paper.arxiv_id.like(f"{base_id}v%"),
            )
            for base_id in dict.fromkeys(base_ids)
        ]
        q = select(Paper.arxiv_id).where(or_(*clauses))
        return {
            normalized
            for normalized in (self._base_arxiv_id(value) for value in self.session.execute(q).scalars())
            if normalized
        }

    def get_by_arxiv_id(self, arxiv_id: str) -> Paper | None:
        q = select(Paper).where(Paper.arxiv_id == arxiv_id)
        return self.session.execute(q).scalar_one_or_none()

    def list_by_read_status(self, status: ReadStatus, limit: int = 200) -> list[Paper]:
        q = (
            select(Paper)
            .where(Paper.read_status == status)
            .order_by(Paper.created_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(q).scalars())

    def list_by_read_status_with_embedding(
        self, statuses: list[str], limit: int = 200
    ) -> list[Paper]:
        """查询指定阅读状态且有 embedding 的论文"""
        status_enums = [ReadStatus(s) for s in statuses]
        q = (
            select(Paper)
            .where(
                Paper.read_status.in_(status_enums),
                Paper.embedding.is_not(None),
            )
            .order_by(Paper.created_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(q).scalars())

    def list_unread_with_embedding(self, limit: int = 200) -> list[Paper]:
        """查询未读但有 embedding 的论文"""
        q = (
            select(Paper)
            .where(
                Paper.read_status == ReadStatus.unread,
                Paper.embedding.is_not(None),
            )
            .order_by(Paper.created_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(q).scalars())

    def list_with_embedding(
        self,
        topic_id: str | None = None,
        limit: int = 200,
    ) -> list[Paper]:
        """查询有 embedding 的论文，可选按 topic 过滤"""
        if topic_id:
            q = (
                select(Paper)
                .join(PaperTopic, Paper.id == PaperTopic.paper_id)
                .where(
                    PaperTopic.topic_id == topic_id,
                    Paper.embedding.is_not(None),
                )
                .order_by(Paper.created_at.desc())
                .limit(limit)
            )
        else:
            q = (
                select(Paper)
                .where(Paper.embedding.is_not(None))
                .order_by(Paper.created_at.desc())
                .limit(limit)
            )
        return list(self.session.execute(q).scalars())

    def list_recent_since(self, since: datetime, limit: int = 500) -> list[Paper]:
        """查询指定时间之后入库的论文"""
        q = (
            select(Paper)
            .where(Paper.created_at >= since)
            .order_by(Paper.created_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(q).scalars())

    def list_recent_between(self, start: datetime, end: datetime, limit: int = 500) -> list[Paper]:
        """查询指定时间区间内入库的论文"""
        q = (
            select(Paper)
            .where(Paper.created_at >= start, Paper.created_at < end)
            .order_by(Paper.created_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(q).scalars())

    def count_all(self) -> int:
        q = select(func.count()).select_from(Paper)
        return self.session.execute(q).scalar() or 0

    def folder_stats(self) -> dict:
        """返回文件夹统计：按手动文件夹、收藏、最近、未分类"""
        from packages.timezone import utc_naive_to_user_date, user_today_start_utc

        total = self.count_all()
        fav_q = select(func.count()).select_from(Paper).where(Paper.favorited == True)  # noqa: E712
        favorites = self.session.execute(fav_q).scalar() or 0

        # "最近 7 天" 用用户时区的今天 0 点往前推 7 天
        user_today_utc = user_today_start_utc()
        week_start_utc = user_today_utc - timedelta(days=7)
        recent_q = (
            select(func.count())
            .select_from(Paper)
            .where(Paper.created_at >= week_start_utc)
        )
        recent_7d = self.session.execute(recent_q).scalar() or 0

        # 有手动文件夹的论文 ID 集合
        has_topic_q = (
            select(func.count(func.distinct(PaperTopic.paper_id)))
            .select_from(PaperTopic)
            .join(TopicSubscription, TopicSubscription.id == PaperTopic.topic_id)
            .where(TopicSubscription.kind == "folder")
        )
        has_topic = self.session.execute(has_topic_q).scalar() or 0
        unclassified = max(0, total - has_topic)

        # 按手动文件夹统计
        topic_counts_q = (
            select(
                TopicSubscription.id,
                TopicSubscription.name,
                func.count(PaperTopic.paper_id),
            )
            .join(PaperTopic, TopicSubscription.id == PaperTopic.topic_id)
            .where(TopicSubscription.kind == "folder")
            .group_by(TopicSubscription.id, TopicSubscription.name)
            .order_by(func.count(PaperTopic.paper_id).desc())
        )
        topic_rows = self.session.execute(topic_counts_q).all()
        by_topic = [{"topic_id": r[0], "topic_name": r[1], "count": r[2]} for r in topic_rows]

        subscription_counts_q = (
            select(
                TopicSubscription.id,
                TopicSubscription.name,
                func.count(PaperTopic.paper_id),
            )
            .join(PaperTopic, TopicSubscription.id == PaperTopic.topic_id)
            .where(TopicSubscription.kind == "subscription")
            .group_by(TopicSubscription.id, TopicSubscription.name)
            .order_by(func.count(PaperTopic.paper_id).desc(), TopicSubscription.name.asc())
        )
        subscription_rows = self.session.execute(subscription_counts_q).all()
        by_subscription = [
            {"topic_id": r[0], "topic_name": r[1], "count": r[2]}
            for r in subscription_rows
        ]

        # 按阅读状态统计
        status_q = select(Paper.read_status, func.count()).group_by(Paper.read_status)
        status_rows = self.session.execute(status_q).all()
        by_status = {r[0].value: r[1] for r in status_rows}

        since_30d = user_today_utc - timedelta(days=30)
        created_rows = self.session.execute(
            select(Paper.created_at)
            .where(Paper.created_at >= since_30d)
            .order_by(Paper.created_at.desc())
        ).all()
        date_counts: Counter[str] = Counter()
        for row in created_rows:
            created_at = row[0]
            if created_at is None:
                continue
            date_counts[str(utc_naive_to_user_date(created_at))] += 1
        by_date = [
            {"date": day, "count": count}
            for day, count in sorted(date_counts.items(), key=lambda item: item[0], reverse=True)
        ]

        return {
            "total": total,
            "favorites": favorites,
            "recent_7d": recent_7d,
            "unclassified": unclassified,
            "by_topic": by_topic,
            "by_subscription": by_subscription,
            "by_status": by_status,
            "by_date": by_date,
        }

    def list_paginated(
        self,
        page: int = 1,
        page_size: int = 20,
        folder: str | None = None,
        topic_id: str | None = None,
        status: str | None = None,
        date_str: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        search: str | None = None,
        keywords: list[str] | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> tuple[list[Paper], int]:
        """分页查询论文，返回 (papers, total_count)"""
        base_q, count_q = self._build_list_queries(
            folder=folder,
            topic_id=topic_id,
            status=status,
            date_str=date_str,
            date_from=date_from,
            date_to=date_to,
            search=search,
            keywords=keywords,
        )

        total = self.session.execute(count_q).scalar() or 0
        offset = (max(1, page) - 1) * page_size
        impact_col = func.coalesce(
            cast(func.json_extract(Paper.metadata_json, "$.citationCount"), Integer),
            cast(func.json_extract(Paper.metadata_json, "$.citation_count"), Integer),
            0,
        )
        _SORT_COLS = {
            "created_at": Paper.created_at,
            "publication_date": Paper.publication_date,
            "title": Paper.title,
            "impact": impact_col,
        }
        sort_col = _SORT_COLS.get(sort_by, Paper.created_at)
        order_expr = sort_col.desc() if sort_order == "desc" else sort_col.asc()
        secondary_order = Paper.created_at.desc() if sort_order == "desc" else Paper.created_at.asc()
        papers = list(
            self.session.execute(
                base_q.order_by(order_expr, secondary_order).offset(offset).limit(page_size)
            ).scalars()
        )
        return papers, total

    def keyword_facets(
        self,
        *,
        folder: str | None = None,
        topic_id: str | None = None,
        status: str | None = None,
        date_str: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        search: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, int | str]]:
        base_q, _ = self._build_list_queries(
            folder=folder,
            topic_id=topic_id,
            status=status,
            date_str=date_str,
            date_from=date_from,
            date_to=date_to,
            search=search,
            keywords=None,
        )
        papers = list(
            self.session.execute(base_q.order_by(Paper.created_at.desc()).limit(2000)).scalars()
        )

        counts: Counter[str] = Counter()
        display_name: dict[str, str] = {}
        for paper in papers:
            for raw_keyword in (paper.metadata_json or {}).get("keywords", []) or []:
                keyword = str(raw_keyword).strip()
                normalized = keyword.lower()
                if not normalized:
                    continue
                counts[normalized] += 1
                display_name.setdefault(normalized, keyword)

        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return [
            {"keyword": display_name[keyword], "count": count}
            for keyword, count in ranked[: max(1, limit)]
        ]

    def list_by_topic(self, topic_id: str, limit: int = 200) -> list[Paper]:
        q = (
            select(Paper)
            .join(PaperTopic, Paper.id == PaperTopic.paper_id)
            .where(PaperTopic.topic_id == topic_id)
            .order_by(Paper.created_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(q).scalars())

    def get_by_id(self, paper_id: UUID) -> Paper:
        paper = self.session.get(Paper, str(paper_id))
        if paper is None:
            raise ValueError(f"paper {paper_id} not found")
        return paper

    def delete_by_ids(self, paper_ids: list[str]) -> tuple[list[str], list[str]]:
        """Delete papers by IDs and return (deleted_ids, pdf_paths)."""
        if not paper_ids:
            return [], []

        # Preserve order and remove duplicates.
        unique_ids = list(dict.fromkeys(str(pid) for pid in paper_ids if pid))
        q = select(Paper).where(Paper.id.in_(unique_ids))
        papers = list(self.session.execute(q).scalars())

        deleted_ids: list[str] = []
        pdf_paths: list[str] = []
        for paper in papers:
            deleted_ids.append(str(paper.id))
            if paper.pdf_path:
                pdf_paths.append(paper.pdf_path)
            self.session.delete(paper)

        self.session.flush()
        return deleted_ids, pdf_paths

    def list_unclassified(self, limit: int = 200) -> list[Paper]:
        """List papers that are not linked to any topic."""
        subq = select(PaperTopic.paper_id).distinct()
        q = (
            select(Paper)
            .where(Paper.id.notin_(subq))
            .order_by(Paper.created_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(q).scalars())

    def set_pdf_path(self, paper_id: UUID, pdf_path: str) -> None:
        paper = self.get_by_id(paper_id)
        paper.pdf_path = pdf_path
        paper.updated_at = datetime.now(UTC)

    def update_embedding(
        self,
        paper_id: UUID,
        embedding: list[float],
        embedding_status: dict | None = None,
    ) -> None:
        paper = self.get_by_id(paper_id)
        paper.embedding = embedding
        if embedding_status is not None:
            metadata = with_schema_version(paper.metadata_json)
            metadata["embedding_status"] = embedding_status
            paper.metadata_json = metadata
        paper.updated_at = datetime.now(UTC)

    def update_read_status(self, paper_id: UUID, status: ReadStatus) -> None:
        paper = self.get_by_id(paper_id)
        upgrade = (
            paper.read_status == ReadStatus.unread
            and status in (ReadStatus.skimmed, ReadStatus.deep_read)
        ) or (paper.read_status == ReadStatus.skimmed and status == ReadStatus.deep_read)
        if upgrade:
            paper.read_status = status

    def similar_by_embedding(
        self,
        vector: list[float],
        exclude: UUID,
        limit: int = 5,
        max_candidates: int | None = None,
    ) -> list[Paper]:
        paper_ids = self._rank_embedding_candidates(
            vector,
            limit=limit,
            exclude_id=str(exclude),
            max_candidates=max_candidates,
        )
        if not paper_ids:
            return []
        papers = list(self.session.execute(select(Paper).where(Paper.id.in_(paper_ids))).scalars())
        paper_map = {paper.id: paper for paper in papers}
        return [paper_map[paper_id] for paper_id in paper_ids if paper_id in paper_map]

    def full_text_candidates(self, query: str, limit: int = 8) -> list[Paper]:
        """按关键词搜索论文，支持简单 OR/AND 查询（如: transformer OR diffusion）"""
        query_text = str(query or "").strip().lower()
        if not query_text:
            return []

        normalized = (
            query_text.replace("（", "(")
            .replace("）", ")")
            .replace("，", " ")
            .replace(",", " ")
            .replace("；", " ")
            .replace(";", " ")
        )
        normalized = re.sub(r"\b(?:all|ti|abs|cat):", " ", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+", " ", normalized).strip()

        # 支持多关键词 OR；每个 OR 分组内部按 AND（或空格）处理
        or_groups = [
            part.strip()
            for part in re.split(r"\s+\bor\b\s+|\|", normalized, flags=re.IGNORECASE)
            if part.strip()
        ]
        if not or_groups:
            return []

        group_conditions = []
        metadata_text = func.lower(cast(Paper.metadata_json, Text))
        for group in or_groups:
            cleaned_group = re.sub(r"[()\"']", " ", group)
            and_chunks = [
                chunk.strip()
                for chunk in re.split(r"\s+\band\b\s+|&&", cleaned_group, flags=re.IGNORECASE)
                if chunk.strip()
            ]
            if not and_chunks:
                and_chunks = [cleaned_group]

            terms: list[str] = []
            for chunk in and_chunks:
                for token in re.split(r"\s+", chunk):
                    token = token.strip()
                    if len(token) < 2:
                        continue
                    if token in {"and", "or", "not"}:
                        continue
                    terms.append(token)

            dedup_terms = list(dict.fromkeys(terms))
            if not dedup_terms:
                continue

            term_conditions = [
                func.lower(Paper.title).contains(token)
                | func.lower(Paper.abstract).contains(token)
                | func.lower(Paper.arxiv_id).contains(token)
                | metadata_text.contains(token)
                for token in dedup_terms
            ]
            if term_conditions:
                group_conditions.append(and_(*term_conditions))

        if not group_conditions:
            fallback_tokens = [
                token
                for token in re.split(r"\s+", re.sub(r"[()\"']", " ", normalized))
                if len(token) >= 2 and token not in {"and", "or", "not"}
            ]
            fallback_terms = list(dict.fromkeys(fallback_tokens))
            if not fallback_terms:
                return []
            group_conditions = [
                and_(
                    *[
                        func.lower(Paper.title).contains(token)
                        | func.lower(Paper.abstract).contains(token)
                        | func.lower(Paper.arxiv_id).contains(token)
                        | metadata_text.contains(token)
                        for token in fallback_terms
                    ]
                )
            ]

        where_clause = (
            group_conditions[0]
            if len(group_conditions) == 1
            else or_(*group_conditions)
        )
        q = (
            select(Paper)
            .where(where_clause)
            .order_by(Paper.created_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(q).scalars())

    def semantic_candidates(
        self,
        query_vector: list[float],
        limit: int = 8,
        max_candidates: int | None = None,
    ) -> list[Paper]:
        paper_ids = self._rank_embedding_candidates(
            query_vector,
            limit=limit,
            max_candidates=max_candidates,
        )
        if not paper_ids:
            return []
        papers = list(self.session.execute(select(Paper).where(Paper.id.in_(paper_ids))).scalars())
        paper_map = {paper.id: paper for paper in papers}
        return [paper_map[paper_id] for paper_id in paper_ids if paper_id in paper_map]

    def _rank_embedding_candidates(
        self,
        vector: list[float],
        *,
        limit: int,
        exclude_id: str | None = None,
        max_candidates: int | None = None,
    ) -> list[str]:
        if not vector or limit <= 0:
            return []

        query = select(Paper.id, Paper.embedding, Paper.created_at).where(Paper.embedding.is_not(None))
        if exclude_id:
            query = query.where(Paper.id != exclude_id)
        if max_candidates is not None and max_candidates > 0:
            query = query.limit(max_candidates)

        best: list[tuple[float, float, str]] = []
        for paper_id, embedding, created_at in self.session.execute(query):
            distance = _cosine_distance(vector, embedding or [])
            entry = (-distance, _created_sort_key(created_at), str(paper_id))
            if len(best) < limit:
                heapq.heappush(best, entry)
                continue
            if entry > best[0]:
                heapq.heapreplace(best, entry)

        ordered = sorted(best, reverse=True)
        return [paper_id for _score, _created_at, paper_id in ordered]

    def link_to_topic(self, paper_id: str, topic_id: str) -> None:
        q = select(PaperTopic).where(
            PaperTopic.paper_id == paper_id,
            PaperTopic.topic_id == topic_id,
        )
        found = self.session.execute(q).scalar_one_or_none()
        if found:
            return
        self.session.add(PaperTopic(paper_id=paper_id, topic_id=topic_id))

    def unlink_from_topic(self, paper_id: str, topic_id: str) -> bool:
        q = select(PaperTopic).where(
            PaperTopic.paper_id == paper_id,
            PaperTopic.topic_id == topic_id,
        )
        found = self.session.execute(q).scalar_one_or_none()
        if not found:
            return False
        self.session.delete(found)
        return True

    def get_topics_for_paper(
        self, paper_id: str, *, kind: str | None = None
    ) -> list[TopicSubscription]:
        q = (
            select(TopicSubscription)
            .join(PaperTopic, PaperTopic.topic_id == TopicSubscription.id)
            .where(PaperTopic.paper_id == paper_id)
            .order_by(TopicSubscription.name.asc())
        )
        if kind:
            q = q.where(TopicSubscription.kind == kind)
        return list(self.session.execute(q).scalars())

    def get_topic_names_for_papers(
        self, paper_ids: list[str], *, kind: str | None = None
    ) -> dict[str, list[str]]:
        """批量查 paper → topic name 映射"""
        if not paper_ids:
            return {}
        q = (
            select(PaperTopic.paper_id, TopicSubscription.name)
            .join(
                TopicSubscription,
                PaperTopic.topic_id == TopicSubscription.id,
            )
            .where(PaperTopic.paper_id.in_(paper_ids))
        )
        if kind:
            q = q.where(TopicSubscription.kind == kind)
        rows = self.session.execute(q).all()
        result: dict[str, list[str]] = {}
        for pid, tname in rows:
            result.setdefault(pid, []).append(tname)
        return result
