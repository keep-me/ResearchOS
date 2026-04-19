from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

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
    """Run the installed MinerU pipeline locally and cache outputs under repo root."""

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
        return Path(__file__).resolve().parents[2]

    @classmethod
    def base_dir(cls) -> Path:
        override = str(os.environ.get("RESEARCHOS_MINERU_DIR") or "").strip()
        if override:
            return Path(override).expanduser().resolve()
        if getattr(sys, "frozen", False):
            appdata = str(os.environ.get("APPDATA") or "").strip()
            if appdata:
                return (Path(appdata).expanduser() / "ResearchOS" / "MinerU").resolve()
            return (Path.home() / "AppData" / "Roaming" / "ResearchOS" / "MinerU").resolve()
        return cls.repo_root() / "MinerU"

    @classmethod
    def runtime_dir(cls) -> Path:
        return cls.base_dir() / "runtime"

    @classmethod
    def cache_dir(cls) -> Path:
        return cls.base_dir() / "cache"

    @classmethod
    def models_dir(cls) -> Path:
        return cls.base_dir() / "models"

    @classmethod
    def local_config_path(cls) -> Path:
        return cls.base_dir() / "mineru.local.json"

    @classmethod
    def _resolve_device_mode(cls) -> str:
        explicit = str(os.environ.get("MINERU_DEVICE_MODE") or "").strip().lower()
        if explicit:
            return explicit
        try:
            import torch
        except Exception:
            return "cpu"
        try:
            if bool(getattr(getattr(torch, "cuda", None), "is_available", lambda: False)()):
                return "cuda"
        except Exception:
            pass
        return "cpu"

    @classmethod
    def prepare_runtime(
        cls,
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> Path:
        cls._ensure_runtime_dirs()
        model_dir = cls._resolve_pipeline_model_dir()
        if cls._is_pipeline_model_dir_ready(model_dir):
            if progress_callback:
                progress_callback("MinerU pipeline 模型已就绪", 20, 100)
            return model_dir

        if progress_callback:
            progress_callback("未检测到本地 MinerU pipeline 模型，开始自动下载...", 18, 100)
        source = cls._download_pipeline_models(model_dir, progress_callback=progress_callback)
        if not cls._is_pipeline_model_dir_ready(model_dir):
            raise RuntimeError(f"MinerU pipeline 模型下载后仍不可用：{model_dir}")
        if progress_callback:
            progress_callback(f"MinerU pipeline 模型已下载完成（{source}）", 34, 100)
        return model_dir

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

            model_dir = cls.prepare_runtime()
            config_path = cls._write_local_config(model_dir)
            device_mode = cls._resolve_device_mode()
            cls._reset_output_root(output_root)

            manifest: dict[str, Any] = {
                "paper_id": str(paper_id),
                "pdf_path": str(pdf_file.resolve()),
                "pdf_sha256": pdf_sha256,
                "output_root": str(output_root.resolve()),
                "backend": "pipeline",
                "model_dir": str(model_dir) if model_dir is not None else "",
                "device_mode": device_mode,
                "updated_at": _utc_now_iso(),
                "status": "running",
            }
            cls._write_manifest(output_root, manifest)

            try:
                cls._run_installed_mineru(
                    pdf_path=pdf_file,
                    output_root=output_root,
                    config_path=config_path,
                    device_mode=device_mode,
                )
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
    def _run_installed_mineru(
        cls,
        *,
        pdf_path: Path,
        output_root: Path,
        config_path: Path,
        device_mode: str,
    ) -> None:
        try:
            from mineru.cli.common import do_parse
        except Exception as exc:
            if getattr(sys, "frozen", False):
                hint = "桌面端后端未正确打包 MinerU 依赖"
            else:
                hint = "当前 Python 环境未完整安装 MinerU 依赖"
            raise RuntimeError(
                f"MinerU 运行时不可用：{hint}。原始错误：{type(exc).__name__}: {exc}"
            ) from exc

        pdf_bytes = pdf_path.read_bytes()
        env_updates = {
            "MINERU_TOOLS_CONFIG_JSON": str(config_path.resolve()),
            "MINERU_MODEL_SOURCE": "local",
            "MINERU_RUNTIME_CACHE_DIR": str(cls.cache_dir().resolve()),
            "MINERU_DEVICE_MODE": str(device_mode or "cpu"),
        }
        logger.info("Running MinerU pipeline with device_mode=%s", device_mode or "cpu")
        with cls._temporary_environ(env_updates):
            do_parse(
                output_dir=str(output_root),
                pdf_file_names=[pdf_path.name],
                pdf_bytes_list=[pdf_bytes],
                p_lang_list=["en"],
                backend="pipeline",
                parse_method="auto",
                formula_enable=True,
                table_enable=True,
                f_draw_layout_bbox=False,
                f_draw_span_bbox=False,
                f_dump_md=True,
                f_dump_middle_json=True,
                f_dump_model_output=False,
                f_dump_orig_pdf=False,
                f_dump_content_list=True,
            )

    @classmethod
    def _ensure_runtime_dirs(cls) -> None:
        for path in (cls.base_dir(), cls.runtime_dir(), cls.cache_dir(), cls.models_dir()):
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
    def _resolve_pipeline_model_dir(cls) -> Path | None:
        env_override = str(os.environ.get("MINERU_PIPELINE_MODEL_DIR") or "").strip()
        if env_override:
            candidate = Path(env_override).expanduser().resolve()
            return candidate

        local_candidate = (cls.models_dir() / "pipeline").resolve()
        if cls._is_pipeline_model_dir_ready(local_candidate):
            return local_candidate

        home_config_candidate = cls._pipeline_model_dir_from_home_config()
        if home_config_candidate is not None:
            return home_config_candidate

        return local_candidate

    @classmethod
    def _pipeline_model_dir_from_home_config(cls) -> Path | None:
        home_config = Path.home() / "mineru.json"
        if not home_config.exists():
            return None
        try:
            payload = json.loads(home_config.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return None
        models_dir = payload.get("models-dir") if isinstance(payload, dict) else None
        candidate = ""
        if isinstance(models_dir, dict):
            candidate = str(models_dir.get("pipeline") or "").strip()
        if not candidate:
            return None
        resolved = Path(candidate).expanduser().resolve()
        return resolved if cls._is_pipeline_model_dir_ready(resolved) else None

    @staticmethod
    def _looks_like_model_dir(path: Path) -> bool:
        if not path.exists() or not path.is_dir():
            return False
        child_names = {child.name for child in path.iterdir()}
        markers = {"models", ".mdl", ".msc", ".mv"}
        return bool(child_names) and bool(child_names & markers or len(child_names) >= 3)

    @classmethod
    def _is_pipeline_model_dir_ready(cls, path: Path | None) -> bool:
        if path is None:
            return False
        if not cls._looks_like_model_dir(path):
            return False
        return all(
            candidate.exists()
            for candidate in (
                path / ".mdl",
                path / ".msc",
                path / ".mv",
                path / "models",
            )
        )

    @classmethod
    def _download_pipeline_models(
        cls,
        model_dir: Path,
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> str:
        lock = _runtime_lock(f"mineru-models:{str(model_dir).lower()}")
        with lock:
            if cls._is_pipeline_model_dir_ready(model_dir):
                return "local"

            try:
                from huggingface_hub import snapshot_download as hf_snapshot_download
            except Exception as exc:
                hf_snapshot_download = None
                hf_import_error = exc
            else:
                hf_import_error = None

            try:
                from modelscope import snapshot_download as ms_snapshot_download
            except Exception as exc:
                ms_snapshot_download = None
                ms_import_error = exc
            else:
                ms_import_error = None

            candidates: list[tuple[str, object, str]] = []
            if ms_snapshot_download is not None:
                candidates.append(("modelscope", ms_snapshot_download, "OpenDataLab/PDF-Extract-Kit-1.0"))
            elif ms_import_error is not None:
                logger.debug("ModelScope unavailable for MinerU download: %s", ms_import_error)
            if hf_snapshot_download is not None:
                candidates.append(("huggingface", hf_snapshot_download, "opendatalab/PDF-Extract-Kit-1.0"))
            elif hf_import_error is not None:
                logger.debug("HuggingFace Hub unavailable for MinerU download: %s", hf_import_error)

            if not candidates:
                raise RuntimeError("未安装 MinerU 模型下载依赖：缺少 modelscope / huggingface_hub")

            errors: list[str] = []
            model_dir.parent.mkdir(parents=True, exist_ok=True)

            for index, (source_name, snapshot_download, repo_id) in enumerate(candidates):
                if progress_callback:
                    progress_callback(
                        f"正在从 {source_name} 下载 MinerU pipeline 模型到 {model_dir}...",
                        20 + index * 8,
                        100,
                    )
                shutil.rmtree(model_dir, ignore_errors=True)
                model_dir.mkdir(parents=True, exist_ok=True)
                try:
                    snapshot_download(
                        repo_id,
                        local_dir=str(model_dir),
                    )
                    if cls._is_pipeline_model_dir_ready(model_dir):
                        logger.info("MinerU pipeline models downloaded via %s to %s", source_name, model_dir)
                        return source_name
                    errors.append(f"{source_name}: download completed but expected model files are missing")
                except Exception as exc:
                    errors.append(f"{source_name}: {type(exc).__name__}: {exc}")
                    logger.warning("MinerU pipeline model download failed via %s: %s", source_name, exc)

            shutil.rmtree(model_dir, ignore_errors=True)
            raise RuntimeError("自动下载 MinerU pipeline 模型失败；" + "；".join(errors))

    @classmethod
    def _write_local_config(cls, model_dir: Path | None) -> Path:
        config_path = cls.local_config_path()
        payload: dict[str, Any] = cls._read_home_config_template()
        payload["models-dir"] = {
            "pipeline": str(model_dir.resolve()) if model_dir is not None else "",
            "vlm": "",
        }
        payload["config_version"] = str(payload.get("config_version") or "1.3.1")
        config_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return config_path

    @staticmethod
    def _read_home_config_template() -> dict[str, Any]:
        template_path = Path.home() / "mineru.json"
        if template_path.exists():
            try:
                payload = json.loads(template_path.read_text(encoding="utf-8", errors="ignore"))
                if isinstance(payload, dict):
                    return payload
            except Exception:
                pass
        return {
            "bucket_info": {},
            "latex-delimiter-config": {
                "display": {"left": "$$", "right": "$$"},
                "inline": {"left": "$", "right": "$"},
            },
            "llm-aided-config": {
                "title_aided": {
                    "api_key": "",
                    "base_url": "",
                    "model": "",
                    "enable_thinking": False,
                    "enable": False,
                }
            },
            "models-dir": {"pipeline": "", "vlm": ""},
            "config_version": "1.3.1",
        }

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
                    "model_dir": str(manifest.get("model_dir") or ""),
                    "device_mode": str(manifest.get("device_mode") or "").strip() or None,
                    "markdown_chars": int(manifest.get("markdown_chars") or 0),
                    "has_structured_output": bool(manifest.get("has_structured_output")),
                    "error": str(manifest.get("error") or "").strip() or None,
                }
                paper.metadata_json = metadata
        except Exception as exc:
            logger.debug("Failed to sync MinerU OCR metadata for %s: %s", str(paper_id)[:8], exc)

    @staticmethod
    @contextmanager
    def _temporary_environ(values: dict[str, str]):
        old_values = {key: os.environ.get(key) for key in values}
        try:
            for key, value in values.items():
                os.environ[key] = str(value)
            yield
        finally:
            for key, old_value in old_values.items():
                if old_value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old_value
