from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from packages.config import get_settings
from packages.ai.paper.document_context import PaperDocumentContext
from packages.storage.db import session_scope
from packages.storage.repositories import PaperRepository

logger = logging.getLogger(__name__)

_MANIFEST_NAME = "manifest.json"
_FAILED_RETRY_COOLDOWN = timedelta(minutes=10)
_LOCKS_GUARD = threading.Lock()
_RUNTIME_LOCKS: dict[str, threading.Lock] = {}
_SKIP_RETRY = object()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_iso_to_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _runtime_lock(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _RUNTIME_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _RUNTIME_LOCKS[key] = lock
        return lock


@dataclass(slots=True)
class MinerUOcrBundle:
    paper_id: UUID
    pdf_path: str
    pdf_sha256: str
    output_root: Path
    manifest: dict[str, Any]
    markdown_text: str
    markdown_files: list[str]
    has_structured_output: bool

    @property
    def available(self) -> bool:
        return bool(self.markdown_text.strip() or self.has_structured_output)

    def build_document_context(self) -> PaperDocumentContext:
        markdown = str(self.markdown_text or "").strip()
        if markdown:
            return PaperDocumentContext.from_markdown(
                markdown,
                source="MinerU OCR Markdown",
            )
        return PaperDocumentContext.from_text("", source="MinerU OCR Markdown")

    def build_targeted_context(
        self,
        *,
        name: str,
        targets: list[str],
        max_chars: int,
        max_sections: int = 6,
        max_figures: int = 4,
        max_tables: int = 4,
        max_equations: int = 3,
        include_outline: bool = True,
        notes: list[str] | None = None,
    ) -> str:
        return self.build_document_context().build_targeted_context(
            name=name,
            targets=targets,
            max_chars=max_chars,
            max_sections=max_sections,
            max_figures=max_figures,
            max_tables=max_tables,
            max_equations=max_equations,
            include_outline=include_outline,
            notes=notes,
        )

    def build_round_context(self, round_name: str, *, max_chars: int) -> str:
        return self.build_document_context().build_round_context(
            round_name,
            max_chars=max_chars,
        )

    def build_analysis_context(self, *, max_chars: int = 18000) -> str:
        return self.build_targeted_context(
            name="全文结构化证据包",
            targets=[
                "overview",
                "method",
                "experiment",
                "results",
                "ablation",
                "limitations",
                "discussion",
                "figure",
                "table",
                "equation",
            ],
            max_chars=max_chars,
            max_sections=10,
            max_figures=6,
            max_tables=6,
            max_equations=5,
            include_outline=True,
            notes=[
                "证据按全文结构跨章节选择，不代表论文只截到某一节。",
                "分析时若某结论缺少明确证据，请直接说明证据不足，不要臆测。",
            ],
        )


class MinerUOcrRuntime:
    """Run MinerU via remote API and cache outputs under repo root."""

    @classmethod
    def get_cached_bundle(
        cls,
        paper_id: UUID,
        pdf_path: str,
    ) -> MinerUOcrBundle | None:
        pdf_file = Path(pdf_path)
        if not pdf_file.exists():
            return None

        cls._ensure_runtime_dirs()
        pdf_sha256 = cls._hash_file(pdf_file)
        output_root = cls.runtime_dir() / str(paper_id) / pdf_sha256[:16]
        manifest = cls._read_manifest(output_root)
        if str(manifest.get("pdf_sha256") or "") != pdf_sha256:
            return None
        status = str(manifest.get("status") or "").strip().lower()
        if status != "success" or not cls._has_outputs(output_root):
            return None
        return cls._build_bundle(
            paper_id=paper_id,
            pdf_path=str(pdf_file),
            pdf_sha256=pdf_sha256,
            output_root=output_root,
            extra_manifest=manifest,
        )

    @classmethod
    def repo_root(cls) -> Path:
        return Path(__file__).resolve().parents[3]

    @classmethod
    def base_dir(cls) -> Path:
        return cls.repo_root() / "MinerU"

    @classmethod
    def runtime_dir(cls) -> Path:
        return cls.base_dir() / "runtime"

    @classmethod
    def ensure_bundle(
        cls,
        paper_id: UUID,
        pdf_path: str,
        *,
        force: bool = False,
    ) -> MinerUOcrBundle | None:
        pdf_file = Path(pdf_path)
        if not pdf_file.exists():
            return None

        cls._ensure_runtime_dirs()
        pdf_sha256 = cls._hash_file(pdf_file)
        output_root = cls.runtime_dir() / str(paper_id) / pdf_sha256[:16]
        lock = _runtime_lock(f"{paper_id}:{pdf_sha256[:16]}")

        with lock:
            existing = cls._load_cached_bundle(
                paper_id=paper_id,
                pdf_path=str(pdf_file),
                pdf_sha256=pdf_sha256,
                output_root=output_root,
                force=force,
            )
            if existing is _SKIP_RETRY:
                return None
            if existing is not None:
                return existing

            backend = cls._resolve_backend()
            cls._reset_output_root(output_root)

            manifest: dict[str, Any] = {
                "paper_id": str(paper_id),
                "pdf_path": str(pdf_file.resolve()),
                "pdf_sha256": pdf_sha256,
                "output_root": str(output_root.resolve()),
                "backend": backend,
                "updated_at": _utc_now_iso(),
                "status": "running",
            }
            cls._write_manifest(output_root, manifest)

            try:
                api_manifest = cls._run_remote_api_mineru(
                    paper_id=paper_id,
                    pdf_path=pdf_file,
                    output_root=output_root,
                )
                manifest.update(api_manifest)
                bundle = cls._build_bundle(
                    paper_id=paper_id,
                    pdf_path=str(pdf_file),
                    pdf_sha256=pdf_sha256,
                    output_root=output_root,
                    extra_manifest=manifest,
                )
                if not bundle.available:
                    raise RuntimeError("MinerU completed but produced no OCR markdown or structured outputs")
                success_manifest = dict(bundle.manifest)
                success_manifest["status"] = "success"
                success_manifest["updated_at"] = _utc_now_iso()
                cls._write_manifest(output_root, success_manifest)
                cls._sync_paper_metadata(paper_id, success_manifest)
                return cls._build_bundle(
                    paper_id=paper_id,
                    pdf_path=str(pdf_file),
                    pdf_sha256=pdf_sha256,
                    output_root=output_root,
                    extra_manifest=success_manifest,
                )
            except Exception as exc:
                failure_manifest = dict(manifest)
                failure_manifest["status"] = "failed"
                failure_manifest["error"] = str(exc)
                failure_manifest["updated_at"] = _utc_now_iso()
                cls._write_manifest(output_root, failure_manifest)
                cls._sync_paper_metadata(paper_id, failure_manifest)
                logger.exception("MinerU OCR unavailable for %s: %s", str(paper_id)[:8], exc)
                return None

    @classmethod
    def _load_cached_bundle(
        cls,
        *,
        paper_id: UUID,
        pdf_path: str,
        pdf_sha256: str,
        output_root: Path,
        force: bool,
    ) -> MinerUOcrBundle | object | None:
        manifest = cls._read_manifest(output_root)
        if not manifest:
            return None
        if str(manifest.get("pdf_sha256") or "") != pdf_sha256:
            return None
        if force:
            return None

        status = str(manifest.get("status") or "").strip().lower()
        if status == "success" and cls._has_outputs(output_root):
            return cls._build_bundle(
                paper_id=paper_id,
                pdf_path=pdf_path,
                pdf_sha256=pdf_sha256,
                output_root=output_root,
                extra_manifest=manifest,
            )

        if status == "failed":
            updated_at = _safe_iso_to_datetime(str(manifest.get("updated_at") or ""))
            if updated_at is None:
                return None
            if datetime.now(UTC) - updated_at < _FAILED_RETRY_COOLDOWN:
                return _SKIP_RETRY
        return None

    @classmethod
    def _build_bundle(
        cls,
        *,
        paper_id: UUID,
        pdf_path: str,
        pdf_sha256: str,
        output_root: Path,
        extra_manifest: dict[str, Any] | None = None,
    ) -> MinerUOcrBundle:
        markdown_files = cls._collect_markdown_files(output_root)
        markdown_text = cls._read_markdown_text(markdown_files)
        manifest = dict(extra_manifest or {})
        manifest["markdown_files"] = [str(path) for path in markdown_files]
        manifest["markdown_chars"] = len(markdown_text)
        manifest["has_structured_output"] = cls._has_structured_outputs(output_root)
        return MinerUOcrBundle(
            paper_id=paper_id,
            pdf_path=pdf_path,
            pdf_sha256=pdf_sha256,
            output_root=output_root,
            manifest=manifest,
            markdown_text=markdown_text,
            markdown_files=[str(path) for path in markdown_files],
            has_structured_output=bool(manifest["has_structured_output"]),
        )

    @classmethod
    def _resolve_backend(cls) -> str:
        api_token = cls._mineru_api_token()
        if not api_token:
            raise RuntimeError("未配置 MINERU_API_TOKEN，无法调用 MinerU API")
        return "api"

    @classmethod
    def _mineru_api_base_url(cls) -> str:
        settings = get_settings()
        return str(
            os.environ.get("MINERU_API_BASE_URL")
            or getattr(settings, "mineru_api_base_url", "")
            or "https://mineru.net"
        ).strip().rstrip("/")

    @classmethod
    def _mineru_api_token(cls) -> str:
        settings = get_settings()
        return str(
            os.environ.get("MINERU_API_TOKEN")
            or getattr(settings, "mineru_api_token", "")
            or ""
        ).strip()

    @classmethod
    def _mineru_api_model_version(cls) -> str:
        settings = get_settings()
        value = str(
            os.environ.get("MINERU_API_MODEL_VERSION")
            or getattr(settings, "mineru_api_model_version", "")
            or "vlm"
        ).strip()
        return value or "vlm"

    @classmethod
    def _mineru_api_poll_interval_seconds(cls) -> float:
        settings = get_settings()
        try:
            value = float(
                os.environ.get("MINERU_API_POLL_INTERVAL_SECONDS")
                or getattr(settings, "mineru_api_poll_interval_seconds", 3.0)
                or 3.0
            )
        except Exception:
            value = 3.0
        return min(max(value, 1.0), 30.0)

    @classmethod
    def _mineru_api_timeout_seconds(cls) -> float:
        settings = get_settings()
        try:
            value = float(
                os.environ.get("MINERU_API_TIMEOUT_SECONDS")
                or getattr(settings, "mineru_api_timeout_seconds", 300)
                or 300
            )
        except Exception:
            value = 300.0
        return min(max(value, 60.0), 3600.0)

    @classmethod
    def _mineru_api_upload_timeout_seconds(cls) -> float:
        settings = get_settings()
        try:
            value = float(
                os.environ.get("MINERU_API_UPLOAD_TIMEOUT_SECONDS")
                or getattr(settings, "mineru_api_upload_timeout_seconds", 600)
                or 600
            )
        except Exception:
            value = 600.0
        return min(max(value, 60.0), 3600.0)

    @classmethod
    def _run_remote_api_mineru(
        cls,
        *,
        paper_id: UUID,
        pdf_path: Path,
        output_root: Path,
    ) -> dict[str, Any]:
        token = cls._mineru_api_token()
        if not token:
            raise RuntimeError("未配置 MINERU_API_TOKEN，无法调用 MinerU API")

        base_url = cls._mineru_api_base_url()
        model_version = cls._mineru_api_model_version()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        timeout = cls._mineru_api_timeout_seconds()
        upload_timeout = cls._mineru_api_upload_timeout_seconds()
        batch_payload = {
            "enable_formula": True,
            "enable_table": True,
            "language": "en",
            "model_version": model_version,
            "files": [
                {
                    "name": pdf_path.name,
                    "data_id": str(paper_id),
                }
            ],
        }
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.post(
                f"{base_url}/api/v4/file-urls/batch",
                headers=headers,
                json=batch_payload,
            )
            response.raise_for_status()
            response_payload = cls._decode_json_response(response, "创建 MinerU 上传任务失败")
            data = cls._extract_response_data(response_payload)
            batch_id = str(data.get("batch_id") or "").strip()
            file_urls = data.get("file_urls")
            if not batch_id or not isinstance(file_urls, list) or not file_urls:
                raise RuntimeError(f"MinerU API 返回缺少 batch_id/file_urls：{response_payload}")
            first_upload = file_urls[0]
            if isinstance(first_upload, dict):
                upload_url = str(first_upload.get("upload_url") or "").strip()
            else:
                upload_url = str(first_upload or "").strip()
            if not upload_url:
                raise RuntimeError(f"MinerU API 返回缺少 upload_url：{response_payload}")

            file_bytes = pdf_path.read_bytes()
            upload_response = client.put(
                upload_url,
                content=file_bytes,
                timeout=upload_timeout,
            )
            upload_response.raise_for_status()

            poll_start = time.monotonic()
            poll_interval = cls._mineru_api_poll_interval_seconds()
            last_payload: dict[str, Any] | None = None
            while True:
                if time.monotonic() - poll_start > timeout:
                    raise RuntimeError(f"等待 MinerU API 结果超时（batch_id={batch_id}）")

                poll_response = client.get(
                    f"{base_url}/api/v4/extract-results/batch/{batch_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                poll_response.raise_for_status()
                poll_payload = cls._decode_json_response(poll_response, "查询 MinerU 批量任务状态失败")
                last_payload = poll_payload
                result_data = cls._extract_response_data(poll_payload)
                file_result = cls._pick_batch_file_result(result_data, paper_id=paper_id, filename=pdf_path.name)
                state = cls._normalize_remote_state(file_result.get("state"))
                if state in {"done", "success", "completed", "finish", "finished"}:
                    full_zip_url = cls._extract_zip_url(file_result)
                    if not full_zip_url:
                        raise RuntimeError(f"MinerU 任务完成但未返回结果包地址：{file_result}")
                    cls._download_and_extract_zip(
                        client=client,
                        zip_url=full_zip_url,
                        output_root=output_root,
                        timeout=upload_timeout,
                    )
                    return {
                        "backend": "api",
                        "mineru_api_base_url": base_url,
                        "mineru_api_batch_id": batch_id,
                        "mineru_api_state": state,
                        "mineru_api_model_version": model_version,
                        "mineru_api_result_url": full_zip_url,
                    }
                if state in {"failed", "error"}:
                    message = cls._extract_remote_error(file_result) or cls._extract_remote_error(result_data)
                    raise RuntimeError(message or f"MinerU API 解析失败（batch_id={batch_id}）")
                time.sleep(poll_interval)

            raise RuntimeError(f"MinerU API 未返回有效结果：{last_payload}")

    @staticmethod
    def _decode_json_response(response: httpx.Response, message: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(f"{message}：{type(exc).__name__}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"{message}：响应不是 JSON 对象")
        code = payload.get("code")
        success_values = {0, 200, "0", "200", None}
        if code not in success_values:
            detail = payload.get("msg") or payload.get("message") or payload.get("detail") or payload
            raise RuntimeError(f"{message}：{detail}")
        return payload

    @staticmethod
    def _extract_response_data(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _normalize_remote_state(value: Any) -> str:
        return str(value or "").strip().lower().replace("_", "-")

    @classmethod
    def _pick_batch_file_result(cls, payload: dict[str, Any], *, paper_id: UUID, filename: str) -> dict[str, Any]:
        files = payload.get("extract_result")
        if not isinstance(files, list):
            files = payload.get("files")
        if not isinstance(files, list):
            return payload
        paper_id_text = str(paper_id)
        for item in files:
            if not isinstance(item, dict):
                continue
            if str(item.get("data_id") or "").strip() == paper_id_text:
                return item
        for item in files:
            if not isinstance(item, dict):
                continue
            if str(item.get("file_name") or item.get("name") or "").strip() == filename:
                return item
        for item in files:
            if isinstance(item, dict):
                return item
        return payload

    @staticmethod
    def _extract_zip_url(file_result: dict[str, Any]) -> str:
        candidates = [
            file_result.get("full_zip_url"),
            file_result.get("zip_url"),
            file_result.get("result_zip_url"),
        ]
        extract_result = file_result.get("extract_result")
        if isinstance(extract_result, dict):
            candidates.extend(
                [
                    extract_result.get("full_zip_url"),
                    extract_result.get("zip_url"),
                    extract_result.get("result_zip_url"),
                ]
            )
        for candidate in candidates:
            value = str(candidate or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _extract_remote_error(payload: Any) -> str:
        if isinstance(payload, dict):
            for key in ("err_msg", "error", "message", "msg", "detail"):
                value = str(payload.get(key) or "").strip()
                if value:
                    return value
        return ""

    @classmethod
    def _download_and_extract_zip(
        cls,
        *,
        client: httpx.Client,
        zip_url: str,
        output_root: Path,
        timeout: float,
    ) -> None:
        response = client.get(zip_url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        archive_path = output_root / "mineru_api_result.zip"
        archive_path.write_bytes(response.content)
        try:
            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(output_root)
        except Exception as exc:
            raise RuntimeError(f"解压 MinerU 结果包失败：{type(exc).__name__}: {exc}") from exc

    @classmethod
    def _ensure_runtime_dirs(cls) -> None:
        for path in (cls.base_dir(), cls.runtime_dir()):
            path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _reset_output_root(cls, output_root: Path) -> None:
        runtime_root = cls.runtime_dir().resolve()
        resolved = output_root.resolve()
        if resolved != runtime_root and runtime_root not in resolved.parents:
            raise RuntimeError(f"Unsafe MinerU runtime path: {resolved}")
        shutil.rmtree(resolved, ignore_errors=True)
        resolved.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _read_manifest(cls, output_root: Path) -> dict[str, Any]:
        manifest_path = output_root / _MANIFEST_NAME
        if not manifest_path.exists():
            return {}
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _write_manifest(cls, output_root: Path, payload: dict[str, Any]) -> None:
        manifest_path = output_root / _MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _collect_markdown_files(cls, output_root: Path) -> list[Path]:
        candidates = [path for path in output_root.rglob("*.md") if path.is_file()]
        return sorted(
            candidates,
            key=lambda path: (-path.stat().st_size, str(path).lower()),
        )

    @staticmethod
    def _read_markdown_text(markdown_files: list[Path], *, max_chars: int = 120000) -> str:
        chunks: list[str] = []
        used_chars = 0
        for path in markdown_files:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception:
                continue
            if not text:
                continue
            remaining = max_chars - used_chars
            if remaining <= 0:
                break
            piece = text[:remaining]
            chunks.append(piece)
            used_chars += len(piece)
        return "\n\n".join(chunks).strip()

    @classmethod
    def _has_outputs(cls, output_root: Path) -> bool:
        return bool(cls._collect_markdown_files(output_root) or cls._has_structured_outputs(output_root))

    @staticmethod
    def _has_structured_outputs(output_root: Path) -> bool:
        return any(output_root.rglob("*_middle.json")) or any(output_root.rglob("*_content_list.json"))

    @staticmethod
    def _split_markdown_sections(markdown: str) -> list[tuple[str, str]]:
        heading_pattern = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")
        matches = list(heading_pattern.finditer(markdown))
        if not matches:
            fallback_pattern = re.compile(
                r"(?m)^((?:\d+(?:\.\d+)*)|(?:Appendix|Supplementary|References))\s+(.{2,120})$"
            )
            matches = list(fallback_pattern.finditer(markdown))

        sections: list[tuple[str, str]] = []
        if not matches:
            return [("全文", markdown)]

        first_start = matches[0].start()
        if first_start > 0:
            preface = markdown[:first_start].strip()
            if preface:
                sections.append(("摘要 / 前置信息", preface))

        for index, match in enumerate(matches):
            title = match.group(0).strip()
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
            body = markdown[start:end].strip()
            if body:
                sections.append((title, body))
        return sections or [("全文", markdown)]

    @staticmethod
    def _build_outline_lines(sections: list[tuple[str, str]], *, max_items: int = 18) -> list[str]:
        if not sections:
            return []
        titles = [title.strip().lstrip("#").strip() for title, _ in sections if title.strip()]
        if len(titles) <= max_items:
            return titles
        selected = MinerUOcrRuntime._select_evenly_spaced_indices(len(titles), max_items)
        return [titles[index] for index in selected]

    @staticmethod
    def _collect_caption_lines(markdown: str, *, max_items: int = 12) -> list[str]:
        captions: list[str] = []
        seen: set[str] = set()
        for raw_line in markdown.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line:
                continue
            lowered = line.lower()
            if not (
                lowered.startswith("fig.")
                or lowered.startswith("figure ")
                or lowered.startswith("table ")
                or lowered.startswith("tab.")
            ):
                continue
            key = line.lower()
            if key in seen:
                continue
            seen.add(key)
            captions.append(line[:220])
            if len(captions) >= max_items:
                break
        return captions

    @staticmethod
    def _select_context_sections(sections: list[tuple[str, str]], *, max_sections: int = 7) -> list[tuple[str, str]]:
        if len(sections) <= max_sections:
            return sections

        def _is_priority_title(title: str) -> bool:
            lowered = title.lower()
            markers = (
                "abstract",
                "introduction",
                "method",
                "approach",
                "experiment",
                "evaluation",
                "result",
                "analysis",
                "ablation",
                "discussion",
                "conclusion",
                "appendix",
                "摘要",
                "引言",
                "方法",
                "实验",
                "结果",
                "分析",
                "消融",
                "讨论",
                "结论",
                "附录",
                "参考文献",
            )
            return any(marker in lowered for marker in markers)

        chosen_indices: list[int] = []
        for candidate in (0, 1, len(sections) - 2, len(sections) - 1):
            if 0 <= candidate < len(sections) and candidate not in chosen_indices:
                chosen_indices.append(candidate)

        for index, (title, _) in enumerate(sections):
            if len(chosen_indices) >= max_sections:
                break
            if _is_priority_title(title) and index not in chosen_indices:
                chosen_indices.append(index)

        if len(chosen_indices) < max_sections:
            for index in MinerUOcrRuntime._select_evenly_spaced_indices(len(sections), max_sections):
                if index not in chosen_indices:
                    chosen_indices.append(index)
                if len(chosen_indices) >= max_sections:
                    break

        chosen_indices = sorted(chosen_indices[:max_sections])
        return [sections[index] for index in chosen_indices]

    @staticmethod
    def _select_evenly_spaced_indices(total: int, count: int) -> list[int]:
        if total <= 0 or count <= 0:
            return []
        if total <= count:
            return list(range(total))
        if count == 1:
            return [0]
        picked = {
            round(step * (total - 1) / (count - 1))
            for step in range(count)
        }
        return sorted(min(total - 1, max(0, int(index))) for index in picked)

    @staticmethod
    def _build_section_excerpt(content: str, budget: int) -> str:
        compact = str(content or "").strip()
        if len(compact) <= budget:
            return compact
        if budget <= 200:
            return MinerUOcrRuntime._truncate_context_block(compact, budget)
        head_budget = int(budget * 0.68)
        tail_budget = max(80, budget - head_budget - 10)
        head = MinerUOcrRuntime._truncate_context_block(compact, head_budget)
        tail = compact[-tail_budget:].strip()
        if not tail:
            return head
        return f"{head}\n...\n{tail}"

    @staticmethod
    def _truncate_context_block(text: str, limit: int) -> str:
        if limit <= 0:
            return ""
        compact = str(text or "").strip()
        if len(compact) <= limit:
            return compact
        window = max(0, limit - 12)
        snippet = compact[:window]
        last_break = max(snippet.rfind("\n"), snippet.rfind(". "), snippet.rfind("。"))
        if last_break >= int(window * 0.65):
            snippet = snippet[: last_break + 1]
        return snippet.rstrip() + "\n...[截断]"

    @classmethod
    def _sync_paper_metadata(cls, paper_id: UUID, manifest: dict[str, Any]) -> None:
        try:
            with session_scope() as session:
                repo = PaperRepository(session)
                paper = repo.get_by_id(paper_id)
                metadata = dict(paper.metadata_json or {})
                metadata["mineru_ocr"] = {
                    "status": str(manifest.get("status") or "").strip(),
                    "updated_at": str(manifest.get("updated_at") or ""),
                    "pdf_sha256": str(manifest.get("pdf_sha256") or ""),
                    "output_root": str(manifest.get("output_root") or ""),
                    "backend": str(manifest.get("backend") or "").strip() or None,
                    "mineru_api_batch_id": str(manifest.get("mineru_api_batch_id") or "").strip() or None,
                    "mineru_api_model_version": str(manifest.get("mineru_api_model_version") or "").strip() or None,
                    "markdown_chars": int(manifest.get("markdown_chars") or 0),
                    "has_structured_output": bool(manifest.get("has_structured_output")),
                    "error": str(manifest.get("error") or "").strip() or None,
                }
                paper.metadata_json = metadata
        except Exception as exc:
            logger.debug("Failed to sync MinerU OCR metadata for %s: %s", str(paper_id)[:8], exc)
