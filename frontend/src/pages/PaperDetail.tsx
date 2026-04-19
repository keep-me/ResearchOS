/**
 * Paper Detail - paper overview, analysis reports, and PDF actions.
 * @author Bamzc
 */
import { useEffect, useState, useCallback, useRef, useMemo, lazy, Suspense } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Card, CardHeader, Button, Badge, Empty, Modal, Spinner } from "@/components/ui";
import { Tabs } from "@/components/ui/Tabs";
import { PaperDetailSkeleton } from "@/components/Skeleton";
import ConfirmDialog from "@/components/ConfirmDialog";
import PaperCitationExpansion from "@/components/graph/PaperCitationExpansion";

// Lazy-load heavier readers/renderers to keep the detail page responsive.
const Markdown = lazy(() => import("@/components/Markdown"));
const PdfReader = lazy(() => import("@/components/PdfReader"));
import { useToast } from "@/contexts/ToastContext";
import { formatDateTime } from "@/lib/utils";
import {
  paperApi,
  pipelineApi,
  resolveApiAssetUrl,
  tasksApi,
  topicApi,
  type FigureAnalysisItem,
  type PaperContentSource,
  type TaskStatus,
} from "@/services/api";
import type {
  AnalysisDetailLevel,
  PaperEvidenceMode,
  Paper,
  PaperTopicAssignment,
  SkimReport,
  DeepDiveReport,
  PaperAnalysisBundle,
  ReasoningChainResult,
  Topic,
} from "@/types";
import {
  ArrowLeft, ExternalLink, Eye, BookOpen, Cpu, Star, AlertTriangle,
  CheckCircle2, Lightbulb, FlaskConical, Microscope, Shield, Sparkles,
  Link2, Tag, Folder, Heart, Image as ImageIcon, BarChart3, Table2,
  FileCode2, Brain, ChevronDown, ChevronRight, TrendingUp, Target,
  ThumbsUp, ThumbsDown, Zap, FileSearch, X, Loader2, Check, Download, Trash2, Plus, Upload, PencilLine, RefreshCw,
} from "lucide-react";

type EmbeddingStatusMeta = {
  source?: string;
  provider?: string | null;
  model?: string | null;
  base_url?: string | null;
  fallback_reason?: string | null;
  updated_at?: string | null;
};

type MineruOcrMeta = {
  status?: string | null;
  updated_at?: string | null;
  markdown_chars?: number | null;
  has_structured_output?: boolean;
  error?: string | null;
};

type AnalysisArtifactMeta = {
  content_source?: string | null;
  content_source_detail?: string | null;
  detail_level?: string | null;
  reasoning_level?: string | null;
  evidence_mode?: string | null;
  updated_at?: string | null;
};

type SavedSkimReport = {
  summary_md: string;
  skim_score: number | null;
  key_insights: Record<string, unknown>;
};

type SavedDeepReport = {
  deep_dive_md: string;
  key_insights: Record<string, unknown>;
};

function getEmbeddingStatusMeta(metadata?: Record<string, unknown>): EmbeddingStatusMeta | null {
  const raw = metadata?.embedding_status;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  return raw as EmbeddingStatusMeta;
}

function getEmbeddingFallbackLabel(reason?: string | null) {
  switch (reason) {
    case "missing_api_key":
      return "缺少 embedding API Key";
    case "unsupported_provider":
      return "当前嵌入提供方不受支持";
    case "remote_embedding_failed":
      return "远端 embedding 调用失败";
    default:
      return "embedding 不可用";
  }
}

function isRealArxivId(value?: string | null) {
  if (!value) return false;
  return /^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?\/\d{7})(?:v\d+)?$/i.test(value.trim());
}

function getPaperSourceUrl(paper: Paper) {
  const metadata = paper.metadata || {};
  const sourceUrl = metadata.source_url;
  if (typeof sourceUrl === "string" && /^https?:\/\//i.test(sourceUrl)) {
    return sourceUrl;
  }
  const scholarId = metadata.scholar_id;
  if (typeof scholarId === "string" && /^https?:\/\//i.test(scholarId)) {
    return scholarId;
  }
  if (
    typeof scholarId === "string"
    && String(metadata.import_source || "").toLowerCase() === "openalex"
    && /^w\d+$/i.test(scholarId.trim())
  ) {
    return `https://openalex.org/${scholarId.trim()}`;
  }
  if (typeof scholarId === "string" && scholarId.trim()) {
    return `https://www.semanticscholar.org/paper/${scholarId.trim()}`;
  }
  const doi = metadata.doi;
  if (typeof doi === "string" && /^https?:\/\//i.test(doi.trim())) {
    return doi.trim();
  }
  return null;
}

function parseKeywordInput(value: string): string[] {
  const seen = new Set<string>();
  const items: string[] = [];
  for (const raw of value.split(/[\n,，;；]+/)) {
    const normalized = raw.trim().toLowerCase();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    items.push(normalized);
  }
  return items;
}

const PAPER_ANALYSIS_DETAIL_LEVEL_KEY = "researchos.paper.analysisDetailLevel";
const PAPER_ANALYSIS_EVIDENCE_MODE_KEY = "researchos.paper.evidenceMode";
const DEEP_DETAIL_LEVEL_KEY = "researchos.paper.deepDetailLevel";
const REASONING_DETAIL_LEVEL_KEY = "researchos.paper.reasoningDetailLevel";

function getStoredDetailLevel(storageKey: string): AnalysisDetailLevel | null {
  const stored = (localStorage.getItem(storageKey) || "").trim().toLowerCase();
  if (stored === "low" || stored === "medium" || stored === "high") {
    return stored;
  }
  return null;
}

function getPreferredPaperAnalysisDetailLevel(): AnalysisDetailLevel {
  return (
    getStoredDetailLevel(PAPER_ANALYSIS_DETAIL_LEVEL_KEY)
    || getStoredDetailLevel(DEEP_DETAIL_LEVEL_KEY)
    || getStoredDetailLevel(REASONING_DETAIL_LEVEL_KEY)
    || "medium"
  );
}

function resolveFigurePreviewUrl(
  paperId: string,
  figure: Pick<FigureAnalysisItem, "id" | "image_url">,
) {
  if (figure.id) {
    return paperApi.figureImageUrl(paperId, figure.id);
  }
  const rawImageUrl = typeof figure.image_url === "string" ? figure.image_url.trim() : "";
  return rawImageUrl ? resolveApiAssetUrl(rawImageUrl) : null;
}

function getStoredEvidenceMode(): PaperEvidenceMode | null {
  const stored = (localStorage.getItem(PAPER_ANALYSIS_EVIDENCE_MODE_KEY) || "").trim().toLowerCase();
  if (stored === "full" || stored === "rough") {
    return stored;
  }
  return null;
}

function getPaperSourceLabel(url: string | null) {
  if (!url) return "外部来源";
  const value = url.toLowerCase();
  if (value.includes("openalex.org")) return "OpenAlex";
  if (value.includes("semanticscholar.org")) return "Semantic Scholar";
  if (value.includes("doi.org")) return "DOI";
  return "外部来源";
}

function hasExternalPdfCandidate(paper: Paper) {
  const metadata = paper.metadata || {};
  for (const key of ["pdf_url", "oa_url", "open_access_pdf_url", "external_pdf_url"]) {
    const value = metadata[key];
    if (typeof value === "string" && value.trim()) {
      return true;
    }
  }
  return String(metadata.import_source || "").toLowerCase() === "openalex";
}

function getPaperPdfUrl(paper: Paper) {
  const metadata = paper.metadata || {};
  for (const key of ["pdf_url", "oa_url", "open_access_pdf_url", "external_pdf_url"]) {
    const value = metadata[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean);
}

function normalizeMarkdown(text: string | null | undefined): string {
  return String(text || "").replace(/\r\n/g, "\n").trim();
}

function RichTextBlock({
  content,
  className = "",
}: {
  content: string;
  className?: string;
}) {
  const normalized = normalizeMarkdown(content);
  if (!normalized) return null;
  return (
    <div className={className}>
      <Suspense fallback={<div className="h-8 animate-pulse rounded bg-surface" />}>
        <Markdown autoMath>{normalized}</Markdown>
      </Suspense>
    </div>
  );
}

function resolveSkimOneLiner(
  oneLiner: string | null | undefined,
  innovations: string[],
  summary: string | null | undefined = "",
): string {
  const primary = String(oneLiner || "").trim();
  if (primary) return primary;
  const summaryLines = String(summary || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const summaryOneLiner = summaryLines
    .map((line) => line.match(/^(?:[-*]\s*)?(?:one-liner|一句话)\s*[:：]\s*(.+)$/i)?.[1]?.trim() || "")
    .find(Boolean) || "";
  if (summaryOneLiner) return summaryOneLiner;
  const summaryFirstLine = summaryLines.find((line) => {
    if (/^(?:[-*]\s*)?(?:one-liner|一句话)\s*[:：]?$/i.test(line)) return false;
    if (/^(?:[-*]\s*)?(?:innovations?|创新点)\s*[:：]?$/i.test(line)) return false;
    return true;
  }) || "";
  if (summaryFirstLine) return summaryFirstLine;
  const innovationFallback = innovations.find((item) => String(item || "").trim()) || "";
  return String(innovationFallback || "").trim();
}

function asPaperAnalysisBundle(value: unknown): PaperAnalysisBundle | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as PaperAnalysisBundle;
}

function hasPaperAnalysis(bundle: PaperAnalysisBundle | null | undefined): boolean {
  return Boolean(
    normalizeMarkdown(bundle?.round_1?.markdown)
    || normalizeMarkdown(bundle?.round_2?.markdown)
    || normalizeMarkdown(bundle?.round_3?.markdown)
    || normalizeMarkdown(bundle?.final_notes?.markdown),
  );
}

function isIncompleteReasoningResult(reasoning: ReasoningChainResult | null | undefined): boolean {
  if (!reasoning || typeof reasoning !== "object") return false;
  const steps = Array.isArray(reasoning.reasoning_steps) ? reasoning.reasoning_steps : [];
  if (steps.length !== 1) return false;
  const firstStep = steps[0];
  if (!firstStep || typeof firstStep !== "object") return false;
  return String(firstStep.step || "").trim() === "分析未完成";
}

function getAnalysisDetailLevelLabel(level?: string | null): string {
  switch ((level || "").toLowerCase()) {
    case "low":
      return "低";
    case "high":
      return "高";
    default:
      return "中";
  }
}

function getReasoningLevelLabel(level?: string | null): string {
  switch ((level || "").toLowerCase()) {
    case "low":
      return "低";
    case "medium":
      return "中";
    case "high":
      return "高";
    case "xhigh":
      return "超高";
    default:
      return "默认";
  }
}

function getProcessingSourceLabel(source: PaperContentSource): string {
  return source === "markdown" ? "Markdown" : "PDF";
}

function compactPlainText(value: string | null | undefined): string {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function stripMarkdownTable(value: string | null | undefined): string {
  return String(value || "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("|"))
    .join(" ");
}

function buildFigureCandidateSummary(figure: FigureAnalysisItem, candidateSourceLabel: string): string {
  const caption = compactPlainText(figure.caption);
  if (caption) {
    return caption.length > 180 ? `${caption.slice(0, 180).trim()}...` : caption;
  }

  const text = compactPlainText(stripMarkdownTable(figure.ocr_markdown));
  if (text) {
    return text.length > 180 ? `${text.slice(0, 180).trim()}...` : text;
  }

  const source = candidateSourceLabel || "当前提取链路";
  if (figure.image_type === "table") {
    return `${source}已识别到表格候选，可勾选后再做图表分析。`;
  }
  return `${source}已识别到图表候选，可勾选后再做图表分析。`;
}

function normalizeSavedPaperContentSource(value: unknown): PaperContentSource | null {
  const raw = String(value || "").trim().toLowerCase();
  if (["markdown", "md", "ocr", "mineru"].includes(raw)) return "markdown";
  if (["pdf", "auto", "direct", "arxiv_source"].includes(raw)) return "pdf";
  return null;
}

function getSavedPaperContentSourceLabel(value: unknown): string {
  return normalizeSavedPaperContentSource(value) === "markdown" ? "Markdown" : "PDF";
}

function getEvidenceModeLabel(mode: string | null | undefined): string {
  return String(mode || "").trim().toLowerCase() === "full" ? "完整" : "粗略";
}

function getAnalysisArtifactMeta(
  metadata: Record<string, unknown> | undefined,
  key: string,
): AnalysisArtifactMeta | null {
  const raw = metadata?.[key];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  return raw as AnalysisArtifactMeta;
}

function getMineruOcrMeta(metadata?: Record<string, unknown>): MineruOcrMeta | null {
  const raw = metadata?.mineru_ocr;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  return raw as MineruOcrMeta;
}

function sleep(ms: number) {
  return new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });
}

const PLAIN_SECTION_HEADINGS = new Set([
  "deep dive report",
  "精读报告",
  "method",
  "methods",
  "方法",
  "experiments",
  "experiment",
  "实验",
  "ablation",
  "ablation study",
  "消融",
  "reviewer risks",
  "reviewer risk",
  "审稿风险",
]);

function parseMarkdownSections(markdown: string): Record<string, string> {
  const sections: Record<string, string> = {};
  let currentHeading = "";
  let buffer: string[] = [];

  const flush = () => {
    if (!currentHeading) return;
    sections[currentHeading] = buffer.join("\n").trim();
    buffer = [];
  };

  for (const line of normalizeMarkdown(markdown).split("\n")) {
    const headingMatch = line.match(/^#{1,6}\s*(.+?)\s*$/);
    if (headingMatch) {
      flush();
      currentHeading = headingMatch[1].trim().toLowerCase();
      continue;
    }
    const plainHeading = line.trim().replace(/[:：]\s*$/, "").toLowerCase();
    if (PLAIN_SECTION_HEADINGS.has(plainHeading)) {
      flush();
      currentHeading = plainHeading;
      continue;
    }
    buffer.push(line);
  }

  flush();
  return sections;
}

function pickSection(
  sections: Record<string, string>,
  ...names: string[]
): string {
  for (const name of names) {
    const value = sections[name.trim().toLowerCase()];
    if (value) return value.trim();
  }
  return "";
}

function parseBullets(text: string): string[] {
  const items = text
    .split("\n")
    .map((line) => {
      const bullet = line.match(/^\s*[-*+]\s+(.+?)\s*$/);
      if (bullet) return bullet[1].trim();
      const numbered = line.match(/^\s*\d+[.)]\s+(.+?)\s*$/);
      if (numbered) return numbered[1].trim();
      return "";
    })
    .filter(Boolean);

  if (items.length > 0) return items;

  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function stripMarkdownForPreview(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]+\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/^#{1,6}\s*/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+[.)]\s+/gm, "")
    .replace(/\$\$?([^$]+)\$\$?/g, "$1")
    .replace(/[*_~>#|]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function truncatePreview(text: string, maxChars = 220): string {
  const normalized = normalizeMarkdown(text);
  if (normalized.length <= maxChars) return normalized;
  return `${normalized.slice(0, maxChars).trim()}...`;
}

function formatAnalysisHeadingLabel(rawHeading: string): string {
  const label = rawHeading.trim().toLowerCase();
  if (!label) return "内容摘要";
  if (label.includes("一句话") || label.includes("one sentence")) return "一句话总结";
  if (label.includes("核心贡献") || label.includes("contribution")) return "核心贡献";
  if (label.includes("方法") || label.includes("mechanism") || label.includes("method")) return "方法机制";
  if (label.includes("实验") || label.includes("result") || label.includes("evaluation")) return "实验结论";
  if (label.includes("复现") || label.includes("implementation")) return "复现要点";
  if (label.includes("风险") || label.includes("局限") || label.includes("open problem")) return "风险与开放问题";
  if (label.includes("鸟瞰") || label.includes("overview")) return "鸟瞰扫描";
  if (label.includes("内容理解") || label.includes("comprehension")) return "内容理解";
  if (label.includes("深度分析") || label.includes("deep analysis")) return "深度分析";
  return rawHeading
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (token) => token.toUpperCase());
}

type AnalysisRoundDigest = {
  headline: string;
  bullets: string[];
  sectionCards: Array<{ label: string; preview: string; content: string }>;
};

type AnalysisRoundSection = {
  id: string;
  rawTitle: string;
  label: string;
  markdown: string;
};

type AnalysisRoundLayout = {
  headline: string;
  summaryMarkdown: string;
  sections: AnalysisRoundSection[];
};

function buildAnalysisRoundDigest(markdown: string): AnalysisRoundDigest {
  const raw = normalizeMarkdown(markdown);
  const sections = parseMarkdownSections(raw);
  const sectionCards = Object.entries(sections)
    .filter(([, content]) => normalizeMarkdown(content))
    .slice(0, 4)
    .map(([heading, content]) => ({
      label: formatAnalysisHeadingLabel(heading),
      preview: truncatePreview(content, 360),
      content: normalizeMarkdown(content),
    }));
  const bulletPool = Array.from(
    new Set(
      parseBullets(raw)
        .map((item) => stripMarkdownForPreview(item))
        .filter(Boolean),
    ),
  ).slice(0, 4);
  const paragraphs = raw
    .split(/\n\s*\n/)
    .map((block) => stripMarkdownForPreview(block))
    .filter(Boolean);
  const headline = truncatePreview(
    paragraphs.find((item) => item.length > 20)
      || bulletPool[0]
      || sectionCards[0]?.content
      || "",
    240,
  );
  return {
    headline,
    bullets: bulletPool,
    sectionCards,
  };
}

function sanitizeAnalysisSectionTitle(rawTitle: string): string {
  return rawTitle
    .replace(/^\d+(?:\.\d+)*\s*[.、)\-：:]?\s*/, "")
    .replace(/^第\s*\d+\s*部分\s*[：:]?\s*/, "")
    .trim();
}

function slugifyAnalysisSection(rawTitle: string, index: number): string {
  const cleaned = sanitizeAnalysisSectionTitle(rawTitle)
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return cleaned || `section-${index + 1}`;
}

function splitAnalysisRoundSections(markdown: string): AnalysisRoundSection[] {
  const raw = normalizeMarkdown(markdown);
  if (!raw) return [];

  const lines = raw.split("\n");
  const sections: AnalysisRoundSection[] = [];
  let currentTitle = "";
  let buffer: string[] = [];
  let sectionIndex = 0;

  const flush = () => {
    const body = normalizeMarkdown(buffer.join("\n"));
    if (!currentTitle || !body) {
      buffer = [];
      return;
    }
    const label = formatAnalysisHeadingLabel(sanitizeAnalysisSectionTitle(currentTitle));
    sections.push({
      id: slugifyAnalysisSection(currentTitle, sectionIndex),
      rawTitle: currentTitle.trim(),
      label,
      markdown: body,
    });
    sectionIndex += 1;
    buffer = [];
  };

  for (const line of lines) {
    const heading = line.match(/^##\s+(.+?)\s*$/);
    if (heading) {
      flush();
      currentTitle = heading[1].trim();
      continue;
    }
    buffer.push(line);
  }

  flush();
  return sections;
}

function buildAnalysisRoundLayout(markdown: string): AnalysisRoundLayout {
  const raw = normalizeMarkdown(markdown);
  const digest = buildAnalysisRoundDigest(raw);
  const sections = splitAnalysisRoundSections(raw);
  const summarySection = sections.find((section) => {
    const key = section.rawTitle.toLowerCase();
    return (
      key.includes("一句话")
      || key.includes("概括")
      || key.includes("概述")
      || key.includes("定位")
      || key.includes("summary")
      || key.includes("overview")
    );
  });

  const summaryMarkdown = summarySection?.markdown
    || (digest.headline ? digest.headline : "");

  const filteredSections = summarySection
    ? sections.filter((section) => section.id !== summarySection.id)
    : sections;

  return {
    headline: digest.headline,
    summaryMarkdown,
    sections: filteredSections,
  };
}

function buildFigureReferenceKey(kind: "figure" | "table", index: string): string {
  return `${kind}:${String(index || "").trim().toLowerCase()}`;
}

function parseFigureReferenceToken(raw: string): { kind: "figure" | "table"; index: string } | null {
  const match = String(raw || "").match(
    /\b(fig(?:ure)?\.?|table|tab\.?|图|表)\s*([a-z]?\d+[a-z]?|[ivxlcdm]+)/i,
  );
  if (!match) return null;
  const kind = /^(?:tab|table|表)/i.test(match[1]) ? "table" : "figure";
  const index = String(match[2] || "").trim().toUpperCase();
  if (!index) return null;
  return { kind, index };
}

function extractAnalysisFigureRefs(markdown: string): Array<{ kind: "figure" | "table"; index: string }> {
  const refs: Array<{ kind: "figure" | "table"; index: string }> = [];
  const seen = new Set<string>();
  const pattern = /\b(fig(?:ure)?\.?|table|tab\.?|图|表)\s*([a-z]?\d+[a-z]?|[ivxlcdm]+)/gi;
  for (const match of String(markdown || "").matchAll(pattern)) {
    const parsed = parseFigureReferenceToken(match[0]);
    if (!parsed) continue;
    const key = buildFigureReferenceKey(parsed.kind, parsed.index);
    if (seen.has(key)) continue;
    seen.add(key);
    refs.push(parsed);
  }
  return refs;
}

function buildFigureReferenceIndex(figures: FigureAnalysisItem[]): Map<string, FigureAnalysisItem> {
  const index = new Map<string, FigureAnalysisItem>();
  for (const figure of figures) {
    const candidates = [figure.figure_label, figure.caption];
    for (const candidate of candidates) {
      const parsed = parseFigureReferenceToken(String(candidate || ""));
      if (!parsed) continue;
      const key = buildFigureReferenceKey(parsed.kind, parsed.index);
      if (!index.has(key)) {
        index.set(key, figure);
      }
    }
  }
  return index;
}

function resolveAnalysisSectionFigures(
  markdown: string,
  figures: FigureAnalysisItem[],
): FigureAnalysisItem[] {
  if (!figures.length) return [];
  const figureIndex = buildFigureReferenceIndex(figures);
  const resolved: FigureAnalysisItem[] = [];
  const seen = new Set<string>();
  for (const ref of extractAnalysisFigureRefs(markdown)) {
    const key = buildFigureReferenceKey(ref.kind, ref.index);
    const figure = figureIndex.get(key);
    const figureId = String(figure?.id || key);
    if (!figure || seen.has(figureId)) continue;
    seen.add(figureId);
    resolved.push(figure);
  }
  return resolved;
}

function getFigureReferenceTitle(figure: FigureAnalysisItem): string {
  const label = String(figure.figure_label || "").trim();
  if (label) return label;
  const parsed = parseFigureReferenceToken(figure.caption || "");
  if (parsed) {
    return `${parsed.kind === "table" ? "Table" : "Fig."} ${parsed.index}`;
  }
  return figure.image_type === "table" ? "Table" : "Figure";
}

function getFigureReferenceExcerpt(figure: FigureAnalysisItem): string {
  const preview = stripMarkdownForPreview(
    normalizeMarkdown(figure.analysis_markdown || figure.ocr_markdown || figure.description || ""),
  );
  return truncatePreview(preview, figure.image_type === "table" ? 260 : 180);
}

function getAnalysisSectionIcon(label: string): React.ReactNode {
  if (label.includes("贡献")) return <Sparkles className="h-4 w-4 text-amber-500" />;
  if (label.includes("问题") || label.includes("定位")) return <Target className="h-4 w-4 text-blue-500" />;
  if (label.includes("方法")) return <FlaskConical className="h-4 w-4 text-blue-500" />;
  if (label.includes("实验") || label.includes("结果")) return <Microscope className="h-4 w-4 text-green-500" />;
  if (label.includes("风险") || label.includes("局限")) return <Shield className="h-4 w-4 text-red-500" />;
  if (label.includes("速度") || label.includes("复杂度")) return <TrendingUp className="h-4 w-4 text-orange-500" />;
  if (label.includes("总结")) return <Lightbulb className="h-4 w-4 text-purple-500" />;
  return <BookOpen className="h-4 w-4 text-slate-500" />;
}

type FigureAnalysisSections = {
  chartType: string;
  coreContent: string;
  keyData: string;
  methodInterpretation: string;
  academicMeaning: string;
  raw: string;
};

function mapFigureSectionKey(rawLabel: string): keyof FigureAnalysisSections | "" {
  const label = rawLabel.trim().toLowerCase();
  if (!label) return "";
  if (
    label.includes("图表类型")
    || label.includes("类型")
    || label.includes("chart type")
    || label.includes("figure type")
  ) return "chartType";
  if (
    label.includes("核心内容")
    || label.includes("关键发现")
    || label.includes("表格内容")
    || label.includes("core content")
    || label.includes("key finding")
  ) return "coreContent";
  if (
    label.includes("关键数据")
    || label.includes("对比分析")
    || label.includes("最优结果")
    || label.includes("key data")
    || label.includes("comparison")
    || label.includes("best result")
  ) return "keyData";
  if (
    label.includes("方法解读")
    || label.includes("流程解读")
    || label.includes("模块作用")
    || label.includes("method interpretation")
    || label.includes("method insight")
  ) return "methodInterpretation";
  if (
    label.includes("学术意义")
    || label.includes("论文作用")
    || label.includes("重要性")
    || label.includes("academic significance")
    || label.includes("significance")
  ) return "academicMeaning";
  return "";
}

function parseFigureAnalysisSections(markdown: string): FigureAnalysisSections {
  const raw = normalizeMarkdown(markdown);
  const out: FigureAnalysisSections = {
    chartType: "",
    coreContent: "",
    keyData: "",
    methodInterpretation: "",
    academicMeaning: "",
    raw,
  };
  if (!raw) return out;

  const append = (key: keyof FigureAnalysisSections, content: string) => {
    const text = content.trim();
    if (!text) return;
    out[key] = out[key] ? `${out[key]}\n${text}` : text;
  };

  let activeKey: keyof FigureAnalysisSections | "" = "";
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    const bulletStripped = trimmed.replace(/^\s*(?:[-*+]|\d+[.)])\s*/, "").trim();
    const headingText = bulletStripped.replace(/^#{1,6}\s*/, "").replace(/[:：]\s*$/, "").trim();
    const headingKey = mapFigureSectionKey(headingText);
    if (headingKey) {
      activeKey = headingKey;
      continue;
    }

    const kv = bulletStripped.match(/^(?:\*\*|__)?([^:*：]+?)(?:\*\*|__)?\s*[:：]\s*(.*)$/);
    if (kv) {
      const key = mapFigureSectionKey(kv[1]);
      if (key) {
        activeKey = key;
        append(key, kv[2]);
        continue;
      }
    }

    if (activeKey) append(activeKey, bulletStripped);
  }

  if (!out.chartType && !out.coreContent && !out.keyData && !out.methodInterpretation && !out.academicMeaning) {
    out.coreContent = raw;
  }
  return out;
}

function parseSavedSkimReport(saved: SavedSkimReport | null): SkimReport | null {
  if (!saved) return null;

  const summary = normalizeMarkdown(saved.summary_md);
  const keyInsights = saved.key_insights || {};

  const oneLiner =
    (typeof keyInsights.one_liner === "string" ? keyInsights.one_liner.trim() : "") ||
    (summary.match(/^\s*[-*]?\s*(?:One-liner|\u4e00\u53e5\u8bdd)[:\uff1a]\s*(.+)\s*$/im)?.[1]?.trim() ?? "");

  let innovations = asStringArray((keyInsights as Record<string, unknown>).skim_innovations);
  if (!innovations.length) {
    const lines = summary.split("\n");
    const extracted: string[] = [];
    let inSection = false;
    for (const line of lines) {
      if (/^\s*[-*]?\s*(?:Innovations|\u521b\u65b0\u70b9)[:\uff1a]?\s*$/i.test(line)) {
        inSection = true;
        continue;
      }
      if (!inSection) continue;
      const match = line.match(/^\s*[-*+]\s+(.+?)\s*$/) || line.match(/^\s*\d+[.)]\s+(.+?)\s*$/);
      if (match) {
        extracted.push(match[1].trim());
        continue;
      }
      const trimmed = line.trim();
      if (!trimmed) {
        if (extracted.length > 0) break;
        continue;
      }
      if (/^\s*[-*]?\s*(?:Keywords?|关键词)[:：]?\s*$/i.test(line)) {
        break;
      }
      extracted.push(trimmed);
    }
    innovations = extracted;
  }

  const resolvedOneLiner = resolveSkimOneLiner(oneLiner, innovations, summary);
  if (!resolvedOneLiner && !innovations.length) {
    return null;
  }

  return {
    one_liner: resolvedOneLiner,
    innovations,
    keywords: asStringArray((keyInsights as Record<string, unknown>).keywords),
    title_zh: typeof keyInsights.title_zh === "string" ? keyInsights.title_zh : "",
    abstract_zh: typeof keyInsights.abstract_zh === "string" ? keyInsights.abstract_zh : "",
    relevance_score: typeof saved.skim_score === "number" ? saved.skim_score : 0,
  };
}

function parseSavedDeepReport(saved: SavedDeepReport | null): DeepDiveReport | null {
  if (!saved) return null;

  const keyInsights = saved.key_insights || {};
  const sections = parseMarkdownSections(saved.deep_dive_md);

  const methodSummary =
    (typeof keyInsights.method_summary === "string" ? keyInsights.method_summary.trim() : "") ||
    pickSection(sections, "method", "\u65b9\u6cd5");
  const experimentsSummary =
    (typeof keyInsights.experiments_summary === "string" ? keyInsights.experiments_summary.trim() : "") ||
    pickSection(sections, "experiments", "\u5b9e\u9a8c");
  const ablationSummary =
    (typeof keyInsights.ablation_summary === "string" ? keyInsights.ablation_summary.trim() : "") ||
    pickSection(sections, "ablation", "\u6d88\u878d");

  let reviewerRisks = asStringArray((keyInsights as Record<string, unknown>).reviewer_risks);
  if (!reviewerRisks.length) {
    reviewerRisks = parseBullets(
      pickSection(sections, "reviewer risks", "reviewer risk", "\u5ba1\u7a3f\u98ce\u9669"),
    );
  }

  if (!methodSummary && !experimentsSummary && !ablationSummary && !reviewerRisks.length) {
    return null;
  }

  return {
    method_summary: methodSummary,
    experiments_summary: experimentsSummary,
    ablation_summary: ablationSummary,
    reviewer_risks: reviewerRisks,
  };
}

/* ================================================================
 * PipelineProgress - pipeline loading states
 * ================================================================ */

const SKIM_STAGES = ["提取论文信息...", "总结核心问题...", "提炼创新点...", "整理结果..."];
const DEEP_STAGES = ["解析论文结构...", "阅读方法细节...", "整理实验结论...", "生成报告..."];
const FIGURE_STAGES = ["准备 PDF 文件...", "提取图表与表格...", "保存提取结果..."];
const OCR_STAGES = ["准备 PDF 文件...", "检查 / 下载 MinerU 模型...", "运行 MinerU 本地 OCR...", "读取 Markdown 结果..."];
const PDF_STAGES = ["检查本地 PDF...", "解析论文来源...", "下载并准备 PDF...", "打开阅读器..."];

function PipelineProgress({
  type,
  onCancel,
  messageOverride,
  progressOverride,
}: {
  type: "skim" | "deep" | "figure" | "reasoning" | "embed" | "ocr" | "pdf";
  onCancel?: () => void;
  messageOverride?: string;
  progressOverride?: number | null;
}) {
  const [progress, setProgress] = useState(0);
  const [stageIdx, setStageIdx] = useState(0);

  const stages =
    type === "skim" ? SKIM_STAGES :
    type === "deep" ? DEEP_STAGES :
    type === "figure" ? FIGURE_STAGES :
    type === "ocr" ? OCR_STAGES :
    type === "pdf" ? PDF_STAGES :
    type === "reasoning" ? ["提取关键证据...", "分析方法链路...", "评估实验设计...", "生成推理报告..."] :
    ["生成论文向量..."];

  const estimate =
    type === "skim" ? "10-20 秒" :
    type === "deep" ? "30-60 秒" :
    type === "figure" ? "1-5 分钟（取决于页数）" :
    type === "ocr" ? "1-5 分钟（取决于页数）" :
    type === "pdf" ? "10-60 秒" :
    type === "reasoning" ? "20-40 秒" : "5-10 秒";

  useEffect(() => {
    if (typeof progressOverride === "number") return;
    const progressTimer = setInterval(() => {
      setProgress((p) => (p < 90 ? p + Math.random() * 3 + 0.5 : p));
    }, 500);
    const stageTimer = setInterval(() => {
      setStageIdx((i) => (i < stages.length - 1 ? i + 1 : i));
    }, type === "embed" ? 3000 : 8000);
    return () => { clearInterval(progressTimer); clearInterval(stageTimer); };
  }, [stages.length, type, progressOverride]);

  useEffect(() => {
    if (typeof progressOverride !== "number") return;
    const next = Math.max(0, Math.min(100, progressOverride));
    setProgress(next);
  }, [progressOverride]);

  const stageText = (messageOverride || "").trim() || stages[stageIdx];

  return (
    <div className="animate-fade-in rounded-2xl border border-primary/20 bg-primary/5 p-5 dark:bg-primary/10">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="relative flex h-10 w-10 items-center justify-center">
            <svg className="h-10 w-10 -rotate-90" viewBox="0 0 36 36">
              <circle cx="18" cy="18" r="15.5" fill="none" stroke="currentColor" strokeWidth="2" className="text-border" />
              <circle
                cx="18" cy="18" r="15.5" fill="none" stroke="currentColor" strokeWidth="2.5"
                className="text-primary transition-all duration-500"
                strokeDasharray={`${progress} ${100 - progress}`}
                strokeLinecap="round"
              />
            </svg>
            <span className="absolute text-[10px] font-bold text-primary">{Math.round(progress)}%</span>
          </div>
          <div>
            <p className="text-sm font-medium text-ink">{stageText}</p>
            <p className="text-xs text-ink-tertiary">预计 {estimate}</p>
          </div>
        </div>
        {onCancel && (
          <button
            onClick={onCancel}
            className="flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs text-ink-tertiary transition-colors hover:bg-hover hover:text-ink"
          >
            <X className="h-3.5 w-3.5" /> 取消
          </button>
        )}
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-border">
        <div
          className="h-full rounded-full bg-gradient-to-r from-primary to-blue-400 transition-all duration-500"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}

/* ================================================================
 * Tab label
 * ================================================================ */
function TabLabel({ label, status }: { label: string; status: "idle" | "loading" | "done" }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      {status === "loading" && <Loader2 className="h-3 w-3 animate-spin text-primary" />}
      {status === "done" && <Check className="h-3 w-3 text-success" />}
      {label}
    </span>
  );
}

/* ================================================================
 * Paper detail page
 * ================================================================ */

export default function PaperDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { toast } = useToast();

  const [paper, setPaper] = useState<Paper | null>(null);
  const [loading, setLoading] = useState(true);

  const [skimReport, setSkimReport] = useState<SkimReport | null>(null);
  const [deepReport, setDeepReport] = useState<DeepDiveReport | null>(null);
  const [savedSkim, setSavedSkim] = useState<SavedSkimReport | null>(null);
  const [savedDeep, setSavedDeep] = useState<SavedDeepReport | null>(null);
  const [similarIds, setSimilarIds] = useState<string[]>([]);
  const [similarItems, setSimilarItems] = useState<{ id: string; title: string; arxiv_id?: string; read_status?: string }[]>([]);

  const [skimLoading, setSkimLoading] = useState(false);
  const [skimTaskId, setSkimTaskId] = useState<string | null>(null);
  const [skimTaskMessage, setSkimTaskMessage] = useState("");
  const [skimTaskProgress, setSkimTaskProgress] = useState<number | null>(null);
  const [deepLoading, setDeepLoading] = useState(false);
  const [deepTaskId, setDeepTaskId] = useState<string | null>(null);
  const [deepTaskMessage, setDeepTaskMessage] = useState("");
  const [deepTaskProgress, setDeepTaskProgress] = useState<number | null>(null);
  const [embedLoading, setEmbedLoading] = useState(false);
  const [embedTaskId, setEmbedTaskId] = useState<string | null>(null);
  const [embedTaskMessage, setEmbedTaskMessage] = useState("");
  const [embedTaskProgress, setEmbedTaskProgress] = useState<number | null>(null);
  const [pdfPreparing, setPdfPreparing] = useState(false);
  const [pdfTaskId, setPdfTaskId] = useState<string | null>(null);
  const [pdfTaskMessage, setPdfTaskMessage] = useState("");
  const [pdfTaskProgress, setPdfTaskProgress] = useState<number | null>(null);
  const [embedDone, setEmbedDone] = useState<boolean | null>(null);
  const [similarLoading, setSimilarLoading] = useState(false);
  const similarFetchKeyRef = useRef("");

  const [figures, setFigures] = useState<FigureAnalysisItem[]>([]);
  const [figuresAnalyzing, setFiguresAnalyzing] = useState(false);
  const [processingSource, setProcessingSource] = useState<PaperContentSource>("pdf");
  const [selectedFigureIds, setSelectedFigureIds] = useState<Set<string>>(new Set());
  const [figureTaskId, setFigureTaskId] = useState<string | null>(null);
  const [figureTaskMessage, setFigureTaskMessage] = useState("");
  const [figureTaskProgress, setFigureTaskProgress] = useState<number | null>(null);
  const [ocrProcessing, setOcrProcessing] = useState(false);
  const [ocrTaskId, setOcrTaskId] = useState<string | null>(null);
  const [ocrTaskMessage, setOcrTaskMessage] = useState("");
  const [ocrTaskProgress, setOcrTaskProgress] = useState<number | null>(null);

  const [reasoning, setReasoning] = useState<ReasoningChainResult | null>(null);
  const [reasoningLoading, setReasoningLoading] = useState(false);
  const [reasoningTaskId, setReasoningTaskId] = useState<string | null>(null);
  const [reasoningTaskMessage, setReasoningTaskMessage] = useState("");
  const [reasoningTaskProgress, setReasoningTaskProgress] = useState<number | null>(null);
  const [analysisRounds, setAnalysisRounds] = useState<PaperAnalysisBundle | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisTaskId, setAnalysisTaskId] = useState<string | null>(null);
  const [analysisTaskMessage, setAnalysisTaskMessage] = useState("");
  const [analysisTaskProgress, setAnalysisTaskProgress] = useState<number | null>(null);
  const [paperAnalysisDetailLevel, setPaperAnalysisDetailLevel] = useState<AnalysisDetailLevel>(() =>
    getPreferredPaperAnalysisDetailLevel());
  const [paperEvidenceMode, setPaperEvidenceMode] = useState<PaperEvidenceMode>(() =>
    getStoredEvidenceMode() || "rough");

  const [readerOpen, setReaderOpen] = useState(false);
  const [reportTab, setReportTab] = useState("skim");
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [topicManagerOpen, setTopicManagerOpen] = useState(false);
  const [metadataEditorOpen, setMetadataEditorOpen] = useState(false);
  const [sourceEditorOpen, setSourceEditorOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [manualRefreshing, setManualRefreshing] = useState(false);

  useEffect(() => {
    setProcessingSource("pdf");
  }, [id]);

  useEffect(() => {
    localStorage.setItem(PAPER_ANALYSIS_DETAIL_LEVEL_KEY, paperAnalysisDetailLevel);
    localStorage.setItem(DEEP_DETAIL_LEVEL_KEY, paperAnalysisDetailLevel);
    localStorage.setItem(REASONING_DETAIL_LEVEL_KEY, paperAnalysisDetailLevel);
  }, [paperAnalysisDetailLevel]);

  useEffect(() => {
    localStorage.setItem(PAPER_ANALYSIS_EVIDENCE_MODE_KEY, paperEvidenceMode);
  }, [paperEvidenceMode]);

  const refreshPaperDetail = useCallback(async (options?: { includeFigures?: boolean }) => {
    if (!id) return null;
    const [nextPaper, figurePayload] = await Promise.all([
      paperApi.detail(id),
      options?.includeFigures
        ? paperApi.getFigures(id).catch(() => ({ items: [] as FigureAnalysisItem[] }))
        : Promise.resolve(null),
    ]);
    setPaper(nextPaper);
    setEmbedDone(nextPaper.has_embedding ?? false);
    setSavedSkim(nextPaper.skim_report ?? null);
    setSavedDeep(nextPaper.deep_report ?? null);
    const rc = nextPaper.metadata?.reasoning_chain as ReasoningChainResult | undefined;
    setReasoning(rc ?? null);
    setAnalysisRounds(asPaperAnalysisBundle(nextPaper.analysis_rounds));
    if (figurePayload) {
      setFigures(Array.isArray(figurePayload.items) ? figurePayload.items : []);
      setSelectedFigureIds(new Set());
    }
    return nextPaper;
  }, [id]);

  const loadSimilarPapers = useCallback(async (
    paperId: string,
    options?: {
      silent?: boolean;
    },
  ) => {
    setSimilarLoading(true);
    try {
      const res = await paperApi.similar(paperId);
      setSimilarIds(res.similar_ids || []);
      setSimilarItems(res.items || []);
    } catch (error) {
      setSimilarIds([]);
      setSimilarItems([]);
      if (!options?.silent) {
        toast("error", error instanceof Error ? error.message : "获取相似论文失败");
      }
    } finally {
      setSimilarLoading(false);
    }
  }, [toast]);

  const handleManualRefresh = useCallback(async () => {
    if (!id) return;
    setManualRefreshing(true);
    try {
      const nextPaper = await refreshPaperDetail({ includeFigures: true });
      if (nextPaper?.has_embedding) {
        similarFetchKeyRef.current = "";
        await loadSimilarPapers(id, { silent: true });
      }
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "刷新论文详情失败");
    } finally {
      setManualRefreshing(false);
    }
  }, [id, loadSimilarPapers, refreshPaperDetail, toast]);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    Promise.all([
      paperApi.detail(id),
      paperApi.getFigures(id).catch(() => ({ items: [] as FigureAnalysisItem[] })),
    ])
      .then(([p, figRes]) => {
        setPaper(p);
        setProcessingSource("pdf");
        setEmbedDone(p.has_embedding ?? false);
        setSavedSkim(p.skim_report ?? null);
        setSavedDeep(p.deep_report ?? null);
        setFigures(Array.isArray(figRes.items) ? figRes.items : []);
        setSelectedFigureIds(new Set());
        const rc = p.metadata?.reasoning_chain as ReasoningChainResult | undefined;
        setReasoning(rc ?? null);
        const nextAnalysisRounds = asPaperAnalysisBundle(p.analysis_rounds);
        setAnalysisRounds(nextAnalysisRounds);
        if (p.deep_report) setReportTab("deep");
        else if (hasPaperAnalysis(nextAnalysisRounds)) setReportTab("analysis");
        else if (p.skim_report) setReportTab("skim");
      })
      .catch(() => { toast("error", "加载论文详情失败"); })
      .finally(() => setLoading(false));
  }, [id, toast]);

  useEffect(() => {
    if (!id) return;
    const nextKey = id && embedDone ? `${id}:embedded` : "";
    if (!nextKey) {
      similarFetchKeyRef.current = "";
      setSimilarIds([]);
      setSimilarItems([]);
      return;
    }
    if (similarFetchKeyRef.current === nextKey) return;
    similarFetchKeyRef.current = nextKey;
    void loadSimilarPapers(id, { silent: true });
  }, [embedDone, id, loadSimilarPapers]);

  const pollTaskResult = useCallback(async <T = Record<string, unknown>>(
    taskId: string,
    options?: {
      timeoutMs?: number;
      intervalMs?: number;
      timeoutMessage?: string;
      onStatus?: (status: TaskStatus) => void;
      fallbackResult?: () => Promise<T>;
    },
  ): Promise<T> => {
    const timeoutAt = Date.now() + (options?.timeoutMs ?? 15 * 60 * 1000);
    const intervalMs = Math.max(600, options?.intervalMs ?? 1200);
    let transientErrors = 0;

    while (true) {
      try {
        const status = await tasksApi.getStatus(taskId);
        transientErrors = 0;
        options?.onStatus?.(status);
        if (status.finished) {
          if (!status.success || status.status === "failed" || status.status === "cancelled") {
            throw new Error(status.error || status.message || "任务失败");
          }
          try {
            const result = await tasksApi.getResult(taskId) as T;
            if (result && (typeof result !== "object" || Object.keys(result as object).length > 0)) {
              return result;
            }
          } catch {
            // Fallthrough to fallback.
          }
          if (options?.fallbackResult) {
            return options.fallbackResult();
          }
          return {} as T;
        }
      } catch (err) {
        transientErrors += 1;
        if (transientErrors >= 5) {
          throw err;
        }
      }

      if (Date.now() > timeoutAt) {
        throw new Error(options?.timeoutMessage || "任务超时，请到任务后台查看进度");
      }
      await sleep(intervalMs);
    }
  }, []);

  const handleOpenPdf = async () => {
    if (!id || !paper) return;
    if (paper.pdf_path) {
      setReaderOpen(true);
      return;
    }
    setPdfPreparing(true);
    setPdfTaskMessage("创建 PDF 下载任务...");
    setPdfTaskProgress(1);
    try {
      const kickoff = await paperApi.downloadPdfAsync(id);
      if (!kickoff.task_id) throw new Error("PDF 下载任务启动失败");
      setPdfTaskId(kickoff.task_id);
      const payload = await pollTaskResult<{ status?: string; pdf_path?: string }>(kickoff.task_id, {
        timeoutMessage: "PDF 下载超时，请到任务后台继续查看进度",
        onStatus: (status) => {
          if (typeof status.progress_pct === "number") {
            setPdfTaskProgress(Math.max(0, Math.min(100, status.progress_pct)));
          }
          if (status.message) setPdfTaskMessage(status.message);
        },
        fallbackResult: async () => {
          const latest = await refreshPaperDetail();
          return {
            status: latest?.pdf_path ? "downloaded" : "",
            pdf_path: latest?.pdf_path || "",
          };
        },
      });
      setPdfTaskProgress(100);
      setPdfTaskMessage("PDF 已就绪");
      const updated = await refreshPaperDetail();
      if (updated?.pdf_path || payload?.pdf_path) {
        setReaderOpen(true);
        toast("success", `PDF ${payload?.status === "exists" ? "已存在，已直接打开" : "下载完成"}`);
      } else {
        throw new Error("PDF 已处理完成，但未找到本地文件");
      }
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "PDF 下载失败");
    } finally {
      setPdfPreparing(false);
      setPdfTaskId(null);
      setPdfTaskMessage("");
      setPdfTaskProgress(null);
    }
  };

  const handleProcessOcr = async (force = false) => {
    if (!id) return;
    setOcrProcessing(true);
    setOcrTaskMessage(force ? "重新创建 OCR 处理任务..." : "创建 OCR 处理任务...");
    setOcrTaskProgress(1);
    try {
      const kickoff = await paperApi.processOcrAsync(id, force);
      if (!kickoff.task_id) throw new Error("OCR 处理任务启动失败");
      setOcrTaskId(kickoff.task_id);
      const payload = await pollTaskResult<{ available?: boolean; status?: string; markdown_chars?: number }>(kickoff.task_id, {
        timeoutMessage: "OCR 处理超时，请到任务后台继续查看进度",
        onStatus: (status) => {
          if (typeof status.progress_pct === "number") {
            setOcrTaskProgress(Math.max(0, Math.min(100, status.progress_pct)));
          }
          if (status.message) setOcrTaskMessage(status.message);
        },
        fallbackResult: async () => paperApi.getOcrStatus(id),
      });
      setOcrTaskProgress(100);
      setOcrTaskMessage("OCR 处理完成");
      await refreshPaperDetail();
      if (payload.available) {
        toast("success", `OCR 处理完成，已生成 Markdown（约 ${payload.markdown_chars || 0} 字），可手动切换到 Markdown`);
      } else {
        toast("warning", "OCR 处理已完成，但当前没有可用 Markdown，可继续使用原有论文内容流程");
      }
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "OCR 处理失败");
    } finally {
      setOcrProcessing(false);
      setOcrTaskId(null);
      setOcrTaskMessage("");
      setOcrTaskProgress(null);
    }
  };

  const handleSkim = async () => {
    if (!id) return;
    setSkimLoading(true);
    setReportTab("skim");
    setSkimTaskMessage("创建粗读任务...");
    setSkimTaskProgress(1);
    try {
      const kickoff = await pipelineApi.skimAsync(id);
      if (!kickoff.task_id) throw new Error("粗读任务启动失败");
      setSkimTaskId(kickoff.task_id);
      const report = await pollTaskResult<SkimReport>(kickoff.task_id, {
        timeoutMessage: "粗读超时，请到任务后台继续查看进度",
        onStatus: (status) => {
          if (typeof status.progress_pct === "number") {
            setSkimTaskProgress(Math.max(0, Math.min(100, status.progress_pct)));
          }
          if (status.message) setSkimTaskMessage(status.message);
        },
      });
      setSkimTaskProgress(100);
      setSkimTaskMessage("粗读完成");
      const reportPayload = (report && typeof report === "object" ? report : {}) as Record<string, unknown>;
      const normalizedInnovations = asStringArray(reportPayload.innovations);
      const normalizedOneLiner = resolveSkimOneLiner(
        typeof reportPayload.one_liner === "string" ? reportPayload.one_liner : "",
        normalizedInnovations,
      );
      setSkimReport({
        one_liner: normalizedOneLiner,
        innovations: normalizedInnovations,
        keywords: asStringArray(reportPayload.keywords),
        title_zh: typeof reportPayload.title_zh === "string" ? reportPayload.title_zh : "",
        abstract_zh: typeof reportPayload.abstract_zh === "string" ? reportPayload.abstract_zh : "",
        relevance_score: typeof reportPayload.relevance_score === "number" ? reportPayload.relevance_score : 0,
      });
      await refreshPaperDetail();
      toast("success", "粗读完成");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "粗读失败");
    } finally {
      setSkimLoading(false);
      setSkimTaskId(null);
      setSkimTaskMessage("");
      setSkimTaskProgress(null);
    }
  };

  const handleDeep = async () => {
    if (!id) return;
    setDeepLoading(true);
    setReportTab("deep");
    setDeepTaskMessage(`创建精读任务（详略: ${paperAnalysisDetailLevel}，证据: ${getEvidenceModeLabel(paperEvidenceMode)}，来源: ${getProcessingSourceLabel(processingSource)}）...`);
    setDeepTaskProgress(1);
    try {
      const kickoff = await pipelineApi.deepAsync(id, {
        detailLevel: paperAnalysisDetailLevel,
        contentSource: processingSource,
        evidenceMode: paperEvidenceMode,
      });
      if (!kickoff.task_id) throw new Error("精读任务启动失败");
      setDeepTaskId(kickoff.task_id);
      const report = await pollTaskResult<DeepDiveReport>(kickoff.task_id, {
        timeoutMessage: "精读超时，请到任务后台继续查看进度",
        onStatus: (status) => {
          if (typeof status.progress_pct === "number") {
            setDeepTaskProgress(Math.max(0, Math.min(100, status.progress_pct)));
          }
          if (status.message) setDeepTaskMessage(status.message);
        },
      });
      setDeepTaskProgress(100);
      setDeepTaskMessage("精读完成");
      setDeepReport(report);
      await refreshPaperDetail();
      // Clear transient state so subsequent page loads use the saved structured report.
      setDeepReport(null);
      toast("success", "精读完成");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "精读失败");
    } finally {
      setDeepLoading(false);
      setDeepTaskId(null);
      setDeepTaskMessage("");
      setDeepTaskProgress(null);
    }
  };

  const handleEmbed = async () => {
    if (!id) return;
    const wasEmbedded = embedDone === true;
    setEmbedLoading(true);
    setEmbedTaskMessage("创建向量化任务...");
    setEmbedTaskProgress(1);
    try {
      const kickoff = await pipelineApi.embedAsync(id);
      if (!kickoff.task_id) throw new Error("向量化任务启动失败");
      setEmbedTaskId(kickoff.task_id);
      await pollTaskResult<Record<string, unknown>>(kickoff.task_id, {
        timeoutMessage: "向量化超时，请到任务后台继续查看进度",
        onStatus: (status) => {
          if (typeof status.progress_pct === "number") {
            setEmbedTaskProgress(Math.max(0, Math.min(100, status.progress_pct)));
          }
          if (status.message) setEmbedTaskMessage(status.message);
        },
      });
      setEmbedTaskProgress(100);
      setEmbedTaskMessage("向量化完成");

      const nextPaper = await refreshPaperDetail();
      setEmbedDone(true);
      similarFetchKeyRef.current = "";
      await loadSimilarPapers(id, { silent: true });
      const nextEmbeddingStatus = getEmbeddingStatusMeta(nextPaper?.metadata as Record<string, unknown> | undefined);
      if (nextEmbeddingStatus?.source === "pseudo_fallback") {
        toast(
          "warning",
          `${wasEmbedded ? "重新向量化已完成，但" : "向量化已完成，但"}当前使用的是伪向量回退：${getEmbeddingFallbackLabel(nextEmbeddingStatus.fallback_reason)}`,
        );
      } else {
        toast("success", wasEmbedded ? "重新向量化完成" : "向量化完成");
      }
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "向量化失败");
    } finally {
      setEmbedLoading(false);
      setEmbedTaskId(null);
      setEmbedTaskMessage("");
      setEmbedTaskProgress(null);
    }
  };

  const extractFigureCandidates = useCallback(async (): Promise<FigureAnalysisItem[]> => {
    if (!id) return [];
    const runSyncFallback = async () => {
      setFigureTaskMessage("直接提取图表候选...");
      setFigureTaskProgress(null);
      const direct = await paperApi.extractFigures(id, 80);
      return Array.isArray(direct.items) ? direct.items : [];
    };

    try {
      setFigureTaskMessage("创建图表提取任务...");
      setFigureTaskProgress(1);
      const kickoff = await paperApi.extractFiguresAsync(id, 80);
      if (!kickoff.task_id) {
        throw new Error("图表提取任务启动失败");
      }
      setFigureTaskId(kickoff.task_id);
      const payload = await pollTaskResult<{ items?: FigureAnalysisItem[] }>(kickoff.task_id, {
        timeoutMessage: "图表提取超时，请到任务后台继续查看进度",
        onStatus: (status) => {
          if (typeof status.progress_pct === "number") {
            setFigureTaskProgress(Math.max(0, Math.min(100, status.progress_pct)));
          }
          if (status.message) setFigureTaskMessage(status.message);
        },
        fallbackResult: async () => {
          const latest = await paperApi.getFigures(id);
          return { items: Array.isArray(latest.items) ? latest.items : [] };
        },
      });
      return Array.isArray(payload?.items) ? payload.items : [];
    } catch (error) {
      const message = error instanceof Error ? error.message : "";
      if (message.includes("图表提取失败")) {
        throw error instanceof Error ? error : new Error(message || "图表候选提取失败");
      }
      console.warn("figure async extraction fallback", error);
      return runSyncFallback();
    }
  }, [id, pollTaskResult]);

  const handleExtractFigures = async () => {
    if (!id) return;
    setFiguresAnalyzing(true);
    setReportTab("figures");
    try {
      const items = await extractFigureCandidates();
      if (items.length === 0) {
        throw new Error("图表提取失败：未找到可用图表候选");
      }
      setFigures(items);
      setSelectedFigureIds(new Set());
      toast("success", `图表候选提取完成，共 ${items.length} 项`);
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "图表候选提取失败");
    } finally {
      setFiguresAnalyzing(false);
      setFigureTaskId(null);
      setFigureTaskMessage("");
      setFigureTaskProgress(null);
    }
  };

  const handleAnalyzeSelectedFigures = async () => {
    if (!id) return;
    const figureIds = [...selectedFigureIds].filter(Boolean);
    if (figureIds.length === 0) {
      toast("warning", "请先勾选需要分析的图表");
      return;
    }
    setFiguresAnalyzing(true);
    setFigureTaskMessage(`创建图表分析任务（${figureIds.length} 项）...`);
    setFigureTaskProgress(1);
    setReportTab("figures");
    try {
      const kickoff = await paperApi.analyzeSelectedFiguresAsync(id, figureIds);
      if (!kickoff.task_id) throw new Error("图表分析任务启动失败");
      setFigureTaskId(kickoff.task_id);
      const res = await pollTaskResult<{ items?: FigureAnalysisItem[] }>(kickoff.task_id, {
        timeoutMessage: "图表分析超时，请到任务后台继续查看进度",
        onStatus: (status) => {
          if (typeof status.progress_pct === "number") {
            setFigureTaskProgress(Math.max(0, Math.min(100, status.progress_pct)));
          }
          if (status.message) setFigureTaskMessage(status.message);
        },
        fallbackResult: async () => {
          const latest = await paperApi.getFigures(id);
          return { items: Array.isArray(latest.items) ? latest.items : [] };
        },
      });
      const items = Array.isArray(res.items) ? res.items : [];
      setFigures(items);
      setSelectedFigureIds(new Set());
      toast("success", `已分析 ${figureIds.length} 个图表候选`);
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "图表分析失败");
    } finally {
      setFiguresAnalyzing(false);
      setFigureTaskId(null);
      setFigureTaskMessage("");
      setFigureTaskProgress(null);
    }
  };

  const selectableFigureIds = figures.flatMap((fig) => (fig.id ? [fig.id] : []));
  const allSelectableChecked =
    selectableFigureIds.length > 0
    && selectableFigureIds.every((figureId) => selectedFigureIds.has(figureId));

  const handleToggleSelectAllFigures = useCallback(() => {
    if (selectableFigureIds.length === 0) return;
    if (allSelectableChecked) {
      setSelectedFigureIds(new Set());
      return;
    }
    setSelectedFigureIds(new Set(selectableFigureIds));
  }, [allSelectableChecked, selectableFigureIds]);

  const handleDeleteSelectedFigures = async () => {
    if (!id) return;
    const figureIds = [...selectedFigureIds].filter(Boolean);
    if (figureIds.length === 0) {
      toast("warning", "请先勾选需要删除的图表候选");
      return;
    }

    setFiguresAnalyzing(true);
    setFigureTaskMessage(`正在删除 ${figureIds.length} 个图表候选...`);
    setFigureTaskProgress(null);
    setReportTab("figures");
    try {
      const res = await paperApi.deleteFigures(id, figureIds);
      const items = Array.isArray(res.items) ? res.items : [];
      setFigures(items);
      setSelectedFigureIds(new Set());
      const deletedCount = Number.isFinite(res.deleted_count) ? Number(res.deleted_count) : figureIds.length;
      toast("success", `已删除 ${deletedCount} 个图表候选`);
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "删除图表候选失败");
    } finally {
      setFiguresAnalyzing(false);
      setFigureTaskMessage("");
      setFigureTaskProgress(null);
    }
  };

  const handleDeleteFigure = useCallback(async (figureId: string) => {
    if (!id) return;
    try {
      const res = await paperApi.deleteFigure(id, figureId);
      const items = Array.isArray(res.items) ? res.items : [];
      setFigures(items);
      setSelectedFigureIds((prev) => {
        const next = new Set(prev);
        next.delete(figureId);
        return next;
      });
      toast("success", "图表候选已删除");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "删除图表候选失败");
    }
  }, [id, toast]);

  const handleReasoning = async () => {
    if (!id) return;
    setReasoningLoading(true);
    setReportTab("reasoning");
    setReasoningTaskMessage(`创建推理链任务（详略: ${paperAnalysisDetailLevel}，证据: ${getEvidenceModeLabel(paperEvidenceMode)}，来源: ${getProcessingSourceLabel(processingSource)}）...`);
    setReasoningTaskProgress(1);
    try {
      const kickoff = await paperApi.reasoningAnalysisAsync(id, {
        detailLevel: paperAnalysisDetailLevel,
        contentSource: processingSource,
        evidenceMode: paperEvidenceMode,
      });
      if (!kickoff.task_id) throw new Error("推理链任务启动失败");
      setReasoningTaskId(kickoff.task_id);
      const res = await pollTaskResult<{ reasoning?: ReasoningChainResult }>(kickoff.task_id, {
        timeoutMessage: "推理链分析超时，请到任务后台继续查看进度",
        onStatus: (status) => {
          if (typeof status.progress_pct === "number") {
            setReasoningTaskProgress(Math.max(0, Math.min(100, status.progress_pct)));
          }
          if (status.message) setReasoningTaskMessage(status.message);
        },
      });
      if (res.reasoning) {
        setReasoning(res.reasoning);
      }
      const refreshed = await refreshPaperDetail();
      const rc = refreshed?.metadata?.reasoning_chain as ReasoningChainResult | undefined;
      if (rc) setReasoning(rc);
      toast("success", "推理链分析完成");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "推理链分析失败");
    } finally {
      setReasoningLoading(false);
      setReasoningTaskId(null);
      setReasoningTaskMessage("");
      setReasoningTaskProgress(null);
    }
  };

  const handleAnalyzeRounds = async () => {
    if (!id) return;
    const shouldRetry = hasPaperAnalysis(analysisRounds);
    setAnalysisLoading(true);
    setReportTab("analysis");
    setAnalysisTaskMessage(
      `${shouldRetry ? "重新创建" : "创建"}三轮分析任务（详略: ${paperAnalysisDetailLevel}，证据: ${getEvidenceModeLabel(paperEvidenceMode)}，来源: ${getProcessingSourceLabel(processingSource)}）...`,
    );
    setAnalysisTaskProgress(1);
    try {
      const kickoff = shouldRetry
        ? await paperApi.retryAnalysis(id, {
          detail_level: paperAnalysisDetailLevel,
          content_source: processingSource,
          evidence_mode: paperEvidenceMode,
        })
        : await paperApi.analyzeAsync(id, {
          detail_level: paperAnalysisDetailLevel,
          content_source: processingSource,
          evidence_mode: paperEvidenceMode,
        });
      if (!kickoff.task_id) throw new Error("论文三轮分析任务启动失败");
      setAnalysisTaskId(kickoff.task_id);
      const payload = await pollTaskResult<{ analysis_rounds?: PaperAnalysisBundle | null }>(kickoff.task_id, {
        timeoutMessage: "论文三轮分析超时，请到任务后台继续查看进度",
        onStatus: (status) => {
          if (typeof status.progress_pct === "number") {
            setAnalysisTaskProgress(Math.max(0, Math.min(100, status.progress_pct)));
          }
          if (status.message) setAnalysisTaskMessage(status.message);
        },
        fallbackResult: async () => {
          const latest = await paperApi.analysis(id);
          return {
            analysis_rounds: asPaperAnalysisBundle(latest.item),
          };
        },
      });
      setAnalysisTaskProgress(100);
      setAnalysisTaskMessage("论文三轮分析完成");
      const refreshed = await refreshPaperDetail().catch(() => null);
      const nextBundle =
        asPaperAnalysisBundle(payload.analysis_rounds)
        || asPaperAnalysisBundle(refreshed?.analysis_rounds)
        || asPaperAnalysisBundle((await paperApi.analysis(id)).item);
      if (nextBundle) {
        setAnalysisRounds(nextBundle);
      }
      toast("success", shouldRetry ? "论文三轮分析已更新" : "论文三轮分析完成");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "论文三轮分析失败");
    } finally {
      setAnalysisLoading(false);
      setAnalysisTaskId(null);
      setAnalysisTaskMessage("");
      setAnalysisTaskProgress(null);
    }
  };

  const handleToggleFavorite = useCallback(async () => {
    if (!id || !paper) return;
    const prevFavorited = paper.favorited;
    try {
      const res = await paperApi.toggleFavorite(id);
      setPaper((prev) => prev ? { ...prev, favorited: res.favorited } : prev);
    } catch {
      toast("error", "更新收藏状态失败");
      setPaper((prev) => prev ? { ...prev, favorited: prevFavorited } : prev);
    }
  }, [id, paper, toast]);

  const handleDeletePaper = useCallback(async () => {
    if (!id) return;
    setDeleting(true);
    try {
      await paperApi.delete(id, true);
      toast("success", "论文已删除");
      navigate("/papers");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "删除失败");
    } finally {
      setDeleting(false);
      setConfirmDeleteOpen(false);
    }
  }, [id, navigate, toast]);

  if (loading) return <PaperDetailSkeleton />;
  if (!paper) {
    return (
      <Empty
        title="论文不存在"
        description="未找到对应论文，可能已被删除。"
        action={<Button variant="secondary" onClick={() => navigate("/papers")}>返回列表</Button>}
      />
    );
  }

  const statusConfig: Record<string, { label: string; variant: "default" | "warning" | "success" }> = {
    unread: { label: "未读", variant: "default" },
    skimmed: { label: "粗读", variant: "warning" },
    deep_read: { label: "精读", variant: "success" },
  };
  const sc = statusConfig[paper.read_status] || statusConfig.unread;

  const parsedSavedSkim = parseSavedSkimReport(savedSkim);
  const parsedSavedDeep = parseSavedDeepReport(savedDeep);
  const activeSkimReport = skimReport ?? parsedSavedSkim;
  const activeDeepReport = deepReport ?? parsedSavedDeep;

  const hasSkim = !!activeSkimReport || !!savedSkim?.summary_md;
  const hasDeep = !!activeDeepReport || !!savedDeep?.deep_dive_md;
  const hasFigures = figures.length > 0;
  const reasoningIncomplete = isIncompleteReasoningResult(reasoning);
  const hasReasoning = !!reasoning && !reasoningIncomplete;
  const hasAnalysis = hasPaperAnalysis(analysisRounds);
  const hasSimilar = similarItems.length > 0 || similarIds.length > 0;
  const hasRealArxiv = isRealArxivId(paper.arxiv_id);
  const hasExternalPdfHint = !hasRealArxiv && hasExternalPdfCandidate(paper);
  const pdfDownloadNote = String(paper.metadata?.pdf_download_note || "").trim();
  const canPreparePdf = !!paper.pdf_path || hasRealArxiv || (hasExternalPdfHint && !pdfDownloadNote);
  const sourceUrl = getPaperSourceUrl(paper);
  const sourceLabel = getPaperSourceLabel(sourceUrl);
  const ocrMeta = getMineruOcrMeta(paper.metadata as Record<string, unknown> | undefined);
  const deepMeta = getAnalysisArtifactMeta(paper.metadata as Record<string, unknown> | undefined, "deep_dive_meta");
  const reasoningMeta = getAnalysisArtifactMeta(paper.metadata as Record<string, unknown> | undefined, "reasoning_chain_meta");
  const hasOcrMarkdown = ocrMeta?.status === "success" && ((ocrMeta.markdown_chars || 0) > 0 || !!ocrMeta.has_structured_output);
  const shouldForceOcr = hasOcrMarkdown || ocrMeta?.status === "failed";
  const embedUsesFallback = getEmbeddingStatusMeta(paper.metadata as Record<string, unknown> | undefined)?.source === "pseudo_fallback";
  const skimStatus: "idle" | "loading" | "done" = skimLoading ? "loading" : hasSkim ? "done" : "idle";
  const deepStatus: "idle" | "loading" | "done" = deepLoading ? "loading" : hasDeep ? "done" : "idle";
  const analysisStatus: "idle" | "loading" | "done" = analysisLoading ? "loading" : hasAnalysis ? "done" : "idle";
  const figureStatus: "idle" | "loading" | "done" = figuresAnalyzing ? "loading" : hasFigures ? "done" : "idle";
  const reasoningStatus: "idle" | "loading" | "done" = reasoningLoading ? "loading" : hasReasoning ? "done" : "idle";
  const relatedStatus: "idle" | "loading" | "done" = (embedLoading || similarLoading) ? "loading" : hasSimilar ? "done" : "idle";

  const anyPipelineRunning = skimLoading || deepLoading || analysisLoading || figuresAnalyzing || reasoningLoading || embedLoading || ocrProcessing;

  return (
    <div className="animate-fade-in space-y-6">
      {/* Top actions */}
      <div className="flex items-center justify-between">
        <button onClick={() => navigate("/papers")} className="flex items-center gap-1.5 text-sm text-ink-secondary transition-colors hover:text-ink">
          <ArrowLeft className="h-4 w-4" /> 返回论文库
        </button>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void handleManualRefresh()}
            disabled={manualRefreshing}
            className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-ink-tertiary transition-colors hover:bg-hover hover:text-ink disabled:opacity-60"
            title="刷新论文详情、图表候选和相关论文"
          >
            <RefreshCw className={`h-4 w-4 ${manualRefreshing ? "animate-spin" : ""}`} />
            刷新
          </button>
          <button
            onClick={() => setMetadataEditorOpen(true)}
            className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-ink-tertiary transition-colors hover:bg-hover hover:text-ink"
            title="编辑标题、摘要与关键词"
          >
            <PencilLine className="h-4 w-4" />
            编辑题录
          </button>
          <button
            onClick={() => setSourceEditorOpen(true)}
            className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-ink-tertiary transition-colors hover:bg-hover hover:text-ink"
            title="修正来源链接或上传 PDF"
          >
            <Link2 className="h-4 w-4" />
            修正来源 / PDF
          </button>
          <button onClick={handleToggleFavorite} className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition-colors hover:bg-error/10" title={paper.favorited ? "取消收藏" : "收藏"}>
            <Heart className={`h-5 w-5 transition-all ${paper.favorited ? "fill-red-500 text-red-500 scale-110" : "text-ink-tertiary"}`} />
            <span className={paper.favorited ? "text-red-500" : "text-ink-tertiary"}>{paper.favorited ? "已收藏" : "收藏"}</span>
          </button>
          <button
            onClick={() => setConfirmDeleteOpen(true)}
            disabled={deleting}
            className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-ink-tertiary transition-colors hover:bg-error/10 hover:text-error disabled:opacity-60"
            title="删除论文"
          >
            <Trash2 className="h-4 w-4" />
            删除
          </button>
        </div>
      </div>

      {/* Metadata card */}
      <Card className="rounded-2xl">
        <div className="flex items-start gap-2">
          <Badge variant={sc.variant}>{sc.label}</Badge>
          {embedDone && !embedUsesFallback && <Badge variant="info">向量化完成</Badge>}
          {embedDone && embedUsesFallback && <Badge variant="warning">伪向量</Badge>}
          {hasOcrMarkdown && <Badge variant="success">Markdown 就绪</Badge>}
          {!hasOcrMarkdown && ocrMeta?.status === "failed" && <Badge variant="warning">OCR 失败</Badge>}
          {hasRealArxiv && (
            <a href={`https://arxiv.org/abs/${paper.arxiv_id}`} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 text-xs text-primary hover:underline">
              <ExternalLink className="h-3 w-3" />{paper.arxiv_id}
            </a>
          )}
          {!hasRealArxiv && sourceUrl && (
            <a href={sourceUrl} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 text-xs text-primary hover:underline">
              <ExternalLink className="h-3 w-3" />{sourceLabel}
            </a>
          )}
        </div>
        <h1 className="mt-3 text-2xl font-bold leading-snug text-ink">{paper.title}</h1>
        {paper.title_zh && <p className="mt-1 text-base text-ink-secondary">{paper.title_zh}</p>}
        {paper.abstract ? (
          <>
            <p className="mt-4 text-sm leading-relaxed text-ink-secondary">{paper.abstract}</p>
            {paper.abstract_zh && (
              <div className="mt-3 rounded-lg border border-border bg-page p-4">
                <p className="mb-1 text-xs font-medium text-ink-tertiary">中文摘要</p>
                <p className="text-sm leading-relaxed text-ink-secondary">{paper.abstract_zh}</p>
              </div>
            )}
          </>
        ) : paper.abstract_zh ? (
          <p className="mt-4 text-sm leading-relaxed text-ink-secondary">{paper.abstract_zh}</p>
        ) : null}
        {paper.publication_date && <p className="mt-3 text-sm text-ink-tertiary">发布日期: {paper.publication_date}</p>}
        <div className="mt-3 flex items-center justify-between gap-3">
          <p className="text-sm font-medium text-ink">文件夹</p>
          <Button
            size="sm"
            variant="secondary"
            icon={<Plus className="h-3.5 w-3.5" />}
            onClick={() => setTopicManagerOpen(true)}
          >
            管理文件夹
          </Button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {paper.topics && paper.topics.length > 0 && paper.topics.map((t) => (
            <span key={t} className="inline-flex items-center gap-1 rounded-md bg-primary-light px-2.5 py-1 text-xs font-medium text-primary">
              <Folder className="h-3 w-3" />{t}
            </span>
          ))}
          {(!paper.topics || paper.topics.length === 0) && (
            <span className="inline-flex items-center gap-1 rounded-md border border-dashed border-border px-2.5 py-1 text-xs text-ink-tertiary">
              暂未加入任何文件夹
            </span>
          )}
          {paper.keywords && paper.keywords.map((kw) => (
            <span key={kw} className="inline-flex items-center gap-1 rounded-md bg-hover px-2.5 py-1 text-xs text-ink-secondary">
              <Tag className="h-3 w-3" />{kw}
            </span>
          ))}
          {paper.categories && paper.categories.map((c) => (
            <span key={c} className="inline-flex items-center rounded-md border border-border bg-surface px-2 py-0.5 text-xs text-ink-tertiary">{c}</span>
          ))}
        </div>
      </Card>

      <PaperCitationExpansion paper={paper} />

      {/* ========== quick actions + tabs + reports ========== */}
      <div className="space-y-3">
        {/* Action cards */}
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 sm:gap-3 xl:grid-cols-3">
          <button
            onClick={() => void handleOpenPdf()}
            disabled={!canPreparePdf || pdfPreparing}
            className="order-1 flex min-h-[72px] items-center gap-3 rounded-xl border border-border bg-white p-3.5 text-left transition-colors duration-150 hover:bg-hover disabled:opacity-50 sm:p-4"
            title={!canPreparePdf ? (pdfDownloadNote || "当前论文没有可用 PDF 来源") : hasRealArxiv ? "自动下载并打开本地 PDF" : "自动从开放来源下载并打开 PDF"}
          >
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
              {pdfPreparing ? <Loader2 className="h-5 w-5 animate-spin" /> : <FileSearch className="h-5 w-5" />}
            </div>
            <div className="text-left">
              <p className="text-sm font-semibold text-ink">阅读 PDF</p>
            </div>
          </button>
          <button
            onClick={handleAnalyzeRounds}
            disabled={analysisLoading}
            className="order-5 flex min-h-[72px] items-center gap-3 rounded-xl border border-border bg-white p-3.5 text-left transition-colors duration-150 hover:bg-hover disabled:opacity-60 sm:p-4"
            title="生成鸟瞰扫描、内容理解、深度分析和最终结构化笔记"
          >
            <div className={`flex h-10 w-10 items-center justify-center rounded-xl ${hasAnalysis ? "bg-success/10 text-success" : "bg-emerald-500/10 text-emerald-500"}`}>
              {analysisLoading ? <Loader2 className="h-5 w-5 animate-spin" /> : hasAnalysis ? <Check className="h-5 w-5" /> : <Sparkles className="h-5 w-5" />}
            </div>
            <div className="text-left">
              <p className="text-sm font-semibold text-ink">{hasAnalysis ? "重新三轮分析" : "三轮分析"}</p>
            </div>
          </button>
          <button
            onClick={handleReasoning}
            disabled={reasoningLoading || !paper.pdf_path}
            title={!paper.pdf_path ? "需要先下载 PDF，才能分析推理链" : ""}
            className="order-4 flex min-h-[72px] items-center gap-3 rounded-xl border border-border bg-white p-3.5 text-left transition-colors duration-150 hover:bg-hover disabled:opacity-60 sm:p-4"
          >
            <div className={`flex h-10 w-10 items-center justify-center rounded-xl ${hasReasoning ? "bg-success/10 text-success" : !paper.pdf_path ? "bg-ink-tertiary/10 text-ink-tertiary" : "bg-purple-500/10 text-purple-500"}`}>
              {reasoningLoading ? <Loader2 className="h-5 w-5 animate-spin" /> : hasReasoning ? <Check className="h-5 w-5" /> : <Brain className="h-5 w-5" />}
            </div>
            <div className="text-left">
              <p className="text-sm font-semibold text-ink">{hasReasoning ? "重新推理链" : "推理链"}</p>
            </div>
          </button>
          <button
            onClick={handleSkim}
            disabled={skimLoading}
            className="order-2 flex min-h-[72px] items-center gap-3 rounded-xl border border-border bg-white p-3.5 text-left transition-colors duration-150 hover:bg-hover disabled:opacity-60 sm:p-4"
          >
            <div className={`flex h-10 w-10 items-center justify-center rounded-xl ${hasSkim ? "bg-success/10 text-success" : "bg-amber-500/10 text-amber-500"}`}>
              {skimLoading ? <Loader2 className="h-5 w-5 animate-spin" /> : hasSkim ? <Check className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
            </div>
            <div className="text-left">
              <p className="text-sm font-semibold text-ink">{hasSkim ? "重新粗读" : "粗读"}</p>
            </div>
          </button>
          <button
            onClick={handleDeep}
            disabled={deepLoading || !paper.pdf_path}
            className="order-3 flex min-h-[72px] items-center gap-3 rounded-xl border border-border bg-white p-3.5 text-left transition-colors duration-150 hover:bg-hover disabled:opacity-60 sm:p-4"
            title={!paper.pdf_path ? "需要先下载 PDF" : ""}
          >
            <div className={`flex h-10 w-10 items-center justify-center rounded-xl ${hasDeep ? "bg-success/10 text-success" : !paper.pdf_path ? "bg-ink-tertiary/10 text-ink-tertiary" : "bg-indigo-500/10 text-indigo-500"}`}>
              {deepLoading ? <Loader2 className="h-5 w-5 animate-spin" /> : hasDeep ? <Check className="h-5 w-5" /> : <BookOpen className="h-5 w-5" />}
            </div>
            <div className="text-left">
              <p className="text-sm font-semibold text-ink">{hasDeep ? "重新精读" : "精读"}</p>
            </div>
          </button>
          <button
            onClick={handleEmbed}
            disabled={embedLoading}
            title={embedDone ? "重新生成向量并覆盖旧结果" : ""}
            className={`order-6 flex min-h-[72px] items-center gap-3 rounded-xl border border-border bg-white p-3.5 text-left transition-colors duration-150 disabled:opacity-50 sm:p-4 ${
              embedUsesFallback
                ? "border-warning/40 bg-warning-light/20 text-warning hover:bg-warning-light/30"
                : "hover:bg-hover"
            }`}
          >
            <div className={`flex h-10 w-10 items-center justify-center rounded-xl ${embedUsesFallback ? "bg-warning/15 text-warning" : "bg-sky-500/10 text-sky-500"}`}>
              {embedLoading ? <Loader2 className="h-5 w-5 animate-spin" /> : embedUsesFallback ? <AlertTriangle className="h-5 w-5" /> : embedDone ? <Check className="h-5 w-5 text-success" /> : <Cpu className="h-5 w-5" />}
            </div>
            <div className="text-left">
              <p className="text-sm font-semibold text-ink">{embedDone ? "重新向量化" : "向量化"}</p>
            </div>
          </button>
        </div>

        {/* Inline actions */}
        <div className="grid gap-2 sm:flex sm:flex-wrap">
          <label className="inline-flex min-h-11 items-center justify-between gap-2 rounded-md border border-border bg-page px-3 py-2 text-xs font-medium text-ink-secondary sm:min-h-0 sm:justify-start">
            分析详略
            <select
              value={paperAnalysisDetailLevel}
              onChange={(event) => setPaperAnalysisDetailLevel(event.target.value as AnalysisDetailLevel)}
              className="bg-transparent text-xs text-ink outline-none"
            >
              <option value="low">低</option>
              <option value="medium">中</option>
              <option value="high">高</option>
            </select>
          </label>
          <label className="inline-flex min-h-11 items-center justify-between gap-2 rounded-md border border-border bg-page px-3 py-2 text-xs font-medium text-ink-secondary sm:min-h-0 sm:justify-start">
            分析来源
            <select
              value={processingSource}
              onChange={(event) => setProcessingSource(event.target.value as PaperContentSource)}
              className="bg-transparent text-xs text-ink outline-none"
            >
              <option value="pdf">PDF</option>
              <option value="markdown">Markdown</option>
            </select>
          </label>
          <label className="inline-flex min-h-11 items-center justify-between gap-2 rounded-md border border-border bg-page px-3 py-2 text-xs font-medium text-ink-secondary sm:min-h-0 sm:justify-start">
            证据模式
            <select
              value={paperEvidenceMode}
              onChange={(event) => setPaperEvidenceMode(event.target.value as PaperEvidenceMode)}
              className="bg-transparent text-xs text-ink outline-none"
            >
              <option value="rough">粗略</option>
              <option value="full">完整</option>
            </select>
          </label>
          <button
            onClick={() => void handleProcessOcr(shouldForceOcr)}
            disabled={ocrProcessing || !canPreparePdf}
            title={!canPreparePdf ? (pdfDownloadNote || "当前论文没有可用 PDF 来源") : shouldForceOcr ? "重新执行 OCR 处理并更新 Markdown" : "执行 OCR 预处理；若缺少模型会自动下载到 MinerU 目录"}
            className="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-md border border-border bg-page px-3 py-2 text-xs font-medium text-ink-secondary transition-colors duration-150 hover:bg-hover hover:text-ink disabled:opacity-50 sm:min-h-0"
          >
            {ocrProcessing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : hasOcrMarkdown ? (
              <Check className="h-3.5 w-3.5 text-success" />
            ) : (
              <FileCode2 className="h-3.5 w-3.5" />
            )}
            {ocrProcessing ? "OCR 处理中" : shouldForceOcr ? "重新 OCR 处理" : "OCR 处理"}
          </button>
        </div>
      </div>

      {/* ========== pipeline progress ========== */}
      {skimLoading && (
        <PipelineProgress
          type="skim"
          messageOverride={skimTaskMessage || undefined}
          progressOverride={skimTaskProgress}
          onCancel={skimTaskId ? () => {
            void tasksApi.cancel(skimTaskId).then(() => {
              toast("info", "已发送粗读终止请求");
            }).catch((err) => {
              toast("error", err instanceof Error ? err.message : "终止粗读失败");
            });
          } : undefined}
        />
      )}
      {pdfPreparing && (
        <PipelineProgress
          type="pdf"
          messageOverride={pdfTaskMessage || undefined}
          progressOverride={pdfTaskProgress}
          onCancel={pdfTaskId ? () => {
            void tasksApi.cancel(pdfTaskId).then(() => {
              toast("info", "已发送 PDF 下载终止请求");
            }).catch((err) => {
              toast("error", err instanceof Error ? err.message : "终止 PDF 下载失败");
            });
          } : undefined}
        />
      )}
      {deepLoading && (
        <PipelineProgress
          type="deep"
          messageOverride={deepTaskMessage || undefined}
          progressOverride={deepTaskProgress}
          onCancel={deepTaskId ? () => {
            void tasksApi.cancel(deepTaskId).then(() => {
              toast("info", "已发送精读终止请求");
            }).catch((err) => {
              toast("error", err instanceof Error ? err.message : "终止精读失败");
            });
          } : undefined}
        />
      )}
      {analysisLoading && (
        <PipelineProgress
          type="deep"
          messageOverride={analysisTaskMessage || undefined}
          progressOverride={analysisTaskProgress}
          onCancel={analysisTaskId ? () => {
            void tasksApi.cancel(analysisTaskId).then(() => {
              toast("info", "已发送三轮分析终止请求");
            }).catch((err) => {
              toast("error", err instanceof Error ? err.message : "终止三轮分析失败");
            });
          } : undefined}
        />
      )}
      {figuresAnalyzing && (
        <PipelineProgress
          type="figure"
          messageOverride={figureTaskMessage || undefined}
          progressOverride={figureTaskProgress}
          onCancel={figureTaskId ? () => {
            void tasksApi.cancel(figureTaskId).then(() => {
              toast("info", "已发送图表任务终止请求");
            }).catch((err) => {
              toast("error", err instanceof Error ? err.message : "终止图表任务失败");
            });
          } : undefined}
        />
      )}
      {ocrProcessing && (
        <PipelineProgress
          type="ocr"
          messageOverride={ocrTaskMessage || undefined}
          progressOverride={ocrTaskProgress}
          onCancel={ocrTaskId ? () => {
            void tasksApi.cancel(ocrTaskId).then(() => {
              toast("info", "已发送 OCR 终止请求");
            }).catch((err) => {
              toast("error", err instanceof Error ? err.message : "终止 OCR 失败");
            });
          } : undefined}
        />
      )}
      {reasoningLoading && (
        <PipelineProgress
          type="reasoning"
          messageOverride={reasoningTaskMessage || undefined}
          progressOverride={reasoningTaskProgress}
          onCancel={reasoningTaskId ? () => {
            void tasksApi.cancel(reasoningTaskId).then(() => {
              toast("info", "已发送推理链终止请求");
            }).catch((err) => {
              toast("error", err instanceof Error ? err.message : "终止推理链失败");
            });
          } : undefined}
        />
      )}
      {embedLoading && (
        <PipelineProgress
          type="embed"
          messageOverride={embedTaskMessage || undefined}
          progressOverride={embedTaskProgress}
          onCancel={embedTaskId ? () => {
            void tasksApi.cancel(embedTaskId).then(() => {
              toast("info", "已发送向量化终止请求");
            }).catch((err) => {
              toast("error", err instanceof Error ? err.message : "终止向量化失败");
            });
          } : undefined}
        />
      )}

      {/* ========== report tabs ========== */}
      <div className="space-y-4">
        <Tabs
          tabs={[
            { id: "skim", label: <TabLabel label="粗读" status={skimStatus} /> },
            { id: "deep", label: <TabLabel label="精读" status={deepStatus} /> },
            { id: "reasoning", label: <TabLabel label="推理链" status={reasoningStatus} /> },
            { id: "analysis", label: <TabLabel label="三轮分析" status={analysisStatus} /> },
            { id: "figures", label: <TabLabel label="图表" status={figureStatus} /> },
            { id: "related", label: <TabLabel label="相似论文" status={relatedStatus} /> },
          ]}
          active={reportTab}
          onChange={setReportTab}
        />

        <div className="min-h-[200px]">
          {/* Tab: skim */}
          {reportTab === "skim" && (
            <div className="animate-fade-in">
              {skimLoading ? null : activeSkimReport ? (
                <Card className="rounded-2xl border-primary/20">
                  <CardHeader title="粗读报告" action={
                    (typeof activeSkimReport.relevance_score === "number" && Number.isFinite(activeSkimReport.relevance_score)) ? (
                      <div className="flex items-center gap-1.5 rounded-full bg-amber-500/10 px-3 py-1">
                        {skimReport ? <Sparkles className="h-4 w-4 text-amber-500" /> : <Star className="h-4 w-4 text-amber-500" />}
                        <span className="text-sm font-bold text-amber-600">{activeSkimReport.relevance_score.toFixed(2)}</span>
                      </div>
                    ) : null
                  } />
                  <StructuredSkimReportCard report={activeSkimReport} />
                </Card>
              ) : savedSkim ? (
                <Card className="rounded-2xl border-primary/20">
                  <CardHeader
                    title="粗读报告"
                    action={savedSkim.skim_score != null ? (
                      <div className="flex items-center gap-1.5 rounded-full bg-amber-500/10 px-3 py-1">
                        <Star className="h-4 w-4 text-amber-500" />
                        <span className="text-sm font-bold text-amber-600">{savedSkim.skim_score.toFixed(2)}</span>
                      </div>
                    ) : null}
                  />
                  <StructuredSkimReportCard
                    report={parsedSavedSkim || {
                      one_liner: "",
                      innovations: [],
                      relevance_score: savedSkim.skim_score ?? 0,
                      keywords: [],
                      title_zh: "",
                      abstract_zh: "",
                    }}
                  />
                </Card>
              ) : (
                <EmptyReport icon={<Eye className="h-8 w-8" />} label="生成粗读报告" />
              )}
            </div>
          )}

          {/* Tab: deep */}
          {reportTab === "deep" && (
            <div className="animate-fade-in">
              {deepLoading ? null : activeDeepReport ? (
                  <Card className="rounded-2xl border-blue-500/20">
                  <CardHeader
                    title="精读报告"
                    action={<ReportSourceBadge source={deepMeta?.content_source} detail={deepMeta?.content_source_detail} />}
                  />
                  <StructuredDeepReportCard
                    report={activeDeepReport}
                  />
                </Card>
              ) : savedDeep ? (
                <Card className="rounded-2xl border-blue-500/20">
                  <CardHeader
                    title="精读报告"
                    action={<ReportSourceBadge source={deepMeta?.content_source} detail={deepMeta?.content_source_detail} />}
                  />
                  <StructuredDeepReportCard
                    report={parsedSavedDeep || {
                      method_summary: "",
                      experiments_summary: "",
                      ablation_summary: "",
                      reviewer_risks: [],
                    }}
                  />
                </Card>
              ) : (
                <EmptyReport icon={<BookOpen className="h-8 w-8" />} label={paper.pdf_path ? "生成精读报告" : "PDF 未就绪"} />
              )}
            </div>
          )}

          {/* Tab: analysis */} 
          {reportTab === "analysis" && (
            <div className="animate-fade-in">
              {analysisLoading ? null : hasAnalysis && analysisRounds ? (
                <Card className="rounded-2xl border-emerald-500/20">
                  <CardHeader
                    title="论文三轮分析"
                    description="鸟瞰扫描 -> 内容理解 -> 深度分析 -> 最终结构化笔记"
                    action={<ReportSourceBadge source={analysisRounds.content_source} detail={analysisRounds.content_source_detail} />}
                  />
                  <PaperAnalysisRoundsPanel bundle={analysisRounds} figures={figures} paperId={id!} />
                </Card>
              ) : (
                <EmptyReport
                  icon={<Sparkles className="h-8 w-8" />}
                  label="暂无三轮分析"
                />
              )}
            </div>
          )}

          {/* Tab: figures */}
          {reportTab === "figures" && (
            <div className="animate-fade-in">
              {figuresAnalyzing ? null : (
                <Card className="rounded-2xl">
                  <CardHeader
                    title="图表候选"
                    description={figures.length > 0
                      ? `已提取 ${figures.length} 项，可手动勾选后分析或删除不需要的候选`
                      : "优先提取 arXiv 源图；若源图不足，会补充 OCR 结构化结果"}
                    action={paper.pdf_path ? (
                      <div className="grid w-full gap-2 sm:flex sm:flex-wrap sm:items-center sm:justify-end">
                        <Button size="sm" variant="secondary" onClick={handleExtractFigures} disabled={figuresAnalyzing}>
                          {figures.length > 0 ? "重新提取候选" : "提取候选图表"}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={handleToggleSelectAllFigures}
                          disabled={figuresAnalyzing || selectableFigureIds.length === 0}
                        >
                          {allSelectableChecked ? "取消全选" : "全选"}
                        </Button>
                        <Button
                          size="sm"
                          onClick={handleAnalyzeSelectedFigures}
                          disabled={figuresAnalyzing || selectedFigureIds.size === 0}
                        >
                          分析选中项
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={handleDeleteSelectedFigures}
                          disabled={figuresAnalyzing || selectedFigureIds.size === 0}
                        >
                          删除选中项
                        </Button>
                      </div>
                    ) : undefined}
                  />
                  {figures.length > 0 ? (
                    <div className="space-y-3">
                      <div className="flex flex-col gap-2 rounded-xl border border-border bg-page px-3 py-2 text-xs text-ink-secondary sm:flex-row sm:items-center sm:justify-between">
                        <span>已选 {selectedFigureIds.size} / {selectableFigureIds.length} 项</span>
                        {selectedFigureIds.size > 0 && (
                          <button
                            onClick={() => setSelectedFigureIds(new Set())}
                            className="text-ink-tertiary transition-colors hover:text-ink"
                          >
                            清空选择
                          </button>
                        )}
                      </div>
                      {figures.map((fig, i) => (
                        <div key={fig.id || `${fig.page_number}-${i}`} className="animate-fade-in" style={{ animationDelay: `${i * 80}ms` }}>
                          <FigureCard
                            figure={fig}
                            index={i}
                            paperId={id!}
                            selected={!!fig.id && selectedFigureIds.has(fig.id)}
                            onToggleSelected={(checked) => {
                              if (!fig.id) return;
                              setSelectedFigureIds((prev) => {
                                const next = new Set(prev);
                                if (checked) next.add(fig.id as string);
                                else next.delete(fig.id as string);
                                return next;
                              });
                            }}
                            onDelete={fig.id ? () => void handleDeleteFigure(fig.id!) : undefined}
                          />
                        </div>
                      ))}
                    </div>
                  ) : (
                    <EmptyReport
                      icon={<ImageIcon className="h-8 w-8" />}
                      label={paper.pdf_path ? "暂无图表候选" : "PDF 未就绪"}
                    />
                  )}
                </Card>
              )}
            </div>
          )}

          {/* Tab: reasoning */}
          {reportTab === "reasoning" && (
            <div className="animate-fade-in">
              {reasoningLoading ? null : hasReasoning ? (
                <Card className="rounded-2xl border-purple-500/20">
                  <CardHeader
                    title="推理链分析"
                    description="问题定义 -> 方法假设 -> 实验验证 -> 风险评估 -> 后续方向"
                    action={<ReportSourceBadge source={reasoningMeta?.content_source} detail={reasoningMeta?.content_source_detail} />}
                  />
                  <ReasoningPanel reasoning={reasoning} />
                </Card>
              ) : reasoningIncomplete ? (
                <Card className="rounded-2xl border-warning/25">
                  <CardHeader
                    title="推理链暂未就绪"
                    description={hasSkim || hasDeep
                      ? "当前保存的是早期降级结果。已有粗读或精读后，建议重新执行一次推理链。"
                      : "当前保存的是降级结果。准备更完整的论文内容后，再重新执行推理链更稳妥。"}
                  />
                </Card>
              ) : (
                <EmptyReport icon={<Brain className="h-8 w-8" />} label={paper.pdf_path ? "暂无推理链" : "PDF 未就绪"} />
              )}
            </div>
          )}

          {/* Tab: related */}
          {reportTab === "related" && (
            <div className="animate-fade-in">
              <SimilarPapersPanel
                loading={similarLoading}
                embedDone={Boolean(embedDone)}
                canPreparePdf={canPreparePdf}
                pdfDownloadNote={pdfDownloadNote}
                items={similarItems}
                ids={similarIds}
                onOpenPaper={(paperId) => navigate(`/papers/${paperId}`)}
              />
            </div>
          )}
        </div>
      </div>

      {/* PDF reader uses the backend PDF endpoint, which can serve local or remote content. */}
      {readerOpen && (paper.pdf_path || canPreparePdf) && (
        <Suspense fallback={<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"><div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" /></div>}>
          <PdfReader
            paperId={id!}
            paperTitle={paper.title}
            paperArxivId={paper.arxiv_id}
            onClose={() => setReaderOpen(false)}
          />
        </Suspense>
      )}

      <PaperTopicManagerModal
        open={topicManagerOpen}
        paper={paper}
        onClose={() => setTopicManagerOpen(false)}
        onChanged={async () => {
          await refreshPaperDetail();
        }}
      />

      <PaperMetadataEditorModal
        open={metadataEditorOpen}
        paper={paper}
        onClose={() => setMetadataEditorOpen(false)}
        onSaved={async (updated) => {
          setPaper(updated);
          setSavedSkim(updated.skim_report ?? null);
          setSavedDeep(updated.deep_report ?? null);
          setMetadataEditorOpen(false);
        }}
      />

      <PaperSourceEditorModal
        open={sourceEditorOpen}
        paper={paper}
        onClose={() => setSourceEditorOpen(false)}
        onSaved={async () => {
          setReaderOpen(false);
          await refreshPaperDetail();
        }}
      />

      <ConfirmDialog
        open={confirmDeleteOpen}
        title="删除这篇论文？"
        description="删除后会移除数据库记录、PDF 文件与分析结果，操作不可恢复。"
        confirmLabel="删除"
        variant="danger"
        onCancel={() => setConfirmDeleteOpen(false)}
        onConfirm={handleDeletePaper}
      />
    </div>
  );
}
/* ================================================================
 * Metadata editor modal
 * ================================================================ */

function PaperMetadataEditorModal({
  open,
  paper,
  onClose,
  onSaved,
}: {
  open: boolean;
  paper: Paper;
  onClose: () => void;
  onSaved: (paper: Paper) => Promise<void>;
}) {
  const { toast } = useToast();
  const [title, setTitle] = useState(paper.title);
  const [titleZh, setTitleZh] = useState(paper.title_zh || "");
  const [abstract, setAbstract] = useState(paper.abstract || "");
  const [abstractZh, setAbstractZh] = useState(paper.abstract_zh || "");
  const [keywordsText, setKeywordsText] = useState((paper.keywords || []).join(", "));
  const [autoTranslate, setAutoTranslate] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTitle(paper.title);
    setTitleZh(paper.title_zh || "");
    setAbstract(paper.abstract || "");
    setAbstractZh(paper.abstract_zh || "");
    setKeywordsText((paper.keywords || []).join(", "));
    setAutoTranslate(!(paper.title_zh || paper.abstract_zh));
  }, [open, paper]);

  const handleSubmit = async () => {
    const nextTitle = title.trim();
    if (!nextTitle) {
      toast("error", "论文标题不能为空");
      return;
    }

    setSubmitting(true);
    try {
      const updated = await paperApi.updateMetadata(paper.id, {
        title: nextTitle,
        abstract: abstract.trim(),
        keywords: parseKeywordInput(keywordsText),
        title_zh: autoTranslate && !titleZh.trim() ? undefined : titleZh.trim(),
        abstract_zh: autoTranslate && !abstractZh.trim() ? undefined : abstractZh.trim(),
        auto_translate: autoTranslate,
      });
      await onSaved(updated);
      toast("success", autoTranslate ? "题录已更新，中文标题/摘要已自动补全" : "题录已更新");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "更新题录失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="编辑论文题录" maxWidth="lg">
      <div className="space-y-4">
        <div className="rounded-xl border border-border bg-page p-4">
          <p className="text-sm font-medium text-ink">{paper.arxiv_id}</p>
        </div>

        <label className="space-y-1.5">
          <span className="text-xs font-medium text-ink-secondary">论文标题</span>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="h-11 w-full rounded-lg border border-border bg-page px-3 text-sm text-ink focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </label>

        <label className="space-y-1.5">
          <span className="text-xs font-medium text-ink-secondary">中文标题</span>
          <input
            value={titleZh}
            onChange={(e) => setTitleZh(e.target.value)}
            placeholder={autoTranslate ? "留空则自动翻译" : "可手动填写或清空"}
            className="h-11 w-full rounded-lg border border-border bg-page px-3 text-sm text-ink focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </label>

        <label className="space-y-1.5">
          <span className="text-xs font-medium text-ink-secondary">论文摘要</span>
          <textarea
            value={abstract}
            onChange={(e) => setAbstract(e.target.value)}
            rows={6}
            className="w-full rounded-lg border border-border bg-page px-3 py-2.5 text-sm leading-6 text-ink focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </label>

        <label className="space-y-1.5">
          <span className="text-xs font-medium text-ink-secondary">中文摘要</span>
          <textarea
            value={abstractZh}
            onChange={(e) => setAbstractZh(e.target.value)}
            rows={5}
            placeholder={autoTranslate ? "留空则自动翻译" : "可手动填写或清空"}
            className="w-full rounded-lg border border-border bg-page px-3 py-2.5 text-sm leading-6 text-ink focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </label>

        <label className="space-y-1.5">
          <span className="text-xs font-medium text-ink-secondary">关键词</span>
          <textarea
            value={keywordsText}
            onChange={(e) => setKeywordsText(e.target.value)}
            rows={3}
            placeholder="多个关键词请用逗号、分号或换行分隔"
            className="w-full rounded-lg border border-border bg-page px-3 py-2.5 text-sm leading-6 text-ink focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </label>

        <label className="flex items-center gap-3 rounded-xl border border-border bg-surface px-4 py-3">
          <input
            type="checkbox"
            checked={autoTranslate}
            onChange={(e) => setAutoTranslate(e.target.checked)}
            className="h-4 w-4 rounded border-border text-primary focus:ring-primary/20"
          />
          <div>
            <p className="text-sm font-medium text-ink">自动补全中文标题与中文摘要</p>
          </div>
        </label>

        <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-end">
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting ? "保存中..." : "保存题录"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/* ================================================================
 * Source editor modal
 * ================================================================ */

function PaperSourceEditorModal({
  open,
  paper,
  onClose,
  onSaved,
}: {
  open: boolean;
  paper: Paper;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const { toast } = useToast();
  const metadata = paper.metadata || {};
  const initialSourceUrl = getPaperSourceUrl(paper) || "";
  const initialPdfUrl = getPaperPdfUrl(paper);
  const initialDoi = typeof metadata.doi === "string" ? metadata.doi : "";
  const initialArxivId = isRealArxivId(paper.arxiv_id)
    ? paper.arxiv_id
    : (typeof metadata.arxiv_id === "string" ? metadata.arxiv_id : "");

  const [sourceUrl, setSourceUrl] = useState(initialSourceUrl);
  const [pdfUrl, setPdfUrl] = useState(initialPdfUrl);
  const [doi, setDoi] = useState(initialDoi);
  const [arxivId, setArxivId] = useState(initialArxivId);
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setSourceUrl(initialSourceUrl);
    setPdfUrl(initialPdfUrl);
    setDoi(initialDoi);
    setArxivId(initialArxivId);
    setFile(null);
  }, [open, initialSourceUrl, initialPdfUrl, initialDoi, initialArxivId]);

  const handleSubmit = async () => {
    const nextSourceUrl = sourceUrl.trim();
    const nextPdfUrl = pdfUrl.trim();
    const nextDoi = doi.trim();
    const nextArxivId = arxivId.trim();
    const sourceChanged = (
      nextSourceUrl !== initialSourceUrl
      || nextPdfUrl !== initialPdfUrl
      || nextDoi !== initialDoi
      || nextArxivId !== initialArxivId
    );
    if (!sourceChanged && !file) {
      onClose();
      return;
    }

    setSubmitting(true);
    try {
      let clearedLocalPdf = false;
      if (sourceChanged) {
        const result = await paperApi.updateSource(paper.id, {
          source_url: nextSourceUrl,
          pdf_url: nextPdfUrl,
          doi: nextDoi,
          arxiv_id: nextArxivId,
        });
        clearedLocalPdf = !!result.local_pdf_cleared;
      }

      if (file) {
        await paperApi.replacePdf(paper.id, file);
      }

      await onSaved();
      if (file && sourceChanged) {
        toast("success", "论文来源已更新，PDF 也已替换");
      } else if (file) {
        toast("success", "PDF 已上传并替换");
      } else if (clearedLocalPdf) {
        toast("success", "论文来源已更新，旧 PDF 已清除，请重新下载或直接阅读新来源");
      } else {
        toast("success", "论文来源已更新");
      }
      onClose();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "更新论文来源失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="修正论文来源 / PDF" maxWidth="lg">
      <div className="space-y-4">
        <div className="rounded-xl border border-border bg-page p-4">
          <p className="text-sm font-medium text-ink">{paper.title}</p>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <label className="space-y-1.5">
            <span className="text-xs font-medium text-ink-secondary">论文源链接</span>
            <input
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder="OpenAlex / DOI / Semantic Scholar / 原始页面链接"
              className="h-10 w-full rounded-lg border border-border bg-page px-3 text-sm text-ink focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
          </label>
          <label className="space-y-1.5">
            <span className="text-xs font-medium text-ink-secondary">PDF 链接</span>
            <input
              value={pdfUrl}
              onChange={(e) => setPdfUrl(e.target.value)}
              placeholder="可直接下载的 PDF URL"
              className="h-10 w-full rounded-lg border border-border bg-page px-3 text-sm text-ink focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
          </label>
          <label className="space-y-1.5">
            <span className="text-xs font-medium text-ink-secondary">arXiv 编号</span>
            <input
              value={arxivId}
              onChange={(e) => setArxivId(e.target.value)}
              placeholder="输入 arXiv ID"
              className="h-10 w-full rounded-lg border border-border bg-page px-3 text-sm text-ink focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
          </label>
          <label className="space-y-1.5">
            <span className="text-xs font-medium text-ink-secondary">DOI 链接</span>
            <input
              value={doi}
              onChange={(e) => setDoi(e.target.value)}
              placeholder="https://doi.org/..."
              className="h-10 w-full rounded-lg border border-border bg-page px-3 text-sm text-ink focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
          </label>
        </div>

        <div className="rounded-xl border border-border bg-surface p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium text-ink">替换本地 PDF</p>
            </div>
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-border bg-page px-3 py-2 text-sm text-ink-secondary transition-colors hover:border-primary/30 hover:text-ink">
              <Upload className="h-4 w-4" />
              <span>{file ? "已选择文件" : "选择 PDF 文件"}</span>
              <input
                type="file"
                accept="application/pdf,.pdf"
                className="hidden"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </label>
          </div>
          {file && (
            <p className="mt-3 text-xs text-primary">
              待上传: {file.name}
            </p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button onClick={() => void handleSubmit()} loading={submitting}>
            保存修改
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/* ================================================================
 * Folder manager modal
 * ================================================================ */

function PaperTopicManagerModal({
  open,
  paper,
  onClose,
  onChanged,
}: {
  open: boolean;
  paper: Paper;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const { toast } = useToast();
  const [topics, setTopics] = useState<Topic[]>([]);
  const [assignedTopics, setAssignedTopics] = useState<PaperTopicAssignment[]>([]);
  const [selectedTopicId, setSelectedTopicId] = useState("");
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const loadData = useCallback(async () => {
    if (!open) return;
    setLoading(true);
    try {
      const [allTopics, linkedTopics] = await Promise.all([
        topicApi.list(false, "folder"),
        paperApi.listTopics(paper.id),
      ]);
      setTopics(allTopics.items);
      setAssignedTopics(linkedTopics.items);
      setSelectedTopicId("");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "加载文件夹失败");
    } finally {
      setLoading(false);
    }
  }, [open, paper.id, toast]);

  useEffect(() => {
    if (!open) return;
    void loadData();
  }, [loadData, open]);

  const assignedIds = new Set(assignedTopics.map((topic) => topic.id));
  const availableTopics = topics.filter((topic) => !assignedIds.has(topic.id));

  const handleAssign = async () => {
    if (!selectedTopicId) {
      toast("error", "请选择一个文件夹");
      return;
    }
    setSubmitting(true);
    try {
      await paperApi.addTopic(paper.id, selectedTopicId);
      await loadData();
      await onChanged();
      toast("success", "已添加到文件夹");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "添加失败");
    } finally {
      setSubmitting(false);
    }
  };

  const handleRemove = async (topicId: string) => {
    setSubmitting(true);
    try {
      await paperApi.removeTopic(paper.id, topicId);
      await loadData();
      await onChanged();
      toast("success", "已从文件夹移除");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "移除失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="管理文件夹" maxWidth="lg">
      <div className="space-y-4">
        <div className="rounded-xl border border-border bg-page p-4">
          <p className="text-sm font-medium text-ink">{paper.title}</p>
        </div>

        {loading ? (
          <div className="py-8">
            <Spinner text="加载文件夹..." />
          </div>
        ) : (
          <>
            <div className="rounded-xl border border-border bg-surface p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <p className="text-sm font-medium text-ink">当前所在文件夹</p>
                <span className="text-xs text-ink-tertiary">{assignedTopics.length} 个</span>
              </div>
              {assignedTopics.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border px-4 py-5 text-sm text-ink-tertiary">
                  暂无文件夹
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {assignedTopics.map((topic) => (
                    <div
                      key={topic.id}
                      className="inline-flex items-center gap-2 rounded-lg bg-primary-light px-3 py-1.5 text-xs font-medium text-primary"
                    >
                      <Folder className="h-3.5 w-3.5" />
                      <span>{topic.name}</span>
                      <button
                        type="button"
                        onClick={() => void handleRemove(topic.id)}
                        className="rounded p-0.5 text-primary/70 transition-colors hover:bg-primary/10 hover:text-primary"
                        title="移出文件夹"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="rounded-xl border border-border bg-surface p-4">
              <p className="mb-3 text-sm font-medium text-ink">添加到文件夹</p>
              {topics.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border px-4 py-5 text-sm text-ink-tertiary">
                  暂无可用文件夹
                </div>
              ) : availableTopics.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border px-4 py-5 text-sm text-ink-tertiary">
                  已加入全部文件夹
                </div>
              ) : (
                <div className="flex flex-col gap-3 sm:flex-row">
                  <select
                    value={selectedTopicId}
                    onChange={(e) => setSelectedTopicId(e.target.value)}
                    className="h-10 flex-1 rounded-lg border border-border bg-page px-3 text-sm text-ink focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                  >
                    <option value="">请选择文件夹</option>
                    {availableTopics.map((topic) => (
                      <option key={topic.id} value={topic.id}>
                        {topic.name}
                      </option>
                    ))}
                  </select>
                  <Button onClick={() => void handleAssign()} loading={submitting}>
                    添加
                  </Button>
                </div>
              )}
            </div>
          </>
        )}

        <div className="flex justify-end">
          <Button variant="secondary" onClick={onClose}>关闭</Button>
        </div>
      </div>
    </Modal>
  );
}

function ReportSourceBadge({
  source,
  detail,
}: {
  source: unknown;
  detail?: string | null;
}) {
  const normalized = normalizeSavedPaperContentSource(source);
  if (!normalized) return null;
  const title = String(detail || "").trim() || `当前结果实际使用 ${getSavedPaperContentSourceLabel(source)} 证据链`;
  return (
    <span
      title={title}
      className="inline-flex items-center rounded-full border border-emerald-500/20 bg-emerald-500/6 px-3 py-1 text-xs font-medium text-emerald-700"
    >
      来源 {getSavedPaperContentSourceLabel(source)}
    </span>
  );
}

function AnalysisSectionFigureRefs({
  paperId,
  figures,
}: {
  paperId: string;
  figures: FigureAnalysisItem[];
}) {
  if (!figures.length) return null;

  return (
    <div className="mt-4 grid gap-3 lg:grid-cols-2">
      {figures.map((figure, index) => {
        const imageUrl = resolveFigurePreviewUrl(paperId, figure);
        const excerpt = getFigureReferenceExcerpt(figure);
        return (
          <div
            key={figure.id || `${figure.page_number}-${figure.image_index || index}-${figure.image_type}`}
            className="overflow-hidden rounded-2xl border border-border/80 bg-surface/82"
          >
            <div className="flex flex-wrap items-center gap-2 border-b border-border/70 px-3 py-2.5">
              <span className="rounded-full bg-page px-2 py-0.5 text-[11px] font-semibold text-ink">
                {getFigureReferenceTitle(figure)}
              </span>
              <span className="rounded-full bg-blue-500/8 px-2 py-0.5 text-[11px] font-medium text-blue-700">
                {figure.image_type === "table" ? "表格" : "图片"}
              </span>
              <span className="text-[11px] text-ink-tertiary">第 {figure.page_number} 页</span>
            </div>
            <div className="space-y-3 px-3 py-3">
              {figure.caption ? (
                <p className="text-sm font-medium leading-6 text-ink">{figure.caption}</p>
              ) : null}
              {imageUrl ? (
                <img
                  src={imageUrl}
                  alt={figure.caption || getFigureReferenceTitle(figure)}
                  className="max-h-56 w-full rounded-xl border border-border/70 object-contain bg-page/70"
                  loading="lazy"
                />
              ) : null}
              {excerpt ? (
                <p className="text-sm leading-6 text-ink-secondary">{excerpt}</p>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function PaperAnalysisRoundsPanel({
  bundle,
  figures,
  paperId,
}: {
  bundle: PaperAnalysisBundle;
  figures: FigureAnalysisItem[];
  paperId: string;
}) {
  const rounds = [
    {
      key: "round_1",
      icon: <Eye className="h-4 w-4 text-blue-500" />,
      item: bundle.round_1,
    },
    {
      key: "round_2",
      icon: <BookOpen className="h-4 w-4 text-green-500" />,
      item: bundle.round_2,
    },
    {
      key: "round_3",
      icon: <Microscope className="h-4 w-4 text-purple-500" />,
      item: bundle.round_3,
    },
    {
      key: "final_notes",
      icon: <Sparkles className="h-4 w-4 text-amber-500" />,
      item: bundle.final_notes,
    },
  ].filter((section) => normalizeMarkdown(section.item?.markdown));
  const preferredRoundKey = rounds.some((section) => section.key === "final_notes")
    ? "final_notes"
    : (rounds[0]?.key || "");
  const [selectedRoundKey, setSelectedRoundKey] = useState(preferredRoundKey);

  useEffect(() => {
    if (!rounds.some((section) => section.key === selectedRoundKey)) {
      setSelectedRoundKey(preferredRoundKey);
    }
  }, [preferredRoundKey, rounds, selectedRoundKey]);

  const activeRound = rounds.find((section) => section.key === selectedRoundKey) || rounds[0] || null;
  const detailLevelValue = String(bundle.detail_level || "").trim().toLowerCase();
  const reasoningLevelValue = String(bundle.reasoning_level || "").trim().toLowerCase();
  const evidenceModeValue = String(bundle.evidence_mode || "").trim().toLowerCase();
  const showReasoningChip = Boolean(reasoningLevelValue && reasoningLevelValue !== detailLevelValue);
  const activeRoundLayout = useMemo(
    () => (activeRound ? buildAnalysisRoundLayout(activeRound.item?.markdown || "") : null),
    [activeRound],
  );
  const summaryFigureRefs = useMemo(
    () => resolveAnalysisSectionFigures(activeRoundLayout?.summaryMarkdown || "", figures),
    [activeRoundLayout?.summaryMarkdown, figures],
  );
  const sectionFigureRefs = useMemo(() => {
    const refs = new Map<string, FigureAnalysisItem[]>();
    for (const section of activeRoundLayout?.sections || []) {
      const matched = resolveAnalysisSectionFigures(section.markdown, figures);
      if (matched.length) {
        refs.set(section.id, matched);
      }
    }
    return refs;
  }, [activeRoundLayout, figures]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-secondary">
        <span className="rounded-full border border-blue-500/20 bg-blue-500/5 px-3 py-1.5">
          详略 {getAnalysisDetailLevelLabel(bundle.detail_level)}
        </span>
        {showReasoningChip ? (
          <span className="rounded-full border border-purple-500/20 bg-purple-500/5 px-3 py-1.5">
            推理 {getReasoningLevelLabel(bundle.reasoning_level)}
          </span>
        ) : null}
        {evidenceModeValue ? (
          <span className="rounded-full border border-amber-500/20 bg-amber-500/5 px-3 py-1.5">
            证据 {getEvidenceModeLabel(bundle.evidence_mode)}
          </span>
        ) : null}
        {bundle.content_source ? (
          <ReportSourceBadge source={bundle.content_source} detail={bundle.content_source_detail} />
        ) : null}
        <span className="rounded-full border border-green-500/20 bg-green-500/5 px-3 py-1.5">
          轮次 {rounds.length}
        </span>
        {bundle.updated_at ? (
          <span className="rounded-full border border-border bg-page px-3 py-1.5 text-ink-tertiary">
            更新于 {formatDateTime(bundle.updated_at)}
          </span>
        ) : null}
      </div>

      <div className="overflow-x-auto pb-1">
        <div className="flex min-w-max gap-2">
          {rounds.map((section) => {
            const active = section.key === activeRound?.key;
            return (
              <button
                key={section.key}
                type="button"
                onClick={() => setSelectedRoundKey(section.key)}
                className={`rounded-md border px-4 py-2.5 text-left transition-colors duration-150 ${
                  active
                    ? "border-border bg-active"
                    : "border-border bg-page hover:bg-hover"
                }`}
              >
                <div className="flex items-center gap-2 text-sm font-medium text-ink">
                  {section.icon}
                  <span>{section.item?.title || "分析结果"}</span>
                  {section.key === "final_notes" ? <Badge variant="warning">建议先看</Badge> : null}
                </div>
                {section.item?.updated_at ? (
                  <div className="mt-1 text-xs text-ink-tertiary">{formatDateTime(section.item.updated_at)}</div>
                ) : null}
              </button>
            );
          })}
        </div>
      </div>

      {activeRound ? (
        <div className="rounded-xl border border-border bg-white">
          <div className="px-5 pt-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-base font-semibold text-ink">
                  {activeRound.icon}
                  <span>{activeRound.item?.title || "分析结果"}</span>
                </div>
              </div>
              <div className="text-xs text-ink-tertiary">
                {activeRound.item?.updated_at ? formatDateTime(activeRound.item.updated_at) : ""}
              </div>
            </div>
          </div>

          <div className="mx-auto max-w-[980px] px-5 py-5 sm:px-6">
            {activeRoundLayout?.summaryMarkdown ? (
              <div className="mb-5 rounded-lg border border-primary/15 bg-primary/5 px-4 py-3.5">
                <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-primary/80">
                  <Sparkles className="h-3.5 w-3.5" />
                  本轮核心结论
                </div>
                <div className="analysis-round-markdown prose prose-sm max-w-none text-[15px] leading-7 text-ink prose-p:my-2 prose-strong:text-ink">
                  <Suspense fallback={<div className="h-16 animate-pulse rounded-lg bg-page" />}>
                    <Markdown autoMath>{activeRoundLayout.summaryMarkdown}</Markdown>
                  </Suspense>
                </div>
                {summaryFigureRefs.length ? (
                  <AnalysisSectionFigureRefs paperId={paperId} figures={summaryFigureRefs} />
                ) : null}
              </div>
            ) : null}

            {activeRoundLayout?.sections.length ? (
              <div className="mb-5 flex flex-wrap gap-2">
                {activeRoundLayout.sections.map((section) => (
                  <button
                    key={section.id}
                    type="button"
                    onClick={() => document.getElementById(`analysis-round-${section.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" })}
                    className="inline-flex items-center gap-2 rounded-md border border-border bg-page px-3 py-1.5 text-xs text-ink transition-colors duration-150 hover:bg-hover"
                  >
                    {getAnalysisSectionIcon(section.label)}
                    <span className="font-medium">{section.label}</span>
                  </button>
                ))}
              </div>
            ) : null}

            {activeRoundLayout?.sections.length ? (
              <div className="space-y-3.5">
                {activeRoundLayout.sections.map((section) => (
                  <section
                    key={section.id}
                    id={`analysis-round-${section.id}`}
                    className="rounded-lg border border-border bg-page px-4 py-4"
                  >
                    <div className="mb-3 flex items-start gap-3">
                      <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-white">
                        {getAnalysisSectionIcon(section.label)}
                      </div>
                      <div className="min-w-0">
                        <h3 className="text-[15px] font-semibold text-ink">
                          {section.label}
                        </h3>
                      </div>
                    </div>
                    <div className="analysis-round-markdown prose prose-base max-w-none text-[15px] leading-8 text-ink-secondary prose-headings:text-ink prose-headings:tracking-[-0.02em] prose-h1:mb-5 prose-h1:text-[1.9rem] prose-h1:leading-tight prose-h2:mt-8 prose-h2:mb-4 prose-h2:text-[1.2rem] prose-h3:mt-6 prose-h3:mb-3 prose-h3:text-[1.03rem] prose-p:my-3 prose-p:leading-8 prose-li:my-1 prose-li:leading-8 prose-ul:my-3 prose-ol:my-3 prose-strong:text-ink prose-code:rounded prose-code:bg-slate-100 prose-code:px-1.5 prose-code:py-0.5 prose-code:text-[0.92em] prose-pre:rounded-lg prose-pre:bg-slate-950 prose-pre:px-4 prose-pre:py-4 prose-pre:text-slate-100 prose-blockquote:my-5 prose-blockquote:border-l-4 prose-blockquote:border-primary/25 prose-blockquote:bg-slate-50 prose-blockquote:px-4 prose-blockquote:py-3 prose-blockquote:text-ink-secondary">
                      <Suspense fallback={<div className="h-28 animate-pulse rounded-lg bg-surface" />}>
                        <Markdown autoMath>{section.markdown}</Markdown>
                      </Suspense>
                    </div>
                    {sectionFigureRefs.get(section.id)?.length ? (
                      <AnalysisSectionFigureRefs paperId={paperId} figures={sectionFigureRefs.get(section.id) || []} />
                    ) : null}
                  </section>
                ))}
              </div>
            ) : (
              <>
                <div className="analysis-round-markdown prose prose-base max-w-none text-[15px] leading-8 text-ink-secondary prose-headings:text-ink prose-headings:tracking-[-0.02em] prose-h1:mb-5 prose-h1:text-[1.9rem] prose-h1:leading-tight prose-h2:mt-8 prose-h2:mb-4 prose-h2:text-[1.25rem] prose-h3:mt-6 prose-h3:mb-3 prose-h3:text-[1.05rem] prose-p:my-4 prose-p:leading-8 prose-li:my-1 prose-li:leading-8 prose-ul:my-4 prose-ol:my-4 prose-strong:text-ink prose-code:rounded prose-code:bg-slate-100 prose-code:px-1.5 prose-code:py-0.5 prose-code:text-[0.92em] prose-pre:rounded-lg prose-pre:bg-slate-950 prose-pre:px-5 prose-pre:py-4 prose-pre:text-slate-100 prose-blockquote:my-6 prose-blockquote:border-l-4 prose-blockquote:border-primary/25 prose-blockquote:bg-slate-50 prose-blockquote:px-4 prose-blockquote:py-3 prose-blockquote:text-ink-secondary">
                  <Suspense fallback={<div className="h-28 animate-pulse rounded-lg bg-page" />}>
                    <Markdown autoMath>{activeRound.item!.markdown}</Markdown>
                  </Suspense>
                </div>
                {resolveAnalysisSectionFigures(activeRound.item?.markdown || "", figures).length ? (
                  <AnalysisSectionFigureRefs
                    paperId={paperId}
                    figures={resolveAnalysisSectionFigures(activeRound.item?.markdown || "", figures)}
                  />
                ) : null}
              </>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/* ================================================================
 * Empty states
 * ================================================================ */

function EmptyReport({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border bg-page/50 py-16 text-center">
      <div className="mb-3 text-ink-tertiary/50">{icon}</div>
      <p className="text-sm text-ink-tertiary">{label}</p>
    </div>
  );
}

function SimilarPapersPanel({
  loading,
  embedDone,
  canPreparePdf,
  pdfDownloadNote,
  items,
  ids,
  onOpenPaper,
}: {
  loading: boolean;
  embedDone: boolean;
  canPreparePdf: boolean;
  pdfDownloadNote: string;
  items: { id: string; title: string; arxiv_id?: string; read_status?: string }[];
  ids: string[];
  onOpenPaper: (paperId: string) => void;
}) {
  const visibleItems: { id: string; title: string; arxiv_id?: string; read_status?: string }[] = items.length > 0
    ? items
    : ids.map((id) => ({ id, title: id, arxiv_id: undefined, read_status: undefined }));
  const hasItems = visibleItems.length > 0;

  return (
    <Card className="rounded-2xl border-border/80 bg-surface/78">
      <CardHeader
        title="相似论文"
        description={
          pdfDownloadNote
            ? pdfDownloadNote
            : canPreparePdf
              ? "阅读 PDF 并完成向量化后，这里会自动刷新语义近邻。"
              : "当前论文暂时没有可用 PDF 来源，相似论文将在可向量化后自动出现。"
        }
        action={hasItems ? <Badge variant="default">{visibleItems.length}</Badge> : undefined}
      />
      {loading ? (
        <Spinner text="正在刷新相似论文..." />
      ) : hasItems ? (
        <div className="space-y-2">
          {visibleItems.map((item) => (
            <button
              key={item.id}
              onClick={() => onOpenPaper(item.id)}
              className="flex w-full items-start justify-between gap-3 rounded-xl border border-border/70 bg-page/90 px-3 py-2.5 text-left transition-colors hover:border-primary/20 hover:bg-hover"
            >
              <div className="min-w-0 flex-1">
                <p className="line-clamp-2 text-sm font-medium leading-5 text-ink">{item.title}</p>
                {item.arxiv_id ? (
                  <p className="mt-1 truncate text-[10px] text-ink-tertiary">{item.arxiv_id}</p>
                ) : null}
              </div>
              <ExternalLink className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-tertiary" />
            </button>
          ))}
        </div>
      ) : embedDone ? (
        <EmptyReport icon={<Link2 className="h-8 w-8" />} label="暂无相似论文" />
      ) : (
        <EmptyReport
          icon={<Cpu className="h-8 w-8" />}
          label={canPreparePdf ? "待向量化" : "PDF 未就绪"}
        />
      )}
    </Card>
  );
}

/* ================================================================
 * Figure cards
 * ================================================================ */

function FigureAnalysisView({ description }: { description: string }) {
  const sections = parseFigureAnalysisSections(description);
  const cards = [
    {
      key: "coreContent",
      label: "核心内容",
      icon: <Brain className="h-3.5 w-3.5 text-blue-500" />,
      content: sections.coreContent,
    },
    {
      key: "keyData",
      label: "关键数据",
      icon: <TrendingUp className="h-3.5 w-3.5 text-emerald-500" />,
      content: sections.keyData,
    },
    {
      key: "methodInterpretation",
      label: "方法解读",
      icon: <Target className="h-3.5 w-3.5 text-purple-500" />,
      content: sections.methodInterpretation,
    },
    {
      key: "academicMeaning",
      label: "学术意义",
      icon: <Lightbulb className="h-3.5 w-3.5 text-amber-500" />,
      content: sections.academicMeaning,
    },
  ].filter((card) => card.content.trim().length > 0);

  return (
    <div className="space-y-3">
      {sections.chartType && (
        <div className="rounded-xl border border-border bg-page px-3 py-2.5">
          <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">
            <FileSearch className="h-3.5 w-3.5" />
            图表类型
          </p>
          <p className="text-sm text-ink">{sections.chartType}</p>
        </div>
      )}

      {cards.length > 0 ? (
        <div className="grid gap-3 lg:grid-cols-2">
          {cards.map((card) => (
            <div key={card.key} className="rounded-xl border border-border bg-page px-3 py-2.5">
              <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold text-ink">
                {card.icon}
                {card.label}
              </p>
              <div className="prose prose-sm max-w-none text-ink-secondary dark:prose-invert">
                <Suspense fallback={<div className="h-8 animate-pulse rounded bg-surface" />}>
                  <Markdown>{card.content}</Markdown>
                </Suspense>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="prose prose-sm max-w-none text-ink-secondary dark:prose-invert">
          <Suspense fallback={<div className="h-8 animate-pulse rounded bg-surface" />}>
            <Markdown>{sections.raw}</Markdown>
          </Suspense>
        </div>
      )}
    </div>
  );
}

const TYPE_ICONS: Record<string, React.ReactNode> = {
  figure: <ImageIcon className="h-4 w-4 text-blue-500" />,
  table: <Table2 className="h-4 w-4 text-amber-500" />,
  algorithm: <FileCode2 className="h-4 w-4 text-green-500" />,
  equation: <BarChart3 className="h-4 w-4 text-purple-500" />,
};

const TYPE_LABELS: Record<string, string> = {
  figure: "图片", table: "表格", algorithm: "算法", equation: "公式",
};

function FigureCard({
  figure,
  index,
  paperId,
  selected,
  onToggleSelected,
  onDelete,
}: {
  figure: FigureAnalysisItem;
  index: number;
  paperId: string;
  selected: boolean;
  onToggleSelected: (checked: boolean) => void;
  onDelete?: () => void;
}) {
  const [expanded, setExpanded] = useState(index < 3);
  const [lightbox, setLightbox] = useState(false);
  const imgUrl = resolveFigurePreviewUrl(paperId, figure);
  const analysisText = String(figure.analysis_markdown || (figure.analyzed ? figure.description : "") || "").trim();
  const analyzed = !!figure.analyzed || !!analysisText;
  const candidateSourceLabel = (() => {
    const raw = String(figure.candidate_source || "").trim().toLowerCase();
    if (!raw) return "";
    if (raw === "arxiv_source") return "arXiv 源图";
    if (raw === "mineru_structured") return "MinerU 结构化";
    if (raw === "mineru_asset") return "MinerU 图片";
    return raw;
  })();
  const candidateSummary = buildFigureCandidateSummary(figure, candidateSourceLabel);

  return (
    <>
      <div className="overflow-hidden rounded-xl border border-border bg-surface/50 transition-all hover:border-border/80">
        <div className="flex items-center gap-3 px-4 py-3">
          <input
            type="checkbox"
            checked={selected}
            disabled={!figure.id}
            onChange={(e) => onToggleSelected(e.target.checked)}
            className="h-4 w-4 rounded border-border text-primary focus:ring-primary/20 disabled:opacity-40"
            title={figure.id ? "勾选后加入分析队列" : "当前候选不可选"}
          />
          <button onClick={() => setExpanded(!expanded)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-page">
              {TYPE_ICONS[figure.image_type] || TYPE_ICONS.figure}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-600 dark:text-blue-400">
                  {TYPE_LABELS[figure.image_type] || figure.image_type}
                </span>
                <span className={`rounded-md px-2 py-0.5 text-[10px] font-medium ${
                  analyzed
                    ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                    : "bg-amber-500/10 text-amber-600 dark:text-amber-400"
                }`}>
                  {analyzed ? "已分析" : "未分析"}
                </span>
                <span className="text-[10px] text-ink-tertiary">
                  第 {figure.page_number} 页
                </span>
                {candidateSourceLabel && (
                  <span className="text-[10px] text-ink-tertiary">
                    · {candidateSourceLabel}
                  </span>
                )}
              </div>
              {figure.caption && <p className="mt-0.5 truncate text-xs font-medium text-ink">{figure.caption}</p>}
            </div>
            {expanded ? <ChevronDown className="h-4 w-4 shrink-0 text-ink-tertiary" /> : <ChevronRight className="h-4 w-4 shrink-0 text-ink-tertiary" />}
          </button>
          {onDelete && (
            <button
              onClick={onDelete}
              className="rounded-lg p-2 text-ink-tertiary transition-colors hover:bg-error/10 hover:text-error"
              title="删除该候选"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          )}
        </div>

        {expanded && (
          <div className="border-t border-border">
            {/* Figure preview */}
            {imgUrl ? (
              <div className="flex justify-center bg-page/50 p-4 dark:bg-black/20">
                <img
                  src={imgUrl}
                  alt={figure.caption || `Figure on page ${figure.page_number}`}
                  className="max-h-[400px] max-w-full cursor-zoom-in rounded-lg object-contain shadow-sm transition-transform hover:scale-[1.02]"
                  onClick={(e) => { e.stopPropagation(); setLightbox(true); }}
                  loading="lazy"
                />
              </div>
            ) : (
              <div className="flex items-center justify-center bg-page/30 px-4 py-6 text-xs text-ink-tertiary">
                <ImageIcon className="mr-1.5 h-4 w-4" /> 暂未生成图像预览
              </div>
            )}

            {/* AI description */}
            <div className="border-t border-border/50 px-4 py-3">
              <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-primary/70">
                <Sparkles className="h-3 w-3" /> {analyzed ? "解析结果" : candidateSummary ? "候选摘要" : "待分析"}
              </div>
              {analyzed ? (
                <FigureAnalysisView description={analysisText} />
              ) : candidateSummary ? (
                <div className="rounded-xl border border-border bg-page px-3 py-2.5">
                  <p className="mb-1 text-[11px] font-semibold text-ink">候选摘要</p>
                  <p className="text-sm text-ink-secondary">{candidateSummary}</p>
                </div>
              ) : (
                <p className="text-sm text-ink-tertiary">
                  未分析
                </p>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Lightbox */}
      {lightbox && imgUrl && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80"
          onClick={() => setLightbox(false)}
        >
          <button
            className="absolute right-4 top-4 rounded-full bg-white/10 p-2 text-white transition-colors hover:bg-white/20"
            onClick={() => setLightbox(false)}
          >
            <X className="h-5 w-5" />
          </button>
          <img
            src={imgUrl}
            alt={figure.caption || ""}
            className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          />
          {figure.caption && (
            <div className="absolute bottom-6 left-1/2 max-w-xl -translate-x-1/2 rounded-lg bg-black/60 px-4 py-2 text-center text-sm text-white/90">
              {figure.caption}
            </div>
          )}
        </div>
      )}
    </>
  );
}

/* ================================================================
 * Reasoning report
 * ================================================================ */

function ReasoningPanel({ reasoning }: { reasoning: ReasoningChainResult }) {
  const steps = Array.isArray(reasoning.reasoning_steps) ? reasoning.reasoning_steps : [];
  const mc = (reasoning.method_chain && typeof reasoning.method_chain === "object")
    ? reasoning.method_chain
    : {} as Record<string, string>;
  const ec = (reasoning.experiment_chain && typeof reasoning.experiment_chain === "object")
    ? reasoning.experiment_chain
    : {} as Record<string, string>;
  const ia = (reasoning.impact_assessment && typeof reasoning.impact_assessment === "object")
    ? reasoning.impact_assessment
    : {} as Record<string, unknown>;

  const novelty = (ia.novelty_score as number) ?? 0;
  const rigor = (ia.rigor_score as number) ?? 0;
  const impact = (ia.impact_score as number) ?? 0;
  const overall = (ia.overall_assessment as string) ?? "";
  const strengths = Array.isArray(ia.strengths) ? (ia.strengths as string[]) : [];
  const weaknesses = Array.isArray(ia.weaknesses) ? (ia.weaknesses as string[]) : [];
  const suggestions = Array.isArray(ia.future_suggestions) ? (ia.future_suggestions as string[]) : [];

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <ScoreCard label="创新性" score={novelty} icon={<Zap className="h-4 w-4" />} color="text-purple-500" bg="bg-purple-500/10" />
        <ScoreCard label="严谨性" score={rigor} icon={<Target className="h-4 w-4" />} color="text-blue-500" bg="bg-blue-500/10" />
        <ScoreCard label="影响力" score={impact} icon={<TrendingUp className="h-4 w-4" />} color="text-orange-500" bg="bg-orange-500/10" />
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MiniStatCard label="推理步骤" value={`${steps.length}`} tone="purple" />
        <MiniStatCard label="方法链节点" value={`${Object.values(mc).filter(Boolean).length}`} tone="blue" />
        <MiniStatCard label="实验链节点" value={`${Object.values(ec).filter(Boolean).length}`} tone="green" />
        <MiniStatCard label="改进建议" value={`${suggestions.length}`} tone="amber" />
      </div>

      {overall && (
        <div className="rounded-xl border border-border bg-page p-4 dark:bg-page/50">
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-ink-tertiary">总体判断</p>
          <RichTextBlock
            content={overall}
            className="prose prose-sm max-w-none text-sm leading-7 text-ink-secondary prose-p:my-2 prose-p:leading-7 prose-strong:text-ink prose-code:rounded prose-code:bg-slate-100 prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.92em]"
          />
        </div>
      )}

      {steps.length > 0 && (
        <div>
          <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold text-ink">
            <Brain className="h-4 w-4 text-purple-500" /> 推理步骤
          </h4>
          <div className="space-y-2">
            {steps.map((step, i) => (
              <ReasoningStepCard key={i} step={step} index={i} />
            ))}
          </div>
        </div>
      )}

      {Object.values(mc).some(Boolean) && (
        <div>
          <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold text-ink">
            <FlaskConical className="h-4 w-4 text-blue-500" /> 方法推演链
          </h4>
          <div className="space-y-3">
            {mc.problem_definition && <ChainItem label="问题定义" text={mc.problem_definition} />}
            {mc.core_hypothesis && <ChainItem label="核心假设" text={mc.core_hypothesis} />}
            {mc.method_derivation && <ChainItem label="方法推导" text={mc.method_derivation} />}
            {mc.theoretical_basis && <ChainItem label="理论基础" text={mc.theoretical_basis} />}
            {mc.innovation_analysis && <ChainItem label="创新分析" text={mc.innovation_analysis} />}
          </div>
        </div>
      )}

      {Object.values(ec).some(Boolean) && (
        <div>
          <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold text-ink">
            <Microscope className="h-4 w-4 text-green-500" /> 实验验证链
          </h4>
          <div className="space-y-3">
            {ec.experimental_design && <ChainItem label="实验设计" text={ec.experimental_design} />}
            {ec.baseline_fairness && <ChainItem label="基线公平性" text={ec.baseline_fairness} />}
            {ec.result_validation && <ChainItem label="结果验证" text={ec.result_validation} />}
            {ec.ablation_insights && <ChainItem label="消融洞察" text={ec.ablation_insights} />}
          </div>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        {strengths.length > 0 && (
          <div>
            <h4 className="mb-2 flex items-center gap-1.5 text-sm font-medium text-ink"><ThumbsUp className="h-4 w-4 text-green-500" /> 优点</h4>
            <ul className="space-y-1.5">
              {strengths.map((s, i) => (
                <li key={i} className="flex items-start gap-2 rounded-xl bg-green-500/5 px-3 py-2.5 text-sm text-ink-secondary dark:bg-green-500/10">
                  <CheckCircle2 className="mt-1 h-3.5 w-3.5 shrink-0 text-green-500" />
                  <RichTextBlock
                    content={s}
                    className="prose prose-sm max-w-none flex-1 text-sm leading-7 text-ink-secondary prose-p:my-0 prose-p:leading-7 prose-strong:text-ink prose-code:rounded prose-code:bg-green-500/10 prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.92em]"
                  />
                </li>
              ))}
            </ul>
          </div>
        )}
        {weaknesses.length > 0 && (
          <div>
            <h4 className="mb-2 flex items-center gap-1.5 text-sm font-medium text-ink"><ThumbsDown className="h-4 w-4 text-red-500" /> 不足</h4>
            <ul className="space-y-1.5">
              {weaknesses.map((w, i) => (
                <li key={i} className="flex items-start gap-2 rounded-xl bg-red-500/5 px-3 py-2.5 text-sm text-ink-secondary dark:bg-red-500/10">
                  <AlertTriangle className="mt-1 h-3.5 w-3.5 shrink-0 text-red-500" />
                  <RichTextBlock
                    content={w}
                    className="prose prose-sm max-w-none flex-1 text-sm leading-7 text-ink-secondary prose-p:my-0 prose-p:leading-7 prose-strong:text-ink prose-code:rounded prose-code:bg-red-500/10 prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.92em]"
                  />
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {suggestions.length > 0 && (
        <div>
          <h4 className="mb-2 flex items-center gap-1.5 text-sm font-medium text-ink"><Lightbulb className="h-4 w-4 text-amber-500" /> 改进建议</h4>
          <ul className="space-y-1.5">
            {suggestions.map((f, i) => (
              <li key={i} className="flex items-start gap-2 rounded-xl bg-amber-500/5 px-3 py-2.5 text-sm text-ink-secondary dark:bg-amber-500/10">
                <Sparkles className="mt-1 h-3.5 w-3.5 shrink-0 text-amber-500" />
                <RichTextBlock
                  content={f}
                  className="prose prose-sm max-w-none flex-1 text-sm leading-7 text-ink-secondary prose-p:my-0 prose-p:leading-7 prose-strong:text-ink prose-code:rounded prose-code:bg-amber-500/10 prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.92em]"
                />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ReasoningStepCard({ step, index }: { step: { step: string; thinking: string; conclusion: string }; index: number }) {
  const [open, setOpen] = useState(index < 2);
  return (
    <div className="rounded-xl border border-border bg-surface/50 transition-all">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-3 px-4 py-3 text-left">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-purple-500/10 text-xs font-bold text-purple-500">{index + 1}</div>
        <span className="flex-1 text-sm font-medium text-ink">{step.step}</span>
        {open ? <ChevronDown className="h-4 w-4 text-ink-tertiary" /> : <ChevronRight className="h-4 w-4 text-ink-tertiary" />}
      </button>
      {open && (
        <div className="space-y-3 border-t border-border px-4 py-3">
          {step.thinking && (
            <div className="rounded-xl bg-purple-500/5 px-3 py-2.5 dark:bg-purple-500/10">
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-purple-500">思考过程</p>
              <RichTextBlock
                content={step.thinking}
                className="prose prose-sm max-w-none text-sm leading-7 text-ink-secondary prose-p:my-1.5 prose-p:leading-7 prose-strong:text-ink prose-code:rounded prose-code:bg-purple-500/10 prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.92em]"
              />
            </div>
          )}
          {step.conclusion && (
            <div className="rounded-xl bg-green-500/5 px-3 py-2.5 dark:bg-green-500/10">
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-green-500">结论</p>
              <RichTextBlock
                content={step.conclusion}
                className="prose prose-sm max-w-none text-sm leading-7 text-ink-secondary prose-p:my-1.5 prose-p:leading-7 prose-strong:text-ink prose-code:rounded prose-code:bg-green-500/10 prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.92em]"
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ScoreCard({ label, score, icon, color, bg }: { label: string; score: number; icon: React.ReactNode; color: string; bg: string }) {
  const pct = Math.round(score * 100);
  return (
    <div className="rounded-xl border border-border bg-surface p-4 text-center">
      <div className={`mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-full ${bg} ${color}`}>{icon}</div>
      <div className="text-2xl font-bold text-ink">{pct}<span className="text-sm text-ink-tertiary">%</span></div>
      <div className="mt-1 text-xs text-ink-tertiary">{label}</div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-hover">
        <div className={`h-full rounded-full transition-all duration-700 ${score > 0.7 ? "bg-green-500" : score > 0.4 ? "bg-amber-500" : "bg-red-500"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function ChainItem({ label, text }: { label: string; text: string }) {
  return (
    <div className="rounded-xl border border-border bg-surface/50 px-4 py-3">
      <p className="mb-1 text-xs font-semibold text-ink-tertiary">{label}</p>
      <RichTextBlock
        content={text}
        className="prose prose-sm max-w-none text-sm leading-7 text-ink-secondary prose-p:my-1.5 prose-p:leading-7 prose-strong:text-ink prose-code:rounded prose-code:bg-slate-100 prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.92em]"
      />
    </div>
  );
}

function ReportSection({ icon, title, content }: { icon: React.ReactNode; title: string; content: string }) {
  return (
    <div>
      <h4 className="mb-2 flex items-center gap-1.5 text-sm font-medium text-ink">{icon}{title}</h4>
      <div className="rounded-xl bg-page px-4 py-3 dark:bg-page/50">
        <RichTextBlock
          content={content}
          className="prose prose-sm max-w-none text-sm leading-7 text-ink-secondary prose-p:my-1.5 prose-p:leading-7 prose-strong:text-ink prose-code:rounded prose-code:bg-slate-100 prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.92em]"
        />
      </div>
    </div>
  );
}

function StructuredSkimReportCard({
  report,
}: {
  report: SkimReport | null;
}) {
  const innovations = report?.innovations || [];
  const oneLiner = resolveSkimOneLiner(report?.one_liner, innovations);

  return (
    <div className="space-y-5">
      <div className="rounded-xl bg-primary/5 p-4 dark:bg-primary/10">
        <div className="flex items-start gap-2">
          <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary/80">一句话结论</p>
            {oneLiner ? (
              <RichTextBlock
                content={oneLiner}
                className="prose prose-sm max-w-none text-sm font-medium leading-7 text-ink prose-p:my-0 prose-p:leading-7 prose-strong:text-ink prose-code:rounded prose-code:bg-primary/10 prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.92em]"
              />
            ) : (
              <p className="text-sm font-medium leading-7 text-ink">当前报告还没有生成一句话总结。</p>
            )}
          </div>
        </div>
      </div>

      <div>
        <h4 className="mb-2 flex items-center gap-1.5 text-sm font-medium text-ink">
          <Lightbulb className="h-4 w-4 text-amber-500" /> 创新点
        </h4>
        {innovations.length > 0 ? (
          <ul className="space-y-1.5">
            {innovations.map((item, index) => (
              <li key={`${item}-${index}`} className="flex items-start gap-2 rounded-xl bg-page px-3 py-2.5 text-sm text-ink-secondary">
                <CheckCircle2 className="mt-1 h-3.5 w-3.5 shrink-0 text-success" />
                <RichTextBlock
                  content={item}
                  className="prose prose-sm max-w-none flex-1 text-sm leading-7 text-ink-secondary prose-p:my-0 prose-p:leading-7 prose-strong:text-ink prose-code:rounded prose-code:bg-amber-500/10 prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.92em]"
                />
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-tertiary">当前报告未提取出单独的创新点列表。</p>
        )}
      </div>
    </div>
  );
}

function StructuredDeepReportCard({
  report,
}: {
  report: DeepDiveReport | null;
}) {
  const sections = [
    {
      key: "method",
      icon: <FlaskConical className="h-4 w-4 text-blue-500" />,
      title: "方法总结",
      content: report?.method_summary || "",
    },
    {
      key: "experiment",
      icon: <Microscope className="h-4 w-4 text-success" />,
      title: "实验总结",
      content: report?.experiments_summary || "",
    },
    {
      key: "ablation",
      icon: <Sparkles className="h-4 w-4 text-amber-500" />,
      title: "消融实验",
      content: report?.ablation_summary || "",
    },
  ].filter((item) => item.content.trim());

  return (
    <div className="space-y-5">
      {sections.length > 0 ? (
        <div className="space-y-4">
          {sections.map((section) => (
            <div key={section.key} className="rounded-xl border border-border bg-surface/50 p-4">
              <h4 className="mb-2 flex items-center gap-1.5 text-sm font-medium text-ink">
                {section.icon}
                {section.title}
              </h4>
              <RichTextBlock
                content={section.content}
                className="prose prose-sm max-w-none text-sm leading-7 text-ink-secondary prose-p:my-1.5 prose-p:leading-7 prose-ul:my-2 prose-ol:my-2 prose-li:my-1 prose-li:leading-7 prose-strong:text-ink prose-code:rounded prose-code:bg-slate-100 prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.92em]"
              />
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-xl border border-dashed border-border bg-page px-4 py-4 text-sm text-ink-tertiary">
          当前精读结果还没有拆出结构化的章节内容。
        </div>
      )}

      {(report?.reviewer_risks || []).length > 0 && (
        <div>
          <h4 className="mb-2 flex items-center gap-1.5 text-sm font-medium text-ink">
            <Shield className="h-4 w-4 text-red-500" /> 审稿风险
          </h4>
          <ul className="space-y-1.5">
            {(report?.reviewer_risks || []).map((risk, index) => (
              <li key={`${risk}-${index}`} className="flex items-start gap-2 rounded-xl bg-red-500/5 px-3 py-2.5 text-sm text-ink-secondary dark:bg-red-500/10">
                <AlertTriangle className="mt-1 h-3.5 w-3.5 shrink-0 text-red-500" />
                <RichTextBlock
                  content={risk}
                  className="prose prose-sm max-w-none flex-1 text-sm leading-7 text-ink-secondary prose-p:my-0 prose-p:leading-7 prose-strong:text-ink prose-code:rounded prose-code:bg-red-500/10 prose-code:px-1 prose-code:py-0.5 prose-code:text-[0.92em]"
                />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function MiniStatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "purple" | "blue" | "green" | "amber";
}) {
  const toneClass = {
    purple: "border-purple-500/20 bg-purple-500/5 text-purple-600",
    blue: "border-blue-500/20 bg-blue-500/5 text-blue-600",
    green: "border-green-500/20 bg-green-500/5 text-green-600",
    amber: "border-amber-500/20 bg-amber-500/5 text-amber-600",
  }[tone];

  return (
    <div className={`rounded-xl border px-4 py-3 ${toneClass}`}>
      <p className="text-[11px] font-semibold uppercase tracking-[0.16em]">{label}</p>
      <p className="mt-2 text-2xl font-bold">{value}</p>
    </div>
  );
}
