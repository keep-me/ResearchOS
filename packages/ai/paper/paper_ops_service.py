"""Shared paper import and figure extraction helpers."""

from __future__ import annotations

import re
import shutil
import threading
from pathlib import Path
from typing import BinaryIO, Callable
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx

from packages.config import get_settings
from packages.domain.schemas import PaperCreate
from packages.storage.db import session_scope
from packages.storage.models import TopicSubscription
from packages.storage.repositories import PaperRepository


class PaperPdfUnavailableError(RuntimeError):
    """Raised when a paper PDF cannot be resolved or downloaded."""


class FigureExtractionEmptyError(RuntimeError):
    """Raised when figure extraction finishes without usable items."""


class PaperUploadValidationError(ValueError):
    """Raised when an uploaded PDF request is invalid."""


class PaperUploadNotFoundError(LookupError):
    """Raised when a PDF upload references a missing resource."""


_RESTRICTED_PDF_HOSTS = {
    "dl.acm.org",
    "ieeexplore.ieee.org",
    "link.springer.com",
    "springer.com",
    "nature.com",
    "www.nature.com",
    "sciencedirect.com",
    "www.sciencedirect.com",
    "onlinelibrary.wiley.com",
    "tandfonline.com",
    "www.tandfonline.com",
    "journals.sagepub.com",
    "www.cambridge.org",
}


def has_real_arxiv_id(arxiv_id: str | None) -> bool:
    if not arxiv_id:
        return False
    return bool(
        re.fullmatch(
            r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?",
            arxiv_id.strip(),
            flags=re.IGNORECASE,
        )
    )


def normalize_manual_paper_id(raw: str | None) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    for prefix in (
        "https://arxiv.org/abs/",
        "http://arxiv.org/abs/",
        "https://arxiv.org/pdf/",
        "http://arxiv.org/pdf/",
    ):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    if value.endswith(".pdf"):
        value = value[:-4]
    return re.sub(r"v\d+$", "", value).strip()


def normalized_url_host(url: str | None) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        host = urlparse(raw).netloc.lower().strip()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def is_restricted_pdf_url(url: str | None) -> bool:
    host = normalized_url_host(url)
    if not host:
        return False
    return any(host == blocked or host.endswith(f".{blocked}") for blocked in _RESTRICTED_PDF_HOSTS)


def sanitize_external_pdf_url(url: str | None) -> tuple[str | None, str | None]:
    candidate = str(url or "").strip()
    if not candidate:
        return None, None
    if is_restricted_pdf_url(candidate):
        host = normalized_url_host(candidate) or "当前来源"
        return None, f"{host} 的 PDF 通常需要机构权限，当前不支持自动下载"
    return candidate, None


def safe_uploaded_filename(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename or "paper").stem).strip("-")
    return stem[:80] or "paper"


def guess_title_from_text(text: str, fallback: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return fallback
    title_parts: list[str] = []
    for line in lines[:10]:
        lower = line.lower()
        if lower in {"abstract", "introduction"}:
            break
        if lower.startswith(("arxiv:", "https://", "http://")):
            continue
        if "@" in line:
            continue
        if len(line) < 4:
            continue
        title_parts.append(line)
        if len(" ".join(title_parts)) >= 32:
            break
    candidate = " ".join(title_parts).strip()
    return candidate[:300] if candidate else fallback


def guess_abstract_from_text(text: str) -> str:
    compact = re.sub(r"\r", "\n", text)
    match = re.search(
        r"(?is)\babstract\b[:\s]*(.{80,2500}?)(?:\n\s*\b(?:1\.?\s+introduction|introduction)\b|\Z)",
        compact,
    )
    if not match:
        return ""
    abstract = re.sub(r"\s+", " ", match.group(1)).strip()
    return abstract[:2500]


def safe_pdf_stem(value: str | None) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-")
    return stem[:120] or uuid4().hex


def extract_openalex_work_id(metadata: dict | None) -> str | None:
    if not isinstance(metadata, dict):
        return None
    for key in ("source_url", "scholar_id", "openalex_id"):
        raw = metadata.get(key)
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if not value:
            continue
        match = re.search(r"(?:openalex\.org/)?(W\d+)", value, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def download_remote_pdf(url: str, filename_stem: str) -> str:
    _, restriction_note = sanitize_external_pdf_url(url)
    if restriction_note:
        raise ValueError(restriction_note)

    target = get_settings().pdf_storage_root / f"{safe_pdf_stem(filename_stem)}.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=120, follow_redirects=True) as client:
        response = client.get(url, headers={"User-Agent": "ResearchOS/1.0"})
        response.raise_for_status()

    content_type = (response.headers.get("content-type") or "").lower()
    content = response.content
    if "pdf" not in content_type and not content.startswith(b"%PDF"):
        raise ValueError("Remote URL did not return a PDF file")

    target.write_bytes(content)
    return str(target)


def resolve_stored_pdf_path(pdf_path: str | None) -> Path | None:
    if not pdf_path:
        return None

    raw = Path(str(pdf_path)).expanduser()
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        project_root = Path(__file__).resolve().parents[2]
        candidates.append(raw)
        candidates.append(Path.cwd() / raw)
        candidates.append(project_root / raw)

        pdf_root = Path(get_settings().pdf_storage_root).expanduser()
        if pdf_root.is_absolute():
            candidates.append(pdf_root.parent.parent / raw)
            candidates.append(pdf_root.parent / raw)
            candidates.append(pdf_root / raw)
            candidates.append(pdf_root / raw.name)

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate.resolve()

    if raw.is_absolute():
        return raw.resolve(strict=False)
    return (Path(__file__).resolve().parents[2] / raw).resolve(strict=False)


def resolve_external_pdf_source(paper) -> dict[str, str | None]:
    from packages.integrations.openalex_client import OpenAlexClient

    metadata = dict(getattr(paper, "metadata_json", None) or {})
    resolved: dict[str, str | None] = {
        "arxiv_id": paper.arxiv_id if has_real_arxiv_id(getattr(paper, "arxiv_id", None)) else None,
        "pdf_url": None,
        "source_url": None,
        "doi": None,
        "blocked_pdf_url": None,
        "download_note": None,
    }

    for key in ("pdf_url", "oa_url", "open_access_pdf_url", "external_pdf_url"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            sanitized_pdf_url, restriction_note = sanitize_external_pdf_url(value)
            if sanitized_pdf_url:
                resolved["pdf_url"] = sanitized_pdf_url
            elif restriction_note:
                resolved["blocked_pdf_url"] = value.strip()
                resolved["download_note"] = restriction_note
            break

    source_url = metadata.get("source_url")
    if isinstance(source_url, str) and source_url.strip():
        resolved["source_url"] = source_url.strip()
    else:
        scholar_id = metadata.get("scholar_id")
        if isinstance(scholar_id, str) and scholar_id.strip().startswith(("http://", "https://")):
            resolved["source_url"] = scholar_id.strip()

    if not resolved["pdf_url"] and isinstance(resolved["source_url"], str) and resolved["source_url"].strip():
        source_candidate = resolved["source_url"].strip()
        source_lower = source_candidate.lower()
        if source_lower.endswith(".pdf") or "/pdf/" in source_lower:
            sanitized_pdf_url, restriction_note = sanitize_external_pdf_url(source_candidate)
            if sanitized_pdf_url:
                resolved["pdf_url"] = sanitized_pdf_url
            elif restriction_note and not resolved["download_note"]:
                resolved["blocked_pdf_url"] = source_candidate
                resolved["download_note"] = restriction_note

    for candidate in (resolved["source_url"], metadata.get("scholar_id"), metadata.get("doi")):
        if isinstance(candidate, str) and "arxiv.org/" in candidate:
            resolved["arxiv_id"] = normalize_manual_paper_id(candidate)
            break

    needs_openalex_lookup = not resolved["arxiv_id"] or not resolved["pdf_url"] or not resolved["source_url"]
    if not needs_openalex_lookup:
        return resolved

    work_id = extract_openalex_work_id(metadata)
    import_source = str(metadata.get("import_source") or "").lower()
    looks_like_openalex = import_source == "openalex" or work_id is not None or (
        isinstance(resolved["source_url"], str) and "openalex.org" in resolved["source_url"].lower()
    )
    if not looks_like_openalex:
        return resolved

    client = OpenAlexClient()
    try:
        work = client.fetch_work(work_id=work_id, title=getattr(paper, "title", None))
    finally:
        client.close()
    if not work:
        return resolved

    resolved["arxiv_id"] = resolved["arxiv_id"] or OpenAlexClient.extract_arxiv_id(work)
    if not resolved["pdf_url"]:
        extracted_pdf_url = OpenAlexClient.extract_pdf_url(work)
        sanitized_pdf_url, restriction_note = sanitize_external_pdf_url(extracted_pdf_url)
        if sanitized_pdf_url:
            resolved["pdf_url"] = sanitized_pdf_url
        elif restriction_note and not resolved["download_note"]:
            resolved["blocked_pdf_url"] = str(extracted_pdf_url or "").strip() or None
            resolved["download_note"] = restriction_note
    resolved["source_url"] = resolved["source_url"] or OpenAlexClient.extract_source_url(work) or work.get("id")
    ids = work.get("ids") or {}
    if isinstance(ids, dict):
        doi = ids.get("doi")
        if isinstance(doi, str) and doi.strip():
            resolved["doi"] = doi.strip()
    return resolved


def apply_external_resolution(paper, resolved: dict[str, str | None]) -> bool:
    changed = False
    metadata = dict(getattr(paper, "metadata_json", None) or {})

    arxiv_id = resolved.get("arxiv_id")
    if arxiv_id and not has_real_arxiv_id(getattr(paper, "arxiv_id", None)):
        paper.arxiv_id = arxiv_id
        changed = True

    pdf_url = resolved.get("pdf_url")
    if pdf_url and metadata.get("pdf_url") != pdf_url:
        metadata["pdf_url"] = pdf_url
        changed = True
    elif not pdf_url:
        blocked_pdf_url = resolved.get("blocked_pdf_url")
        if blocked_pdf_url and metadata.get("pdf_url") == blocked_pdf_url:
            metadata.pop("pdf_url", None)
            changed = True

    download_note = resolved.get("download_note")
    if download_note:
        if metadata.get("pdf_download_note") != download_note:
            metadata["pdf_download_note"] = download_note
            changed = True
    elif metadata.get("pdf_download_note"):
        metadata.pop("pdf_download_note", None)
        changed = True

    source_url = resolved.get("source_url")
    if source_url and not metadata.get("source_url"):
        metadata["source_url"] = source_url
        changed = True

    doi = resolved.get("doi")
    if doi and metadata.get("doi") != doi:
        metadata["doi"] = doi
        changed = True

    if changed:
        paper.metadata_json = metadata
    return changed


def ensure_paper_pdf(session, repo: PaperRepository, paper, paper_id: UUID) -> str:
    from packages.integrations.arxiv_client import ArxivClient

    pdf_path = getattr(paper, "pdf_path", None)
    resolved_local_pdf = resolve_stored_pdf_path(pdf_path)
    if resolved_local_pdf and resolved_local_pdf.exists():
        return str(resolved_local_pdf)

    resolved = resolve_external_pdf_source(paper)
    apply_external_resolution(paper, resolved)

    errors: list[str] = []
    if has_real_arxiv_id(getattr(paper, "arxiv_id", None)):
        try:
            pdf_path = ArxivClient().download_pdf(paper.arxiv_id)
            repo.set_pdf_path(paper_id, pdf_path)
            session.flush()
            return pdf_path
        except Exception as exc:
            errors.append(str(exc))

    pdf_url = resolved.get("pdf_url")
    if pdf_url:
        try:
            pdf_path = download_remote_pdf(pdf_url, getattr(paper, "arxiv_id", None) or str(paper_id))
            repo.set_pdf_path(paper_id, pdf_path)
            session.flush()
            return pdf_path
        except Exception as exc:
            errors.append(str(exc))

    download_note = str(resolved.get("download_note") or "").strip()
    if download_note:
        errors.append(download_note)

    detail = "；".join(error for error in errors if error) or "当前论文没有可用的 arXiv 或开放 PDF 来源，请打开来源页或手动导入 PDF"
    raise PaperPdfUnavailableError(detail)


def extract_uploaded_pdf_metadata(pdf_path: Path, fallback_title: str) -> tuple[str, str]:
    title = fallback_title
    abstract = ""
    try:
        import fitz  # type: ignore

        doc = fitz.open(str(pdf_path))
        meta_title = ((doc.metadata or {}).get("title") or "").strip()
        first_page = doc.load_page(0).get_text("text") if len(doc) else ""
        preview_pages = [doc.load_page(i).get_text("text")[:4000] for i in range(min(2, len(doc)))]
        doc.close()
        if meta_title and meta_title.lower() not in {"untitled", "microsoft word"}:
            title = meta_title[:300]
        else:
            title = guess_title_from_text(first_page, fallback_title)
        abstract = guess_abstract_from_text("\n".join(preview_pages))
    except Exception:
        pass
    return title, abstract


def _ensure_pdf_upload_allowed(filename: str, content_type: str | None) -> None:
    normalized_name = str(filename or "").strip().lower()
    normalized_type = str(content_type or "").strip().lower()
    if normalized_name.endswith(".pdf") or normalized_type == "application/pdf":
        return
    raise PaperUploadValidationError("仅支持上传 PDF 文件")


def _delete_file_if_exists(path: str | None) -> None:
    if not path:
        return
    target = Path(path)
    if not target.exists() or not target.is_file():
        return
    target.unlink(missing_ok=True)


def upload_paper_pdf(
    *,
    file_obj: BinaryIO,
    filename: str,
    content_type: str | None,
    title: str = "",
    arxiv_id: str = "",
    topic_id: str = "",
) -> dict:
    _ensure_pdf_upload_allowed(filename, content_type)

    settings = get_settings()
    upload_dir = settings.pdf_storage_root / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = safe_uploaded_filename(filename or "paper.pdf")
    stored_path = upload_dir / f"{safe_name}-{uuid4().hex[:8]}.pdf"
    old_pdf_path: str | None = None

    try:
        with stored_path.open("wb") as buffer:
            shutil.copyfileobj(file_obj, buffer)

        normalized_id = normalize_manual_paper_id(arxiv_id)
        fallback_title = title.strip() or Path(filename).stem.replace("_", " ").replace("-", " ").strip()
        extracted_title, extracted_abstract = extract_uploaded_pdf_metadata(
            stored_path,
            fallback_title or safe_name,
        )
        resolved_title = (title.strip() or extracted_title or safe_name).strip()[:300]
        resolved_abstract = extracted_abstract.strip()
        metadata = {
            "source": "pdf_upload",
            "original_filename": filename,
        }

        created = False
        linked_topic_id = topic_id.strip() or None
        with session_scope() as session:
            repo = PaperRepository(session)
            paper = repo.get_by_arxiv_id(normalized_id) if normalized_id else None
            if paper is None:
                created = True
                paper_key = normalized_id or f"upload-{uuid4().hex[:12]}"
                paper = repo.upsert_paper(
                    data=PaperCreate(
                        arxiv_id=paper_key,
                        title=resolved_title,
                        abstract=resolved_abstract,
                        metadata=metadata,
                    )
                )
            else:
                old_pdf_path = paper.pdf_path
                current_meta = dict(paper.metadata_json or {})
                current_meta.update(metadata)
                if title.strip():
                    paper.title = resolved_title
                elif not (paper.title or "").strip():
                    paper.title = resolved_title
                if resolved_abstract and not (paper.abstract or "").strip():
                    paper.abstract = resolved_abstract
                paper.metadata_json = current_meta

            repo.set_pdf_path(paper.id, str(stored_path))

            if linked_topic_id:
                topic = session.get(TopicSubscription, linked_topic_id)
                if topic is None:
                    raise PaperUploadNotFoundError(f"topic {linked_topic_id} not found")
                if getattr(topic, "kind", "subscription") != "folder":
                    raise PaperUploadValidationError("只能将 PDF 关联到文件夹类型的 topic")
                repo.link_to_topic(str(paper.id), linked_topic_id)

            result = {
                "status": "created" if created else "updated",
                "created": created,
                "paper": {
                    "id": str(paper.id),
                    "title": paper.title,
                    "arxiv_id": paper.arxiv_id,
                    "publication_date": (
                        paper.publication_date.isoformat() if paper.publication_date else None
                    ),
                },
                "pdf_path": str(stored_path),
                "topic_id": linked_topic_id,
            }

        if old_pdf_path and old_pdf_path != str(stored_path):
            _delete_file_if_exists(old_pdf_path)

        return result
    except Exception:
        stored_path.unlink(missing_ok=True)
        raise


def replace_paper_pdf(
    *,
    paper_id: UUID,
    file_obj: BinaryIO,
    filename: str,
    content_type: str | None,
) -> dict:
    _ensure_pdf_upload_allowed(filename, content_type)

    settings = get_settings()
    upload_dir = settings.pdf_storage_root / "manual"
    upload_dir.mkdir(parents=True, exist_ok=True)

    old_pdf_path: str | None = None
    stored_path: Path | None = None

    try:
        with session_scope() as session:
            repo = PaperRepository(session)
            try:
                paper = repo.get_by_id(paper_id)
            except ValueError as exc:
                raise PaperUploadNotFoundError(str(exc)) from exc

            target_stem = getattr(paper, "arxiv_id", None) or str(paper.id)
            stored_path = upload_dir / f"{safe_pdf_stem(target_stem)}-{uuid4().hex[:8]}.pdf"
            with stored_path.open("wb") as buffer:
                shutil.copyfileobj(file_obj, buffer)

            old_pdf_path = paper.pdf_path
            _, extracted_abstract = extract_uploaded_pdf_metadata(
                stored_path,
                paper.title or Path(filename).stem or "paper",
            )
            if extracted_abstract and not (paper.abstract or "").strip():
                paper.abstract = extracted_abstract.strip()

            metadata = dict(paper.metadata_json or {})
            metadata["source"] = "manual_pdf_upload"
            metadata["original_filename"] = filename
            paper.metadata_json = metadata
            repo.set_pdf_path(paper.id, str(stored_path))
            session.flush()

            result = {
                "status": "updated",
                "paper_id": str(paper.id),
                "pdf_path": str(stored_path),
            }

        if old_pdf_path and stored_path and old_pdf_path != str(stored_path):
            _delete_file_if_exists(old_pdf_path)

        return result
    except Exception:
        if stored_path is not None:
            stored_path.unlink(missing_ok=True)
        raise


def paper_figure_items(paper_id: UUID) -> list[dict]:
    from packages.ai.paper.figure_service import FigureService

    items = FigureService.get_paper_analyses(paper_id)
    normalized: list[dict] = []
    paper_id_text = str(paper_id)
    for item in items:
        payload = dict(item)
        if payload.get("has_image") and payload.get("id"):
            payload["image_url"] = f"/papers/{paper_id_text}/figures/{payload['id']}/image"
        else:
            payload["image_url"] = None
        normalized.append(payload)
    return normalized


def extract_paper_figures_payload(
    paper_id: UUID,
    max_figures: int,
    *,
    extract_mode: str | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> dict:
    try:
        from packages.ai.paper.figure_service import FigureService
    except Exception as exc:
        raise RuntimeError(f"Figure extraction dependencies unavailable: {exc}") from exc

    def _progress(message: str, current: int, total: int = 100) -> None:
        if progress_callback:
            progress_callback(message, current, total)

    def _start_pulse(message: str, *, start: int = 20, end: int = 85, step: int = 2, interval: float = 1.8):
        if not progress_callback:
            return None, None
        stop_event = threading.Event()

        def _runner() -> None:
            current = start
            while not stop_event.wait(interval):
                _progress(message, current, 100)
                if current < end:
                    current = min(end, current + step)

        thread = threading.Thread(target=_runner, daemon=True, name=f"figure-progress-{str(paper_id)[:8]}")
        thread.start()
        return stop_event, thread

    _progress("准备 PDF 文件...", 5, 100)
    with session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.get_by_id(paper_id)
        pdf_path = ensure_paper_pdf(session, repo, paper, paper_id)
        source_arxiv_id = paper.arxiv_id if has_real_arxiv_id(getattr(paper, "arxiv_id", None)) else None

    raw_mode = str(extract_mode or get_settings().figure_extract_mode or "arxiv_source").strip().lower()
    if raw_mode in {"mineru", "magic_pdf", "magic-pdf", "pdf_direct"}:
        selected_mode = "mineru"
    elif raw_mode == "arxiv_source":
        selected_mode = "arxiv_source"
    else:
        selected_mode = "arxiv_source"
    if selected_mode == "mineru":
        kickoff_message = "启动 MinerU 本地 OCR 图表提取..."
        pulse_message = "正在通过本地 MinerU pipeline 解析 PDF 图表..."
    else:
        kickoff_message = "启动图表提取..."
        pulse_message = "正在提取 arXiv 图片；若原图不可用且 OCR 已就绪，将回退到 OCR 图像候选并补充表格..."

    _progress(kickoff_message, 15, 100)
    stop_event, pulse_thread = _start_pulse(pulse_message, start=20, end=85)
    svc = FigureService()
    try:
        svc.extract_paper_figure_candidates(
            paper_id=paper_id,
            pdf_path=pdf_path,
            max_figures=max_figures,
            arxiv_id=source_arxiv_id,
            extract_mode=selected_mode,
        )
    finally:
        if stop_event is not None:
            stop_event.set()
        if pulse_thread is not None:
            pulse_thread.join(timeout=0.5)

    _progress("读取提取结果...", 90, 100)
    items = paper_figure_items(paper_id)
    if not items:
        failure_message = (
            "图表提取失败：未找到可用 arXiv 图片，也没有可用的 OCR 图像或表格候选"
            if selected_mode == "arxiv_source"
            else "图表提取失败：未找到可用 MinerU 图表候选"
        )
        _progress(failure_message, 100, 100)
        raise FigureExtractionEmptyError(failure_message)
    _progress("图表提取完成", 100, 100)
    return {"paper_id": str(paper_id), "count": len(items), "items": items}
