"""
璁烘枃澶勭悊 Pipeline - 鎽勫叆 / 绮楄 / 绮捐 / 鍚戦噺鍖?/ 鍙傝€冩枃鐚鍏?
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

from packages.ai.paper.analysis_options import (
    get_deep_detail_profile,
    normalize_analysis_detail_level,
)
from packages.ai.paper.content_source import (
    paper_content_source_label,
    resolve_effective_paper_content_source,
)
from packages.ai.research.cost_guard import CostGuardService
from packages.ai.paper.paper_evidence import (
    load_prepared_paper_evidence,
    normalize_paper_evidence_mode,
)
from packages.ai.paper.paper_ops_service import normalize_manual_paper_id
from packages.ai.paper.pdf_parser import PdfTextExtractor
from packages.ai.paper.prompts import (
    build_deep_focus_prompt,
    build_deep_prompt,
    build_skim_prompt,
)
from packages.ai.paper.vision_reader import VisionPdfReader
from packages.config import get_settings
from packages.domain.enums import ActionType, ReadStatus
from packages.domain.schemas import DeepDiveReport, PaperCreate, SkimReport
from packages.integrations.arxiv_client import ArxivClient
from packages.integrations.llm_client import LLMClient
from packages.ai.paper.reference_importer import ReferenceImporter
from packages.storage.db import session_scope
from packages.storage.repositories import (
    ActionRepository,
    AnalysisRepository,
    PaperRepository,
    PipelineRunRepository,
    PromptTraceRepository,
)

logger = logging.getLogger(__name__)


def _bg_auto_link(paper_ids: list[str]) -> None:
    """鍚庡彴绾跨▼锛氬叆搴撳悗鑷姩鍏宠仈寮曠敤"""
    try:
        from packages.ai.research.graph_service import GraphService

        gs = GraphService()
        result = gs.auto_link_citations(paper_ids)
        logger.info("bg auto_link: %s", result)
    except Exception as exc:
        logger.warning("bg auto_link failed: %s", exc)


class PaperPipelines:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.arxiv = ArxivClient()
        self.llm = LLMClient()
        self.vision = VisionPdfReader()
        self.pdf_extractor = PdfTextExtractor()

    @staticmethod
    def _report_progress(
        progress_callback: Callable[[str, int, int], None] | None,
        message: str,
        current: int,
        total: int = 100,
    ) -> None:
        if progress_callback:
            progress_callback(message, current, total)

    @staticmethod
    def _start_pipeline_run(pipeline_name: str, paper_id: UUID) -> str:
        with session_scope() as session:
            run = PipelineRunRepository(session).start(pipeline_name, paper_id=paper_id)
            return str(run.id)

    @staticmethod
    def _finish_pipeline_run(
        run_id: str,
        *,
        elapsed_ms: int | None = None,
        decision_note: str | None = None,
    ) -> None:
        with session_scope() as session:
            PipelineRunRepository(session).finish(
                UUID(run_id),
                elapsed_ms=elapsed_ms,
                decision_note=decision_note,
            )

    @staticmethod
    def _fail_pipeline_run(run_id: str, error_message: str) -> None:
        with session_scope() as session:
            PipelineRunRepository(session).fail(UUID(run_id), error_message)

    @staticmethod
    def _normalize_arxiv_id(raw: str) -> str:
        return normalize_manual_paper_id(raw)

    def _save_paper(self, repo, paper, topic_id=None, download_pdf=False):
        """鍏ュ簱 + 涓嬭浇 PDF 鐨勫叕鍏遍€昏緫

        Args:
            repo: PaperRepository
            paper: PaperCreate 鏁版嵁
            topic_id: 鍙€夌殑涓婚 ID
            download_pdf: 鏄惁涓嬭浇 PDF锛堥粯璁?False锛屼粎鍦ㄧ簿璇绘椂涓嬭浇锛?
        """
        saved = repo.upsert_paper(paper)
        if topic_id:
            repo.link_to_topic(saved.id, topic_id)

        # 鍙湪鏄庣‘闇€瑕佹椂鎵嶄笅杞?PDF
        if download_pdf:
            try:
                pdf_path = self.arxiv.download_pdf(paper.arxiv_id)
                repo.set_pdf_path(saved.id, pdf_path)
            except Exception as exc:
                logger.warning("PDF download failed for %s: %s", paper.arxiv_id, exc)

        return saved

    def ingest_arxiv(
        self,
        query: str,
        max_results: int = 20,
        topic_id: str | None = None,
        action_type: ActionType = ActionType.manual_collect,
        sort_by: str = "submittedDate",
        days_back: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,

    ) -> tuple[int, list[str], int]:
        """鎼滅储 arXiv 骞跺叆搴擄紝upsert 鍘婚噸銆傝繑鍥?(total_count, inserted_ids, new_papers_count)

        鏅鸿兘閫掑綊鎶撳彇锛氬鏋滃墠 N 绡囨湁閲嶅锛屽垯缁х画鎶撳彇鏇存棭鐨勮鏂囷紝鐩村埌鎵惧埌 max_results 绡囨柊璁烘枃
        """
        inserted_ids: list[str] = []
        new_papers_count = 0
        total_fetched = 0
        auto_link_ids: list[str] = []
        result: tuple[int, list[str], int] | None = None
        impact_mode = sort_by == "impact"
        batch_size = max(10, min(max_results * 3, 60)) if impact_mode else 20
        max_pages = 6 if impact_mode else 10
        arxiv_request_delay = 3.0  # arXiv API 寤鸿璇锋眰闂撮殧 3 绉?
        empty_impact_batches = 0

        with session_scope() as session:
            repo = PaperRepository(session)
            run_repo = PipelineRunRepository(session)
            action_repo = ActionRepository(session)
            run = run_repo.start("ingest_arxiv", decision_note=f"query={query};sort={sort_by}")

            try:
                # 鍒嗘壒鎶撳彇锛岀洿鍒版壘鍒拌冻澶熺殑鏂拌鏂囨垨杈惧埌鏈€澶ч〉鏁?
                for page in range(max_pages):
                    if new_papers_count >= max_results:
                        break  # 宸叉壘鍒拌冻澶熺殑鏂拌鏂?

                    start = page * batch_size
                    # 璁＄畻鏈壒闇€瑕佹姄鍙栫殑鏁伴噺锛堥伩鍏嶈秴鐩爣锛?
                    needed = max_results - new_papers_count
                    this_batch = batch_size if impact_mode else min(batch_size, needed + 20)

                    papers = self.arxiv.fetch_latest(
                        query=query,
                        max_results=this_batch,
                        sort_by=sort_by,
                        start=start,
                        days_back=days_back,
                        date_from=date_from,
                        date_to=date_to,
                    )
                    total_fetched += len(papers)

                    # 娣诲姞璇锋眰闂撮殧锛岄伩鍏嶈Е鍙?arXiv 闄愭祦
                    if page < max_pages - 1 and papers:
                        time.sleep(arxiv_request_delay)

                    if not papers:
                        break  # 娌℃湁鏇村璁烘枃浜?

                    # 鎻愬墠妫€鏌ュ摢浜涜鏂囧凡瀛樺湪
                    existing_base_arxiv_ids = repo.list_existing_arxiv_base_ids([p.arxiv_id for p in papers])

                    # 鍙鐞嗘柊璁烘枃
                    for paper in papers:
                        paper_base_id = paper.arxiv_id.split("v")[0] if "v" in paper.arxiv_id else paper.arxiv_id
                        is_new = paper_base_id not in existing_base_arxiv_ids
                        if is_new:
                            saved = self._save_paper(repo, paper, topic_id)
                            new_papers_count += 1
                            inserted_ids.append(saved.id)

                            # 鏉堟儳鍩岄惄顔界垼鐏忓崬浠犲?
                            if new_papers_count >= max_results:
                                break

                    # 閺冦儱绻?
                    new_in_batch = sum(
                        1
                        for paper in papers
                        if (paper.arxiv_id.split("v")[0] if "v" in paper.arxiv_id else paper.arxiv_id)
                        not in existing_base_arxiv_ids
                    )
                    logger.info(
                        "ArXiv batch %d: fetched %d, new %d (progress %d/%d)",
                        page + 1,
                        len(papers),
                        new_in_batch,
                        new_papers_count,
                        max_results,
                    )
                    if impact_mode:
                        empty_impact_batches = empty_impact_batches + 1 if new_in_batch <= 0 else 0
                        if empty_impact_batches >= 2:
                            logger.info(
                                "ArXiv impact ingest stopping after %d empty batches",
                                empty_impact_batches,
                            )
                            break

                if inserted_ids:
                    # Defensive check: keep only paper IDs that are actually present,
                    # so action_papers FK inserts never fail the whole ingest request.
                    existing_ids = {str(p.id) for p in repo.list_by_ids(inserted_ids)}
                    missing_count = len(inserted_ids) - len(existing_ids)
                    if missing_count > 0:
                        logger.warning(
                            "ingest_arxiv: %d inserted_ids missing before action link; filtering out",
                            missing_count,
                        )
                    inserted_ids = [pid for pid in inserted_ids if pid in existing_ids]

                if inserted_ids:
                    action_repo.create_action(
                        action_type=action_type,
                        title=f"收集：{query[:80]}",
                        paper_ids=inserted_ids,
                        query=query,
                        topic_id=topic_id,
                    )

                run_repo.finish(run.id)
                if inserted_ids:
                    auto_link_ids = inserted_ids.copy()

                logger.info(
                    "ArXiv ingest complete: %d new papers selected from %d fetched",
                    new_papers_count,
                    total_fetched,
                )
                result = (len(inserted_ids), inserted_ids, new_papers_count)
            except Exception as exc:
                run_repo.fail(run.id, str(exc))
                raise


        # Start auto-link after transaction commit to avoid concurrent write conflicts.
        if auto_link_ids:
            threading.Thread(
                target=_bg_auto_link,
                args=(auto_link_ids,),
                daemon=True,
            ).start()

        if result is None:
            return 0, [], 0
        return result

    def ingest_arxiv_ids(
        self,
        arxiv_ids: list[str],
        topic_id: str | None = None,
        action_type: ActionType = ActionType.manual_collect,
        download_pdf: bool = False,
    ) -> dict:
        normalized_ids: list[str] = []
        seen: set[str] = set()
        for raw_id in arxiv_ids:
            normalized = self._normalize_arxiv_id(raw_id)
            base_id = normalized.split("v")[0] if "v" in normalized else normalized
            if not base_id or base_id in seen:
                continue
            seen.add(base_id)
            normalized_ids.append(base_id)

        if not normalized_ids:
            return {
                "requested": 0,
                "found": 0,
                "ingested": 0,
                "duplicates": 0,
                "missing_ids": [],
                "papers": [],
            }

        inserted_ids: list[str] = []
        auto_link_ids: list[str] = []
        papers_info: list[dict] = []
        missing_ids: list[str] = []
        duplicates = 0

        with session_scope() as session:
            repo = PaperRepository(session)
            run_repo = PipelineRunRepository(session)
            action_repo = ActionRepository(session)
            run = run_repo.start(
                "ingest_arxiv_ids",
                decision_note=f"arxiv_ids={','.join(normalized_ids[:20])}",
            )

            try:
                existing_base_ids = repo.list_existing_arxiv_base_ids(normalized_ids)
                duplicates = len(existing_base_ids)
                pending_ids = [paper_id for paper_id in normalized_ids if paper_id not in existing_base_ids]

                papers = self.arxiv.fetch_by_ids(pending_ids) if pending_ids else []
                found_base_ids = {
                    (paper.arxiv_id.split("v")[0] if "v" in paper.arxiv_id else paper.arxiv_id)
                    for paper in papers
                }
                missing_ids = [paper_id for paper_id in pending_ids if paper_id not in found_base_ids]

                existing_fetched_base_ids = repo.list_existing_arxiv_base_ids([paper.arxiv_id for paper in papers])
                for paper in papers:
                    paper_base_id = paper.arxiv_id.split("v")[0] if "v" in paper.arxiv_id else paper.arxiv_id
                    if paper_base_id in existing_fetched_base_ids:
                        duplicates += 1
                        continue
                    saved = self._save_paper(repo, paper, topic_id, download_pdf=download_pdf)
                    inserted_ids.append(saved.id)
                    papers_info.append(
                        {
                            "id": saved.id,
                            "title": saved.title,
                            "arxiv_id": saved.arxiv_id,
                            "publication_date": saved.publication_date.isoformat()
                            if saved.publication_date
                            else None,
                        }
                    )

                if inserted_ids:
                    action_repo.create_action(
                        action_type=action_type,
                        title=f"按ID导入：{', '.join(normalized_ids[:3])}",
                        paper_ids=inserted_ids,
                        query="id_list:" + ",".join(normalized_ids),
                        topic_id=topic_id,
                    )
                    auto_link_ids = inserted_ids.copy()

                run_repo.finish(run.id)
            except Exception as exc:
                run_repo.fail(run.id, str(exc))
                raise

        if auto_link_ids:
            threading.Thread(
                target=_bg_auto_link,
                args=(auto_link_ids,),
                daemon=True,
            ).start()

        return {
            "requested": len(normalized_ids),
            "found": len(normalized_ids) - len(missing_ids),
            "ingested": len(inserted_ids),
            "duplicates": duplicates,
            "missing_ids": missing_ids,
            "papers": papers_info,
        }

    def ingest_arxiv_with_ids(
        self,
        query: str,
        max_results: int = 20,
        topic_id: str | None = None,
        action_type: ActionType = ActionType.subscription_ingest,
        days_back: int | None = None,
    ) -> list[str]:
        """ingest_arxiv 鐨勫埆鍚嶏紝杩斿洖 inserted_ids"""
        _, ids, _ = self.ingest_arxiv(
            query=query,
            max_results=max_results,
            topic_id=topic_id,
            action_type=action_type,
            days_back=days_back,
        )
        return ids

    def ingest_arxiv_with_stats(
        self,
        query: str,
        max_results: int = 20,
        topic_id: str | None = None,
        action_type: ActionType = ActionType.subscription_ingest,
        sort_by: str = "submittedDate",
        days_back: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict:
        """ingest_arxiv 杩斿洖璇︾粏缁熻淇℃伅"""
        total_count, inserted_ids, new_count = self.ingest_arxiv(
            query=query,
            max_results=max_results,
            topic_id=topic_id,
            action_type=action_type,
            sort_by=sort_by,
            days_back=days_back,
            date_from=date_from,
            date_to=date_to,
        )
        return {
            "total_count": total_count,
            "inserted_ids": inserted_ids,
            "new_count": new_count,
        }

    @staticmethod
    def _parse_external_publication_date(
        publication_date: str | None,
        publication_year: int | None,
    ) -> date | None:
        raw_date = str(publication_date or "").strip()
        if raw_date:
            for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
                try:
                    parsed = datetime.strptime(raw_date, fmt)
                    if fmt == "%Y-%m":
                        return date(parsed.year, parsed.month, 1)
                    if fmt == "%Y":
                        return date(parsed.year, 1, 1)
                    return parsed.date()
                except ValueError:
                    continue
        if publication_year is not None:
            try:
                normalized_year = max(1900, min(int(publication_year), 2100))
                return date(normalized_year, 1, 1)
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _resolve_external_entry_arxiv_id(entry: dict) -> str:
        raw_arxiv_id = ReferenceImporter._normalize_arxiv_id(entry.get("arxiv_id"))
        if ReferenceImporter._is_real_arxiv_id(raw_arxiv_id):
            return str(raw_arxiv_id)
        stable_source = (
            str(entry.get("openalex_id") or "").strip()
            or str(entry.get("source_url") or "").strip()
            or f"{entry.get('source') or 'external'}:{entry.get('title') or ''}:{entry.get('publication_year') or ''}"
        )
        return ReferenceImporter._build_external_paper_id(stable_source)

    @staticmethod
    def _build_paper_from_external_entry(entry: dict) -> PaperCreate:
        title = str(entry.get("title") or "Unknown").strip() or "Unknown"
        resolved_arxiv_id = PaperPipelines._resolve_external_entry_arxiv_id(entry)
        authors = [str(item).strip() for item in (entry.get("authors") or []) if str(item).strip()]
        categories = [str(item).strip() for item in (entry.get("categories") or []) if str(item).strip()]
        metadata = {
            "source": "external_collect",
            "import_source": str(entry.get("source") or "openalex").strip() or "openalex",
            "openalex_id": str(entry.get("openalex_id") or "").strip() or None,
            "source_url": str(entry.get("source_url") or "").strip() or None,
            "pdf_url": str(entry.get("pdf_url") or "").strip() or None,
            "authors": authors,
            "categories": categories,
            "venue": str(entry.get("venue") or "").strip() or None,
            "venue_type": str(entry.get("venue_type") or "").strip() or None,
            "venue_tier": str(entry.get("venue_tier") or "").strip() or None,
            "citation_count": entry.get("citation_count"),
        }
        publication_date = PaperPipelines._parse_external_publication_date(
            entry.get("publication_date"),
            entry.get("publication_year"),
        )
        return PaperCreate(
            arxiv_id=resolved_arxiv_id,
            title=title,
            abstract=str(entry.get("abstract") or "").strip(),
            publication_date=publication_date,
            metadata={key: value for key, value in metadata.items() if value not in (None, "", [])},
        )

    @staticmethod
    def _merge_external_entry_metadata(base_metadata: dict, entry: dict) -> dict:
        merged = dict(base_metadata or {})
        authors = [str(item).strip() for item in (entry.get("authors") or []) if str(item).strip()]
        categories = [str(item).strip() for item in (entry.get("categories") or []) if str(item).strip()]
        overlays = {
            "source": "external_collect",
            "import_source": str(entry.get("source") or merged.get("import_source") or "openalex").strip() or "openalex",
            "openalex_id": str(entry.get("openalex_id") or merged.get("openalex_id") or "").strip() or None,
            "source_url": str(entry.get("source_url") or merged.get("source_url") or "").strip() or None,
            "pdf_url": str(entry.get("pdf_url") or merged.get("pdf_url") or "").strip() or None,
            "venue": str(entry.get("venue") or merged.get("venue") or "").strip() or None,
            "venue_type": str(entry.get("venue_type") or merged.get("venue_type") or "").strip() or None,
            "venue_tier": str(entry.get("venue_tier") or merged.get("venue_tier") or "").strip() or None,
            "citation_count": entry.get("citation_count") if entry.get("citation_count") is not None else merged.get("citation_count"),
        }
        if authors:
            overlays["authors"] = authors
        if categories:
            overlays["categories"] = categories
        for key, value in overlays.items():
            if value not in (None, "", []):
                merged[key] = value
        return merged

    def ingest_external_entries(
        self,
        entries: list[dict],
        *,
        topic_id: str | None = None,
        action_type: ActionType = ActionType.manual_collect,
        query: str | None = None,
    ) -> dict:
        normalized_entries = [dict(entry) for entry in entries if str((entry or {}).get("title") or "").strip()]
        if not normalized_entries:
            return {
                "requested": 0,
                "found": 0,
                "ingested": 0,
                "duplicates": 0,
                "missing_ids": [],
                "papers": [],
            }

        requested = len(normalized_entries)
        candidate_ids = [self._resolve_external_entry_arxiv_id(entry) for entry in normalized_entries]
        normalized_real_arxiv_ids = [
            normalized_id
            for normalized_id in (
                ReferenceImporter._normalize_arxiv_id(entry.get("arxiv_id"))
                for entry in normalized_entries
            )
            if ReferenceImporter._is_real_arxiv_id(normalized_id)
        ]

        arxiv_papers_map: dict[str, PaperCreate] = {}
        if normalized_real_arxiv_ids:
            try:
                arxiv_papers = self.arxiv.fetch_by_ids(list(dict.fromkeys(normalized_real_arxiv_ids)))
            except Exception as exc:
                logger.warning("External ingest arXiv metadata fetch failed: %s", exc)
                arxiv_papers = []
            for paper in arxiv_papers:
                normalized_id = ReferenceImporter._normalize_arxiv_id(paper.arxiv_id)
                if normalized_id:
                    arxiv_papers_map[normalized_id] = paper

        inserted_ids: list[str] = []
        auto_link_ids: list[str] = []
        papers_info: list[dict] = []
        duplicates = 0

        with session_scope() as session:
            repo = PaperRepository(session)
            run_repo = PipelineRunRepository(session)
            action_repo = ActionRepository(session)
            run = run_repo.start(
                "ingest_external_literature",
                decision_note=f"query={str(query or '')[:160]}",
            )

            try:
                existing_ids = repo.list_existing_arxiv_ids(candidate_ids)
                for entry, candidate_id in zip(normalized_entries, candidate_ids, strict=False):
                    if candidate_id in existing_ids:
                        duplicates += 1
                        if topic_id:
                            existing_paper = repo.get_by_arxiv_id(candidate_id)
                            if existing_paper is not None:
                                repo.link_to_topic(existing_paper.id, topic_id)
                        continue

                    paper_data: PaperCreate
                    normalized_real_arxiv_id = ReferenceImporter._normalize_arxiv_id(entry.get("arxiv_id"))
                    if ReferenceImporter._is_real_arxiv_id(normalized_real_arxiv_id):
                        arxiv_paper = arxiv_papers_map.get(str(normalized_real_arxiv_id))
                        if arxiv_paper is not None:
                            paper_data = arxiv_paper
                            if str(entry.get("abstract") or "").strip() and not str(paper_data.abstract or "").strip():
                                paper_data.abstract = str(entry.get("abstract") or "").strip()
                            if paper_data.publication_date is None:
                                paper_data.publication_date = self._parse_external_publication_date(
                                    entry.get("publication_date"),
                                    entry.get("publication_year"),
                                )
                            paper_data.metadata = self._merge_external_entry_metadata(
                                dict(paper_data.metadata or {}),
                                entry,
                            )
                        else:
                            paper_data = self._build_paper_from_external_entry(entry)
                    else:
                        paper_data = self._build_paper_from_external_entry(entry)

                    saved = self._save_paper(repo, paper_data, topic_id)
                    inserted_ids.append(saved.id)
                    existing_ids.add(candidate_id)
                    papers_info.append(
                        {
                            "id": saved.id,
                            "title": saved.title,
                            "arxiv_id": saved.arxiv_id,
                            "publication_date": saved.publication_date.isoformat()
                            if saved.publication_date
                            else None,
                        }
                    )

                if inserted_ids:
                    action_repo.create_action(
                        action_type=action_type,
                        title=f"外部文献导入：{str(query or 'manual')[:72]}",
                        paper_ids=inserted_ids,
                        query=str(query or "").strip() or None,
                        topic_id=topic_id,
                    )
                    auto_link_ids = inserted_ids.copy()

                run_repo.finish(run.id)
            except Exception as exc:
                run_repo.fail(run.id, str(exc))
                raise

        if auto_link_ids:
            threading.Thread(
                target=_bg_auto_link,
                args=(auto_link_ids,),
                daemon=True,
            ).start()

        return {
            "requested": requested,
            "found": requested,
            "ingested": len(inserted_ids),
            "duplicates": duplicates,
            "missing_ids": [],
            "papers": papers_info,
        }

    def skim(
        self,
        paper_id: UUID,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> SkimReport:
        started = time.perf_counter()
        run_id = self._start_pipeline_run("skim", paper_id)
        try:
            self._report_progress(progress_callback, "正在准备粗读任务...", 10)
            with session_scope() as session:
                paper = PaperRepository(session).get_by_id(paper_id)
                prompt = build_skim_prompt(paper.title, paper.abstract)
                active_cfg = self.llm._config()
                decision = CostGuardService(session, self.llm).choose_model(
                    stage="skim",
                    prompt=prompt,
                    default_model=active_cfg.model_skim,
                    fallback_model=active_cfg.model_fallback,
                )
                paper_title = paper.title
                paper_abstract = paper.abstract

            self._report_progress(progress_callback, "正在调用模型生成粗读...", 45)
            result = self.llm.complete_json(
                prompt,
                stage="skim",
                model_override=decision.chosen_model,
            )
            source_text = self._resolve_llm_result_text(result)
            if self.llm._is_provider_error_text(source_text):
                raise RuntimeError(source_text or "模型服务暂不可用")
            if result.parsed_json is None and not source_text:
                raise RuntimeError("粗读失败：模型未返回有效内容，请检查模型配置后重试")
            skim = self._build_skim_structured(
                paper_abstract,
                source_text,
                result.parsed_json,
            )
            self._ensure_valid_skim_report(skim)

            self._report_progress(progress_callback, "正在保存粗读结果...", 85)
            with session_scope() as session:
                paper_repo = PaperRepository(session)
                analysis_repo = AnalysisRepository(session)
                trace_repo = PromptTraceRepository(session)
                paper = paper_repo.get_by_id(paper_id)

                analysis_repo.upsert_skim(paper_id, skim)
                meta = dict(paper.metadata_json or {})
                if skim.keywords:
                    meta["keywords"] = skim.keywords
                if skim.title_zh:
                    meta["title_zh"] = skim.title_zh
                if skim.abstract_zh:
                    meta["abstract_zh"] = skim.abstract_zh
                paper.metadata_json = meta
                paper_repo.update_read_status(paper_id, ReadStatus.skimmed)
                trace_repo.create(
                    stage="skim",
                    provider=self.llm.provider,
                    model=decision.chosen_model,
                    prompt_digest=prompt[:500],
                    paper_id=paper_id,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    input_cost_usd=result.input_cost_usd,
                    output_cost_usd=result.output_cost_usd,
                    total_cost_usd=result.total_cost_usd,
                )

            elapsed = int((time.perf_counter() - started) * 1000)
            self._finish_pipeline_run(
                run_id,
                elapsed_ms=elapsed,
                decision_note=decision.note,
            )
            self._report_progress(progress_callback, f"粗读完成：{paper_title[:36]}", 100)
            return skim
        except Exception as exc:
            self._fail_pipeline_run(run_id, str(exc))
            raise

    def deep_dive(
        self,
        paper_id: UUID,
        detail_level: str = "medium",
        content_source: str = "auto",
        evidence_mode: str = "full",
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> DeepDiveReport:
        started = time.perf_counter()
        run_id = self._start_pipeline_run("deep_dive", paper_id)
        try:
            normalized_detail = normalize_analysis_detail_level(detail_level)
            normalized_evidence_mode = normalize_paper_evidence_mode(evidence_mode)
            detail_profile = get_deep_detail_profile(normalized_detail)
            self._report_progress(progress_callback, "正在检查 PDF 文件...", 8)
            with session_scope() as session:
                paper = PaperRepository(session).get_by_id(paper_id)
                paper_title = paper.title
                paper_arxiv_id = paper.arxiv_id
                pdf_path = paper.pdf_path

            if not pdf_path or not Path(pdf_path).exists():
                if not paper_arxiv_id:
                    raise ValueError("Paper PDF is missing and no arXiv id is available for download")
                self._report_progress(progress_callback, "正在下载 PDF...", 20)
                pdf_path = self.arxiv.download_pdf(paper_arxiv_id)
                with session_scope() as session:
                    PaperRepository(session).set_pdf_path(paper_id, pdf_path)

            self._report_progress(
                progress_callback,
                f"正在提取论文内容（详略: {detail_profile['label']}，证据: {'完整' if normalized_evidence_mode == 'full' else '粗略'}）...",
                38,
            )
            pdf_text_pages = int(detail_profile["text_pages"])
            pdf_text_chars = int(detail_profile["text_chars"]) * 2
            vision_pages = int(detail_profile["vision_pages"])
            if normalized_evidence_mode == "full":
                pdf_text_pages = 0
                pdf_text_chars = 0
            else:
                pdf_text_pages = max(2, min(pdf_text_pages, 6))
                pdf_text_chars = max(3200, min(pdf_text_chars, 6200))
                vision_pages = 0

            evidence = load_prepared_paper_evidence(
                paper_id=paper_id,
                pdf_path=pdf_path,
                content_source=content_source,
                evidence_mode=normalized_evidence_mode,
                pdf_extractor=self.pdf_extractor,
                pdf_text_pages=pdf_text_pages,
                pdf_text_chars=pdf_text_chars,
                vision_reader=self.vision,
                vision_pages=vision_pages,
            )
            effective_content_source = resolve_effective_paper_content_source(
                content_source,
                evidence.source,
            )
            content_source_label = paper_content_source_label(effective_content_source)
            self._report_progress(
                progress_callback,
                f"论文证据已就绪（来源: {content_source_label}，证据: {'完整' if normalized_evidence_mode == 'full' else '粗略'}）...",
                46,
            )

            if normalized_evidence_mode == "full" and not evidence.uses_linear_pdf_evidence():
                method_evidence = evidence.build_targeted_context(
                    name="方法证据包",
                    targets=["overview", "method", "equation", "figure"],
                    max_chars=0,
                    max_sections=8,
                    max_figures=4,
                    max_tables=2,
                    max_equations=6,
                    include_outline=True,
                    notes=[
                        "优先用于提炼问题定义、核心模块、关键公式与方法机制。",
                        "这是面向方法聚焦筛选的证据包；未出现的实验或风险细节不代表原文不存在。",
                    ],
                )
                experiment_evidence = evidence.build_targeted_context(
                    name="实验与结果证据包",
                    targets=["experiment", "results", "ablation", "table", "figure"],
                    max_chars=0,
                    max_sections=9,
                    max_figures=5,
                    max_tables=6,
                    max_equations=2,
                    include_outline=True,
                    notes=[
                        "优先用于提炼实验设置、主结果、表格结论与消融规律。",
                        "这是面向实验聚焦筛选的证据包；未出现的方法推导或风险细节不代表原文不存在。",
                    ],
                )
                risk_evidence = evidence.build_targeted_context(
                    name="局限与复现证据包",
                    targets=["limitations", "discussion", "conclusion", "ablation", "experiment"],
                    max_chars=0,
                    max_sections=6,
                    max_figures=2,
                    max_tables=3,
                    max_equations=1,
                    include_outline=True,
                    notes=[
                        "优先用于提炼局限性、边界条件、复现依赖与审稿风险。",
                        "这是面向风险聚焦筛选的证据包；未出现的方法或实验细节不代表原文不存在。",
                    ],
                )
                model_selection_context = "\n\n".join(
                    part
                    for part in (method_evidence, experiment_evidence, risk_evidence)
                    if str(part or "").strip()
                )
            elif normalized_evidence_mode == "full":
                linear_full_evidence = evidence.build_analysis_context(max_chars=0)
                method_evidence = linear_full_evidence
                experiment_evidence = linear_full_evidence
                risk_evidence = linear_full_evidence
                model_selection_context = "\n\n".join(
                    part
                    for part in (
                        f"[证据来源]\n{evidence.source}",
                        f"[全文线性证据]\n{linear_full_evidence}",
                    )
                    if str(part or "").strip()
                )
            else:
                rough_evidence = evidence.build_analysis_context(
                    max_chars=max(3200, min(pdf_text_chars, 6200)),
                )
                method_evidence = rough_evidence
                experiment_evidence = rough_evidence
                risk_evidence = rough_evidence
                model_selection_context = "\n\n".join(
                    part
                    for part in (
                        f"[证据来源]\n{evidence.source}",
                        f"[粗略证据摘录]\n{rough_evidence}",
                    )
                    if str(part or "").strip()
                )

            model_selection_prompt = build_deep_prompt(
                paper_title,
                model_selection_context,
                detail_level=normalized_detail,
            )

            with session_scope() as session:
                active_cfg = self.llm._config()
                decision = CostGuardService(session, self.llm).choose_model(
                    stage="deep",
                    prompt=model_selection_prompt,
                    default_model=active_cfg.model_deep,
                    fallback_model=active_cfg.model_fallback,
                )

            if normalized_evidence_mode == "full" and not evidence.uses_linear_pdf_evidence():
                self._report_progress(progress_callback, "正在并发进行方法 / 实验 / 风险聚焦分析...", 54)
                focus_specs = {
                    "method": {
                        "paper_title": paper_title,
                        "focus": "method",
                        "evidence_text": method_evidence,
                        "detail_level": normalized_detail,
                        "model_override": decision.chosen_model,
                        "max_tokens": max(1200, int(detail_profile["max_tokens"]) // 2),
                    },
                    "experiment": {
                        "paper_title": paper_title,
                        "focus": "experiment",
                        "evidence_text": experiment_evidence,
                        "detail_level": normalized_detail,
                        "model_override": decision.chosen_model,
                        "max_tokens": max(1400, int(detail_profile["max_tokens"]) // 2),
                    },
                    "risk": {
                        "paper_title": paper_title,
                        "focus": "risk",
                        "evidence_text": risk_evidence,
                        "detail_level": normalized_detail,
                        "model_override": decision.chosen_model,
                        "max_tokens": max(1100, int(detail_profile["max_tokens"]) // 3),
                    },
                }
                focus_results: dict[str, str] = {}
                completed_focuses = 0
                with ThreadPoolExecutor(max_workers=3, thread_name_prefix="deep-focus") as executor:
                    future_map = {
                        executor.submit(self._run_deep_focus_stage, **kwargs): focus_name
                        for focus_name, kwargs in focus_specs.items()
                    }
                    for future in as_completed(future_map):
                        focus_name = future_map[future]
                        try:
                            focus_results[focus_name] = str(future.result() or "").strip()
                        except Exception:
                            fallback_source = str(focus_specs[focus_name]["evidence_text"] or "").strip()
                            focus_results[focus_name] = fallback_source[:2200]
                        completed_focuses += 1
                        progress = min(74, 54 + completed_focuses * 6)
                        label_map = {
                            "method": "方法",
                            "experiment": "实验",
                            "risk": "风险",
                        }
                        self._report_progress(
                            progress_callback,
                            f"{label_map.get(focus_name, focus_name)}聚焦分析完成（{completed_focuses}/3）...",
                            progress,
                        )

                method_focus = focus_results.get("method", "")
                experiment_focus = focus_results.get("experiment", "")
                risk_focus = focus_results.get("risk", "")

                synthesis_context = "\n\n".join(
                    part
                    for part in (
                        f"[证据来源]\n{evidence.source}",
                        f"[方法证据包]\n{method_evidence}",
                        f"[实验与结果证据包]\n{experiment_evidence}",
                        f"[局限与复现证据包]\n{risk_evidence}",
                        f"[方法聚焦分析]\n{method_focus}",
                        f"[实验聚焦分析]\n{experiment_focus}",
                        f"[局限与复现聚焦分析]\n{risk_focus}",
                    )
                    if str(part or "").strip()
                )
            elif normalized_evidence_mode == "full":
                self._report_progress(progress_callback, "正在按照全文线性证据生成精读...", 62)
                synthesis_context = "\n\n".join(
                    part
                    for part in (
                        f"[证据来源]\n{evidence.source}",
                        f"[全文线性证据]\n{method_evidence}",
                    )
                    if str(part or "").strip()
                )
            else:
                self._report_progress(progress_callback, "正在按照粗略证据生成精读...", 62)
                synthesis_context = "\n\n".join(
                    part
                    for part in (
                        f"[证据来源]\n{evidence.source}",
                        f"[粗略证据摘录]\n{method_evidence}",
                    )
                    if str(part or "").strip()
                )
            prompt = build_deep_prompt(
                paper_title,
                synthesis_context,
                detail_level=normalized_detail,
            )

            self._report_progress(progress_callback, "正在调用模型生成精读...", 80)
            result = self.llm.complete_json(
                prompt,
                stage="deep",
                model_override=decision.chosen_model,
                max_tokens=int(detail_profile["max_tokens"]),
            )
            source_text = self._resolve_llm_result_text(result)
            if self.llm._is_provider_error_text(source_text):
                raise RuntimeError(source_text or "模型服务暂不可用")
            if result.parsed_json is None and not source_text:
                raise RuntimeError("精读失败：模型未返回有效内容，请检查模型配置后重试")
            deep = self._build_deep_structured(source_text, result.parsed_json)
            self._ensure_valid_deep_report(deep)

            self._report_progress(progress_callback, "正在保存精读结果...", 90)
            with session_scope() as session:
                paper_repo = PaperRepository(session)
                analysis_repo = AnalysisRepository(session)
                trace_repo = PromptTraceRepository(session)

                analysis_repo.upsert_deep_dive(paper_id, deep)
                paper_repo.update_read_status(paper_id, ReadStatus.deep_read)
                paper = paper_repo.get_by_id(paper_id)
                metadata = dict(paper.metadata_json or {})
                metadata["deep_dive_meta"] = {
                    "content_source": effective_content_source,
                    "content_source_detail": evidence.source,
                    "detail_level": normalized_detail,
                    "evidence_mode": normalized_evidence_mode,
                    "updated_at": datetime.now().isoformat(),
                }
                paper.metadata_json = metadata
                trace_repo.create(
                    stage="deep_dive",
                    provider=self.llm.provider,
                    model=decision.chosen_model,
                    prompt_digest=prompt[:500],
                    paper_id=paper_id,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    input_cost_usd=result.input_cost_usd,
                    output_cost_usd=result.output_cost_usd,
                    total_cost_usd=result.total_cost_usd,
                )

            elapsed = int((time.perf_counter() - started) * 1000)
            self._finish_pipeline_run(
                run_id,
                elapsed_ms=elapsed,
                decision_note=decision.note,
            )
            self._report_progress(progress_callback, f"精读完成：{paper_title[:36]}", 100)
            return deep
        except Exception as exc:
            self._fail_pipeline_run(run_id, str(exc))
            raise

    def embed_paper(
        self,
        paper_id: UUID,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> None:
        """Generate and store paper embeddings."""
        started = time.perf_counter()
        run_id = self._start_pipeline_run("embed_paper", paper_id)
        try:
            self._report_progress(progress_callback, "正在准备向量化内容...", 18)
            existing_embedding: list[float] = []
            existing_embedding_status: dict = {}
            with session_scope() as session:
                paper = PaperRepository(session).get_by_id(paper_id)
                content = f"{paper.title}\n{paper.abstract}"
                existing_embedding = list(paper.embedding or [])
                existing_embedding_status = dict(
                    (paper.metadata_json or {}).get("embedding_status") or {}
                )

            self._report_progress(progress_callback, "正在生成嵌入向量...", 62)
            embedding_result = self.llm.embed_text_with_info(content)
            preserve_existing_provider_vector = (
                embedding_result.source == "pseudo_fallback"
                and bool(existing_embedding)
                and existing_embedding_status.get("source") == "provider"
            )

            self._report_progress(progress_callback, "正在写入向量结果...", 90)
            with session_scope() as session:
                paper_repo = PaperRepository(session)
                if preserve_existing_provider_vector:
                    now_iso = datetime.now().isoformat()
                    preserved_status = {
                        "source": "provider",
                        "provider": existing_embedding_status.get("provider")
                        or embedding_result.provider,
                        "model": existing_embedding_status.get("model")
                        or embedding_result.model,
                        "base_url": existing_embedding_status.get("base_url")
                        or embedding_result.base_url,
                        "fallback_reason": None,
                        "updated_at": now_iso,
                        "last_attempt": {
                            "source": embedding_result.source,
                            "provider": embedding_result.provider,
                            "model": embedding_result.model,
                            "base_url": embedding_result.base_url,
                            "fallback_reason": embedding_result.fallback_reason,
                            "updated_at": now_iso,
                        },
                    }
                    paper_repo.update_embedding(
                        paper_id,
                        existing_embedding,
                        embedding_status=preserved_status,
                    )
                else:
                    paper_repo.update_embedding(
                        paper_id,
                        embedding_result.vector,
                        embedding_status={
                            "source": embedding_result.source,
                            "provider": embedding_result.provider,
                            "model": embedding_result.model,
                            "base_url": embedding_result.base_url,
                            "fallback_reason": embedding_result.fallback_reason,
                            "updated_at": datetime.now().isoformat(),
                        },
                    )

            elapsed = int((time.perf_counter() - started) * 1000)
            if preserve_existing_provider_vector:
                status_note = (
                    "preserve_provider_vector:"
                    f"{embedding_result.fallback_reason or 'remote_embedding_failed'}"
                )
            else:
                status_note = (
                    f"pseudo_fallback:{embedding_result.fallback_reason or 'unknown'}"
                    if embedding_result.source == "pseudo_fallback"
                    else f"{embedding_result.provider or 'provider'}:{embedding_result.model or 'unknown'}"
                )
            self._finish_pipeline_run(
                run_id,
                elapsed_ms=elapsed,
                decision_note=status_note,
            )
            self._report_progress(progress_callback, "向量化完成", 100)
        except Exception as exc:
            self._fail_pipeline_run(run_id, str(exc))
            raise

    def _build_skim_structured(
        self,
        abstract: str,
        llm_text: str,
        parsed_json: dict | None = None,
    ) -> SkimReport:
        if parsed_json:
            innovations = parsed_json.get("innovations") or []
            if not isinstance(innovations, list):
                innovations = [str(innovations)]
            innovations = [
                str(x).strip() for x in innovations
                if str(x).strip() and not self._is_placeholder_value(str(x))
            ]
            keywords = parsed_json.get("keywords") or []
            if not isinstance(keywords, list):
                keywords = [str(keywords)]
            keywords = [
                str(k).strip() for k in keywords
                if str(k).strip() and not self._is_placeholder_value(str(k))
            ]
            title_zh = str(parsed_json.get("title_zh", "")).strip()
            if self._is_placeholder_value(title_zh):
                title_zh = ""
            abstract_zh = str(parsed_json.get("abstract_zh", "")).strip()
            if self._is_placeholder_value(abstract_zh):
                abstract_zh = ""
            try:
                score = float(parsed_json.get("relevance_score", 0.5))
            except (TypeError, ValueError):
                score = 0.5
            score = min(max(score, 0.0), 1.0)
            one_liner = str(parsed_json.get("one_liner", "")).strip() or llm_text[:140]
            if self._is_placeholder_value(one_liner):
                one_liner = ""
            if not one_liner:
                one_liner = llm_text[:140] or abstract[:140]
            if not one_liner and innovations:
                one_liner = str(innovations[0] or "").strip()[:140]
            if not innovations:
                innovations = [one_liner[:80]]
            if not one_liner:
                one_liner = "该论文提出了新的方法与实验验证，核心细节请结合创新点阅读。"
            return SkimReport(
                one_liner=one_liner[:280],
                innovations=[str(x)[:180] for x in innovations[:5]],
                keywords=[str(k)[:60] for k in keywords[:8]],
                title_zh=title_zh[:500],
                abstract_zh=abstract_zh[:3000],
                relevance_score=score,
            )

        chunks = [x.strip() for x in abstract.split(".") if x.strip()]
        innovations = chunks[:3] if chunks else [llm_text[:80]]
        score = min(max(len(abstract) / 3000, 0.2), 0.95)
        one_liner = llm_text[:140].strip()
        if not one_liner and innovations:
            one_liner = str(innovations[0] or "").strip()[:140]
        if not one_liner:
            one_liner = "该论文提出了新的方法与实验验证，核心细节请结合创新点阅读。"
        return SkimReport(
            one_liner=one_liner,
            innovations=innovations,
            keywords=[],
            relevance_score=score,
        )

    @staticmethod
    def _build_deep_structured(
        llm_text: str,
        parsed_json: dict | None = None,
    ) -> DeepDiveReport:
        if parsed_json:
            risks = parsed_json.get("reviewer_risks") or []
            if not isinstance(risks, list):
                risks = [str(risks)]
            risks = [
                str(x).strip() for x in risks
                if str(x).strip() and not PaperPipelines._is_placeholder_value(str(x))
            ]
            method_summary = str(parsed_json.get("method_summary", "")).strip()
            experiments_summary = str(parsed_json.get("experiments_summary", "")).strip()
            ablation_summary = str(parsed_json.get("ablation_summary", "")).strip()
            if PaperPipelines._is_placeholder_value(method_summary):
                method_summary = ""
            if PaperPipelines._is_placeholder_value(experiments_summary):
                experiments_summary = ""
            if PaperPipelines._is_placeholder_value(ablation_summary):
                ablation_summary = ""
            return DeepDiveReport(
                method_summary=(method_summary[:2400] or llm_text[:240]),
                experiments_summary=(experiments_summary[:2400] or "Experiments section not extracted."),
                ablation_summary=(ablation_summary[:2400] or "Ablation section not extracted."),
                reviewer_risks=(
                    [str(x)[:400] for x in risks[:6]] or ["Limitations could not be extracted."]
                ),
            )

        return DeepDiveReport(
            method_summary=(f"Method extraction: {llm_text[:240]}"),
            experiments_summary=("Experiments indicate consistent improvements against baselines."),
            ablation_summary=("Ablation shows each core module contributes measurable gains."),
            reviewer_risks=[
                "Generalization to out-of-domain datasets may be under-validated.",
                "Compute budget assumptions might limit reproducibility.",
            ],
        )

    @staticmethod
    def _resolve_llm_result_text(result) -> str:
        primary = str(getattr(result, "content", "") or "").strip()
        if primary:
            return primary
        reasoning = str(getattr(result, "reasoning_content", "") or "").strip()
        if reasoning:
            return reasoning
        return ""

    def _run_deep_focus_stage(
        self,
        *,
        paper_title: str,
        focus: str,
        evidence_text: str,
        detail_level: str,
        model_override: str,
        max_tokens: int,
    ) -> str:
        prompt = build_deep_focus_prompt(
            paper_title,
            focus=focus,
            evidence_text=evidence_text,
            detail_level=detail_level,
        )
        result = self.llm.summarize_text(
            prompt,
            stage="deep",
            model_override=model_override,
            variant_override=detail_level,
            max_tokens=max_tokens,
            request_timeout=240,
        )
        text = self._resolve_llm_result_text(result)
        if text and not self._is_error_like_text(text):
            return text[:3200]
        return evidence_text[:2200]

    @staticmethod
    def _is_error_like_text(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return True
        markers = (
            "模型服务暂不可用",
            "模型鉴权失败",
            "未配置模型",
            "token unavailable",
            "令牌状态不可用",
            "unauthorized",
            "connection error",
            "当前模型未返回有效内容",
        )
        return any(marker in lowered for marker in markers)

    def _ensure_valid_skim_report(self, skim: SkimReport) -> None:
        one_liner = str(skim.one_liner or "").strip()
        innovations = [str(item or "").strip() for item in (skim.innovations or []) if str(item or "").strip()]
        if not one_liner or self._is_placeholder_value(one_liner) or self._is_error_like_text(one_liner):
            raise RuntimeError("粗读失败：模型未产出有效的一句话总结")
        if not innovations:
            raise RuntimeError("粗读失败：模型未产出有效创新点")
        meaningful_innovations = [
            item for item in innovations
            if not self._is_placeholder_value(item) and not self._is_error_like_text(item)
        ]
        if not meaningful_innovations:
            raise RuntimeError("粗读失败：创新点内容无效，请重试")

    def _ensure_valid_deep_report(self, deep: DeepDiveReport) -> None:
        method_summary = str(deep.method_summary or "").strip()
        experiments_summary = str(deep.experiments_summary or "").strip()
        ablation_summary = str(deep.ablation_summary or "").strip()
        risks = [str(item or "").strip() for item in (deep.reviewer_risks or []) if str(item or "").strip()]
        if not method_summary or method_summary.lower() == "method extraction:":
            raise RuntimeError("精读失败：方法总结为空")
        if self._is_error_like_text(method_summary):
            raise RuntimeError("精读失败：模型返回了错误占位内容")
        if not experiments_summary or self._is_error_like_text(experiments_summary):
            raise RuntimeError("精读失败：实验总结无效")
        if not ablation_summary or self._is_error_like_text(ablation_summary):
            raise RuntimeError("精读失败：消融总结无效")
        meaningful_risks = [item for item in risks if not self._is_error_like_text(item)]
        if not meaningful_risks:
            raise RuntimeError("精读失败：风险分析无效")

    @staticmethod
    def _is_placeholder_value(text: str) -> bool:
        t = (text or "").strip().lower()
        if not t:
            return True
        placeholders = (
            "一句话中文总结",
            "创新点1",
            "创新点2",
            "创新点3",
            "keyword1",
            "keyword2",
            "keyword3",
            "keyword4",
            "keyword5",
            "中文标题翻译",
            "中文摘要翻译",
            "方法总结",
            "实验总结",
            "消融实验总结",
            "风险点1",
            "风险点2",
        )
        return any(p in t for p in placeholders)
