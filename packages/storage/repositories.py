"""
数据仓储层
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from packages.domain.enums import (
    ActionType,
    PipelineStatus,
)
from packages.domain.schemas import DeepDiveReport, SkimReport
from packages.storage import agent_session_repository as _agent_session_repository
from packages.storage import paper_repository as _paper_repository
from packages.storage import project_repository as _project_repository
from packages.storage import topic_repository as _topic_repository
from packages.storage.models import (
    ActionPaper,
    AnalysisReport,
    Citation,
    CollectionAction,
    EmailConfig,
    FeishuConfig,
    GeneratedContent,
    LLMProviderConfig,
    Paper,
    PipelineRun,
    ProjectResearchWikiEdge,
    ProjectResearchWikiNode,
    ProjectGpuLease,
    PromptTrace,
    ResearchKGEdge,
    ResearchKGNode,
    ResearchKGPaperState,
    SourceCheckpoint,
)
from packages.storage import task_repository as _task_repository



logger = logging.getLogger(__name__)
_UNSET = object()

TaskRepository = _task_repository.TaskRepository
PaperRepository = _paper_repository.PaperRepository
TopicRepository = _topic_repository.TopicRepository
ProjectRepository = _project_repository.ProjectRepository
AgentConversationRepository = _agent_session_repository.AgentConversationRepository
AgentMessageRepository = _agent_session_repository.AgentMessageRepository
AgentProjectRepository = _agent_session_repository.AgentProjectRepository
AgentPermissionRuleSetRepository = _agent_session_repository.AgentPermissionRuleSetRepository
AgentPendingActionRepository = _agent_session_repository.AgentPendingActionRepository
AgentSessionRepository = _agent_session_repository.AgentSessionRepository
AgentSessionMessageRepository = _agent_session_repository.AgentSessionMessageRepository
AgentSessionPartRepository = _agent_session_repository.AgentSessionPartRepository
AgentSessionTodoRepository = _agent_session_repository.AgentSessionTodoRepository


def _normalize_workspace_match_key(value: str | None) -> str:
    raw = str(value or "").strip().replace("\\", "/").rstrip("/")
    if not raw:
        return ""
    return raw.casefold()


def _path_matches_workspace(candidate: str | None, workspace_path: str | None) -> bool:
    base = _normalize_workspace_match_key(candidate)
    target = _normalize_workspace_match_key(workspace_path)
    if not base or not target:
        return False
    return target == base or target.startswith(f"{base}/")


class BaseQuery:
    """
    基础查询类 - 提供通用的查询方法减少重复代码
    """

    def __init__(self, session: Session):
        self.session = session

    def _paginate(self, query: Select, page: int, page_size: int) -> Select:
        """
        添加分页到查询

        Args:
            query: SQLAlchemy 查询对象
            page: 页码（从 1 开始）
            page_size: 每页大小

        Returns:
            添加了分页的查询对象
        """
        offset = (max(1, page) - 1) * page_size
        return query.offset(offset).limit(page_size)

    def _execute_paginated(
        self, query: Select, page: int = 1, page_size: int = 20
    ) -> tuple[list, int]:
        """
        执行分页查询，返回 (结果列表, 总数)

        Args:
            query: SQLAlchemy 查询对象
            page: 页码（从 1 开始）
            page_size: 每页大小

        Returns:
            (结果列表, 总数)
        """
        count_query = select(func.count()).select_from(query.alias())
        total = self.session.execute(count_query).scalar() or 0

        paginated_query = self._paginate(query, page, page_size)
        results = list(self.session.execute(paginated_query).scalars())

        return results, total


class ProjectGpuLeaseRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_slot(self, workspace_server_id: str, gpu_index: int) -> ProjectGpuLease | None:
        query = select(ProjectGpuLease).where(
            ProjectGpuLease.workspace_server_id == workspace_server_id,
            ProjectGpuLease.gpu_index == int(gpu_index),
        )
        return self.session.execute(query).scalars().first()

    def list_leases(
        self,
        *,
        workspace_server_id: str | None = None,
        active_only: bool = False,
    ) -> list[ProjectGpuLease]:
        query = select(ProjectGpuLease)
        if workspace_server_id:
            query = query.where(ProjectGpuLease.workspace_server_id == workspace_server_id)
        if active_only:
            query = query.where(ProjectGpuLease.active == True)  # noqa: E712
        query = query.order_by(ProjectGpuLease.workspace_server_id.asc(), ProjectGpuLease.gpu_index.asc())
        return list(self.session.execute(query).scalars().all())

    def acquire(
        self,
        *,
        workspace_server_id: str,
        gpu_index: int,
        gpu_name: str | None = None,
        project_id: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        remote_session_name: str | None = None,
        holder_title: str | None = None,
        metadata: dict | None = None,
    ) -> ProjectGpuLease:
        row = self.get_slot(workspace_server_id, gpu_index)
        now = datetime.now(UTC)
        if row is None:
            row = ProjectGpuLease(
                workspace_server_id=workspace_server_id,
                gpu_index=int(gpu_index),
            )
            self.session.add(row)
        elif row.active and str(row.run_id or "").strip() not in {"", str(run_id or "").strip()}:
            raise ValueError(f"GPU {workspace_server_id}:{gpu_index} 已被其他运行占用")
        row.gpu_name = gpu_name
        row.active = True
        row.project_id = project_id
        row.run_id = run_id
        row.task_id = task_id
        row.remote_session_name = remote_session_name
        row.holder_title = holder_title
        row.metadata_json = dict(metadata or {})
        row.release_reason = None
        row.locked_at = now
        row.heartbeat_at = now
        row.released_at = None
        self.session.flush()
        return row

    def release(
        self,
        *,
        workspace_server_id: str,
        gpu_index: int,
        run_id: str | None = None,
        remote_session_name: str | None = None,
        reason: str | None = None,
    ) -> ProjectGpuLease | None:
        row = self.get_slot(workspace_server_id, gpu_index)
        if row is None:
            return None
        if run_id and str(row.run_id or "").strip() not in {"", str(run_id).strip()}:
            return None
        if remote_session_name and str(row.remote_session_name or "").strip() not in {"", str(remote_session_name).strip()}:
            return None
        now = datetime.now(UTC)
        row.active = False
        row.release_reason = str(reason or "").strip() or row.release_reason
        row.heartbeat_at = now
        row.released_at = now
        self.session.flush()
        return row

    def touch(
        self,
        *,
        workspace_server_id: str,
        gpu_index: int,
        metadata: dict | None = None,
    ) -> ProjectGpuLease | None:
        row = self.get_slot(workspace_server_id, gpu_index)
        if row is None:
            return None
        row.heartbeat_at = datetime.now(UTC)
        if metadata is not None:
            row.metadata_json = dict(metadata)
        self.session.flush()
        return row

    def reconcile_missing_sessions(
        self,
        *,
        workspace_server_id: str,
        active_session_names: list[str],
        reason: str = "remote_session_missing",
    ) -> list[ProjectGpuLease]:
        active_names = {str(item or "").strip() for item in active_session_names if str(item or "").strip()}
        now = datetime.now(UTC)
        released: list[ProjectGpuLease] = []
        for row in self.list_leases(workspace_server_id=workspace_server_id, active_only=True):
            session_name = str(row.remote_session_name or "").strip()
            if session_name and session_name in active_names:
                row.heartbeat_at = now
                continue
            row.active = False
            row.release_reason = reason
            row.heartbeat_at = now
            row.released_at = now
            released.append(row)
        self.session.flush()
        return released


class AnalysisRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_skim(self, paper_id: UUID, skim: SkimReport) -> None:
        report = self._get_or_create(paper_id)
        normalized_innovations = [str(x).strip() for x in (skim.innovations or []) if str(x).strip()]
        one_liner = str(skim.one_liner or "").strip()
        if not one_liner and normalized_innovations:
            one_liner = normalized_innovations[0][:140]
        if not one_liner:
            one_liner = "该论文提出了新的方法与实验验证，核心细节请结合创新点阅读。"

        innovations = "".join([f"  - {x}\n" for x in normalized_innovations])
        report.summary_md = f"- One-liner: {one_liner}\n- Innovations:\n{innovations}"
        report.skim_score = skim.relevance_score
        report.key_insights = {
            **(report.key_insights or {}),
            "one_liner": one_liner,
            "skim_innovations": normalized_innovations,
            "keywords": skim.keywords,
            "title_zh": skim.title_zh,
            "abstract_zh": skim.abstract_zh,
        }

    def upsert_deep_dive(self, paper_id: UUID, deep: DeepDiveReport) -> None:
        report = self._get_or_create(paper_id)
        risks = "".join([f"- {x}\n" for x in deep.reviewer_risks])
        report.deep_dive_md = (
            f"## Method\n{deep.method_summary}\n\n"
            f"## Experiments\n{deep.experiments_summary}\n\n"
            f"## Ablation\n{deep.ablation_summary}\n\n"
            f"## Reviewer Risks\n{risks}"
        )
        report.key_insights = {
            **(report.key_insights or {}),
            "method_summary": deep.method_summary,
            "experiments_summary": deep.experiments_summary,
            "ablation_summary": deep.ablation_summary,
            "reviewer_risks": deep.reviewer_risks,
        }

    def _get_or_create(self, paper_id: UUID) -> AnalysisReport:
        pid = str(paper_id)
        q = select(AnalysisReport).where(AnalysisReport.paper_id == pid)
        found = self.session.execute(q).scalar_one_or_none()
        if found:
            return found
        report = AnalysisReport(paper_id=pid, key_insights={})
        self.session.add(report)
        self.session.flush()
        return report

    def summaries_for_papers(self, paper_ids: list[str]) -> dict[str, str]:
        if not paper_ids:
            return {}
        q = select(AnalysisReport).where(AnalysisReport.paper_id.in_(paper_ids))
        reports = list(self.session.execute(q).scalars())
        return {x.paper_id: x.summary_md or "" for x in reports}

    def contexts_for_papers(self, paper_ids: list[str]) -> dict[str, str]:
        if not paper_ids:
            return {}
        q = select(AnalysisReport).where(AnalysisReport.paper_id.in_(paper_ids))
        reports = list(self.session.execute(q).scalars())
        out: dict[str, str] = {}
        for x in reports:
            combined = []
            if x.summary_md:
                combined.append(x.summary_md)
            if x.deep_dive_md:
                combined.append(x.deep_dive_md[:2000])
            out[x.paper_id] = "\n\n".join(combined)
        return out


class PipelineRunRepository:
    def __init__(self, session: Session):
        self.session = session

    def start(
        self,
        pipeline_name: str,
        paper_id: UUID | None = None,
        decision_note: str | None = None,
    ) -> PipelineRun:
        last_exc: OperationalError | None = None
        for attempt in range(4):
            run = PipelineRun(
                pipeline_name=pipeline_name,
                paper_id=str(paper_id) if paper_id else None,
                status=PipelineStatus.running,
                decision_note=decision_note,
            )
            self.session.add(run)
            try:
                self.session.flush()
                return run
            except OperationalError as exc:
                self.session.rollback()
                message = str(exc).lower()
                if "database is locked" not in message:
                    raise
                last_exc = exc
                wait_seconds = 0.5 * (attempt + 1)
                logger.warning(
                    "PipelineRunRepository.start hit SQLite lock, retrying in %.1fs (%d/4)",
                    wait_seconds,
                    attempt + 1,
                )
                time.sleep(wait_seconds)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("PipelineRunRepository.start failed without captured exception")

    def finish(
        self,
        run_id: UUID,
        elapsed_ms: int | None = None,
        decision_note: str | None = None,
    ) -> None:
        values: dict[str, object] = {
            "status": PipelineStatus.succeeded,
            "elapsed_ms": elapsed_ms,
            "updated_at": datetime.now(UTC),
        }
        if decision_note is not None:
            values["decision_note"] = decision_note
        self.session.execute(
            update(PipelineRun)
            .where(PipelineRun.id == str(run_id))
            .values(**values)
        )

    def fail(self, run_id: UUID, error_message: str) -> None:
        self.session.execute(
            update(PipelineRun)
            .where(PipelineRun.id == str(run_id))
            .values(
                status=PipelineStatus.failed,
                retry_count=PipelineRun.retry_count + 1,
                error_message=error_message,
                updated_at=datetime.now(UTC),
            )
        )

    def list_latest(self, limit: int = 30) -> list[PipelineRun]:
        q = select(PipelineRun).order_by(PipelineRun.created_at.desc()).limit(limit)
        return list(self.session.execute(q).scalars())


class PromptTraceRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        *,
        stage: str,
        provider: str,
        model: str,
        prompt_digest: str,
        paper_id: UUID | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        input_cost_usd: float | None = None,
        output_cost_usd: float | None = None,
        total_cost_usd: float | None = None,
    ) -> None:
        self.session.add(
            PromptTrace(
                stage=stage,
                provider=provider,
                model=model,
                prompt_digest=prompt_digest,
                paper_id=str(paper_id) if paper_id else None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_cost_usd=input_cost_usd,
                output_cost_usd=output_cost_usd,
                total_cost_usd=total_cost_usd,
            )
        )

    def summarize_costs(self, days: int = 7) -> dict:
        since = datetime.now(UTC) - timedelta(days=max(days, 1))
        total_q = select(
            func.count(PromptTrace.id),
            func.coalesce(func.sum(PromptTrace.input_tokens), 0),
            func.coalesce(func.sum(PromptTrace.output_tokens), 0),
            func.coalesce(func.sum(PromptTrace.total_cost_usd), 0.0),
        ).where(PromptTrace.created_at >= since)
        count, in_tokens, out_tokens, total_cost = self.session.execute(total_q).one()

        by_stage_q = (
            select(
                PromptTrace.stage,
                func.count(PromptTrace.id),
                func.coalesce(func.sum(PromptTrace.total_cost_usd), 0.0),
                func.coalesce(func.sum(PromptTrace.input_tokens), 0),
                func.coalesce(func.sum(PromptTrace.output_tokens), 0),
            )
            .where(PromptTrace.created_at >= since)
            .group_by(PromptTrace.stage)
        )
        by_model_q = (
            select(
                PromptTrace.provider,
                PromptTrace.model,
                func.count(PromptTrace.id),
                func.coalesce(func.sum(PromptTrace.total_cost_usd), 0.0),
                func.coalesce(func.sum(PromptTrace.input_tokens), 0),
                func.coalesce(func.sum(PromptTrace.output_tokens), 0),
            )
            .where(PromptTrace.created_at >= since)
            .group_by(PromptTrace.provider, PromptTrace.model)
        )

        by_stage = [
            {
                "stage": stage,
                "calls": calls,
                "total_cost_usd": float(cost),
                "input_tokens": int(in_t or 0),
                "output_tokens": int(out_t or 0),
            }
            for stage, calls, cost, in_t, out_t in self.session.execute(by_stage_q).all()
        ]
        by_model = [
            {
                "provider": prov,
                "model": mdl,
                "calls": calls,
                "total_cost_usd": float(cost),
                "input_tokens": int(in_t or 0),
                "output_tokens": int(out_t or 0),
            }
            for prov, mdl, calls, cost, in_t, out_t in self.session.execute(by_model_q).all()
        ]

        return {
            "window_days": days,
            "calls": int(count),
            "input_tokens": int(in_tokens or 0),
            "output_tokens": int(out_tokens or 0),
            "total_cost_usd": float(total_cost or 0.0),
            "by_stage": by_stage,
            "by_model": by_model,
        }


class SourceCheckpointRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, source: str) -> SourceCheckpoint | None:
        q = select(SourceCheckpoint).where(SourceCheckpoint.source == source)
        return self.session.execute(q).scalar_one_or_none()

    def upsert(self, source: str, last_published_date: date | None) -> None:
        found = self.get(source)
        now = datetime.now(UTC)
        if found:
            found.last_fetch_at = now
            if last_published_date and (
                found.last_published_date is None or last_published_date > found.last_published_date
            ):
                found.last_published_date = last_published_date
            return
        self.session.add(
            SourceCheckpoint(
                source=source,
                last_fetch_at=now,
                last_published_date=last_published_date,
            )
        )


class CitationRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_edge(
        self,
        source_paper_id: str,
        target_paper_id: str,
        context: str | None = None,
    ) -> None:
        q = select(Citation).where(
            Citation.source_paper_id == source_paper_id,
            Citation.target_paper_id == target_paper_id,
        )
        found = self.session.execute(q).scalar_one_or_none()
        if found:
            if context:
                found.context = context
            return
        self.session.add(
            Citation(
                source_paper_id=source_paper_id,
                target_paper_id=target_paper_id,
                context=context,
            )
        )

    def list_all(self, limit: int = 10000) -> list[Citation]:
        """
        查询所有引用关系（带分页限制）

        Args:
            limit: 最大返回数量，默认 10000

        Returns:
            引用关系列表
        """
        q = select(Citation).order_by(Citation.source_paper_id).limit(limit)
        return list(self.session.execute(q).scalars())

    def list_for_paper_ids(self, paper_ids: list[str]) -> list[Citation]:
        if not paper_ids:
            return []
        q = select(Citation).where(
            Citation.source_paper_id.in_(paper_ids) | Citation.target_paper_id.in_(paper_ids)
        )
        return list(self.session.execute(q).scalars())


class ResearchKGRepository:
    """论文库级 GraphRAG 知识图谱仓储。"""

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def normalize_name(value: str | None) -> str:
        import re

        normalized = re.sub(r"\s+", " ", str(value or "").strip().casefold())
        return normalized[:512]

    @staticmethod
    def _merge_unique(existing: list | None, values: list | None, *, limit: int = 100) -> list:
        merged: list = []
        seen: set[str] = set()
        for item in list(existing or []) + list(values or []):
            key = str(item).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= limit:
                break
        return merged

    def get_node(self, node_id: str) -> ResearchKGNode | None:
        return self.session.get(ResearchKGNode, node_id)

    def get_paper_state(self, paper_id: str) -> ResearchKGPaperState | None:
        return self.session.get(ResearchKGPaperState, str(paper_id))

    def upsert_paper_state(
        self,
        *,
        paper_id: str,
        content_hash: str,
        status: str,
        node_count: int = 0,
        edge_count: int = 0,
        error: str = "",
        built_at: datetime | None = None,
    ) -> ResearchKGPaperState:
        state = self.get_paper_state(paper_id)
        if state is None:
            state = ResearchKGPaperState(paper_id=str(paper_id))
            self.session.add(state)
        state.content_hash = str(content_hash or "")
        state.status = str(status or "pending").strip() or "pending"
        state.node_count = max(0, int(node_count or 0))
        state.edge_count = max(0, int(edge_count or 0))
        state.error = str(error or "").strip()
        state.built_at = built_at
        self.session.flush()
        return state

    def list_paper_states(self, paper_ids: list[str] | None = None) -> list[ResearchKGPaperState]:
        query = select(ResearchKGPaperState)
        if paper_ids:
            query = query.where(ResearchKGPaperState.paper_id.in_([str(pid) for pid in paper_ids]))
        query = query.order_by(ResearchKGPaperState.updated_at.desc())
        return list(self.session.execute(query).scalars())

    def upsert_node(
        self,
        *,
        node_type: str,
        name: str,
        summary: str | None = None,
        paper_id: str | None = None,
        metadata: dict | None = None,
    ) -> ResearchKGNode:
        normalized_name = self.normalize_name(name)
        cleaned_type = str(node_type or "concept").strip().lower()[:64] or "concept"
        if not normalized_name:
            raise ValueError("KG node name is empty")
        node = self.session.execute(
            select(ResearchKGNode).where(
                ResearchKGNode.node_type == cleaned_type,
                ResearchKGNode.normalized_name == normalized_name,
            )
        ).scalar_one_or_none()
        if node is None:
            node = ResearchKGNode(
                node_type=cleaned_type,
                name=str(name or "").strip()[:512],
                normalized_name=normalized_name,
            )
            self.session.add(node)
        if len(str(summary or "").strip()) > len(node.summary or ""):
            node.summary = str(summary or "").strip()
        elif not node.summary:
            node.summary = str(summary or "").strip()
        merged_metadata = dict(node.metadata_json or {})
        merged_metadata.update(dict(metadata or {}))
        if paper_id:
            merged_metadata["paper_ids"] = self._merge_unique(
                list(merged_metadata.get("paper_ids") or []),
                [str(paper_id)],
                limit=500,
            )
        node.metadata_json = merged_metadata
        self.session.flush()
        return node

    def upsert_edge(
        self,
        *,
        source_node_id: str,
        target_node_id: str,
        edge_type: str,
        paper_id: str | None = None,
        evidence: str | None = None,
        weight: float = 1.0,
        metadata: dict | None = None,
    ) -> ResearchKGEdge:
        cleaned_type = str(edge_type or "related_to").strip().lower()[:64] or "related_to"
        edge = self.session.execute(
            select(ResearchKGEdge).where(
                ResearchKGEdge.source_node_id == str(source_node_id),
                ResearchKGEdge.target_node_id == str(target_node_id),
                ResearchKGEdge.edge_type == cleaned_type,
            )
        ).scalar_one_or_none()
        if edge is None:
            edge = ResearchKGEdge(
                source_node_id=str(source_node_id),
                target_node_id=str(target_node_id),
                edge_type=cleaned_type,
            )
            self.session.add(edge)
        cleaned_evidence = str(evidence or "").strip()
        if cleaned_evidence and (not edge.evidence or len(cleaned_evidence) > len(edge.evidence)):
            edge.evidence = cleaned_evidence[:2000]
        edge.weight = max(float(edge.weight or 0), float(weight or 1.0))
        merged_metadata = dict(edge.metadata_json or {})
        merged_metadata.update(dict(metadata or {}))
        if paper_id:
            merged_metadata["paper_ids"] = self._merge_unique(
                list(merged_metadata.get("paper_ids") or []),
                [str(paper_id)],
                limit=500,
            )
        if cleaned_evidence:
            evidence_items = list(merged_metadata.get("evidence_items") or [])
            evidence_items.append(
                {
                    "paper_id": str(paper_id or ""),
                    "evidence": cleaned_evidence[:600],
                }
            )
            unique_items: list[dict] = []
            seen_items: set[tuple[str, str]] = set()
            for item in evidence_items:
                if not isinstance(item, dict):
                    continue
                key = (str(item.get("paper_id") or ""), str(item.get("evidence") or ""))
                if not key[1] or key in seen_items:
                    continue
                seen_items.add(key)
                unique_items.append(item)
                if len(unique_items) >= 12:
                    break
            merged_metadata["evidence_items"] = unique_items
        edge.metadata_json = merged_metadata
        self.session.flush()
        return edge

    def list_nodes(
        self,
        *,
        node_type: str | None = None,
        limit: int | None = None,
    ) -> list[ResearchKGNode]:
        query = select(ResearchKGNode)
        if node_type:
            query = query.where(ResearchKGNode.node_type == str(node_type).strip().lower())
        query = query.order_by(ResearchKGNode.updated_at.desc(), ResearchKGNode.created_at.desc())
        if isinstance(limit, int) and limit > 0:
            query = query.limit(limit)
        return list(self.session.execute(query).scalars())

    def search_nodes(self, query_text: str, *, limit: int = 20) -> list[ResearchKGNode]:
        tokens = [
            token
            for token in str(query_text or "").strip().casefold().replace("/", " ").replace("-", " ").split()
            if len(token) >= 2
        ]
        if not tokens:
            return self.list_nodes(limit=limit)
        conditions = []
        for token in tokens[:8]:
            like = f"%{token}%"
            conditions.append(
                func.lower(ResearchKGNode.name).like(like)
                | func.lower(ResearchKGNode.summary).like(like)
                | func.lower(ResearchKGNode.normalized_name).like(like)
            )
        query = select(ResearchKGNode).where(or_(*conditions)).limit(max(1, min(limit * 3, 200)))
        candidates = list(self.session.execute(query).scalars())
        scored: list[tuple[int, str, ResearchKGNode]] = []
        for node in candidates:
            haystack = " ".join(
                [
                    str(node.name or ""),
                    str(node.normalized_name or ""),
                    str(node.summary or ""),
                ]
            ).casefold()
            score = sum(1 for token in tokens if token in haystack)
            scored.append((score, str(node.updated_at or ""), node))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [node for score, _updated, node in scored if score > 0][: max(1, limit)]

    def list_edges_for_node_ids(
        self,
        node_ids: list[str],
        *,
        limit: int = 80,
    ) -> list[ResearchKGEdge]:
        normalized_ids = [str(node_id) for node_id in node_ids if str(node_id or "").strip()]
        if not normalized_ids:
            return []
        query = (
            select(ResearchKGEdge)
            .where(
                ResearchKGEdge.source_node_id.in_(normalized_ids)
                | ResearchKGEdge.target_node_id.in_(normalized_ids)
            )
            .order_by(ResearchKGEdge.weight.desc(), ResearchKGEdge.updated_at.desc())
            .limit(max(1, min(limit, 500)))
        )
        return list(self.session.execute(query).scalars())

    def list_edges_for_paper_ids(
        self,
        paper_ids: list[str],
        *,
        limit: int = 80,
    ) -> list[ResearchKGEdge]:
        normalized_ids = {str(paper_id) for paper_id in paper_ids if str(paper_id or "").strip()}
        if not normalized_ids:
            return []
        edges = self.session.execute(
            select(ResearchKGEdge)
            .order_by(ResearchKGEdge.weight.desc(), ResearchKGEdge.updated_at.desc())
            .limit(max(1, min(limit * 5, 1000)))
        ).scalars()
        matched: list[ResearchKGEdge] = []
        for edge in edges:
            metadata = dict(edge.metadata_json or {})
            edge_paper_ids = {str(item) for item in list(metadata.get("paper_ids") or [])}
            if normalized_ids & edge_paper_ids:
                matched.append(edge)
                if len(matched) >= limit:
                    break
        return matched

    def stats(self) -> dict:
        node_count = int(self.session.scalar(select(func.count()).select_from(ResearchKGNode)) or 0)
        edge_count = int(self.session.scalar(select(func.count()).select_from(ResearchKGEdge)) or 0)
        state_count = int(self.session.scalar(select(func.count()).select_from(ResearchKGPaperState)) or 0)
        complete_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(ResearchKGPaperState)
                .where(ResearchKGPaperState.status == "complete")
            )
            or 0
        )
        failed_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(ResearchKGPaperState)
                .where(ResearchKGPaperState.status == "failed")
            )
            or 0
        )
        return {
            "node_count": node_count,
            "edge_count": edge_count,
            "paper_state_count": state_count,
            "complete_paper_count": complete_count,
            "failed_paper_count": failed_count,
        }


class LLMConfigRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_all(self) -> list[LLMProviderConfig]:
        q = select(LLMProviderConfig).order_by(LLMProviderConfig.created_at.desc())
        return list(self.session.execute(q).scalars())

    def get_active(self) -> LLMProviderConfig | None:
        q = select(LLMProviderConfig).where(LLMProviderConfig.is_active.is_(True))
        return self.session.execute(q).scalar_one_or_none()

    def get_by_id(self, config_id: str) -> LLMProviderConfig:
        cfg = self.session.get(LLMProviderConfig, config_id)
        if cfg is None:
            raise ValueError(f"llm_config {config_id} not found")
        return cfg

    def create(
        self,
        *,
        name: str,
        provider: str,
        api_key: str,
        api_base_url: str | None,
        model_skim: str,
        model_deep: str,
        model_vision: str | None,
        embedding_provider: str | None,
        embedding_api_key: str | None,
        embedding_api_base_url: str | None,
        model_embedding: str,
        model_fallback: str,
        image_provider: str | None = None,
        image_api_key: str | None = None,
        image_api_base_url: str | None = None,
        model_image: str | None = None,
    ) -> LLMProviderConfig:
        cfg = LLMProviderConfig(
            name=name,
            provider=provider,
            api_key=api_key,
            api_base_url=api_base_url,
            model_skim=model_skim,
            model_deep=model_deep,
            model_vision=model_vision,
            embedding_provider=(embedding_provider or "").strip(),
            embedding_api_key=(embedding_api_key or "").strip(),
            embedding_api_base_url=(embedding_api_base_url or "").strip(),
            model_embedding=model_embedding,
            model_fallback=model_fallback,
            image_provider=(image_provider or "").strip(),
            image_api_key=(image_api_key or "").strip(),
            image_api_base_url=(image_api_base_url or "").strip(),
            model_image=(model_image or "").strip(),
            is_active=False,
        )
        self.session.add(cfg)
        self.session.flush()
        return cfg

    def update(
        self,
        config_id: str,
        *,
        name: str | None = None,
        provider: str | None = None,
        api_key: str | None = None,
        api_base_url: str | None = None,
        model_skim: str | None = None,
        model_deep: str | None = None,
        model_vision: str | None = None,
        embedding_provider: str | None = None,
        embedding_api_key: str | None = None,
        embedding_api_base_url: str | None = None,
        model_embedding: str | None = None,
        model_fallback: str | None = None,
        image_provider: str | None = None,
        image_api_key: str | None = None,
        image_api_base_url: str | None = None,
        model_image: str | None = None,
    ) -> LLMProviderConfig:
        cfg = self.get_by_id(config_id)
        if name is not None:
            cfg.name = name
        if provider is not None:
            cfg.provider = provider
        if api_key is not None:
            cfg.api_key = api_key
        if api_base_url is not None:
            cfg.api_base_url = api_base_url
        if model_skim is not None:
            cfg.model_skim = model_skim
        if model_deep is not None:
            cfg.model_deep = model_deep
        if model_vision is not None:
            cfg.model_vision = model_vision
        if embedding_provider is not None:
            cfg.embedding_provider = embedding_provider.strip()
        if embedding_api_key is not None:
            cfg.embedding_api_key = embedding_api_key.strip()
        if embedding_api_base_url is not None:
            cfg.embedding_api_base_url = embedding_api_base_url.strip()
        if model_embedding is not None:
            cfg.model_embedding = model_embedding
        if model_fallback is not None:
            cfg.model_fallback = model_fallback
        if image_provider is not None:
            cfg.image_provider = image_provider.strip()
        if image_api_key is not None:
            cfg.image_api_key = image_api_key.strip()
        if image_api_base_url is not None:
            cfg.image_api_base_url = image_api_base_url.strip()
        if model_image is not None:
            cfg.model_image = model_image.strip()
        cfg.updated_at = datetime.now(UTC)
        self.session.flush()
        return cfg

    def delete(self, config_id: str) -> None:
        cfg = self.session.get(LLMProviderConfig, config_id)
        if cfg is not None:
            self.session.delete(cfg)

    def activate(self, config_id: str) -> LLMProviderConfig:
        """激活指定配置，同时取消其他配置的激活状态"""
        all_cfgs = self.list_all()
        for c in all_cfgs:
            c.is_active = c.id == config_id
        self.session.flush()
        return self.get_by_id(config_id)

    def deactivate_all(self) -> None:
        """取消所有配置的激活状态（回退到 .env 默认配置）"""
        all_cfgs = self.list_all()
        for c in all_cfgs:
            c.is_active = False
        self.session.flush()


class GeneratedContentRepository:
    """持久化生成内容（Wiki / Brief）"""

    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        *,
        content_type: str,
        title: str,
        markdown: str,
        keyword: str | None = None,
        paper_id: str | None = None,
        metadata_json: dict | None = None,
    ) -> GeneratedContent:
        gc = GeneratedContent(
            content_type=content_type,
            title=title,
            markdown=markdown,
            keyword=keyword,
            paper_id=paper_id,
            metadata_json=metadata_json or {},
        )
        self.session.add(gc)
        self.session.flush()
        return gc

    def list_by_type(self, content_type: str, limit: int = 50) -> list[GeneratedContent]:
        q = (
            select(GeneratedContent)
            .where(GeneratedContent.content_type == content_type)
            .order_by(GeneratedContent.created_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(q).scalars())

    def list_by_type_and_keyword(
        self,
        content_type: str,
        keyword: str,
        limit: int = 50,
    ) -> list[GeneratedContent]:
        q = (
            select(GeneratedContent)
            .where(
                GeneratedContent.content_type == content_type,
                GeneratedContent.keyword == keyword,
            )
            .order_by(GeneratedContent.created_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(q).scalars())

    def get_latest_by_type_and_keyword(
        self,
        content_type: str,
        keyword: str,
    ) -> GeneratedContent | None:
        q = (
            select(GeneratedContent)
            .where(
                GeneratedContent.content_type == content_type,
                GeneratedContent.keyword == keyword,
            )
            .order_by(GeneratedContent.created_at.desc())
            .limit(1)
        )
        return self.session.execute(q).scalars().first()

    def get_by_id(self, content_id: str) -> GeneratedContent:
        gc = self.session.get(GeneratedContent, content_id)
        if gc is None:
            raise ValueError(f"generated_content {content_id} not found")
        return gc

    def delete(self, content_id: str) -> None:
        gc = self.session.get(GeneratedContent, content_id)
        if gc is not None:
            self.session.delete(gc)


class ActionRepository:
    """论文入库行动记录的数据仓储"""

    def __init__(self, session: Session):
        self.session = session

    def create_action(
        self,
        action_type: ActionType,
        title: str,
        paper_ids: list[str],
        query: str | None = None,
        topic_id: str | None = None,
    ) -> CollectionAction:
        """创建一条行动记录并关联论文"""
        action = CollectionAction(
            action_type=action_type,
            title=title,
            query=query,
            topic_id=topic_id,
            paper_count=len(paper_ids),
        )
        self.session.add(action)
        self.session.flush()

        for pid in paper_ids:
            self.session.add(
                ActionPaper(
                    action_id=action.id,
                    paper_id=pid,
                )
            )
        self.session.flush()
        return action

    def list_actions(
        self,
        action_type: str | None = None,
        topic_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[CollectionAction], int]:
        """分页列出行动记录"""
        base = select(CollectionAction)
        count_q = select(func.count()).select_from(CollectionAction)

        if action_type:
            base = base.where(CollectionAction.action_type == action_type)
            count_q = count_q.where(CollectionAction.action_type == action_type)
        if topic_id:
            base = base.where(CollectionAction.topic_id == topic_id)
            count_q = count_q.where(CollectionAction.topic_id == topic_id)

        total = self.session.execute(count_q).scalar() or 0
        rows = (
            self.session.execute(
                base.order_by(CollectionAction.created_at.desc()).limit(limit).offset(offset)
            )
            .scalars()
            .all()
        )
        return list(rows), total

    def get_action(self, action_id: str) -> CollectionAction | None:
        return self.session.get(CollectionAction, action_id)

    def delete_action(self, action_id: str) -> bool:
        action = self.session.get(CollectionAction, action_id)
        if action is None:
            return False
        self.session.delete(action)
        self.session.flush()
        return True

    def get_paper_ids_by_action(self, action_id: str) -> list[str]:
        """获取某次行动关联的所有论文 ID"""
        rows = (
            self.session.execute(
                select(ActionPaper.paper_id).where(ActionPaper.action_id == action_id)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def get_papers_by_action(
        self,
        action_id: str,
        limit: int = 200,
    ) -> list[Paper]:
        """获取某次行动关联的论文列表"""
        rows = (
            self.session.execute(
                select(Paper)
                .join(ActionPaper, Paper.id == ActionPaper.paper_id)
                .where(ActionPaper.action_id == action_id)
                .order_by(Paper.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows)


class ProjectResearchWikiRepository:
    """项目 research wiki 仓储。"""

    def __init__(self, session: Session):
        self.session = session

    def get_node(self, node_id: str) -> ProjectResearchWikiNode | None:
        return self.session.get(ProjectResearchWikiNode, node_id)

    def get_node_by_key(self, project_id: str, node_key: str) -> ProjectResearchWikiNode | None:
        return self.session.execute(
            select(ProjectResearchWikiNode).where(
                ProjectResearchWikiNode.project_id == project_id,
                ProjectResearchWikiNode.node_key == node_key,
            )
        ).scalar_one_or_none()

    def list_nodes(
        self,
        project_id: str,
        *,
        node_type: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[ProjectResearchWikiNode]:
        query = select(ProjectResearchWikiNode).where(ProjectResearchWikiNode.project_id == project_id)
        if node_type:
            query = query.where(ProjectResearchWikiNode.node_type == node_type)
        if status:
            query = query.where(ProjectResearchWikiNode.status == status)
        query = query.order_by(ProjectResearchWikiNode.updated_at.desc(), ProjectResearchWikiNode.created_at.desc())
        if isinstance(limit, int) and limit > 0:
            query = query.limit(limit)
        return list(self.session.execute(query).scalars().all())

    def upsert_node(
        self,
        *,
        project_id: str,
        node_key: str,
        node_type: str,
        title: str,
        summary: str | None = None,
        body_md: str | None = None,
        status: str | None = None,
        source_paper_id: str | None = None,
        source_run_id: str | None = None,
        metadata: dict | None = None,
    ) -> ProjectResearchWikiNode:
        node = self.get_node_by_key(project_id, node_key)
        if node is None:
            node = ProjectResearchWikiNode(
                project_id=project_id,
                node_key=node_key,
            )
            self.session.add(node)
        node.node_type = str(node_type or "note").strip() or "note"
        node.title = str(title or "").strip()[:512] or node.node_key
        node.summary = str(summary or "").strip()
        node.body_md = str(body_md or "").strip()
        node.status = str(status or "active").strip() or "active"
        node.source_paper_id = str(source_paper_id or "").strip() or None
        node.source_run_id = str(source_run_id or "").strip() or None
        node.metadata_json = dict(metadata or {})
        self.session.flush()
        return node

    def update_node(self, node_id: str, **kwargs) -> ProjectResearchWikiNode | None:
        node = self.get_node(node_id)
        if node is None:
            return None
        for key, value in kwargs.items():
            if key == "metadata":
                node.metadata_json = dict(value or {})
            elif hasattr(node, key):
                setattr(node, key, value)
        self.session.flush()
        return node

    def list_edges(
        self,
        project_id: str,
        *,
        edge_type: str | None = None,
        limit: int | None = None,
    ) -> list[ProjectResearchWikiEdge]:
        query = select(ProjectResearchWikiEdge).where(ProjectResearchWikiEdge.project_id == project_id)
        if edge_type:
            query = query.where(ProjectResearchWikiEdge.edge_type == edge_type)
        query = query.order_by(ProjectResearchWikiEdge.created_at.desc())
        if isinstance(limit, int) and limit > 0:
            query = query.limit(limit)
        return list(self.session.execute(query).scalars().all())

    def delete_edges_for_source(
        self,
        *,
        project_id: str,
        source_node_id: str,
        edge_type: str | None = None,
    ) -> int:
        query = delete(ProjectResearchWikiEdge).where(
            ProjectResearchWikiEdge.project_id == project_id,
            ProjectResearchWikiEdge.source_node_id == source_node_id,
        )
        if edge_type:
            query = query.where(ProjectResearchWikiEdge.edge_type == edge_type)
        result = self.session.execute(query)
        self.session.flush()
        return int(result.rowcount or 0)

    def upsert_edge(
        self,
        *,
        project_id: str,
        source_node_id: str,
        target_node_id: str,
        edge_type: str,
        metadata: dict | None = None,
    ) -> ProjectResearchWikiEdge:
        edge = self.session.execute(
            select(ProjectResearchWikiEdge).where(
                ProjectResearchWikiEdge.project_id == project_id,
                ProjectResearchWikiEdge.source_node_id == source_node_id,
                ProjectResearchWikiEdge.target_node_id == target_node_id,
                ProjectResearchWikiEdge.edge_type == edge_type,
            )
        ).scalar_one_or_none()
        if edge is None:
            edge = ProjectResearchWikiEdge(
                project_id=project_id,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                edge_type=edge_type,
            )
            self.session.add(edge)
        edge.metadata_json = dict(metadata or {})
        self.session.flush()
        return edge


class EmailConfigRepository:
    """邮箱配置仓储"""

    def __init__(self, session: Session):
        self.session = session

    def list_all(self) -> list[EmailConfig]:
        """获取所有邮箱配置"""
        q = select(EmailConfig).order_by(EmailConfig.created_at.desc())
        return list(self.session.execute(q).scalars())

    def get_active(self) -> EmailConfig | None:
        """获取激活的邮箱配置"""
        q = select(EmailConfig).where(EmailConfig.is_active == True)
        return self.session.execute(q).scalar_one_or_none()

    def get_by_id(self, config_id: str) -> EmailConfig | None:
        """根据 ID 获取配置"""
        return self.session.get(EmailConfig, config_id)

    def create(
        self,
        name: str,
        smtp_server: str,
        smtp_port: int,
        smtp_use_tls: bool,
        sender_email: str,
        sender_name: str,
        username: str,
        password: str,
    ) -> EmailConfig:
        """创建邮箱配置"""
        config = EmailConfig(
            name=name,
            smtp_server=smtp_server,
            smtp_port=smtp_port,
            smtp_use_tls=smtp_use_tls,
            sender_email=sender_email,
            sender_name=sender_name,
            username=username,
            password=password,
        )
        self.session.add(config)
        self.session.flush()
        return config

    def update(self, config_id: str, **kwargs) -> EmailConfig | None:
        """更新邮箱配置"""
        config = self.get_by_id(config_id)
        if config:
            for key, value in kwargs.items():
                if hasattr(config, key):
                    setattr(config, key, value)
            self.session.flush()
        return config

    def delete(self, config_id: str) -> bool:
        """删除邮箱配置"""
        config = self.get_by_id(config_id)
        if config:
            self.session.delete(config)
            self.session.flush()
            return True
        return False

    def set_active(self, config_id: str) -> EmailConfig | None:
        """激活指定配置，取消其他配置的激活状态"""
        all_configs = self.list_all()
        for cfg in all_configs:
            cfg.is_active = False
        config = self.get_by_id(config_id)
        if config:
            config.is_active = True
            self.session.flush()
        return config


class FeishuConfigRepository:
    """飞书通知配置仓储。"""

    def __init__(self, session: Session):
        self.session = session

    def get_active(self) -> FeishuConfig | None:
        q = (
            select(FeishuConfig)
            .where(FeishuConfig.is_active == True)
            .order_by(FeishuConfig.updated_at.desc())
        )
        return self.session.execute(q).scalar_one_or_none()

    def upsert_active(
        self,
        *,
        mode: str,
        webhook_url: str | None,
        webhook_secret: str | None,
        bridge_url: str | None,
        timeout_seconds: int,
        timeout_action: str,
    ) -> FeishuConfig:
        config = self.get_active()
        if config is None:
            config = FeishuConfig(
                mode=mode,
                webhook_url=webhook_url,
                webhook_secret=webhook_secret,
                bridge_url=bridge_url,
                timeout_seconds=timeout_seconds,
                timeout_action=timeout_action,
                is_active=True,
            )
            self.session.add(config)
        else:
            config.mode = mode
            config.webhook_url = webhook_url
            config.webhook_secret = webhook_secret
            config.bridge_url = bridge_url
            config.timeout_seconds = timeout_seconds
            config.timeout_action = timeout_action
            config.is_active = True
        self.session.flush()
        return config
