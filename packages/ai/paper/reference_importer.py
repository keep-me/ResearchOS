"""Reference import pipeline helpers extracted from `pipelines.py`."""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import date, datetime
from difflib import SequenceMatcher
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from packages.config import get_settings
from packages.domain.enums import ActionType
from packages.domain.schemas import PaperCreate
from packages.domain.task_tracker import global_tracker
from packages.integrations.arxiv_client import ArxivClient
from packages.integrations.openalex_client import OpenAlexClient
from packages.integrations.semantic_scholar_client import SemanticScholarClient
from packages.storage.db import session_scope
from packages.storage.repositories import ActionRepository, CitationRepository, PaperRepository

logger = logging.getLogger(__name__)

_import_tasks: dict[str, dict] = {}


def get_import_task(task_id: str) -> dict | None:
    return _import_tasks.get(task_id)


class ReferenceImporter:
    """Import external reference papers into the local paper library."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.arxiv = ArxivClient()
        self.openalex = OpenAlexClient(email=self.settings.openalex_email)
        self.scholar = SemanticScholarClient(
            api_key=self.settings.semantic_scholar_api_key,
        )

    @staticmethod
    def _normalize_arxiv_id(aid: str | None) -> str | None:
        if not aid:
            return None
        return re.sub(r"v\d+$", "", aid.strip())

    @staticmethod
    def _is_real_arxiv_id(aid: str | None) -> bool:
        if not aid:
            return False
        normalized = aid.strip()
        return bool(
            re.fullmatch(
                r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?",
                normalized,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _normalize_title_for_match(title: str | None) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()

    @staticmethod
    def _build_external_paper_id(source_id: str | None) -> str:
        stable_source = str(source_id or uuid4())
        return f"ext-{uuid5(NAMESPACE_URL, stable_source).hex[:24]}"

    @staticmethod
    def _build_source_url(source_id: str | None) -> str | None:
        value = str(source_id or "").strip()
        if not value:
            return None
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return f"https://www.semanticscholar.org/paper/{value}"

    @staticmethod
    def _extract_openalex_work_id(value: str | None) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        match = re.search(r"(W\d+)", text, flags=re.IGNORECASE)
        if not match:
            return None
        return f"https://openalex.org/{match.group(1).upper()}"

    def _fetch_openalex_work(self, entry: dict) -> dict | None:
        for key in ("openalex_id", "scholar_id", "source_url"):
            work_id = self._extract_openalex_work_id(entry.get(key))
            if not work_id:
                continue
            work = self.openalex.fetch_work(work_id=work_id)
            if work and work.get("id"):
                return work
        return None

    @staticmethod
    def _extract_openalex_abstract(work: dict) -> str:
        inverted_index = work.get("abstract_inverted_index")
        if not isinstance(inverted_index, dict):
            return ""
        word_positions: list[tuple[int, str]] = []
        for word, positions in inverted_index.items():
            for pos in positions:
                word_positions.append((pos, word))
        word_positions.sort()
        return " ".join(word for _, word in word_positions).strip()

    @staticmethod
    def _apply_openalex_metadata(metadata: dict, work: dict | None) -> dict:
        if not work:
            return metadata

        merged = dict(metadata or {})
        openalex_id = str(work.get("id") or "").strip() or None
        if openalex_id:
            merged["scholar_id"] = openalex_id
            merged["openalex_id"] = openalex_id
            merged["source_url"] = openalex_id
            merged["import_source"] = "openalex"

        landing_page_url = OpenAlexClient.extract_source_url(work)
        if landing_page_url and landing_page_url != openalex_id:
            merged["landing_page_url"] = landing_page_url

        pdf_url = OpenAlexClient.extract_pdf_url(work)
        if pdf_url:
            merged["pdf_url"] = pdf_url

        ids = work.get("ids") or {}
        if isinstance(ids, dict):
            doi = ids.get("doi")
            if isinstance(doi, str) and doi.strip():
                merged["doi"] = doi.strip()

        authors: list[str] = []
        for authorship in work.get("authorships") or []:
            if not isinstance(authorship, dict):
                continue
            author = authorship.get("author") or {}
            name = str(author.get("display_name") or "").strip()
            if name:
                authors.append(name)
        if authors and not merged.get("authors"):
            merged["authors"] = authors

        concepts = work.get("concepts") or []
        fields = [
            str(concept.get("display_name") or "").strip()
            for concept in concepts
            if isinstance(concept, dict) and str(concept.get("display_name") or "").strip()
        ][:5]
        if fields and not merged.get("fields_of_study"):
            merged["fields_of_study"] = fields

        location = work.get("primary_location") or {}
        source = location.get("source") or {}
        venue = str(source.get("display_name") or "").strip()
        if venue and not merged.get("venue"):
            merged["venue"] = venue

        citation_count = work.get("cited_by_count")
        if citation_count is not None:
            merged["citation_count"] = citation_count

        arxiv_id = OpenAlexClient.extract_arxiv_id(work)
        if arxiv_id and not merged.get("arxiv_id"):
            merged["arxiv_id"] = arxiv_id

        return merged

    @staticmethod
    def _build_paper_from_openalex_work(
        work: dict,
        source_paper_id: str,
    ) -> PaperCreate:
        openalex_id = str(work.get("id") or "").strip()
        arxiv_id = OpenAlexClient.extract_arxiv_id(work)
        if not ReferenceImporter._is_real_arxiv_id(arxiv_id):
            arxiv_id = ReferenceImporter._build_external_paper_id(
                openalex_id or OpenAlexClient.extract_source_url(work) or work.get("title")
            )

        publication_date = None
        raw_publication_date = work.get("publication_date")
        if isinstance(raw_publication_date, str) and raw_publication_date.strip():
            try:
                publication_date = datetime.strptime(
                    raw_publication_date.strip(), "%Y-%m-%d"
                ).date()
            except ValueError:
                publication_date = None
        if publication_date is None and work.get("publication_year"):
            publication_date = date(int(work["publication_year"]), 1, 1)

        metadata = ReferenceImporter._apply_openalex_metadata(
            {
                "source": "reference_import",
                "source_paper_id": source_paper_id,
            },
            work,
        )

        return PaperCreate(
            arxiv_id=arxiv_id,
            title=str(work.get("title") or "Unknown").strip() or "Unknown",
            abstract=ReferenceImporter._extract_openalex_abstract(work),
            publication_date=publication_date,
            metadata=metadata,
        )

    def _resolve_arxiv_by_title(self, title: str | None) -> PaperCreate | None:
        normalized_title = self._normalize_title_for_match(title)
        if not normalized_title:
            return None

        candidates: list[PaperCreate] = []
        queries = [f'ti:"{title}"', str(title or "")]
        for query in queries:
            if not query.strip():
                continue
            try:
                candidates = self.arxiv.fetch_latest(
                    query,
                    max_results=5,
                    sort_by="relevance",
                    days_back=None,
                )
            except Exception:
                candidates = []
            if candidates:
                break

        best_match: PaperCreate | None = None
        best_score = 0.0
        for candidate in candidates:
            candidate_title = self._normalize_title_for_match(candidate.title)
            if not candidate_title:
                continue
            score = SequenceMatcher(None, normalized_title, candidate_title).ratio()
            if candidate_title == normalized_title:
                score = 1.0
            if score > best_score:
                best_score = score
                best_match = candidate

        if best_match and best_score >= 0.9:
            return best_match
        return None

    @staticmethod
    def _sync_import_task_to_tracker(task: dict) -> None:
        task_id = str(task.get("task_id") or "")
        if not task_id:
            return
        total = max(int(task.get("total") or 0), 1)
        completed = max(0, min(int(task.get("completed") or 0), total))
        current_title = str(task.get("current") or "").strip()
        imported = int(task.get("imported") or 0)
        skipped = int(task.get("skipped") or 0)
        failed = int(task.get("failed") or 0)
        status = str(task.get("status") or "running")
        if status == "completed":
            message = f"导入完成：成功 {imported}，跳过 {skipped}，失败 {failed}"
        elif status == "failed":
            message = str(task.get("error") or "导入失败")
        else:
            message = f"正在导入 {completed}/{total}" + (
                f" · {current_title[:40]}" if current_title else ""
            )
        global_tracker.update(task_id, completed, message, total=total)

    def start_import(
        self,
        *,
        source_paper_id: str,
        source_paper_title: str,
        entries: list[dict],
        topic_ids: list[str] | None = None,
    ) -> str:
        """鍚姩鍚庡彴瀵煎叆浠诲姟锛岃繑鍥?task_id"""
        task_id = str(uuid4())
        _import_tasks[task_id] = {
            "task_id": task_id,
            "status": "running",
            "source_paper_id": source_paper_id,
            "total": len(entries),
            "completed": 0,
            "imported": 0,
            "skipped": 0,
            "failed": 0,
            "current": "",
            "results": [],
        }
        global_tracker.start(
            task_id,
            "reference_import",
            f"参考文献导入: {source_paper_title[:30] or source_paper_id[:12]}",
            total=max(len(entries), 1),
        )
        self._sync_import_task_to_tracker(_import_tasks[task_id])
        threading.Thread(
            target=self._run_import,
            args=(task_id, source_paper_id, source_paper_title, entries, topic_ids or []),
            daemon=True,
        ).start()
        return task_id

    def _run_import(
        self,
        task_id: str,
        source_paper_id: str,
        source_paper_title: str,
        entries: list[dict],
        topic_ids: list[str],
    ) -> None:
        task = _import_tasks[task_id]
        inserted_ids: list[str] = []

        try:
            # 1) 瀵よ櫣鐝涙惔鎾冲敶瀹稿弶婀?arxiv_id 闂嗗棗鎮庨敍鍫㈡暏娴滃骸骞撻柌宥忕礆
            with session_scope() as session:
                repo = PaperRepository(session)
                existing_norms: set[str] = set()
                for p in repo.list_all(limit=50000):
                    n = self._normalize_arxiv_id(p.arxiv_id)
                    if n:
                        existing_norms.add(n)

            # 2) 鎶?entries 鍒嗘垚涓ょ粍锛氭湁 arxiv_id / 鏃?arxiv_id
            arxiv_entries: list[dict] = []
            ss_only_entries: list[dict] = []
            skip_entries: list[dict] = []

            for entry in entries:
                arxiv_id = entry.get("arxiv_id")
                norm = self._normalize_arxiv_id(arxiv_id)
                if norm and norm in existing_norms:
                    skip_entries.append(entry)
                elif arxiv_id:
                    arxiv_entries.append(entry)
                else:
                    ss_only_entries.append(entry)

            task["skipped"] = len(skip_entries)
            task["completed"] = len(skip_entries)
            for e in skip_entries:
                task["results"].append(
                    {
                        "title": e.get("title", ""),
                        "status": "skipped",
                        "reason": "已在库中",
                    }
                )
            self._sync_import_task_to_tracker(task)

            # 3) 鎵归噺閫氳繃 arXiv API 鎷夊彇鏈?arxiv_id 鐨勮鏂?
            if arxiv_entries:
                self._import_arxiv_batch(
                    task,
                    arxiv_entries,
                    source_paper_id,
                    topic_ids,
                    inserted_ids,
                    existing_norms,
                )

            # 4) 閺?arxiv_id 閻ㄥ嫯顔戦弬鍥╂暏 SS 閸忓啯鏆熼幑顔碱嚤閸?
            if ss_only_entries:
                self._import_ss_batch(
                    task,
                    ss_only_entries,
                    source_paper_id,
                    topic_ids,
                    inserted_ids,
                )

            # 5) 璁板綍 CollectionAction
            if inserted_ids:
                with session_scope() as session:
                    action_repo = ActionRepository(session)
                    action_repo.create_action(
                        action_type=ActionType.reference_import,
                        title=f"参考文献导入：{source_paper_title[:60]}",
                        paper_ids=inserted_ids,
                        query=source_paper_id,
                    )

            # 6) 鍚庡彴瑙﹀彂绮楄 + 鍚戦噺鍖?
            if inserted_ids:
                threading.Thread(
                    target=self._bg_skim_and_embed,
                    args=(inserted_ids,),
                    daemon=True,
                ).start()

            task["status"] = "completed"
            self._sync_import_task_to_tracker(task)
            global_tracker.set_result(
                task_id,
                {
                    "source_paper_id": source_paper_id,
                    "imported": task.get("imported", 0),
                    "skipped": task.get("skipped", 0),
                    "failed": task.get("failed", 0),
                    "results": task.get("results", []),
                },
            )
            global_tracker.finish(task_id, success=True)

        except Exception as exc:
            logger.exception("Reference import failed: %s", exc)
            task["status"] = "failed"
            task["error"] = str(exc)
            self._sync_import_task_to_tracker(task)
            global_tracker.finish(task_id, success=False, error=str(exc)[:200])

    def _import_arxiv_batch(
        self,
        task: dict,
        entries: list[dict],
        source_paper_id: str,
        topic_ids: list[str],
        inserted_ids: list[str],
        existing_norms: set[str],
    ) -> None:
        """鎵归噺浠?arXiv 鎷夊彇瀹屾暣璁烘枃鏁版嵁"""
        arxiv_ids = [e["arxiv_id"] for e in entries]

        # arXiv API 涓€娆℃渶澶氳幏鍙?50 涓紝鍒嗘壒澶勭悊
        batch_size = 30
        arxiv_papers_map: dict[str, PaperCreate] = {}
        for i in range(0, len(arxiv_ids), batch_size):
            batch = arxiv_ids[i : i + batch_size]
            try:
                papers = self.arxiv.fetch_by_ids(batch)
                for p in papers:
                    n = self._normalize_arxiv_id(p.arxiv_id)
                    if n:
                        arxiv_papers_map[n] = p
            except Exception as exc:
                logger.warning("arXiv batch fetch failed: %s", exc)
            time.sleep(1)

        for entry in entries:
            title = entry.get("title", "Unknown")
            task["current"] = title[:50]
            self._sync_import_task_to_tracker(task)
            arxiv_id = entry["arxiv_id"]
            norm = self._normalize_arxiv_id(arxiv_id)
            openalex_work = self._fetch_openalex_work(entry)

            arxiv_paper = arxiv_papers_map.get(norm) if norm else None

            if arxiv_paper:
                # 鐢?arXiv 鐨勫畬鏁存暟鎹?+ SS 鐨勯澶栦俊鎭悎骞?
                meta = dict(arxiv_paper.metadata or {})
                meta["source"] = "reference_import"
                meta["source_paper_id"] = source_paper_id
                meta["scholar_id"] = entry.get("scholar_id")
                if entry.get("venue"):
                    meta["venue"] = entry["venue"]
                if entry.get("citation_count") is not None:
                    meta["citation_count"] = entry["citation_count"]
                meta = self._apply_openalex_metadata(meta, openalex_work)
                arxiv_paper.metadata = meta
                paper_data = arxiv_paper
            elif openalex_work:
                paper_data = self._build_paper_from_openalex_work(
                    openalex_work,
                    source_paper_id,
                )
                if self._is_real_arxiv_id(arxiv_id):
                    paper_data.arxiv_id = arxiv_id
            else:
                # arXiv API 娌℃壘鍒帮紙鍙兘鏄棫璁烘枃锛夛紝鐢?SS 鏁版嵁鍒涘缓
                paper_data = self._build_paper_from_entry(
                    entry,
                    source_paper_id,
                )

            try:
                with session_scope() as session:
                    repo = PaperRepository(session)
                    saved = repo.upsert_paper(paper_data)
                    for tid in topic_ids:
                        repo.link_to_topic(saved.id, tid)
                    try:
                        pdf_path = self.arxiv.download_pdf(
                            paper_data.arxiv_id,
                        )
                        repo.set_pdf_path(saved.id, pdf_path)
                    except Exception:
                        pass
                    saved_id = str(saved.id)

                try:
                    with session_scope() as session:
                        cit_repo = CitationRepository(session)
                        direction = entry.get("direction", "reference")
                        if direction == "reference":
                            cit_repo.upsert_edge(
                                source_paper_id,
                                saved_id,
                                context="reference",
                            )
                        else:
                            cit_repo.upsert_edge(
                                saved_id,
                                source_paper_id,
                                context="citation",
                            )
                except Exception as exc:
                    logger.warning("Citation edge import failed for %s: %s", title, exc)

                inserted_ids.append(saved_id)
                existing_norms.add(norm or "")
                task["imported"] += 1
                task["results"].append(
                    {
                        "title": title,
                        "status": "imported",
                        "paper_id": saved_id,
                        "source": "arxiv",
                    }
                )
            except Exception as exc:
                logger.warning("Import failed for %s: %s", title, exc)
                task["failed"] += 1
                task["results"].append(
                    {
                        "title": title,
                        "status": "failed",
                        "reason": str(exc)[:100],
                    }
                )

            task["completed"] += 1
            self._sync_import_task_to_tracker(task)

    def _import_ss_batch(
        self,
        task: dict,
        entries: list[dict],
        source_paper_id: str,
        topic_ids: list[str],
        inserted_ids: list[str],
    ) -> None:
        """Import papers without arXiv IDs using Semantic Scholar metadata."""
        for entry in entries:
            title = entry.get("title", "Unknown")
            task["current"] = title[:50]
            self._sync_import_task_to_tracker(task)
            scholar_id = entry.get("scholar_id")
            openalex_work = self._fetch_openalex_work(entry)

            if openalex_work:
                paper_data = self._build_paper_from_openalex_work(
                    openalex_work,
                    source_paper_id,
                )
                if not self._is_real_arxiv_id(paper_data.arxiv_id):
                    matched_arxiv = self._resolve_arxiv_by_title(title)
                    if matched_arxiv:
                        paper_data.arxiv_id = matched_arxiv.arxiv_id
                        current_abstract = str(paper_data.abstract or "").strip()
                        if not current_abstract or len(current_abstract) < 240:
                            paper_data.abstract = matched_arxiv.abstract
                        if paper_data.publication_date is None:
                            paper_data.publication_date = matched_arxiv.publication_date
                        metadata = dict(paper_data.metadata or {})
                        metadata["arxiv_id"] = matched_arxiv.arxiv_id
                        paper_data.metadata = metadata
                if self._is_real_arxiv_id(paper_data.arxiv_id):
                    entry["arxiv_id"] = paper_data.arxiv_id
                    if not entry.get("abstract") and paper_data.abstract:
                        entry["abstract"] = paper_data.abstract
                    if not entry.get("year") and paper_data.publication_date:
                        entry["year"] = paper_data.publication_date.year
            else:
                # 灏濊瘯浠?SS 鑾峰彇鏇翠赴瀵岀殑淇℃伅
                detail = None
                if scholar_id:
                    try:
                        detail = self.scholar.fetch_paper_by_scholar_id(
                            scholar_id,
                        )
                        time.sleep(0.5)
                    except Exception:
                        pass

                matched_arxiv = None
                if detail and not self._is_real_arxiv_id(detail.get("arxiv_id")):
                    matched_arxiv = self._resolve_arxiv_by_title(detail.get("title") or title)
                elif not detail and not self._is_real_arxiv_id(entry.get("arxiv_id")):
                    matched_arxiv = self._resolve_arxiv_by_title(title)

                if matched_arxiv:
                    if detail is not None:
                        detail["arxiv_id"] = matched_arxiv.arxiv_id
                        detail["abstract"] = detail.get("abstract") or matched_arxiv.abstract
                        if not detail.get("publication_date") and matched_arxiv.publication_date:
                            detail["publication_date"] = matched_arxiv.publication_date.isoformat()
                        if not detail.get("year") and matched_arxiv.publication_date:
                            detail["year"] = matched_arxiv.publication_date.year
                    else:
                        entry["arxiv_id"] = matched_arxiv.arxiv_id
                        entry["abstract"] = entry.get("abstract") or matched_arxiv.abstract
                        if not entry.get("year") and matched_arxiv.publication_date:
                            entry["year"] = matched_arxiv.publication_date.year

                if detail and detail.get("arxiv_id"):
                    # SS 鏉╂柨娲栨禍?arXiv ID閿涘苯宕岀痪褌璐?arXiv 鐎电厧鍙?
                    entry["arxiv_id"] = detail["arxiv_id"]
                    paper_data = self._build_paper_from_detail(
                        detail,
                        source_paper_id,
                    )
                elif detail:
                    paper_data = self._build_paper_from_detail(
                        detail,
                        source_paper_id,
                    )
                else:
                    paper_data = self._build_paper_from_entry(
                        entry,
                        source_paper_id,
                    )

            try:
                with session_scope() as session:
                    repo = PaperRepository(session)
                    saved = repo.upsert_paper(paper_data)
                    for tid in topic_ids:
                        repo.link_to_topic(saved.id, tid)
                    if self._is_real_arxiv_id(paper_data.arxiv_id):
                        try:
                            pdf_path = self.arxiv.download_pdf(
                                paper_data.arxiv_id,
                            )
                            repo.set_pdf_path(saved.id, pdf_path)
                        except Exception:
                            pass
                    saved_id = str(saved.id)

                try:
                    with session_scope() as session:
                        cit_repo = CitationRepository(session)
                        direction = entry.get("direction", "reference")
                        if direction == "reference":
                            cit_repo.upsert_edge(
                                source_paper_id,
                                saved_id,
                                context="reference",
                            )
                        else:
                            cit_repo.upsert_edge(
                                saved_id,
                                source_paper_id,
                                context="citation",
                            )
                except Exception as exc:
                    logger.warning("Citation edge import failed for %s: %s", title, exc)

                inserted_ids.append(saved_id)
                task["imported"] += 1
                task["results"].append(
                    {
                        "title": title,
                        "status": "imported",
                        "paper_id": saved_id,
                        "source": "openalex" if openalex_work else "semantic_scholar",
                    }
                )
            except Exception as exc:
                logger.warning("SS import failed for %s: %s", title, exc)
                task["failed"] += 1
                task["results"].append(
                    {
                        "title": title,
                        "status": "failed",
                        "reason": str(exc)[:100],
                    }
                )

            task["completed"] += 1
            self._sync_import_task_to_tracker(task)

    @staticmethod
    def _build_paper_from_entry(
        entry: dict,
        source_paper_id: str,
    ) -> PaperCreate:
        """娴?citation entry 閺嬪嫬缂?PaperCreate"""
        arxiv_id = entry.get("arxiv_id")
        scholar_id = str(entry.get("scholar_id") or str(uuid4())[:12])
        source_url = ReferenceImporter._build_source_url(scholar_id)
        if not ReferenceImporter._is_real_arxiv_id(arxiv_id):
            arxiv_id = ReferenceImporter._build_external_paper_id(scholar_id)
        return PaperCreate(
            arxiv_id=arxiv_id,
            title=entry.get("title", "Unknown"),
            abstract=entry.get("abstract") or "",
            publication_date=(date(entry["year"], 1, 1) if entry.get("year") else None),
            metadata={
                "source": "reference_import",
                "source_paper_id": source_paper_id,
                "scholar_id": entry.get("scholar_id"),
                "source_url": source_url,
                "venue": entry.get("venue"),
                "citation_count": entry.get("citation_count"),
                "import_source": "openalex"
                if source_url and "openalex.org" in source_url
                else "semantic_scholar",
            },
        )

    @staticmethod
    def _build_paper_from_detail(
        detail: dict,
        source_paper_id: str,
    ) -> PaperCreate:
        """Build PaperCreate from Semantic Scholar detailed metadata."""
        arxiv_id = detail.get("arxiv_id")
        scholar_id = str(detail.get("scholar_id") or str(uuid4())[:12])
        source_url = ReferenceImporter._build_source_url(scholar_id)
        if not ReferenceImporter._is_real_arxiv_id(arxiv_id):
            arxiv_id = ReferenceImporter._build_external_paper_id(scholar_id)

        pub_date = None
        if detail.get("publication_date"):
            try:
                pub_date = datetime.strptime(
                    detail["publication_date"],
                    "%Y-%m-%d",
                ).date()
            except (ValueError, TypeError):
                pass
        if not pub_date and detail.get("year"):
            pub_date = date(detail["year"], 1, 1)

        return PaperCreate(
            arxiv_id=arxiv_id,
            title=detail.get("title") or "Unknown",
            abstract=detail.get("abstract") or "",
            publication_date=pub_date,
            metadata={
                "source": "reference_import",
                "source_paper_id": source_paper_id,
                "scholar_id": detail.get("scholar_id"),
                "source_url": source_url,
                "authors": detail.get("authors", []),
                "venue": detail.get("venue"),
                "citation_count": detail.get("citation_count"),
                "fields_of_study": detail.get("fields_of_study", []),
                "import_source": "openalex"
                if source_url and "openalex.org" in source_url
                else "semantic_scholar",
            },
        )

    def _bg_skim_and_embed(self, paper_ids: list[str]) -> None:
        """Run background skim and embedding jobs for newly imported papers."""
        from packages.ai.paper.pipelines import PaperPipelines

        pipeline = PaperPipelines()
        for pid in paper_ids:
            try:
                pipeline.embed_paper(UUID(pid))
            except Exception as exc:
                logger.warning("Embed failed for %s: %s", pid, exc)
            try:
                pipeline.skim(UUID(pid))
            except Exception as exc:
                logger.warning("Skim failed for %s: %s", pid, exc)
