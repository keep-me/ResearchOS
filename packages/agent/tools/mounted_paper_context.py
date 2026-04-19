from __future__ import annotations

import re

from packages.agent.tools.skill_registry import list_local_skills
from packages.storage.db import session_scope
from packages.storage.models import Paper


def _normalize_ids(values: list[str] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values or []:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _is_real_arxiv_id(value: str | None) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    return bool(
        re.fullmatch(
            r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?",
            raw,
            flags=re.IGNORECASE,
        )
    )


def resolve_research_skill_ids(
    explicit_ids: list[str] | None,
    mounted_paper_ids: list[str] | None,
) -> list[str]:
    resolved = _normalize_ids(explicit_ids)
    if not _normalize_ids(mounted_paper_ids):
        return resolved

    auto_ids: list[str] = []
    try:
        for item in list_local_skills():
            skill_id = str(item.get("id") or "").strip()
            if not skill_id.startswith("project:researchos-"):
                continue
            auto_ids.append(skill_id)
    except Exception:
        return resolved
    return _normalize_ids([*resolved, *auto_ids])


def _paper_pdf_summary(paper: Paper) -> str:
    metadata = dict(getattr(paper, "metadata_json", None) or {})
    local_pdf = str(getattr(paper, "pdf_path", "") or "").strip()
    if local_pdf:
        return f"本地 PDF：{local_pdf}"
    pdf_url = str(metadata.get("pdf_url") or "").strip()
    if pdf_url:
        return f"远程 PDF：{pdf_url}"
    arxiv_id = str(getattr(paper, "arxiv_id", "") or "").strip()
    if _is_real_arxiv_id(arxiv_id):
        return f"PDF 可按 arXiv 下载：{arxiv_id}"
    return "PDF：暂未就绪"


def _paper_analysis_summary(paper: Paper) -> str:
    metadata = dict(getattr(paper, "metadata_json", None) or {})
    parts: list[str] = []
    skim_report = metadata.get("skim_report")
    if not isinstance(skim_report, dict):
        skim_report = getattr(paper, "skim_report_json", None)
    if isinstance(skim_report, dict) and skim_report:
        parts.append("粗读")
    deep_report = metadata.get("deep_report")
    if not isinstance(deep_report, dict):
        deep_report = getattr(paper, "deep_report_json", None)
    if isinstance(deep_report, dict) and deep_report:
        parts.append("精读")
    analysis_rounds = metadata.get("analysis_rounds") if isinstance(metadata.get("analysis_rounds"), dict) else {}
    round_keys: list[str] = []
    for key in ("round_1", "round_2", "round_3", "final_notes"):
        payload = analysis_rounds.get(key) if isinstance(analysis_rounds, dict) else None
        if isinstance(payload, dict) and str(payload.get("markdown") or "").strip():
            round_keys.append(key)
    if round_keys:
        parts.append(f"三轮分析({', '.join(round_keys)})")
    return "、".join(parts) if parts else "无"


def _paper_figure_summary(paper_id: str) -> str | None:
    try:
        from packages.ai.paper.figure_service import FigureService

        count = len(FigureService.get_paper_analyses(paper_id))
    except Exception:
        return None
    if count <= 0:
        return None
    return "图表"


def _paper_asset_summary(paper: Paper) -> str:
    statuses: list[str] = []
    if str(getattr(paper, "pdf_path", "") or "").strip() or _is_real_arxiv_id(getattr(paper, "arxiv_id", None)):
        statuses.append("PDF")
    if getattr(paper, "embedding", None):
        statuses.append("向量")

    analysis = _paper_analysis_summary(paper)
    if analysis != "无":
        statuses.extend(part for part in analysis.split("、") if part)

    figure_label = _paper_figure_summary(str(paper.id))
    if figure_label:
        statuses.append(figure_label)
    return " / ".join(statuses) if statuses else "无可用资产"


def build_mounted_papers_prompt(
    mounted_paper_ids: list[str] | None,
    mounted_primary_paper_id: str | None = None,
) -> str:
    paper_ids = _normalize_ids(mounted_paper_ids)
    if not paper_ids:
        return ""

    primary_id = str(mounted_primary_paper_id or "").strip()
    lines = [
        "以下论文已由用户显式导入当前研究助手会话。",
        "导入代表当前会话默认可访问这些论文；这不是把全文、摘要和分析结果整批注入上下文。",
        "不要再要求用户重新上传 PDF、重新提供标题或重新粘贴链接。",
        "当用户说“这篇论文”“导入的论文”而未额外指明时，默认优先指向 primary paper。",
        "当前提示只列出论文元信息、ID 和资产状态；需要证据时按 paper_id 调用论文详情、粗读、精读、图表分析、三轮分析或 PDF/OCR 读取工具。",
        "优先只展开与当前问题最相关的 1-3 篇导入论文；不要一开始就把全部导入论文都当成本轮重点。",
        "当前导入论文足以回答时，不要先搜索整个论文库或外部文献；只有证据不足或用户明确要求找更多相关论文时再检索。",
        "回答前先优先读取本地论文详情与已有分析；若现有分析不足，再按问题类型调用粗读、精读、图表分析或三轮分析工具。",
        "若问题涉及框架、结构、编码器、解码器、流程、模块、图表，请优先引用原论文图表；不要用 ASCII 方框图替代原文图片。",
        "若问题需要精确公式、变量定义、表格数值、超参数或原句，请优先核对 Markdown/PDF/图表，不要只依赖摘要型分析。",
    ]
    if primary_id:
        lines.append(f"Primary paper ID：{primary_id}")
    lines.append("")
    lines.append("[Mounted Papers]")

    with session_scope() as session:
        for index, paper_id in enumerate(paper_ids, start=1):
            paper = session.get(Paper, paper_id)
            if paper is None:
                lines.append(f"{index}. {paper_id}（本地论文记录不存在）")
                continue

            metadata = dict(getattr(paper, "metadata_json", None) or {})
            source_url = str(metadata.get("source_url") or "").strip()
            year = getattr(paper, "publication_date", None)

            header = f"{index}. {str(getattr(paper, 'title', '') or '').strip() or paper_id}"
            if str(getattr(paper, "id", "") or "").strip() == primary_id:
                header += " [primary]"
            lines.append(header)
            lines.append(f"   paper_id：{paper.id}")
            if str(getattr(paper, "arxiv_id", "") or "").strip():
                lines.append(f"   arXiv：{paper.arxiv_id}")
            if year is not None:
                lines.append(f"   年份：{getattr(year, 'year', year)}")
            lines.append(f"   资产：{_paper_asset_summary(paper)}")
            authors = metadata.get("authors") if isinstance(metadata.get("authors"), list) else []
            if authors:
                lines.append(f"   作者：{'、'.join(str(item).strip() for item in authors if str(item).strip())}")
            keywords = metadata.get("keywords") if isinstance(metadata.get("keywords"), list) else []
            if keywords:
                lines.append(f"   关键词：{'、'.join(str(item).strip() for item in keywords if str(item).strip())}")
            analysis_summary = _paper_analysis_summary(paper)
            if analysis_summary != "无":
                lines.append(f"   已有分析：{analysis_summary}")
            lines.append(f"   {_paper_pdf_summary(paper)}")
            if source_url:
                lines.append(f"   来源：{source_url}")
    return "\n".join(lines).strip()

