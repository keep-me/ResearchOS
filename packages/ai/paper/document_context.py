from __future__ import annotations

from dataclasses import dataclass, field
import re

_HEADING_RE = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")
_FALLBACK_HEADING_RE = re.compile(
    r"(?m)^((?:\d+(?:\.\d+)*)|(?:Appendix|Supplementary|References|Acknowledgements))\s+(.{2,160})$"
)
_CAPTION_RE = re.compile(
    r"^(?P<label>(?:figure|fig\.|table|tab\.|algorithm)\s*\d+[a-z]?(?:\.\d+)?)[\s:.-]*(?P<body>.+)$",
    re.IGNORECASE,
)
_HTML_TABLE_RE = re.compile(r"(?is)<table\b[^>]*>.*?</table>")
_MARKDOWN_TABLE_RE = re.compile(
    r"(?ms)(^ *\|.+\|\s*\n^ *\|(?: *:?-+:? *\|)+\s*\n(?:^ *\|.*\|\s*\n?)+)"
)
_DISPLAY_EQUATION_RE = re.compile(r"(?s)\$\$(.+?)\$\$")
_LATEX_EQUATION_RE = re.compile(
    r"(?s)\\begin\{(?:equation|align|gather)\*?\}(.+?)\\end\{(?:equation|align|gather)\*?\}"
)
_BRACKET_EQUATION_RE = re.compile(r"(?s)\\\[(.+?)\\\]")
_NON_WORD_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+", re.IGNORECASE)
_TARGET_KEYWORDS = {
    "overview": (
        "abstract", "summary", "overview", "introduction", "motivation",
        "conclusion", "discussion", "摘要", "概述", "引言", "结论", "讨论",
    ),
    "method": (
        "method", "methods", "approach", "model", "architecture", "framework",
        "algorithm", "design", "preliminar", "methodology", "方法", "模型",
        "框架", "算法", "设计", "原理",
    ),
    "experiment": (
        "experiment", "evaluation", "result", "results", "benchmark", "dataset",
        "implementation", "setting", "实验", "评估", "结果", "数据集", "实现", "设置",
    ),
    "results": (
        "result", "results", "benchmark", "comparison", "main result", "主结果", "结果", "对比",
    ),
    "ablation": (
        "ablation", "analysis", "case study", "sensitivity", "error analysis",
        "消融", "分析", "案例", "误差", "敏感性",
    ),
    "limitations": (
        "limitation", "limitations", "failure", "future work", "threat", "caveat",
        "局限", "失败", "未来工作", "风险", "威胁",
    ),
    "discussion": ("discussion", "conclusion", "讨论", "结论"),
    "conclusion": ("conclusion", "future work", "结论", "未来工作"),
    "appendix": ("appendix", "supplementary", "附录", "补充"),
    "equation": (
        "theory", "theoretical", "derivation", "objective", "loss", "proof",
        "公式", "定理", "推导", "目标", "损失", "证明",
    ),
    "figure": ("figure", "fig.", "diagram", "chart", "plot", "图", "示意图", "曲线"),
    "table": ("table", "tab.", "表", "统计", "结果表"),
}
_ROUND_CONFIG = {
    "overview": {
        "targets": ["overview", "results", "discussion", "figure", "table"],
        "max_sections": 6,
        "max_figures": 5,
        "max_tables": 3,
        "max_equations": 1,
        "include_outline": True,
    },
    "comprehension": {
        "targets": ["overview", "method", "experiment", "results", "ablation", "table", "figure"],
        "max_sections": 8,
        "max_figures": 6,
        "max_tables": 5,
        "max_equations": 3,
        "include_outline": True,
    },
    "deep_analysis": {
        "targets": ["method", "experiment", "results", "ablation", "limitations", "discussion", "table", "figure", "equation"],
        "max_sections": 10,
        "max_figures": 6,
        "max_tables": 6,
        "max_equations": 5,
        "include_outline": True,
    },
}


def _clean_text(value: str | None) -> str:
    return str(value or "").strip()


def _compact_block(text: str | None) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _text_key(value: str | None) -> str:
    return _NON_WORD_RE.sub(" ", str(value or "").lower()).strip()


def _truncate_block(text: str, limit: int) -> str:
    compact = _compact_block(text)
    if limit <= 0:
        return compact
    if len(compact) <= limit:
        return compact
    if limit <= 200:
        return compact[: max(0, limit - 8)].rstrip() + " ..."
    head_budget = int(limit * 0.72)
    tail_budget = max(72, limit - head_budget - 7)
    head = compact[:head_budget].rstrip()
    tail = compact[-tail_budget:].lstrip()
    if not head:
        return compact[:limit].rstrip()
    if not tail:
        return head
    return f"{head}\n...\n{tail}"


def _extract_candidate_body(lines: list[str], start_index: int, *, max_lines: int = 6, max_chars: int = 900) -> str:
    body_lines: list[str] = []
    used_chars = 0
    for index in range(start_index + 1, min(len(lines), start_index + 1 + max_lines)):
        raw_line = lines[index]
        line = raw_line.strip()
        if not line:
            if body_lines:
                break
            continue
        if _HEADING_RE.match(line) or _CAPTION_RE.match(line):
            break
        used_chars += len(line)
        if used_chars > max_chars and body_lines:
            break
        body_lines.append(line)
    return _compact_block("\n".join(body_lines))


def _clean_html_table(raw_html: str) -> str:
    rows = re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", raw_html)
    rendered_rows: list[str] = []
    for row in rows[:10]:
        cells = re.findall(r"(?is)<t[dh]\b[^>]*>(.*?)</t[dh]>", row)
        cleaned = []
        for cell in cells:
            text = re.sub(r"(?is)<[^>]+>", " ", cell)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                cleaned.append(text)
        if cleaned:
            rendered_rows.append(" | ".join(cleaned))
    return _compact_block("\n".join(rendered_rows))


def _clean_markdown_table(raw_table: str) -> str:
    lines = []
    for raw_line in raw_table.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lines.append(line)
    return _compact_block("\n".join(lines[:10]))


def _nearest_caption(lines: list[str], line_index: int, *, kind: str) -> str:
    normalized_kind = "table" if kind == "table" else "figure"
    for index in range(line_index - 1, max(-1, line_index - 5), -1):
        if index < 0:
            break
        line = _compact_block(lines[index])
        if not line:
            continue
        match = _CAPTION_RE.match(line)
        if not match:
            continue
        label = str(match.group("label") or "").lower()
        if normalized_kind == "table" and not label.startswith(("table", "tab.")):
            continue
        if normalized_kind == "figure" and label.startswith(("table", "tab.")):
            continue
        return line
    return ""


@dataclass(slots=True)
class DocumentSection:
    title: str
    body: str
    level: int
    order: int
    source: str

    @property
    def title_key(self) -> str:
        return _text_key(self.title)


@dataclass(slots=True)
class StructuredEvidence:
    kind: str
    caption: str
    body: str
    order: int
    source: str

    @property
    def title(self) -> str:
        return self.caption or f"{self.kind.title()} 证据"

    @property
    def search_text(self) -> str:
        return _text_key(f"{self.caption}\n{self.body}")


@dataclass(slots=True)
class AnalysisEvidenceBundle:
    name: str
    source: str
    outline: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sections: list[DocumentSection] = field(default_factory=list)
    figures: list[StructuredEvidence] = field(default_factory=list)
    tables: list[StructuredEvidence] = field(default_factory=list)
    equations: list[StructuredEvidence] = field(default_factory=list)

    def render(self, *, max_chars: int) -> str:
        header = [
            f"以下是“{self.name}”的结构化证据包。",
            f"来源：{self.source}。",
            "说明：证据包按全文结构选择章节、图表、表格与公式，不代表论文只到某一节；不要把摘录边界误判为全文边界。",
        ]
        if self.notes:
            header.extend(str(note).strip() for note in self.notes if str(note).strip())
        blocks: list[str] = []
        if self.outline:
            blocks.append("[全文结构]\n" + "\n".join(f"- {item}" for item in self.outline))
        for section in self.sections:
            blocks.append(f"[章节 | {section.title}]\n{section.body}")
        for table in self.tables:
            content = table.body or table.caption
            blocks.append(f"[表格证据 | {table.caption or '表格'}]\n{content}")
        for figure in self.figures:
            content = figure.body or figure.caption
            blocks.append(f"[图像证据 | {figure.caption or '图像'}]\n{content}")
        for equation in self.equations:
            content = equation.body or equation.caption
            blocks.append(f"[公式证据 | {equation.caption or '公式'}]\n{content}")

        preface = "\n".join(f"- {line}" for line in header).strip()
        if max_chars <= 0:
            if not blocks:
                return preface
            return f"{preface}\n\n" + "\n\n".join(_compact_block(block) for block in blocks if _compact_block(block))
        if not blocks:
            return _truncate_block(preface, max_chars)
        budget = max_chars - len(preface) - 2
        if budget <= 0:
            return _truncate_block(preface, max_chars)
        rendered_blocks: list[str] = []
        used_chars = 0
        for index, block in enumerate(blocks):
            remaining = budget - used_chars
            if remaining <= 0:
                break
            remaining_blocks = len(blocks) - index
            block_budget = max(260, remaining // max(1, remaining_blocks))
            excerpt = _truncate_block(block, block_budget)
            if not excerpt:
                continue
            rendered_blocks.append(excerpt)
            used_chars += len(excerpt) + 2
        if not rendered_blocks:
            return _truncate_block(preface, max_chars)
        return f"{preface}\n\n" + "\n\n".join(rendered_blocks)


@dataclass(slots=True)
class PaperDocumentContext:
    source: str
    raw_text: str
    sections: list[DocumentSection] = field(default_factory=list)
    figures: list[StructuredEvidence] = field(default_factory=list)
    tables: list[StructuredEvidence] = field(default_factory=list)
    equations: list[StructuredEvidence] = field(default_factory=list)

    @classmethod
    def from_markdown(cls, markdown: str, *, source: str = "OCR Markdown") -> PaperDocumentContext:
        normalized = _compact_block(markdown)
        sections = cls._split_sections(normalized, source=source)
        figures, tables = cls._extract_caption_items(normalized, source=source)
        tables.extend(cls._extract_table_blocks(normalized, source=source))
        equations = cls._extract_equation_items(normalized, source=source)
        return cls(
            source=source,
            raw_text=normalized,
            sections=sections,
            figures=cls._dedupe_items(figures),
            tables=cls._dedupe_items(tables),
            equations=cls._dedupe_items(equations),
        )

    @classmethod
    def from_text(cls, text: str, *, source: str = "PDF 文本") -> PaperDocumentContext:
        normalized = _compact_block(text)
        sections = cls._split_sections(normalized, source=source)
        equations = cls._extract_equation_items(normalized, source=source)
        return cls(
            source=source,
            raw_text=normalized,
            sections=sections,
            equations=cls._dedupe_items(equations),
        )

    @staticmethod
    def _dedupe_items(items: list[StructuredEvidence]) -> list[StructuredEvidence]:
        deduped: list[StructuredEvidence] = []
        seen: set[str] = set()
        for item in items:
            key = f"{item.kind}:{_text_key(item.caption)}:{_text_key(item.body[:240])}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    @staticmethod
    def _split_sections(text: str, *, source: str) -> list[DocumentSection]:
        if not text:
            return []
        heading_matches = list(_HEADING_RE.finditer(text))
        numeric_matches = list(_FALLBACK_HEADING_RE.finditer(text)) if not heading_matches else []
        matches = heading_matches or numeric_matches
        sections: list[DocumentSection] = []
        if not matches:
            return [DocumentSection(title="全文", body=text, level=1, order=0, source=source)]
        first_start = matches[0].start()
        order = 0
        if first_start > 0:
            preface = _compact_block(text[:first_start])
            if preface:
                sections.append(
                    DocumentSection(
                        title="摘要 / 前置信息",
                        body=preface,
                        level=1,
                        order=order,
                        source=source,
                    )
                )
                order += 1
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            body = _compact_block(text[start:end])
            if not body:
                continue
            if heading_matches:
                level = len(match.group(1) or "#")
                title = _clean_text(match.group(2) or match.group(0))
            else:
                numeric_prefix = _clean_text(match.group(1) or "")
                level = max(1, numeric_prefix.count(".") + 1) if numeric_prefix else 1
                title = _clean_text(match.group(0))
            sections.append(
                DocumentSection(
                    title=title or f"章节 {order + 1}",
                    body=body,
                    level=level,
                    order=order,
                    source=source,
                )
            )
            order += 1
        return sections

    @staticmethod
    def _extract_caption_items(text: str, *, source: str) -> tuple[list[StructuredEvidence], list[StructuredEvidence]]:
        lines = text.splitlines()
        figures: list[StructuredEvidence] = []
        tables: list[StructuredEvidence] = []
        for index, raw_line in enumerate(lines):
            line = _compact_block(raw_line)
            if not line:
                continue
            match = _CAPTION_RE.match(line)
            if not match:
                continue
            label = str(match.group("label") or "").strip()
            lowered = label.lower()
            kind = "table" if lowered.startswith(("table", "tab.")) else "figure"
            body = _extract_candidate_body(lines, index)
            item = StructuredEvidence(
                kind=kind,
                caption=line,
                body=body,
                order=index,
                source=source,
            )
            if kind == "table":
                tables.append(item)
            else:
                figures.append(item)
        return figures, tables

    @staticmethod
    def _extract_table_blocks(text: str, *, source: str) -> list[StructuredEvidence]:
        tables: list[StructuredEvidence] = []
        lines = text.splitlines()
        for match in _HTML_TABLE_RE.finditer(text):
            raw = match.group(0)
            start_line = text[: match.start()].count("\n")
            caption = _nearest_caption(lines, start_line, kind="table")
            body = _clean_html_table(raw)
            if not body:
                continue
            tables.append(
                StructuredEvidence(
                    kind="table",
                    caption=caption or f"Table body excerpt {len(tables) + 1}",
                    body=body,
                    order=start_line,
                    source=source,
                )
            )
        for match in _MARKDOWN_TABLE_RE.finditer(text):
            raw = match.group(0)
            start_line = text[: match.start()].count("\n")
            caption = _nearest_caption(lines, start_line, kind="table")
            body = _clean_markdown_table(raw)
            if not body:
                continue
            tables.append(
                StructuredEvidence(
                    kind="table",
                    caption=caption or f"Table body excerpt {len(tables) + 1}",
                    body=body,
                    order=start_line,
                    source=source,
                )
            )
        return tables

    @staticmethod
    def _extract_equation_items(text: str, *, source: str) -> list[StructuredEvidence]:
        equations: list[StructuredEvidence] = []
        matches = []
        matches.extend(_DISPLAY_EQUATION_RE.finditer(text))
        matches.extend(_LATEX_EQUATION_RE.finditer(text))
        matches.extend(_BRACKET_EQUATION_RE.finditer(text))
        matches = sorted(matches, key=lambda item: item.start())
        for index, match in enumerate(matches[:10]):
            body = _compact_block(match.group(0))
            if not body:
                continue
            equations.append(
                StructuredEvidence(
                    kind="equation",
                    caption=f"Equation excerpt {index + 1}",
                    body=_truncate_block(body, 600),
                    order=match.start(),
                    source=source,
                )
            )
        return equations

    def build_outline(self, *, max_items: int = 18) -> list[str]:
        titles = [section.title.strip() for section in self.sections if section.title.strip()]
        if max_items <= 0:
            return titles
        if len(titles) <= max_items:
            return titles
        indices = self._evenly_spaced_indices(len(titles), max_items)
        return [titles[index] for index in indices]

    def build_targeted_bundle(
        self,
        *,
        name: str,
        targets: list[str],
        max_sections: int = 6,
        max_figures: int = 4,
        max_tables: int = 4,
        max_equations: int = 3,
        include_outline: bool = True,
        notes: list[str] | None = None,
    ) -> AnalysisEvidenceBundle:
        normalized_targets = [str(target or "").strip().lower() for target in targets if str(target or "").strip()]
        selected_sections = self._select_sections(normalized_targets, limit=max_sections)
        selected_figures = self._select_items(self.figures, normalized_targets, limit=max_figures)
        selected_tables = self._select_items(self.tables, normalized_targets, limit=max_tables)
        selected_equations = self._select_items(self.equations, normalized_targets, limit=max_equations)
        outline_max_items = 0 if any(limit <= 0 for limit in (max_sections, max_figures, max_tables, max_equations)) else 18
        return AnalysisEvidenceBundle(
            name=name,
            source=self.source,
            outline=self.build_outline(max_items=outline_max_items) if include_outline else [],
            notes=[str(note).strip() for note in (notes or []) if str(note).strip()],
            sections=selected_sections,
            figures=selected_figures,
            tables=selected_tables,
            equations=selected_equations,
        )

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
        bundle = self.build_targeted_bundle(
            name=name,
            targets=targets,
            max_sections=max_sections,
            max_figures=max_figures,
            max_tables=max_tables,
            max_equations=max_equations,
            include_outline=include_outline,
            notes=notes,
        )
        return bundle.render(max_chars=max_chars)

    def build_round_context(self, round_name: str, *, max_chars: int) -> str:
        config = _ROUND_CONFIG.get(str(round_name or "").strip().lower(), _ROUND_CONFIG["overview"])
        return self.build_targeted_context(
            name=f"{round_name or 'analysis'} 证据包",
            targets=list(config["targets"]),
            max_chars=max_chars,
            max_sections=int(config["max_sections"]),
            max_figures=int(config["max_figures"]),
            max_tables=int(config["max_tables"]),
            max_equations=int(config["max_equations"]),
            include_outline=bool(config["include_outline"]),
        )

    def _select_sections(self, targets: list[str], *, limit: int) -> list[DocumentSection]:
        if not self.sections:
            return []
        if limit <= 0:
            return list(self.sections)
        scored: list[tuple[int, int, DocumentSection]] = []
        for section in self.sections:
            score = self._section_score(section, targets)
            if score > 0:
                scored.append((score, -section.order, section))
        chosen: list[DocumentSection] = []
        seen_orders: set[int] = set()
        for _score, _neg_order, section in sorted(scored, key=lambda item: (-item[0], item[1])):
            if section.order in seen_orders:
                continue
            chosen.append(section)
            seen_orders.add(section.order)
            if len(chosen) >= limit:
                break
        if len(chosen) < limit:
            for index in self._evenly_spaced_indices(len(self.sections), limit):
                section = self.sections[index]
                if section.order in seen_orders:
                    continue
                chosen.append(section)
                seen_orders.add(section.order)
                if len(chosen) >= limit:
                    break
        return sorted(chosen, key=lambda item: item.order)

    def _select_items(
        self,
        items: list[StructuredEvidence],
        targets: list[str],
        *,
        limit: int,
    ) -> list[StructuredEvidence]:
        if not items:
            return []
        if limit <= 0:
            return list(items)
        scored: list[tuple[int, int, StructuredEvidence]] = []
        for item in items:
            score = self._item_score(item, targets)
            if score > 0:
                scored.append((score, -item.order, item))
        chosen: list[StructuredEvidence] = []
        seen_keys: set[str] = set()
        for _score, _neg_order, item in sorted(scored, key=lambda entry: (-entry[0], entry[1])):
            key = f"{item.kind}:{item.order}:{_text_key(item.caption)}"
            if key in seen_keys:
                continue
            chosen.append(item)
            seen_keys.add(key)
            if len(chosen) >= limit:
                break
        if len(chosen) < limit:
            for item in items[:limit]:
                key = f"{item.kind}:{item.order}:{_text_key(item.caption)}"
                if key in seen_keys:
                    continue
                chosen.append(item)
                seen_keys.add(key)
                if len(chosen) >= limit:
                    break
        return chosen

    def _section_score(self, section: DocumentSection, targets: list[str]) -> int:
        title_key = section.title_key
        body_key = _text_key(section.body[:2400])
        score = 0
        for target in targets:
            aliases = _TARGET_KEYWORDS.get(target, (target,))
            for alias in aliases:
                alias_key = _text_key(alias)
                if not alias_key:
                    continue
                if alias_key in title_key:
                    score += 8
                elif alias_key in body_key:
                    score += 2
        if section.order == 0 and any(target in {"overview", "method"} for target in targets):
            score += 3
        if section.order >= max(0, len(self.sections) - 2) and any(target in {"discussion", "conclusion", "limitations"} for target in targets):
            score += 3
        return score

    def _item_score(self, item: StructuredEvidence, targets: list[str]) -> int:
        text = item.search_text
        score = 0
        if item.kind == "table" and any(target in {"experiment", "results", "ablation", "table"} for target in targets):
            score += 3
        if item.kind == "figure" and any(target in {"method", "experiment", "figure"} for target in targets):
            score += 2
        if item.kind == "equation" and any(target in {"method", "equation"} for target in targets):
            score += 4
        for target in targets:
            aliases = _TARGET_KEYWORDS.get(target, (target,))
            for alias in aliases:
                alias_key = _text_key(alias)
                if alias_key and alias_key in text:
                    score += 5
        return score

    @staticmethod
    def _evenly_spaced_indices(total: int, count: int) -> list[int]:
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
