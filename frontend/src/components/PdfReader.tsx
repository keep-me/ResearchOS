import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode, type TouchEvent as ReactTouchEvent } from "react";
import { Document, Page } from "@/components/PdfDocument";
import { useSignedApiAssetUrl } from "@/hooks/useSignedApiAssetUrl";
import Markdown from "@/components/Markdown";
import { timeAgo } from "@/lib/utils";
import { cn } from "@/lib/utils";
import { paperApi, tasksApi, type PaperOcrStatus, type TaskStatus } from "@/services/api";
import type {
  PaperReaderAction,
  PaperReaderDocumentBlock,
  PaperReaderDocumentResponse,
  PaperReaderNote,
  PaperReaderQueryResponse,
  PaperReaderScope,
} from "@/types";
import {
  BookOpen,
  Check,
  Copy,
  FileText,
  Highlighter,
  Image as ImageIcon,
  Languages,
  Loader2,
  Maximize2,
  MessageSquareText,
  Minimize2,
  PencilLine,
  Pin,
  RefreshCw,
  ScanSearch,
  SendHorizontal,
  Sparkles,
  Trash2,
  Wand2,
  X,
  ZoomIn,
  ZoomOut,
} from "@/lib/lucide";

interface PdfReaderProps {
  paperId: string;
  paperTitle: string;
  paperArxivId?: string;
  onOcrUpdated?: () => void | Promise<void>;
  onClose: () => void;
}

interface ReaderResult extends PaperReaderQueryResponse {
  id: string;
  label: string;
  excerpt: string;
}

interface RegionRect {
  page: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

interface RegionSelection extends RegionRect {
  imageBase64: string;
  previewUrl: string;
}

interface RegionDrag extends RegionRect {
  pageLeft: number;
  pageTop: number;
  pageWidth: number;
  pageHeight: number;
  originX: number;
  originY: number;
}

type ReaderWorkspaceTab = "pdf" | "ocr" | "notes" | "results";
type NoteColor = PaperReaderNote["color"];

interface NoteEditorState {
  id?: string;
  kind: "text";
  title: string;
  content: string;
  quote: string;
  page_number?: number | null;
  color: NoteColor;
  tags: string[];
  pinned: boolean;
  source: PaperReaderNote["source"];
  anchor_source?: PaperReaderNote["anchor_source"] | null;
  anchor_id?: string | null;
  section_id?: string | null;
  section_title?: string | null;
}

interface SelectionAnchor {
  anchor_source: "pdf_selection" | "ocr_block";
  anchor_id?: string | null;
  section_id?: string | null;
  section_title?: string | null;
  page_number?: number | null;
  matchedBlock?: PaperReaderDocumentBlock | null;
}

interface PdfNoteMarker {
  key: string;
  page: number;
  count: number;
  block: PaperReaderDocumentBlock | null;
}

const ACTION_LABEL: Record<PaperReaderAction, string> = {
  analyze: "分析",
  explain: "分析",
  translate: "翻译",
  summarize: "分析",
  ask: "问答",
};

const NOTE_COLORS: NoteColor[] = ["amber", "blue", "emerald", "rose", "violet", "slate"];
const NOTE_COLOR_LABEL: Record<NoteColor, string> = {
  amber: "黄",
  blue: "蓝",
  emerald: "绿",
  rose: "红",
  violet: "紫",
  slate: "灰",
};

const NOTE_COLOR_BUTTON_CLASS: Record<NoteColor, string> = {
  amber: "border-amber-300/65 bg-amber-500/85 text-white",
  blue: "border-sky-300/65 bg-sky-500/85 text-white",
  emerald: "border-emerald-300/65 bg-emerald-500/85 text-white",
  rose: "border-rose-300/65 bg-rose-500/85 text-white",
  violet: "border-violet-300/65 bg-violet-500/85 text-white",
  slate: "border-slate-300/65 bg-slate-500/85 text-white",
};

const NOTE_COLOR_BADGE_CLASS: Record<NoteColor, string> = {
  amber: "border-amber-400/30 bg-amber-500/10",
  blue: "border-sky-400/30 bg-sky-500/10",
  emerald: "border-emerald-400/30 bg-emerald-500/10",
  rose: "border-rose-400/30 bg-rose-500/10",
  violet: "border-violet-400/30 bg-violet-500/10",
  slate: "border-slate-400/30 bg-slate-500/10",
};

const READER_PANEL_MIN_WIDTH = 360;
const READER_PANEL_MAX_WIDTH = 760;
const MOBILE_WORKSPACE_TABBAR_HEIGHT = 72;
const MOBILE_TOPBAR_HEIGHT = 44;

const DOCUMENT_BLOCK_LABEL: Record<PaperReaderDocumentBlock["type"], string> = {
  heading: "标题",
  text: "正文",
  aside_text: "旁注",
  list: "列表",
  equation: "公式",
  image: "图片",
  table: "表格",
};

function clamp(v: number, min: number, max: number) {
  return Math.min(Math.max(v, min), max);
}

function touchDistance(event: ReactTouchEvent<HTMLElement>) {
  if (event.touches.length < 2) return 0;
  const [first, second] = [event.touches[0], event.touches[1]];
  return Math.hypot(second.clientX - first.clientX, second.clientY - first.clientY);
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function normalizeText(value: string) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function normalizeMatchKey(value: string) {
  return normalizeText(value).toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]+/g, " ").trim();
}

function preview(value: string, max = 120) {
  const text = normalizeText(value);
  return text.length > max ? `${text.slice(0, max).trim()}...` : text;
}

function getSelectionPage(node: Node | null, container: HTMLElement | null) {
  if (!node || !container) return null;
  const element = node instanceof Element ? node : node.parentElement;
  if (!element || !container.contains(element)) return null;
  const page = Number(element.closest("[data-page]")?.getAttribute("data-page") || "");
  return Number.isFinite(page) && page > 0 ? page : null;
}

function resultLabel(item: PaperReaderQueryResponse) {
  const prefix = item.scope === "paper" ? "全文" : item.scope === "selection" ? "选区" : item.figure_id ? "图表" : "区域";
  return `${prefix}${ACTION_LABEL[item.action]}`;
}

function buildNoteEditorState(seed?: Partial<PaperReaderNote> & {
  quote?: string | null;
  page_number?: number | null;
  source?: PaperReaderNote["source"];
  anchor_source?: PaperReaderNote["anchor_source"] | null;
  anchor_id?: string | null;
  section_id?: string | null;
  section_title?: string | null;
}): NoteEditorState {
  return {
    id: seed?.id,
    kind: "text",
    title: String(seed?.title || "").trim(),
    content: String(seed?.content || "").trim(),
    quote: String(seed?.quote || "").trim(),
    page_number: seed?.page_number ?? null,
    color: (seed?.color || "amber") as NoteColor,
    tags: Array.isArray(seed?.tags) ? seed?.tags : [],
    pinned: Boolean(seed?.pinned),
    source: (seed?.source || "manual") as PaperReaderNote["source"],
    anchor_source: seed?.anchor_source ?? null,
    anchor_id: seed?.anchor_id ?? null,
    section_id: seed?.section_id ?? null,
    section_title: seed?.section_title ?? null,
  };
}

function buildSectionLabel(note: Pick<PaperReaderNote, "section_title" | "page_number" | "anchor_source">) {
  if (note.section_title) return note.section_title;
  if (note.anchor_source === "pdf_selection" && note.page_number) return `第 ${note.page_number} 页选区`;
  if (note.page_number) return `第 ${note.page_number} 页`;
  return "未定位";
}

function isBlockAnnotatable(block: PaperReaderDocumentBlock, sectionTitle?: string | null) {
  if (block.type === "image" || block.type === "table") return false;
  if (block.type === "heading" || block.type === "aside_text") return false;
  const text = normalizeText(block.text);
  if (!text) return false;
  if (looksLikeMetadataBlock(text, block.page_number, sectionTitle)) return false;
  return true;
}

function canLinkBlockToPdf(block: PaperReaderDocumentBlock) {
  return Boolean(block.page_number && block.bbox);
}

function getPdfOverlayStyle(block: PaperReaderDocumentBlock | null, canvasEl?: HTMLCanvasElement | null) {
  const bbox = block?.bbox;
  if (!block || !bbox) return null;
  if (block.bbox_normalized) {
    if (canvasEl) {
      const width = canvasEl.clientWidth || 0;
      const height = canvasEl.clientHeight || 0;
      if (width > 0 && height > 0) {
        return {
          left: `${(bbox.x0 / 1000) * width}px`,
          top: `${(bbox.y0 / 1000) * height}px`,
          width: `${(bbox.width / 1000) * width}px`,
          height: `${(bbox.height / 1000) * height}px`,
        };
      }
    }
    return {
      left: `${bbox.x0 / 10}%`,
      top: `${bbox.y0 / 10}%`,
      width: `${bbox.width / 10}%`,
      height: `${bbox.height / 10}%`,
    };
  }
  return {
    left: bbox.x,
    top: bbox.y,
    width: bbox.width,
    height: bbox.height,
  };
}

function getPdfSideMarkerStyle(block: PaperReaderDocumentBlock | null, offsetPx = 18, canvasEl?: HTMLCanvasElement | null) {
  const bbox = block?.bbox;
  if (!block || !bbox) return null;
  if (block.bbox_normalized) {
    if (canvasEl) {
      const width = canvasEl.clientWidth || 0;
      const height = canvasEl.clientHeight || 0;
      if (width > 0 && height > 0) {
        return {
          left: `${width + offsetPx}px`,
          top: `${(bbox.y0 / 1000) * height + ((bbox.height / 1000) * height) / 2}px`,
          transform: "translateY(-50%)",
        };
      }
    }
    const top = clamp(bbox.y0 + (bbox.height / 2), 22, 978) / 10;
    return {
      left: `calc(100% + ${offsetPx}px)`,
      top: `${top}%`,
      transform: "translateY(-50%)",
    };
  }
  return {
    left: `calc(100% + ${offsetPx}px)`,
    top: clamp(bbox.y + (bbox.height / 2), 18, 99999),
    transform: "translateY(-50%)",
  };
}

function getPdfPageSideMarkerStyle(index: number, offsetPx = 14) {
  return {
    left: `calc(100% + ${offsetPx}px)`,
    top: `${1.25 + (index * 2.25)}rem`,
  };
}

function looksLikeMetadataBlock(text: string, pageNumber?: number | null, sectionTitle?: string | null) {
  const normalized = normalizeText(text);
  if (!normalized) return true;
  const lower = normalized.toLowerCase();
  const inFrontMatter = (sectionTitle || "").trim() === "前置信息" || (pageNumber || 0) <= 2;

  if (/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i.test(normalized)) return true;
  if (/^(?:https?:\/\/|www\.)/i.test(normalized)) return true;

  if (!inFrontMatter) return false;

  if (
    /(university|institute|department|laboratory|school of|research center|corresponding author|equal contribution|project page|anonymous|affiliation)/i.test(lower)
  ) {
    return true;
  }

  if (!/[.!?;:。！？；：]/.test(normalized) && normalized.length <= 90) {
    return true;
  }

  return false;
}

function buildRenderableBlockMarkdown(block: PaperReaderDocumentBlock) {
  const raw = String(block.markdown || block.text || "").trim();
  if (block.type !== "equation") return raw;

  const stripped = raw.replace(/^\$\$?/, "").replace(/\$\$?$/, "").trim();
  if (!stripped) return raw;
  return `$$\n${stripped}\n$$`;
}

function pickPdfLinkedBlock(
  page: number,
  bounds: DOMRect,
  clientX: number,
  clientY: number,
  blocks: PaperReaderDocumentBlock[],
) {
  const localX = clientX - bounds.left;
  const localY = clientY - bounds.top;
  if (localX < 0 || localY < 0 || localX > bounds.width || localY > bounds.height) return null;

  let best: PaperReaderDocumentBlock | null = null;
  let bestArea = Number.POSITIVE_INFINITY;
  for (const block of blocks) {
    if (block.page_number !== page || !block.bbox) continue;
    const bbox = block.bbox;
    const pointX = block.bbox_normalized ? (localX / bounds.width) * 1000 : localX;
    const pointY = block.bbox_normalized ? (localY / bounds.height) * 1000 : localY;
    if (pointX < bbox.x0 || pointX > bbox.x1 || pointY < bbox.y0 || pointY > bbox.y1) continue;
    const area = Math.max(1, bbox.width * bbox.height);
    if (area < bestArea) {
      best = block;
      bestArea = area;
    }
  }
  return best;
}

function findMatchingDocumentBlock(
  selectionText: string,
  selectedPage: number | null,
  blocks: PaperReaderDocumentBlock[],
) {
  const selectionKey = normalizeMatchKey(selectionText);
  if (!selectionKey || selectionKey.length < 3) return null;
  const tokens = selectionKey.split(" ").filter(Boolean);
  let best: PaperReaderDocumentBlock | null = null;
  let bestScore = 0;

  for (const block of blocks) {
    if (block.type === "heading") continue;
    const blockKey = normalizeMatchKey(block.text);
    if (!blockKey) continue;

    let score = 0;
    if (selectedPage && block.page_number === selectedPage) score += 3;
    else if (selectedPage && block.page_number && Math.abs(block.page_number - selectedPage) === 1) score += 1;

    if (blockKey.includes(selectionKey) || selectionKey.includes(blockKey)) score += 6;
    if (tokens.length > 0) {
      let overlap = 0;
      for (const token of tokens) {
        if (blockKey.includes(token)) overlap += 1;
      }
      score += (overlap / tokens.length) * 4;
    }

    if (score > bestScore) {
      bestScore = score;
      best = block;
    }
  }

  return bestScore >= 2.4 ? best : null;
}

function ActionButton({
  onClick,
  disabled,
  children,
  tone = "default",
}: {
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
  tone?: "default" | "primary";
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "rounded-lg px-2.5 py-2 text-[11px] transition-colors disabled:opacity-50 sm:px-3 sm:text-xs",
        tone === "primary" ? "bg-primary/20 text-primary hover:bg-primary/30" : "bg-surface/72 text-ink-secondary hover:bg-hover",
      )}
    >
      {children}
    </button>
  );
}

function TabButton({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex min-w-0 items-center justify-center gap-1 rounded-xl px-2 py-1.5 text-[11px] transition-colors sm:gap-1.5 sm:px-3 sm:py-2 sm:text-xs",
        active ? "bg-primary/18 text-primary" : "text-ink-secondary hover:bg-hover hover:text-ink",
      )}
    >
      {icon}
      <span className="truncate">{label}</span>
    </button>
  );
}

function NoteEditorCard({
  note,
  busy,
  onChange,
  onSave,
  onCancel,
}: {
  note: NoteEditorState;
  busy: boolean;
  onChange: (patch: Partial<NoteEditorState>) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="rounded-2xl border border-primary/25 bg-primary/5 p-3 sm:p-3.5">
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm text-ink">{note.id ? "编辑笔记" : note.source === "ai_draft" ? "AI 草稿" : "新建笔记"}</div>
        <button onClick={onCancel} className="rounded-md p-1 text-ink-tertiary transition hover:bg-hover hover:text-ink-secondary">
          <X className="h-4 w-4" />
        </button>
      </div>

      {note.section_title || note.page_number ? (
        <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-ink-tertiary">
          {note.section_title ? <span>{note.section_title}</span> : null}
          {note.page_number ? <span>第 {note.page_number} 页</span> : null}
          {note.anchor_source === "ocr_block" ? <span>OCR 段落</span> : note.anchor_source === "pdf_selection" ? <span>PDF 选区</span> : null}
        </div>
      ) : null}
      <textarea
        value={note.content}
        onChange={(event) => onChange({ content: event.target.value })}
        placeholder="记录方法、证据、限制或待核实点"
        className="mt-3 min-h-[112px] w-full resize-y rounded-xl border border-border bg-page/72 px-3 py-2.5 text-sm leading-6 text-ink outline-none placeholder:text-ink-placeholder sm:min-h-[140px]"
      />

      <div className="mt-3 flex flex-wrap gap-2">
        {NOTE_COLORS.map((color) => (
          <button
            key={color}
            onClick={() => onChange({ color })}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold transition-all",
              NOTE_COLOR_BUTTON_CLASS[color],
              note.color === color ? "scale-[1.02] ring-2 ring-primary/60" : "opacity-78 hover:opacity-100",
            )}
          >
            {note.color === color ? <Check className="h-3.5 w-3.5" /> : <span className="h-3.5 w-3.5 rounded-full border border-white/40 bg-white/20" />}
            {NOTE_COLOR_LABEL[color]}
          </button>
        ))}
        <button
          onClick={() => onChange({ pinned: !note.pinned })}
          className={cn(
            "rounded-full border px-2.5 py-1 text-[11px] transition-colors",
            note.pinned ? "border-primary/40 bg-primary/12 text-primary" : "border-border bg-page/72 text-ink-tertiary hover:text-ink-secondary",
          )}
        >
          <span className="inline-flex items-center gap-1">
            <Pin className="h-3 w-3" />
            {note.pinned ? "已置顶" : "置顶"}
          </span>
        </button>
      </div>

      <div className="mt-4 flex justify-end gap-2">
        <ActionButton onClick={onCancel}>取消</ActionButton>
        <ActionButton onClick={onSave} disabled={busy || !note.content.trim()} tone="primary">
          <span className="inline-flex items-center gap-2">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            保存笔记
          </span>
        </ActionButton>
      </div>
    </div>
  );
}

function SavedNoteCard({
  note,
  onLocate,
  onEdit,
  onDelete,
  onTogglePin,
  active = false,
  compact = false,
}: {
  note: PaperReaderNote;
  onLocate?: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onTogglePin: () => void;
  active?: boolean;
  compact?: boolean;
}) {
  return (
    <div
      role={onLocate ? "button" : undefined}
      tabIndex={onLocate ? 0 : undefined}
      onClick={onLocate}
      onKeyDown={(event) => {
        if (!onLocate) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onLocate();
        }
      }}
      className={cn(
        "rounded-xl border px-3 py-3",
        NOTE_COLOR_BADGE_CLASS[note.color],
        compact ? "bg-page/72" : "bg-page/80",
        active && "border-primary/40 bg-primary/8 shadow-[0_0_0_1px_rgba(37,99,235,0.18)]",
        onLocate && "cursor-pointer transition-colors hover:border-primary/35 hover:bg-primary/6",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-tertiary">
            <span>{buildSectionLabel(note)}</span>
            <span>{timeAgo(note.updated_at)}</span>
            {note.pinned ? <span className="rounded-full border border-primary/25 bg-primary/10 px-2 py-0.5 text-[10px] text-primary">置顶</span> : null}
            {note.source === "ai_draft" ? <span className="rounded-full border border-primary/20 bg-primary/10 px-2 py-0.5 text-[10px] text-primary">AI 草稿保存</span> : null}
            {note.tags.slice(0, 4).map((tag) => (
              <span key={`${note.id}-${tag}`} className="rounded-full border border-border/60 bg-page/72 px-2 py-0.5 text-[10px] text-ink-secondary">
                {tag}
              </span>
            ))}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button onClick={(event) => { event.stopPropagation(); onTogglePin(); }} className="rounded-md p-1.5 text-ink-tertiary transition hover:bg-hover hover:text-ink-secondary">
            <Pin className="h-3.5 w-3.5" />
          </button>
          <button onClick={(event) => { event.stopPropagation(); onEdit(); }} className="rounded-md p-1.5 text-ink-tertiary transition hover:bg-hover hover:text-ink-secondary">
            <PencilLine className="h-3.5 w-3.5" />
          </button>
          <button onClick={(event) => { event.stopPropagation(); onDelete(); }} className="rounded-md p-1.5 text-ink-tertiary transition hover:bg-red-500/10 hover:text-red-300">
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      <div className="mt-3 max-w-none text-sm">
        <Markdown className="pdf-ai-markdown">{note.content}</Markdown>
      </div>
    </div>
  );
}

export default function PdfReader({ paperId, paperTitle, paperArxivId, onOcrUpdated, onClose }: PdfReaderProps) {
  const [numPages, setNumPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [scale, setScale] = useState(1.2);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const [isMobileViewport, setIsMobileViewport] = useState(false);
  const [activeTab, setActiveTab] = useState<ReaderWorkspaceTab>("ocr");

  const [selectedText, setSelectedText] = useState("");
  const [selectedPage, setSelectedPage] = useState<number | null>(null);
  const [paperQuestion, setPaperQuestion] = useState("");
  const [selectionQuestion, setSelectionQuestion] = useState("");
  const [regionQuestion, setRegionQuestion] = useState("");

  const [results, setResults] = useState<ReaderResult[]>([]);
  const [loadingLabel, setLoadingLabel] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const [readerDocument, setReaderDocument] = useState<PaperReaderDocumentResponse | null>(null);
  const [documentLoading, setDocumentLoading] = useState(false);
  const [ocrStatus, setOcrStatus] = useState<PaperOcrStatus | null>(null);
  const [ocrTaskLabel, setOcrTaskLabel] = useState<string | null>(null);
  const [ocrHoveredBlockId, setOcrHoveredBlockId] = useState<string | null>(null);
  const [pdfHoveredBlockId, setPdfHoveredBlockId] = useState<string | null>(null);
  const [linkedBlockId, setLinkedBlockId] = useState<string | null>(null);

  const [notes, setNotes] = useState<PaperReaderNote[]>([]);
  const [notesLoading, setNotesLoading] = useState(false);
  const [noteEditor, setNoteEditor] = useState<NoteEditorState | null>(null);
  const [noteBusyLabel, setNoteBusyLabel] = useState<string | null>(null);

  const [regionMode, setRegionMode] = useState(false);
  const [regionDrag, setRegionDrag] = useState<RegionDrag | null>(null);
  const [regionSelection, setRegionSelection] = useState<RegionSelection | null>(null);

  const [pageInput, setPageInput] = useState("");
  const [workspaceWidth, setWorkspaceWidth] = useState(440);
  const [activeNoteId, setActiveNoteId] = useState<string | null>(null);
  const [mobileViewportHeight, setMobileViewportHeight] = useState(0);
  const [mobileSheetHeight, setMobileSheetHeight] = useState(0);
  const [mobileSheetDragging, setMobileSheetDragging] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const blockCardRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const noteCardRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const pinchStateRef = useRef<{ distance: number; scale: number } | null>(null);
  const sidebarResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const mobileSheetDragRef = useRef<{ startY: number; startHeight: number; lastHeight: number } | null>(null);
  const mobileSheetRef = useRef<HTMLDivElement>(null);
  const mobileSheetRafRef = useRef<number | null>(null);
  const mobileSheetVisualHeightRef = useRef(0);
  const autoOcrAttemptedRef = useRef(false);
  const ocrRunRef = useRef(false);

  const basePdfUrl = useMemo(() => paperApi.pdfUrl(paperId, paperArxivId), [paperArxivId, paperId]);
  const pdfUrl = useSignedApiAssetUrl(basePdfUrl);
  const pages = useMemo(() => Array.from({ length: numPages }, (_, i) => i + 1), [numPages]);
  const sectionsById = useMemo(() => {
    const map = new Map<string, { title: string; level: number; order: number; page_start?: number | null }>();
    for (const section of readerDocument?.sections || []) {
      map.set(section.id, section);
    }
    return map;
  }, [readerDocument]);
  const groupedDocumentSections = useMemo(() => {
    const blocksBySection = new Map<string, PaperReaderDocumentBlock[]>();
    for (const block of readerDocument?.blocks || []) {
      const list = blocksBySection.get(block.section_id) || [];
      list.push(block);
      blocksBySection.set(block.section_id, list);
    }
    return (readerDocument?.sections || []).map((section) => ({
      section,
      blocks: blocksBySection.get(section.id) || [],
    }));
  }, [readerDocument]);
  const documentBlocks = readerDocument?.blocks || [];
  const blocksById = useMemo(() => {
    const map = new Map<string, PaperReaderDocumentBlock>();
    for (const block of documentBlocks) {
      map.set(block.id, block);
    }
    return map;
  }, [documentBlocks]);
  const inlineEditorBlockId = noteEditor?.anchor_source === "ocr_block" ? noteEditor.anchor_id || null : null;
  const activePdfLinkedBlockId = ocrHoveredBlockId || pdfHoveredBlockId || linkedBlockId || inlineEditorBlockId || null;
  const activePdfLinkedBlock = useMemo(
    () => documentBlocks.find((block) => block.id === activePdfLinkedBlockId) || null,
    [activePdfLinkedBlockId, documentBlocks],
  );
  const blockNotes = useMemo(() => {
    const map = new Map<string, PaperReaderNote[]>();
    for (const note of notes) {
      if (note.anchor_source !== "ocr_block" || !note.anchor_id) continue;
      const list = map.get(note.anchor_id) || [];
      list.push(note);
      map.set(note.anchor_id, list);
    }
    return map;
  }, [notes]);
  const anchoredNotesByBlockId = useMemo(() => {
    const map = new Map<string, PaperReaderNote[]>();
    for (const note of notes) {
      if (!note.anchor_id) continue;
      const list = map.get(note.anchor_id) || [];
      list.push(note);
      map.set(note.anchor_id, list);
    }
    return map;
  }, [notes]);
  const notesBySection = useMemo(() => {
    const groups = new Map<string, { key: string; title: string; notes: PaperReaderNote[]; order: number }>();
    for (const note of notes) {
      const section = note.section_id ? sectionsById.get(note.section_id) : null;
      const key = note.section_id || (note.page_number ? `page-${note.page_number}` : "unassigned");
      if (!groups.has(key)) {
        groups.set(key, {
          key,
          title: note.section_title || section?.title || buildSectionLabel(note),
          notes: [],
          order: section?.order ?? (note.page_number ?? 9999),
        });
      }
      groups.get(key)?.notes.push(note);
    }
    return Array.from(groups.values()).sort((a, b) => a.order - b.order);
  }, [notes, sectionsById]);
  const pdfNoteMarkersByPage = useMemo(() => {
    const anchored = new Map<number, Map<string, PdfNoteMarker>>();
    const fallback = new Map<number, number>();

    for (const note of notes) {
      let block = note.anchor_id ? blocksById.get(note.anchor_id) || null : null;
      if (!block && note.anchor_source === "pdf_selection") {
        const sourceText = normalizeText(note.quote || note.content || "");
        if (sourceText) {
          block = findMatchingDocumentBlock(sourceText, note.page_number ?? null, documentBlocks);
        }
      }

      const page = block?.page_number ?? note.page_number ?? null;
      if (!page) continue;

      if (block?.bbox) {
        const pageMap = anchored.get(page) || new Map<string, PdfNoteMarker>();
        const existing = pageMap.get(block.id);
        if (existing) {
          existing.count += 1;
        } else {
          pageMap.set(block.id, {
            key: `marker-${page}-${block.id}`,
            page,
            count: 1,
            block,
          });
        }
        anchored.set(page, pageMap);
        continue;
      }

      fallback.set(page, (fallback.get(page) || 0) + 1);
    }

    return {
      anchored: new Map(
        Array.from(anchored.entries()).map(([page, markers]) => [
          page,
          Array.from(markers.values()).sort((left, right) => {
            const leftY = left.block?.bbox?.y0 ?? 0;
            const rightY = right.block?.bbox?.y0 ?? 0;
            return leftY - rightY;
          }),
        ]),
      ),
      fallback,
    };
  }, [blocksById, documentBlocks, notes]);
  const inlinePdfSelectionEditor = Boolean(
    noteEditor
    && noteEditor.anchor_source === "pdf_selection"
    && (!noteEditor.id || noteEditor.source === "ai_draft"),
  );
  const selectionOverlayVisible = Boolean(selectedText && !regionMode && (!isMobileViewport || activeTab === "pdf"));
  const workspaceVisible = !isMobileViewport ? panelOpen : activeTab !== "pdf";
  const currentWorkspaceTitle = activeTab === "ocr" ? "OCR" : activeTab === "notes" ? "笔记" : activeTab === "results" ? "助手" : "PDF";
  const mobileSheetBounds = useMemo(() => {
    const viewport = mobileViewportHeight || 820;
    const max = Math.max(360, viewport - MOBILE_TOPBAR_HEIGHT - 10);
    const min = clamp(Math.round(viewport * 0.28), 188, max);
    const medium = clamp(Math.round(viewport * 0.52), min, max);
    const large = clamp(Math.round(viewport * 0.76), medium, max);
    return { min, medium, large, full: max };
  }, [mobileViewportHeight]);
  const resolveDefaultMobileSheetHeight = useCallback((tab: ReaderWorkspaceTab) => {
    if (tab === "ocr") return mobileSheetBounds.large;
    if (tab === "notes") return mobileSheetBounds.medium;
    return clamp(Math.round(mobileSheetBounds.medium * 0.92), mobileSheetBounds.min, mobileSheetBounds.full);
  }, [mobileSheetBounds]);
  const mobileSheetHeightPx = workspaceVisible
    ? clamp(mobileSheetHeight || resolveDefaultMobileSheetHeight(activeTab), mobileSheetBounds.min, mobileSheetBounds.full)
    : clamp(mobileSheetHeight || resolveDefaultMobileSheetHeight("ocr"), mobileSheetBounds.min, mobileSheetBounds.full);
  const mobileScrollPaddingBottom = isMobileViewport
    ? `${(workspaceVisible ? mobileSheetHeightPx : 0) + MOBILE_WORKSPACE_TABBAR_HEIGHT + 16}px`
    : undefined;

  const setCopied = useCallback((key: string) => {
    setCopiedKey(key);
    window.setTimeout(() => setCopiedKey((current) => (current === key ? null : current)), 1500);
  }, []);

  const ensureWorkspaceTab = useCallback((tab: ReaderWorkspaceTab) => {
    setActiveTab(tab);
    if (!isMobileViewport) {
      setPanelOpen(true);
      return;
    }
    if (tab !== "pdf") {
      setMobileSheetHeight((current) => {
        const nextBase = current > 0 ? current : resolveDefaultMobileSheetHeight(tab);
        return clamp(nextBase, mobileSheetBounds.min, mobileSheetBounds.full);
      });
    }
  }, [isMobileViewport, mobileSheetBounds.full, mobileSheetBounds.min, resolveDefaultMobileSheetHeight]);

  const appendResult = useCallback((response: PaperReaderQueryResponse, fallbackText?: string, fallbackQuestion?: string) => {
    setResults((prev) => [
      {
        ...response,
        id: `reader-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        label: resultLabel(response),
        excerpt: preview(String(response.caption || fallbackQuestion || response.text || fallbackText || "")),
      },
      ...prev,
    ].slice(0, 30));
    ensureWorkspaceTab("results");
  }, [ensureWorkspaceTab]);

  const runReaderQuery = useCallback(async (
    body: {
      scope: PaperReaderScope;
      action: PaperReaderAction;
      text?: string;
      question?: string;
      figure_id?: string;
      image_base64?: string;
      page_number?: number;
    },
    label: string,
  ) => {
    setLoadingLabel(label);
    try {
      const response = await paperApi.readerQuery(paperId, body);
      appendResult(response, body.text, body.question);
      return response;
    } catch (error) {
      appendResult(
        {
          scope: body.scope,
          action: body.action,
          result: `错误: ${error instanceof Error ? error.message : String(error)}`,
        },
        body.text,
        body.question,
      );
      return null;
    } finally {
      setLoadingLabel(null);
    }
  }, [appendResult, paperId]);

  const pollTaskResult = useCallback(async <T,>(
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
    const intervalMs = Math.max(700, options?.intervalMs ?? 1200);
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
            return await tasksApi.getResult(taskId) as T;
          } catch {
            if (options?.fallbackResult) {
              return options.fallbackResult();
            }
            return {} as T;
          }
        }
      } catch (error) {
        transientErrors += 1;
        if (transientErrors >= 5) throw error;
      }
      if (Date.now() > timeoutAt) {
        throw new Error(options?.timeoutMessage || "任务超时，请到任务中心查看");
      }
      await sleep(intervalMs);
    }
  }, []);

  const loadDocument = useCallback(async (silent = false) => {
    if (!silent) setDocumentLoading(true);
    try {
      const [doc, status] = await Promise.all([
        paperApi.getReaderDocument(paperId).catch(() => null),
        paperApi.getOcrStatus(paperId).catch(() => null),
      ]);
      setReaderDocument(doc);
      setOcrStatus(status);
    } finally {
      if (!silent) setDocumentLoading(false);
    }
  }, [paperId]);

  const loadNotes = useCallback(async (silent = false) => {
    if (!silent) setNotesLoading(true);
    try {
      const response = await paperApi.getReaderNotes(paperId);
      setNotes(response.items || []);
    } finally {
      if (!silent) setNotesLoading(false);
    }
  }, [paperId]);

  const setPageRef = useCallback((page: number, el: HTMLDivElement | null) => {
    if (el) pageRefs.current.set(page, el);
    else pageRefs.current.delete(page);
  }, []);

  const setBlockCardRef = useCallback((blockId: string, el: HTMLDivElement | null) => {
    if (el) blockCardRefs.current.set(blockId, el);
    else blockCardRefs.current.delete(blockId);
  }, []);

  const setNoteCardRef = useCallback((noteId: string, el: HTMLDivElement | null) => {
    if (el) noteCardRefs.current.set(noteId, el);
    else noteCardRefs.current.delete(noteId);
  }, []);

  const scrollToPage = useCallback((page: number) => {
    const target = Math.max(1, Math.min(page, numPages || 1));
    pageRefs.current.get(target)?.scrollIntoView({ behavior: "smooth", block: "start" });
    setCurrentPage(target);
  }, [numPages]);

  const scrollOcrBlockIntoView = useCallback((blockId: string) => {
    blockCardRefs.current.get(blockId)?.scrollIntoView({ block: "center", inline: "nearest" });
  }, []);

  const scrollNoteCardIntoView = useCallback((noteId: string) => {
    noteCardRefs.current.get(noteId)?.scrollIntoView({ block: "center", inline: "nearest" });
  }, []);

  const handleFocusDocumentBlock = useCallback((block: PaperReaderDocumentBlock) => {
    setLinkedBlockId(block.id);
    scrollOcrBlockIntoView(block.id);
    if (block.page_number) {
      scrollToPage(block.page_number);
    }
  }, [scrollOcrBlockIntoView, scrollToPage]);

  const handlePdfMouseMove = useCallback((page: number, event: ReactMouseEvent<HTMLDivElement>) => {
    if (regionMode) return;
    const block = pickPdfLinkedBlock(page, event.currentTarget.getBoundingClientRect(), event.clientX, event.clientY, documentBlocks);
    setPdfHoveredBlockId((current) => {
      const nextId = block?.id || null;
      return current === nextId ? current : nextId;
    });
  }, [documentBlocks, regionMode]);

  const handlePdfMouseLeave = useCallback(() => {
    setPdfHoveredBlockId(null);
  }, []);

  const handlePdfClick = useCallback((page: number, event: ReactMouseEvent<HTMLDivElement>) => {
    if (regionMode) return;
    const selected = normalizeText(window.getSelection?.()?.toString() || "");
    if (selected) return;
    const block = pickPdfLinkedBlock(page, event.currentTarget.getBoundingClientRect(), event.clientX, event.clientY, documentBlocks);
    if (!block) return;
    ensureWorkspaceTab("ocr");
    handleFocusDocumentBlock(block);
  }, [documentBlocks, ensureWorkspaceTab, handleFocusDocumentBlock, regionMode]);

  const handleStartSidebarResize = useCallback((event: ReactMouseEvent<HTMLButtonElement>) => {
    if (isMobileViewport || !panelOpen) return;
    const nextState = {
      startX: event.clientX,
      startWidth: workspaceWidth,
    };
    sidebarResizeRef.current = nextState;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const onMove = (moveEvent: MouseEvent) => {
      const delta = nextState.startX - moveEvent.clientX;
      setWorkspaceWidth(clamp(nextState.startWidth + delta, READER_PANEL_MIN_WIDTH, READER_PANEL_MAX_WIDTH));
    };

    const onUp = () => {
      sidebarResizeRef.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    event.preventDefault();
  }, [isMobileViewport, panelOpen, workspaceWidth]);

  const cropRegion = useCallback((rect: RegionRect) => {
    const pageEl = pageRefs.current.get(rect.page);
    const canvas = pageEl?.querySelector("canvas");
    if (!(canvas instanceof HTMLCanvasElement)) return null;
    const bounds = canvas.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return null;
    const sx = clamp(Math.round(rect.x * (canvas.width / bounds.width)), 0, canvas.width);
    const sy = clamp(Math.round(rect.y * (canvas.height / bounds.height)), 0, canvas.height);
    const sw = clamp(Math.round(rect.width * (canvas.width / bounds.width)), 1, canvas.width - sx);
    const sh = clamp(Math.round(rect.height * (canvas.height / bounds.height)), 1, canvas.height - sy);
    const out = document.createElement("canvas");
    out.width = sw;
    out.height = sh;
    const ctx = out.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(canvas, sx, sy, sw, sh, 0, 0, sw, sh);
    const previewUrl = out.toDataURL("image/png");
    const imageBase64 = previewUrl.split(",", 2)[1] || "";
    return imageBase64 ? { previewUrl, imageBase64 } : null;
  }, []);

  const finalizeRegion = useCallback((drag: RegionDrag) => {
    if (drag.width < 12 || drag.height < 12) {
      setRegionDrag(null);
      return;
    }
    const cropped = cropRegion(drag);
    if (!cropped) {
      setRegionDrag(null);
      return;
    }
    setRegionSelection({ page: drag.page, x: drag.x, y: drag.y, width: drag.width, height: drag.height, ...cropped });
    setRegionDrag(null);
    setRegionMode(false);
    ensureWorkspaceTab("results");
  }, [cropRegion, ensureWorkspaceTab]);

  const startRegion = useCallback((page: number, event: ReactMouseEvent<HTMLDivElement>) => {
    if (!regionMode) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const originX = clamp(event.clientX - bounds.left, 0, bounds.width);
    const originY = clamp(event.clientY - bounds.top, 0, bounds.height);
    setRegionDrag({
      page,
      x: originX,
      y: originY,
      width: 0,
      height: 0,
      originX,
      originY,
      pageLeft: bounds.left,
      pageTop: bounds.top,
      pageWidth: bounds.width,
      pageHeight: bounds.height,
    });
    event.preventDefault();
    event.stopPropagation();
  }, [regionMode]);

  const handleCopy = useCallback(async (key: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(key);
    } catch {
      return;
    }
  }, [setCopied]);

  const handlePdfTouchStart = useCallback((event: ReactTouchEvent<HTMLDivElement>) => {
    if (!isMobileViewport || activeTab !== "pdf") return;
    if (event.touches.length === 2) {
      pinchStateRef.current = {
        distance: touchDistance(event),
        scale,
      };
    }
  }, [activeTab, isMobileViewport, scale]);

  const handlePdfTouchMove = useCallback((event: ReactTouchEvent<HTMLDivElement>) => {
    const pinch = pinchStateRef.current;
    if (!isMobileViewport || activeTab !== "pdf" || !pinch || event.touches.length !== 2) return;
    const nextDistance = touchDistance(event);
    if (!nextDistance || !pinch.distance) return;
    event.preventDefault();
    const ratio = nextDistance / pinch.distance;
    setScale(clamp(Number((pinch.scale * ratio).toFixed(2)), 0.6, 3));
  }, [activeTab, isMobileViewport]);

  const handlePdfTouchEnd = useCallback(() => {
    pinchStateRef.current = null;
  }, []);

  const resolveSelectionAnchor = useCallback((text: string, page: number | null): SelectionAnchor => {
    const matchedBlock = findMatchingDocumentBlock(text, page, readerDocument?.blocks || []);
    if (matchedBlock) {
      const section = sectionsById.get(matchedBlock.section_id);
      return {
        anchor_source: "pdf_selection",
        anchor_id: matchedBlock.id,
        section_id: matchedBlock.section_id,
        section_title: section?.title || null,
        page_number: matchedBlock.page_number ?? page,
        matchedBlock,
      };
    }
    return {
      anchor_source: "pdf_selection",
      anchor_id: null,
      section_id: null,
      section_title: null,
      page_number: page,
      matchedBlock: null,
    };
  }, [readerDocument?.blocks, sectionsById]);

  const openManualNoteEditor = useCallback((seed: NoteEditorState, preferredTab?: ReaderWorkspaceTab | null) => {
    setNoteEditor(seed);
    if (preferredTab) ensureWorkspaceTab(preferredTab);
  }, [ensureWorkspaceTab]);

  const handleCreateSelectionNote = useCallback(() => {
    if (!selectedText) return;
    const anchor = resolveSelectionAnchor(selectedText, selectedPage);
    const seed = buildNoteEditorState({
      title: preview(selectedText, 36),
      content: "",
      quote: selectedText,
      page_number: anchor.page_number,
      anchor_source: "pdf_selection",
      anchor_id: anchor.anchor_id,
      section_id: anchor.section_id,
      section_title: anchor.section_title,
      source: "manual",
      color: "amber",
      tags: [],
      pinned: false,
    });
    openManualNoteEditor(seed, null);
  }, [openManualNoteEditor, resolveSelectionAnchor, selectedPage, selectedText]);

  const handleCreateBlockNote = useCallback((block: PaperReaderDocumentBlock) => {
    const section = sectionsById.get(block.section_id);
    openManualNoteEditor(
      buildNoteEditorState({
        title: block.type === "heading" ? block.text : preview(block.text, 36),
        content: "",
        quote: block.text,
        page_number: block.page_number,
        anchor_source: "ocr_block",
        anchor_id: block.id,
        section_id: block.section_id,
        section_title: section?.title || null,
        source: "manual",
        color: "amber",
        tags: [],
        pinned: false,
      }),
      "ocr",
    );
  }, [openManualNoteEditor, sectionsById]);

  const requestNoteDraft = useCallback(async (
    body: {
      text: string;
      quote?: string;
      page_number?: number | null;
      anchor_source?: "pdf_selection" | "ocr_block" | null;
      anchor_id?: string | null;
      section_id?: string | null;
      section_title?: string | null;
    },
    preferredTab?: ReaderWorkspaceTab | null,
  ) => {
    setNoteBusyLabel("正在生成 AI 草稿...");
    try {
      const response = await paperApi.generateReaderNoteDraft(paperId, {
        text: body.text,
        quote: body.quote,
        page_number: body.page_number ?? undefined,
        anchor_source: body.anchor_source ?? null,
        anchor_id: body.anchor_id ?? null,
        section_id: body.section_id ?? null,
        section_title: body.section_title ?? null,
      });
      setNoteEditor(buildNoteEditorState(response.item));
      if (preferredTab) ensureWorkspaceTab(preferredTab);
    } catch (error) {
      appendResult(
        {
          scope: "selection",
          action: "summarize",
          result: `AI 批注失败: ${error instanceof Error ? error.message : String(error)}`,
        },
        body.quote || body.text,
      );
    } finally {
      setNoteBusyLabel(null);
    }
  }, [appendResult, ensureWorkspaceTab, paperId]);

  const handleGenerateSelectionNoteDraft = useCallback(async () => {
    if (!selectedText) return;
    const anchor = resolveSelectionAnchor(selectedText, selectedPage);
    await requestNoteDraft(
      {
        text: selectedText,
        quote: selectedText,
        page_number: anchor.page_number,
        anchor_source: "pdf_selection",
        anchor_id: anchor.anchor_id,
        section_id: anchor.section_id,
        section_title: anchor.section_title,
      },
      null,
    );
  }, [requestNoteDraft, resolveSelectionAnchor, selectedPage, selectedText]);

  const handleGenerateBlockNoteDraft = useCallback(async (block: PaperReaderDocumentBlock) => {
    const section = sectionsById.get(block.section_id);
    await requestNoteDraft(
      {
        text: block.text,
        quote: block.text,
        page_number: block.page_number,
        anchor_source: "ocr_block",
        anchor_id: block.id,
        section_id: block.section_id,
        section_title: section?.title || null,
      },
      "ocr",
    );
  }, [requestNoteDraft, sectionsById]);

  const handleSaveNote = useCallback(async () => {
    if (!noteEditor) return;
    setNoteBusyLabel("正在保存笔记...");
    try {
      const response = await paperApi.saveReaderNote(paperId, {
        id: noteEditor.id,
        kind: noteEditor.kind,
        title: noteEditor.title.trim(),
        content: noteEditor.content.trim(),
        quote: noteEditor.quote.trim() || undefined,
        page_number: noteEditor.page_number ?? undefined,
        color: noteEditor.color,
        tags: noteEditor.tags,
        pinned: noteEditor.pinned,
        source: noteEditor.source,
        anchor_source: noteEditor.anchor_source ?? null,
        anchor_id: noteEditor.anchor_id ?? null,
        section_id: noteEditor.section_id ?? null,
        section_title: noteEditor.section_title ?? null,
      });
      setNotes(response.items || []);
      if (noteEditor.anchor_id) {
        setLinkedBlockId(noteEditor.anchor_id);
      }
      const shouldStayInline = noteEditor.anchor_source === "pdf_selection" && (!noteEditor.id || noteEditor.source === "ai_draft");
      setNoteEditor(null);
      if (!shouldStayInline) {
        ensureWorkspaceTab(noteEditor.anchor_source === "ocr_block" ? "ocr" : "notes");
      }
    } catch (error) {
      appendResult(
        {
          scope: "selection",
          action: "summarize",
          result: `保存笔记失败: ${error instanceof Error ? error.message : String(error)}`,
        },
        noteEditor.quote,
      );
    } finally {
      setNoteBusyLabel(null);
    }
  }, [appendResult, ensureWorkspaceTab, noteEditor, paperId]);

  const handleDeleteNote = useCallback(async (noteId: string) => {
    setNoteBusyLabel("正在删除笔记...");
    try {
      const response = await paperApi.deleteReaderNote(paperId, noteId);
      setNotes(response.items || []);
      setNoteEditor((current) => (current?.id === noteId ? null : current));
    } catch (error) {
      appendResult(
        {
          scope: "selection",
          action: "summarize",
          result: `删除笔记失败: ${error instanceof Error ? error.message : String(error)}`,
        },
      );
    } finally {
      setNoteBusyLabel(null);
    }
  }, [appendResult, paperId]);

  const handleTogglePinned = useCallback(async (note: PaperReaderNote) => {
    setNoteBusyLabel("正在更新笔记...");
    try {
      const response = await paperApi.saveReaderNote(paperId, {
        id: note.id,
        kind: note.kind,
        title: note.title,
        content: note.content,
        quote: note.quote || undefined,
        page_number: note.page_number || undefined,
        figure_id: note.figure_id || undefined,
        color: note.color,
        tags: note.tags,
        pinned: !note.pinned,
        source: note.source,
        anchor_source: note.anchor_source ?? null,
        anchor_id: note.anchor_id ?? null,
        section_id: note.section_id ?? null,
        section_title: note.section_title ?? null,
      });
      setNotes(response.items || []);
    } finally {
      setNoteBusyLabel(null);
    }
  }, [paperId]);

  const handleLocateNote = useCallback((note: PaperReaderNote) => {
    if (note.anchor_id) {
      setLinkedBlockId(note.anchor_id);
      if (!isMobileViewport && note.anchor_source === "ocr_block") {
        scrollOcrBlockIntoView(note.anchor_id);
      }
    }
    if (note.page_number) {
      if (isMobileViewport) {
        setActiveTab("pdf");
      }
      scrollToPage(note.page_number);
    } else if (note.anchor_id && isMobileViewport) {
      setActiveTab("pdf");
    }
  }, [isMobileViewport, scrollOcrBlockIntoView, scrollToPage]);

  const focusNoteCard = useCallback((noteId: string) => {
    setActiveNoteId(noteId);
    ensureWorkspaceTab("notes");
    window.setTimeout(() => {
      scrollNoteCardIntoView(noteId);
    }, 80);
    window.setTimeout(() => {
      setActiveNoteId((current) => (current === noteId ? null : current));
    }, 2200);
  }, [ensureWorkspaceTab, scrollNoteCardIntoView]);

  const handleOpenNotesForBlock = useCallback((block: PaperReaderDocumentBlock) => {
    setLinkedBlockId(block.id);
    if (block.page_number) {
      scrollToPage(block.page_number);
    }
    const note = (anchoredNotesByBlockId.get(block.id) || [])[0];
    if (note) {
      focusNoteCard(note.id);
      return;
    }
    ensureWorkspaceTab("notes");
  }, [anchoredNotesByBlockId, ensureWorkspaceTab, focusNoteCard, scrollToPage]);

  const handleOpenNotesForPage = useCallback((page: number) => {
    const note = notes.find((item) => (item.page_number || 0) === page);
    if (note) {
      if (note.anchor_id) {
        setLinkedBlockId(note.anchor_id);
      }
      focusNoteCard(note.id);
      scrollToPage(page);
      return;
    }
    ensureWorkspaceTab("notes");
    scrollToPage(page);
  }, [ensureWorkspaceTab, focusNoteCard, notes, scrollToPage]);

  const handleMobileSheetTouchStart = useCallback((event: ReactTouchEvent<HTMLDivElement>) => {
    if (!isMobileViewport || activeTab === "pdf" || event.touches.length !== 1) return;
    mobileSheetDragRef.current = {
      startY: event.touches[0].clientY,
      startHeight: mobileSheetHeightPx,
      lastHeight: mobileSheetHeightPx,
    };
    mobileSheetVisualHeightRef.current = mobileSheetHeightPx;
    setMobileSheetDragging(true);
  }, [activeTab, isMobileViewport, mobileSheetHeightPx]);

  const handleMobileSheetTouchMove = useCallback((event: ReactTouchEvent<HTMLDivElement>) => {
    const dragState = mobileSheetDragRef.current;
    if (!dragState || event.touches.length !== 1) return;
    const delta = dragState.startY - event.touches[0].clientY;
    const nextHeight = clamp(dragState.startHeight + delta, 0, mobileSheetBounds.full);
    dragState.lastHeight = nextHeight;
    mobileSheetVisualHeightRef.current = nextHeight;
    if (mobileSheetRafRef.current == null) {
      mobileSheetRafRef.current = window.requestAnimationFrame(() => {
        mobileSheetRafRef.current = null;
        if (mobileSheetRef.current) {
          mobileSheetRef.current.style.height = `${mobileSheetVisualHeightRef.current}px`;
        }
      });
    }
    event.preventDefault();
  }, [mobileSheetBounds.full]);

  const handleMobileSheetTouchEnd = useCallback(() => {
    const dragState = mobileSheetDragRef.current;
    if (!dragState) return;
    mobileSheetDragRef.current = null;
    if (mobileSheetRafRef.current != null) {
      window.cancelAnimationFrame(mobileSheetRafRef.current);
      mobileSheetRafRef.current = null;
    }
    setMobileSheetDragging(false);
    const current = dragState.lastHeight;
    if (current < mobileSheetBounds.min * 0.72) {
      if (mobileSheetRef.current) {
        mobileSheetRef.current.style.height = "0px";
      }
      setMobileSheetHeight(0);
      setActiveTab("pdf");
      return;
    }
    const snapPoints = [mobileSheetBounds.min, mobileSheetBounds.medium, mobileSheetBounds.large, mobileSheetBounds.full];
    const nextHeight = snapPoints.reduce((best, point) => (
      Math.abs(point - current) < Math.abs(best - current) ? point : best
    ), snapPoints[0]);
    setMobileSheetHeight(nextHeight);
  }, [mobileSheetBounds.full, mobileSheetBounds.large, mobileSheetBounds.medium, mobileSheetBounds.min]);

  useEffect(() => {
    return () => {
      if (mobileSheetRafRef.current != null) {
        window.cancelAnimationFrame(mobileSheetRafRef.current);
      }
    };
  }, []);

  useEffect(() => {
    autoOcrAttemptedRef.current = false;
    ocrRunRef.current = false;
  }, [paperId]);

  const handleStartOcr = useCallback(async (options?: { openWorkspace?: boolean }) => {
    const openWorkspace = options?.openWorkspace ?? true;
    if (ocrRunRef.current) return;
    const currentStatus = String(ocrStatus?.status || "").trim().toLowerCase();
    if (["running", "queued", "pending"].includes(currentStatus)) {
      setOcrTaskLabel("OCR 已在后台处理中，请稍候刷新");
      if (openWorkspace) {
        ensureWorkspaceTab("ocr");
      }
      return;
    }

    ocrRunRef.current = true;
    setOcrTaskLabel(openWorkspace ? "正在提交 OCR 任务..." : "已自动启动 OCR，正在生成 Markdown...");
    if (openWorkspace) {
      ensureWorkspaceTab("ocr");
    }
    try {
      const kickoff = await paperApi.processOcrAsync(paperId);
      await pollTaskResult(kickoff.task_id, {
        timeoutMessage: "OCR 处理超时，请到任务中心查看",
        onStatus: (status) => setOcrTaskLabel(status.message || `OCR 处理中... ${Math.max(0, Math.min(100, status.progress_pct))}%`),
        fallbackResult: () => paperApi.getOcrStatus(paperId),
      });
      await Promise.all([loadDocument(true), loadNotes(true)]);
      setOcrTaskLabel("OCR 已更新");
      await Promise.resolve(onOcrUpdated?.());
    } catch (error) {
      setOcrTaskLabel(`OCR 失败: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      ocrRunRef.current = false;
    }
  }, [ensureWorkspaceTab, loadDocument, loadNotes, ocrStatus?.status, onOcrUpdated, paperId, pollTaskResult]);

  useEffect(() => {
    if (documentLoading) return;
    if (readerDocument?.available) return;
    if (autoOcrAttemptedRef.current || ocrRunRef.current) return;
    if (ocrStatus?.available) return;

    const status = String(ocrStatus?.status || "").trim().toLowerCase();
    if (status === "success") return;
    if (["running", "queued", "pending"].includes(status)) {
      setOcrTaskLabel((current) => current || "OCR 正在后台处理中...");
      return;
    }

    autoOcrAttemptedRef.current = true;
    void handleStartOcr({ openWorkspace: false });
  }, [documentLoading, handleStartOcr, ocrStatus?.available, ocrStatus?.status, readerDocument?.available]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const media = window.matchMedia("(max-width: 767px)");
    const sync = () => {
      setIsMobileViewport(media.matches);
      setMobileViewportHeight(window.innerHeight);
    };
    sync();
    media.addEventListener("change", sync);
    window.addEventListener("resize", sync);
    return () => {
      media.removeEventListener("change", sync);
      window.removeEventListener("resize", sync);
    };
  }, []);

  useEffect(() => {
    if (isMobileViewport) {
      setScale((value) => (value > 1 ? 0.9 : value));
    }
  }, [isMobileViewport]);

  useEffect(() => {
    if (!isMobileViewport && activeTab === "pdf") {
      setActiveTab("ocr");
    }
  }, [activeTab, isMobileViewport]);

  useEffect(() => {
    if (!isMobileViewport) return;
    if (activeTab === "pdf") return;
    setMobileSheetHeight((current) => {
      const next = current > 0 ? current : resolveDefaultMobileSheetHeight(activeTab);
      return clamp(next, mobileSheetBounds.min, mobileSheetBounds.full);
    });
  }, [activeTab, isMobileViewport, mobileSheetBounds.full, mobileSheetBounds.min, resolveDefaultMobileSheetHeight]);

  useEffect(() => {
    const onLoadState = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onLoadState);
    return () => document.removeEventListener("fullscreenchange", onLoadState);
  }, []);

  useEffect(() => () => {
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  }, []);

  useEffect(() => {
    if (!numPages || !scrollRef.current) return;
    const observer = new IntersectionObserver((entries) => {
      let page = currentPage;
      let ratio = 0;
      for (const entry of entries) {
        const nextPage = Number(entry.target.getAttribute("data-page"));
        if (entry.isIntersecting && nextPage && entry.intersectionRatio > ratio) {
          ratio = entry.intersectionRatio;
          page = nextPage;
        }
      }
      if (page !== currentPage) setCurrentPage(page);
    }, { root: scrollRef.current, threshold: [0, 0.25, 0.5, 0.75, 1] });
    pageRefs.current.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [currentPage, numPages]);

  useEffect(() => {
    const handler = (event: MouseEvent) => {
      if (regionMode) return;
      const target = event.target as Node | null;
      if (!scrollRef.current || (target && !scrollRef.current.contains(target))) return;
      const selection = window.getSelection();
      const text = normalizeText(selection?.toString() || "");
      if (!text || text.length <= 2) {
        setSelectedText("");
        setSelectedPage(null);
        return;
      }
      const range = selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null;
      setSelectedText(text);
      setSelectedPage(getSelectionPage(range?.commonAncestorContainer || selection?.anchorNode || null, scrollRef.current));
    };
    document.addEventListener("mouseup", handler);
    return () => document.removeEventListener("mouseup", handler);
  }, [regionMode]);

  useEffect(() => {
    if (!regionDrag) return;
    const onMove = (event: MouseEvent) => {
      const currentX = clamp(event.clientX - regionDrag.pageLeft, 0, regionDrag.pageWidth);
      const currentY = clamp(event.clientY - regionDrag.pageTop, 0, regionDrag.pageHeight);
      setRegionDrag((current) => current ? {
        ...current,
        x: Math.min(current.originX, currentX),
        y: Math.min(current.originY, currentY),
        width: Math.abs(currentX - current.originX),
        height: Math.abs(currentY - current.originY),
      } : current);
    };
    const onUp = () => finalizeRegion(regionDrag);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp, { once: true });
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [finalizeRegion, regionDrag]);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (regionMode) {
          setRegionMode(false);
          setRegionDrag(null);
        } else {
          onClose();
        }
      }
      if ((event.ctrlKey || event.metaKey) && (event.key === "=" || event.key === "+")) {
        event.preventDefault();
        setScale((value) => Math.min(value + 0.2, 3));
      }
      if ((event.ctrlKey || event.metaKey) && event.key === "-") {
        event.preventDefault();
        setScale((value) => Math.max(value - 0.2, 0.5));
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose, regionMode]);

  useEffect(() => {
    void Promise.all([loadDocument(), loadNotes()]);
  }, [loadDocument, loadNotes]);

  const renderOcrContent = () => {
    if (documentLoading) {
      return (
        <div className="flex items-center justify-center py-12 text-ink-tertiary">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          正在读取 OCR 文档...
        </div>
      );
    }

    if (!readerDocument?.available) {
      return (
        <div className="rounded-2xl border border-dashed border-border bg-surface/50 p-4 text-sm text-ink-secondary">
          <div className="text-ink">当前还没有可用 OCR / Markdown 文档。</div>
          <div className="mt-2 text-xs text-ink-tertiary">
            状态：{ocrStatus?.status || "unknown"}
            {ocrStatus?.updated_at ? ` · ${timeAgo(ocrStatus.updated_at)}` : ""}
          </div>
          {ocrStatus?.available ? (
            <div className="mt-2 text-xs text-amber-300">
              已检测到 OCR 元数据，但阅读器缓存还未恢复成功。点击“刷新”，若仍无内容可重新跑一次 OCR。
            </div>
          ) : null}
          {ocrTaskLabel ? <div className="mt-3 rounded-xl border border-primary/20 bg-primary/5 px-3 py-2 text-sm text-primary">{ocrTaskLabel}</div> : null}
          <div className="mt-4 flex flex-wrap gap-2">
            <ActionButton onClick={() => void handleStartOcr()} tone="primary">
              <span className="inline-flex items-center gap-2">
                <Sparkles className="h-4 w-4" />
                启动 OCR
              </span>
            </ActionButton>
            <ActionButton onClick={() => void loadDocument()}>
              <span className="inline-flex items-center gap-2">
                <RefreshCw className="h-4 w-4" />
                刷新
              </span>
            </ActionButton>
          </div>
        </div>
      );
    }

    return (
      <div className="space-y-4">
        {groupedDocumentSections.map(({ section, blocks }) => (
          <section key={section.id} className="rounded-2xl border border-border/70 bg-surface/72 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 pb-3">
              <div>
                <div className="text-sm text-ink">{section.title}</div>
                <div className="mt-1 text-[11px] text-ink-tertiary">
                  level {section.level}
                  {section.page_start ? ` · 第 ${section.page_start} 页起` : ""}
                </div>
              </div>
              <div className="rounded-full border border-border/60 bg-page/72 px-2 py-1 text-[11px] text-ink-tertiary">
                {blocks.filter((block) => !(block.type === "heading" && normalizeText(block.text) === normalizeText(section.title))).length} 段
              </div>
            </div>

            <div className="mt-3 space-y-3">
              {blocks
                .filter((block) => !(block.type === "heading" && normalizeText(block.text) === normalizeText(section.title)))
                .map((block) => {
                const inlineNotes = blockNotes.get(block.id) || [];
                const showInlineEditor = inlineEditorBlockId === block.id && noteEditor;
                const canAnnotate = isBlockAnnotatable(block, section.title);
                const canLink = canLinkBlockToPdf(block);
                const isActive = activePdfLinkedBlockId === block.id;
                const markdownContent = buildRenderableBlockMarkdown(block);
                return (
                  <div
                    key={block.id}
                    ref={(el) => setBlockCardRef(block.id, el)}
                    className={cn(
                      "rounded-xl border p-3 transition-colors",
                      canLink && "cursor-pointer hover:border-primary/30 hover:bg-primary/5",
                      isActive ? "border-primary/35 bg-primary/6" : "border-border/60 bg-page/72",
                    )}
                    onClick={() => {
                      if (!canLink) return;
                      const selected = normalizeText(window.getSelection?.()?.toString() || "");
                      if (selected) return;
                      handleFocusDocumentBlock(block);
                      if (isMobileViewport) {
                        setActiveTab("pdf");
                      }
                    }}
                    onMouseEnter={() => {
                      if (canLink) setOcrHoveredBlockId(block.id);
                    }}
                    onMouseLeave={() => {
                      if (canLink) setOcrHoveredBlockId((current) => (current === block.id ? null : current));
                    }}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-tertiary">
                        <span>{DOCUMENT_BLOCK_LABEL[block.type]}</span>
                        {block.page_number ? <span>第 {block.page_number} 页</span> : null}
                        {canLink ? <span className="rounded-full border border-primary/20 bg-primary/10 px-2 py-0.5 text-primary">已联动 PDF</span> : null}
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {canAnnotate ? (
                          <>
                            <div onClick={(event) => event.stopPropagation()}>
                              <ActionButton onClick={() => handleCreateBlockNote(block)} disabled={!!noteBusyLabel}>
                                <span className="inline-flex items-center gap-1.5"><PencilLine className="h-3.5 w-3.5" />写笔记</span>
                              </ActionButton>
                            </div>
                            <div onClick={(event) => event.stopPropagation()}>
                              <ActionButton onClick={() => void handleGenerateBlockNoteDraft(block)} disabled={!!noteBusyLabel}>
                                <span className="inline-flex items-center gap-1.5"><Wand2 className="h-3.5 w-3.5" />AI 批注</span>
                              </ActionButton>
                            </div>
                          </>
                        ) : (
                          <div className="self-center text-[11px] text-ink-tertiary">信息块</div>
                        )}
                      </div>
                    </div>

                    <div className="mt-3 max-w-none text-sm text-ink-secondary">
                      <Markdown
                        className={cn(
                          "pdf-ai-markdown",
                          block.type === "equation" && "text-ink",
                          block.type === "image" && "pdf-ai-markdown-figure",
                        )}
                        autoMath={false}
                      >
                        {markdownContent || block.text}
                      </Markdown>
                    </div>

                    {showInlineEditor ? (
                      <div className="mt-3">
                        <NoteEditorCard
                          note={noteEditor}
                          busy={Boolean(noteBusyLabel)}
                          onChange={(patch) => setNoteEditor((current) => (current ? { ...current, ...patch } : current))}
                          onSave={() => void handleSaveNote()}
                          onCancel={() => setNoteEditor(null)}
                        />
                      </div>
                    ) : null}

                    {inlineNotes.length > 0 ? (
                      <div className="mt-3 space-y-2">
                        {inlineNotes.map((note) => (
                          <SavedNoteCard
                            key={note.id}
                            note={note}
                            compact
                            onLocate={() => handleLocateNote(note)}
                            active={activeNoteId === note.id}
                            onEdit={() => { setNoteEditor(buildNoteEditorState(note)); ensureWorkspaceTab("notes"); }}
                            onDelete={() => void handleDeleteNote(note.id)}
                            onTogglePin={() => void handleTogglePinned(note)}
                          />
                        ))}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    );
  };

  const renderNotesContent = () => {
    return (
      <div className="space-y-4">
        {noteEditor && noteEditor.anchor_source !== "ocr_block" && !inlinePdfSelectionEditor ? (
          <NoteEditorCard
            note={noteEditor}
            busy={Boolean(noteBusyLabel)}
            onChange={(patch) => setNoteEditor((current) => (current ? { ...current, ...patch } : current))}
            onSave={() => void handleSaveNote()}
            onCancel={() => setNoteEditor(null)}
          />
        ) : null}

        {notesLoading ? (
          <div className="flex items-center justify-center py-12 text-ink-tertiary">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            正在加载笔记...
          </div>
        ) : notesBySection.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-border bg-surface/50 p-4 text-sm text-ink-tertiary">
            还没有保存的段落笔记。可以从 PDF 选区或 OCR 段落直接创建。
          </div>
        ) : (
          notesBySection.map((group) => (
            <section key={group.key} className="rounded-2xl border border-border/70 bg-surface/72 p-3">
              <div className="border-b border-border/60 pb-3 text-sm text-ink">{group.title}</div>
              <div className="mt-3 space-y-3">
                {group.notes.map((note) => (
                  <div key={note.id} ref={(el) => setNoteCardRef(note.id, el)}>
                    <SavedNoteCard
                      note={note}
                      onLocate={() => handleLocateNote(note)}
                      active={activeNoteId === note.id}
                      onEdit={() => setNoteEditor(buildNoteEditorState(note))}
                      onDelete={() => void handleDeleteNote(note.id)}
                      onTogglePin={() => void handleTogglePinned(note)}
                    />
                  </div>
                ))}
              </div>
            </section>
          ))
        )}
      </div>
    );
  };

  const renderResultsContent = () => {
    return (
      <div className="space-y-4">
        <section className="rounded-2xl border border-border/70 bg-surface/72 p-3">
          <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-ink-tertiary">
            <BookOpen className="h-3.5 w-3.5" />
            助手 · 整篇论文
          </div>
          <div className="flex flex-wrap gap-2">
            <ActionButton disabled={!!loadingLabel} onClick={() => void runReaderQuery({ scope: "paper", action: "analyze" }, "正在分析整篇论文...")}>
              分析整篇
            </ActionButton>
          </div>
          <div className="mt-3 flex flex-col gap-2 rounded-xl border border-border bg-page/72 p-2 sm:flex-row">
            <input
              value={paperQuestion}
              onChange={(event) => setPaperQuestion(event.target.value)}
              placeholder="基于整篇论文提问"
              className="h-9 min-w-0 flex-1 bg-transparent px-1 text-sm text-ink outline-none placeholder:text-ink-placeholder"
            />
            <ActionButton
              tone="primary"
              disabled={!paperQuestion.trim() || !!loadingLabel}
              onClick={() => {
                void runReaderQuery({ scope: "paper", action: "ask", question: paperQuestion.trim() }, "正在基于整篇论文回答问题...");
                setPaperQuestion("");
              }}
            >
              <span className="inline-flex items-center gap-2"><SendHorizontal className="h-4 w-4" />提问</span>
            </ActionButton>
          </div>
        </section>

        <section className="rounded-2xl border border-border/70 bg-surface/72 p-3">
          <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-ink-tertiary">
            <Highlighter className="h-3.5 w-3.5" />
            助手 · 当前选区
          </div>
          {selectedText ? (
            <>
              <div className="rounded-xl border border-border bg-page/72 px-3 py-2.5 text-xs leading-6 text-ink-secondary">{selectedText}</div>
              <div className="mt-3 flex flex-wrap gap-2">
                <ActionButton disabled={!!loadingLabel} onClick={() => void runReaderQuery({ scope: "selection", action: "translate", text: selectedText, page_number: selectedPage || undefined }, "正在翻译选中文本...")}>
                  <span className="inline-flex items-center gap-1.5"><Languages className="h-3.5 w-3.5" />翻译</span>
                </ActionButton>
                <ActionButton disabled={!!loadingLabel} onClick={() => void runReaderQuery({ scope: "selection", action: "analyze", text: selectedText, page_number: selectedPage || undefined }, "正在分析选中文本...")}>
                  分析
                </ActionButton>
                <ActionButton disabled={!!noteBusyLabel} onClick={handleCreateSelectionNote}>
                  <span className="inline-flex items-center gap-1.5"><PencilLine className="h-3.5 w-3.5" />写笔记</span>
                </ActionButton>
                <ActionButton disabled={!!noteBusyLabel} onClick={() => void handleGenerateSelectionNoteDraft()}>
                  <span className="inline-flex items-center gap-1.5"><Wand2 className="h-3.5 w-3.5" />AI 批注</span>
                </ActionButton>
              </div>
              <div className="mt-3 flex flex-col gap-2 rounded-xl border border-border bg-page/72 p-2 sm:flex-row">
                <input
                  value={selectionQuestion}
                  onChange={(event) => setSelectionQuestion(event.target.value)}
                  placeholder="针对选区提问"
                  className="h-9 min-w-0 flex-1 bg-transparent px-1 text-sm text-ink outline-none placeholder:text-ink-placeholder"
                />
                <ActionButton
                  tone="primary"
                  disabled={!selectionQuestion.trim() || !!loadingLabel}
                  onClick={() => {
                    void runReaderQuery({
                      scope: "selection",
                      action: "ask",
                      text: selectedText,
                      question: selectionQuestion.trim(),
                      page_number: selectedPage || undefined,
                    }, "正在回答选区问题...");
                    setSelectionQuestion("");
                  }}
                >
                  <SendHorizontal className="h-4 w-4" />
                </ActionButton>
              </div>
            </>
          ) : (
            <div className="rounded-xl border border-dashed border-border px-3 py-6 text-center text-xs text-ink-tertiary">先在 PDF 中选择一段文字。</div>
          )}
        </section>

        <section className="rounded-2xl border border-border/70 bg-surface/72 p-3">
          <div className="mb-3 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-ink-tertiary">
              <ScanSearch className="h-3.5 w-3.5" />
              助手 · 框选区域
            </div>
            <div className="flex gap-2">
              <ActionButton onClick={() => { setRegionMode((value) => !value); setRegionDrag(null); }}>{regionMode ? "取消" : "开始框选"}</ActionButton>
              {regionSelection ? <ActionButton onClick={() => { setRegionSelection(null); setRegionQuestion(""); }}>清除</ActionButton> : null}
            </div>
          </div>
          {regionSelection ? (
            <>
              <div className="overflow-hidden rounded-xl border border-border bg-page/72">
                <img src={regionSelection.previewUrl} alt="selected region" className="max-h-48 w-full object-contain" />
              </div>
              <div className="mt-2 text-[11px] text-ink-tertiary">第 {regionSelection.page} 页</div>
              <div className="mt-3 flex flex-wrap gap-2">
                <ActionButton disabled={!!loadingLabel} onClick={() => void runReaderQuery({ scope: "figure", action: "analyze", image_base64: regionSelection.imageBase64, page_number: regionSelection.page }, "正在分析框选区域...")}>
                  分析区域
                </ActionButton>
              </div>
              <div className="mt-3 flex flex-col gap-2 rounded-xl border border-border bg-page/72 p-2 sm:flex-row">
                <input
                  value={regionQuestion}
                  onChange={(event) => setRegionQuestion(event.target.value)}
                  placeholder="针对区域提问"
                  className="h-9 min-w-0 flex-1 bg-transparent px-1 text-sm text-ink outline-none placeholder:text-ink-placeholder"
                />
                <ActionButton
                  tone="primary"
                  disabled={!regionQuestion.trim() || !!loadingLabel}
                  onClick={() => {
                    void runReaderQuery({
                      scope: "figure",
                      action: "ask",
                      image_base64: regionSelection.imageBase64,
                      page_number: regionSelection.page,
                      question: regionQuestion.trim(),
                    }, "正在回答区域问题...");
                    setRegionQuestion("");
                  }}
                >
                  <SendHorizontal className="h-4 w-4" />
                </ActionButton>
              </div>
            </>
          ) : (
            <div className="rounded-xl border border-dashed border-border px-3 py-6 text-center text-xs text-ink-tertiary">
              {regionMode ? "在 PDF 页面拖拽一个矩形区域。" : "可以框选图表、公式或局部截图进行分析。"}
            </div>
          )}
        </section>

        {loadingLabel ? (
          <div className="flex items-center gap-2 rounded-xl border border-primary/20 bg-primary/5 px-3 py-2 text-sm text-primary">
            <Loader2 className="h-4 w-4 animate-spin" />
            {loadingLabel}
          </div>
        ) : null}

        <section className="rounded-2xl border border-border/70 bg-surface/72 p-3">
          <div className="mb-3 flex items-center justify-between gap-2 text-xs uppercase tracking-[0.16em] text-ink-tertiary">
            <span className="inline-flex items-center gap-2"><Sparkles className="h-3.5 w-3.5" />助手记录</span>
            <button onClick={() => setResults([])} className="rounded-md p-1 text-ink-tertiary transition hover:bg-hover hover:text-ink-secondary">
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
          </div>
          {results.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border px-3 py-6 text-center text-xs text-ink-tertiary">暂无助手输出</div>
          ) : (
            <div className="space-y-3">
              {results.map((item) => {
                const copyKey = `result-${item.id}`;
                return (
                  <div key={item.id} className="rounded-xl border border-border/70 bg-page/72 p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-sm text-ink">{item.label}</div>
                        {item.excerpt ? <div className="mt-1 text-xs text-ink-tertiary">{item.excerpt}</div> : null}
                      </div>
                      <button onClick={() => void handleCopy(copyKey, item.result)} className="rounded-md p-1.5 text-ink-tertiary transition hover:bg-hover hover:text-ink-secondary">
                        {copiedKey === copyKey ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
                      </button>
                    </div>
                    <div className="mt-3 max-w-none text-sm">
                      <Markdown className="pdf-ai-markdown">{item.result}</Markdown>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    );
  };

  const renderWorkspaceBody = () => {
    if (activeTab === "ocr") return renderOcrContent();
    if (activeTab === "notes") return renderNotesContent();
    return renderResultsContent();
  };

  return (
    <div ref={containerRef} className="fixed inset-0 z-50 bg-page/95 backdrop-blur-sm">
      <div className="theme-terminal-header absolute left-0 right-0 top-0 z-40 border-b px-2 py-1 sm:px-4 sm:py-2">
        <div className="flex items-center justify-between gap-2">
          {!isMobileViewport ? (
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 text-ink">
                <BookOpen className="h-3.5 w-3.5 text-primary sm:h-4 sm:w-4" />
                <span className="truncate text-[13px] sm:text-sm">{paperTitle}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-ink-tertiary">
                {selectedPage ? <span>选区 p.{selectedPage}</span> : null}
                {regionSelection ? <span>区域 p.{regionSelection.page}</span> : null}
                {regionMode ? <span className="text-primary">框选中</span> : null}
                {readerDocument?.available ? <span>{readerDocument.source}</span> : null}
              </div>
            </div>
          ) : (
            <div className="flex min-w-0 items-center gap-1.5 text-[10px] text-ink-tertiary">
              <div className="rounded-full border border-border/60 bg-surface/72 px-2 py-0.5 text-[10px] text-ink-secondary">
                p.{currentPage}/{numPages || "-"}
              </div>
              <span>{currentWorkspaceTitle}</span>
              {regionMode ? <span className="text-primary">框选</span> : null}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-1 sm:justify-end">
            {!isMobileViewport ? (
              <>
                <div className="flex items-center gap-1 rounded-md border border-border/60 bg-surface/72 px-2 py-1">
                  <input
                    value={pageInput}
                    onChange={(event) => setPageInput(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        scrollToPage(Number.parseInt(pageInput, 10));
                        setPageInput("");
                      }
                    }}
                    placeholder={String(currentPage)}
                    className="w-8 bg-transparent text-center text-xs text-ink outline-none placeholder:text-ink-placeholder"
                  />
                  <span className="text-xs text-ink-tertiary">/ {numPages}</span>
                </div>
                <button onClick={() => setScale((value) => Math.max(value - 0.2, 0.5))} className="toolbar-btn"><ZoomOut className="h-4 w-4" /></button>
                <button onClick={() => setScale(1.2)} className="toolbar-btn-text">{Math.round(scale * 100)}%</button>
                <button onClick={() => setScale((value) => Math.min(value + 0.2, 3))} className="toolbar-btn"><ZoomIn className="h-4 w-4" /></button>
              </>
            ) : (
              <button onClick={() => setScale((value) => Math.max(value - 0.15, 0.6))} className="toolbar-btn"><ZoomOut className="h-4 w-4" /></button>
            )}
            {isMobileViewport ? (
              <button onClick={() => setScale(1)} className="toolbar-btn-text min-w-[3.25rem]">{Math.round(scale * 100)}%</button>
            ) : null}
            {isMobileViewport ? (
              <button onClick={() => setScale((value) => Math.min(value + 0.15, 3))} className="toolbar-btn"><ZoomIn className="h-4 w-4" /></button>
            ) : null}
            <button onClick={() => { setRegionMode((value) => !value); setRegionDrag(null); }} className={cn("toolbar-btn", regionMode && "bg-primary/30 text-primary")}><ImageIcon className="h-4 w-4" /></button>
            <button onClick={() => !isFullscreen ? containerRef.current?.requestFullscreen?.() : document.exitFullscreen?.()} className="toolbar-btn">
              {isFullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
            </button>
            {!isMobileViewport ? (
              <button onClick={() => setPanelOpen((value) => !value)} className={cn("toolbar-btn", panelOpen && "bg-primary/30 text-primary")}>
                <MessageSquareText className="h-4 w-4" />
              </button>
            ) : null}
            <button onClick={onClose} className="toolbar-btn hover:bg-red-500/20 hover:text-red-300"><X className="h-4 w-4" /></button>
          </div>
        </div>
      </div>

      <div className="flex h-full pt-[44px] sm:pt-12">
        <div className="relative min-w-0 flex-1">
          <div
            ref={scrollRef}
            className={cn(
              "h-full overflow-auto pb-12",
            )}
            style={isMobileViewport ? { paddingBottom: mobileScrollPaddingBottom } : undefined}
            onTouchStart={handlePdfTouchStart}
            onTouchMove={handlePdfTouchMove}
            onTouchEnd={handlePdfTouchEnd}
            onTouchCancel={handlePdfTouchEnd}
          >
            {loadError ? (
              <div className="flex h-full items-center justify-center text-red-300">{loadError}</div>
            ) : (
              <Document
                file={pdfUrl}
                onLoadSuccess={({ numPages: total }) => { setNumPages(total); setLoadError(null); }}
                onLoadError={(error) => setLoadError(`PDF 加载失败: ${error.message}`)}
                loading={<div className="flex h-96 items-center justify-center"><Loader2 className="h-8 w-8 animate-spin text-primary" /></div>}
              >
                <div className="flex flex-col items-center gap-4 px-2 py-4 pr-10 sm:pr-20 sm:py-6">
                  {pages.map((page) => {
                    const nearby = Math.abs(page - currentPage) <= 3;
                    const activeRect = regionSelection?.page === page ? regionSelection : null;
                    const dragRect = regionDrag?.page === page ? regionDrag : null;
                    const linkedBlockForPage = !regionMode && activePdfLinkedBlock?.page_number === page ? activePdfLinkedBlock : null;
                    const pageCanvas = pageRefs.current.get(page)?.querySelector("canvas") as HTMLCanvasElement | null;
                    const linkedOverlayStyle = getPdfOverlayStyle(linkedBlockForPage, pageCanvas);
                    const noteMarkersOnPage = pdfNoteMarkersByPage.anchored.get(page) || [];
                    const fallbackPageNoteCount = pdfNoteMarkersByPage.fallback.get(page) || 0;
                    return (
                      <div
                        key={page}
                        data-page={page}
                        ref={(el) => setPageRef(page, el)}
                        className="relative max-w-full overflow-visible"
                        style={!nearby ? { minHeight: `${Math.round(792 * scale)}px`, width: `${Math.round(612 * scale)}px`, maxWidth: "100%" } : undefined}
                      >
                        <div className="absolute left-1/2 top-0 z-10 -translate-x-1/2 -translate-y-full pb-1">
                          <span className="rounded-full border border-border/60 bg-surface/72 px-2 py-0.5 text-[10px] text-ink-tertiary">{page}</span>
                        </div>
                        {nearby ? (
                          <div
                            className={cn("relative inline-block max-w-full overflow-visible", regionMode && "pdf-region-mode")}
                            onMouseMove={(event) => handlePdfMouseMove(page, event)}
                            onMouseLeave={handlePdfMouseLeave}
                            onClick={(event) => handlePdfClick(page, event)}
                          >
                            <Page
                              pageNumber={page}
                              scale={scale}
                              className="pdf-page-shadow"
                              renderTextLayer={!regionMode}
                              renderAnnotationLayer={!regionMode}
                            />
                            <div
                              className={cn(
                                "absolute inset-0 z-20",
                                regionMode ? "cursor-crosshair bg-primary/5" : "pointer-events-none",
                              )}
                              style={regionMode ? { touchAction: "none" } : undefined}
                              onMouseDown={(event) => startRegion(page, event)}
                            />
                            {linkedOverlayStyle ? (
                              <div
                                className="pointer-events-none absolute z-20 rounded border-2 border-primary bg-primary/10 shadow-[0_0_0_1px_rgba(37,99,235,0.14)]"
                                style={linkedOverlayStyle}
                              />
                            ) : null}
                            {noteMarkersOnPage.map((marker) => {
                              const block = marker.block;
                              const markerStyle = getPdfSideMarkerStyle(block, isMobileViewport ? 8 : 20, pageCanvas);
                              if (!markerStyle || !block) return null;
                              return (
                                <button
                                  key={marker.key}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    handleOpenNotesForBlock(block);
                                  }}
                                  className="absolute z-30 inline-flex min-h-6 min-w-6 items-center justify-center gap-1 rounded-full border border-amber-400/70 bg-page/96 px-1.5 text-[10px] font-semibold text-amber-700 shadow-sm transition-colors hover:border-amber-500 hover:bg-amber-50"
                                  style={markerStyle}
                                  title={`该位置已有 ${marker.count} 条笔记`}
                                >
                                  <Pin className="h-3 w-3" />
                                  <span>{marker.count}</span>
                                </button>
                              );
                            })}
                            {activeRect ? <div className="pointer-events-none absolute z-30 border-2 border-primary shadow-[0_0_0_9999px_rgba(0,0,0,0.18)]" style={{ left: activeRect.x, top: activeRect.y, width: activeRect.width, height: activeRect.height }} /> : null}
                            {dragRect ? <div className="pointer-events-none absolute z-30 border-2 border-primary bg-primary/10" style={{ left: dragRect.x, top: dragRect.y, width: dragRect.width, height: dragRect.height }} /> : null}
                          </div>
                        ) : (
                          <div className="rounded bg-surface/10" style={{ width: Math.round(612 * scale), height: Math.round(792 * scale) }} />
                        )}
                        {fallbackPageNoteCount > 0 ? (
                          <button
                            onClick={() => handleOpenNotesForPage(page)}
                            className="absolute z-20 inline-flex min-h-6 min-w-6 items-center justify-center gap-1 rounded-full border border-amber-400/70 bg-page/96 px-1.5 text-[10px] font-semibold text-amber-700 shadow-sm transition-colors hover:border-amber-500 hover:bg-amber-50"
                            style={getPdfPageSideMarkerStyle(0, isMobileViewport ? 8 : 14)}
                            title={`本页有 ${fallbackPageNoteCount} 条未精确定位的笔记`}
                          >
                            <Pin className="h-3 w-3" />
                            <span>{fallbackPageNoteCount}</span>
                          </button>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </Document>
            )}
          </div>

          {selectionOverlayVisible ? (
            <div className={cn(
              "absolute z-30 rounded-2xl border border-primary/20 bg-surface/92 p-2 shadow-xl backdrop-blur",
              isMobileViewport
                ? "bottom-[4.85rem] left-2 right-2 max-h-[min(34vh,18rem)] overflow-auto"
                : "bottom-4 left-3 max-w-[calc(100%-1.5rem)]",
            )}>
              {!inlinePdfSelectionEditor ? (
                <div className="mb-2 max-w-[24rem] text-xs leading-6 text-ink-secondary">{preview(selectedText, isMobileViewport ? 96 : 140)}</div>
              ) : null}
              <div className="flex flex-wrap gap-2">
                <ActionButton disabled={!!loadingLabel} onClick={() => void runReaderQuery({ scope: "selection", action: "translate", text: selectedText, page_number: selectedPage || undefined }, "正在翻译选中文本...")}>
                  <span className="inline-flex items-center gap-1.5"><Languages className="h-3.5 w-3.5" />翻译</span>
                </ActionButton>
                <ActionButton disabled={!!loadingLabel} onClick={() => void runReaderQuery({ scope: "selection", action: "analyze", text: selectedText, page_number: selectedPage || undefined }, "正在分析选中文本...")}>
                  分析
                </ActionButton>
                <ActionButton disabled={!!noteBusyLabel} onClick={handleCreateSelectionNote}>
                  <span className="inline-flex items-center gap-1.5"><PencilLine className="h-3.5 w-3.5" />写笔记</span>
                </ActionButton>
                <ActionButton disabled={!!noteBusyLabel} onClick={() => void handleGenerateSelectionNoteDraft()}>
                  <span className="inline-flex items-center gap-1.5"><Wand2 className="h-3.5 w-3.5" />AI 批注</span>
                </ActionButton>
              </div>
              {inlinePdfSelectionEditor && noteEditor ? (
                <div className="mt-3 w-full sm:w-[min(28rem,calc(100vw-2.5rem))]">
                  <NoteEditorCard
                    note={noteEditor}
                    busy={Boolean(noteBusyLabel)}
                    onChange={(patch) => setNoteEditor((current) => (current ? { ...current, ...patch } : current))}
                    onSave={() => void handleSaveNote()}
                    onCancel={() => setNoteEditor(null)}
                  />
                </div>
              ) : null}
            </div>
          ) : null}

        </div>

        {!isMobileViewport ? (
          <>
            {panelOpen ? (
              <button
                onMouseDown={handleStartSidebarResize}
                className="flex w-3 shrink-0 cursor-col-resize items-center justify-center bg-transparent"
                aria-label="调整右侧面板宽度"
              >
                <span className="h-16 w-1 rounded-full bg-border/80 transition-colors hover:bg-primary/50" />
              </button>
            ) : null}
            <div
              className={cn(
                "overflow-hidden border-l border-border bg-surface transition-all duration-300",
                panelOpen ? "" : "w-0",
              )}
              style={panelOpen ? { width: `${workspaceWidth}px` } : undefined}
            >
              <div className="flex h-full flex-col" style={{ width: `${workspaceWidth}px` }}>
              <div className="border-b border-border px-3 py-3">
                <div className="flex gap-2">
                  <TabButton active={activeTab === "ocr"} icon={<FileText className="h-3.5 w-3.5" />} label="OCR" onClick={() => setActiveTab("ocr")} />
                  <TabButton active={activeTab === "notes"} icon={<PencilLine className="h-3.5 w-3.5" />} label="笔记" onClick={() => setActiveTab("notes")} />
                  <TabButton active={activeTab === "results"} icon={<Sparkles className="h-3.5 w-3.5" />} label="助手" onClick={() => setActiveTab("results")} />
                </div>
                {activeTab === "ocr" ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    <ActionButton onClick={() => void loadDocument()} disabled={documentLoading}>
                      <span className="inline-flex items-center gap-1.5"><RefreshCw className="h-3.5 w-3.5" />刷新</span>
                    </ActionButton>
                    <ActionButton onClick={() => void handleStartOcr()} tone="primary">
                      <span className="inline-flex items-center gap-1.5"><Sparkles className="h-3.5 w-3.5" />重跑 OCR</span>
                    </ActionButton>
                  </div>
                ) : null}
              </div>
              <div className="flex-1 overflow-auto px-3 py-3">
                {renderWorkspaceBody()}
              </div>
            </div>
            </div>
          </>
        ) : null}
      </div>

      {isMobileViewport ? (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 z-40 flex flex-col">
          <div
            ref={mobileSheetRef}
            className={cn(
              "overflow-hidden rounded-t-[1.75rem] border-t border-border bg-surface/96 shadow-[0_-16px_32px_rgba(15,23,42,0.18)] will-change-[height,transform,opacity]",
              mobileSheetDragging ? "transition-none" : "transition-all duration-300",
            )}
            style={{
              height: `${mobileSheetHeightPx}px`,
              opacity: workspaceVisible ? 1 : 0,
              transform: workspaceVisible ? "translateY(0)" : "translateY(calc(100% + 0.75rem))",
              pointerEvents: workspaceVisible ? "auto" : "none",
            }}
          >
            <div className="flex h-full flex-col">
              <div className="border-b border-border px-3 py-1.5">
                <div
                  className="mb-1.5 flex justify-center"
                  style={{ touchAction: "none" }}
                  onTouchStart={handleMobileSheetTouchStart}
                  onTouchMove={handleMobileSheetTouchMove}
                  onTouchEnd={handleMobileSheetTouchEnd}
                  onTouchCancel={handleMobileSheetTouchEnd}
                >
                  <div className="h-1.5 w-14 rounded-full bg-border/80" />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 text-sm text-ink">{currentWorkspaceTitle}</div>
                  <button onClick={() => setActiveTab("pdf")} className="toolbar-btn">
                    <Minimize2 className="h-4 w-4" />
                  </button>
                </div>
                {activeTab === "ocr" ? (
                  <div className="mt-2 flex flex-wrap gap-2">
                    <ActionButton onClick={() => void loadDocument()} disabled={documentLoading}>
                      <span className="inline-flex items-center gap-1.5"><RefreshCw className="h-3.5 w-3.5" />刷新</span>
                    </ActionButton>
                    <ActionButton onClick={() => void handleStartOcr()} tone="primary">
                      <span className="inline-flex items-center gap-1.5"><Sparkles className="h-3.5 w-3.5" />重跑 OCR</span>
                    </ActionButton>
                  </div>
                ) : null}
              </div>
              <div className="flex-1 overflow-auto px-3 py-3">
                {renderWorkspaceBody()}
              </div>
            </div>
          </div>

          <div className="pointer-events-auto border-t border-border bg-surface/98 px-2 pb-[max(env(safe-area-inset-bottom),0.5rem)] pt-1.5 shadow-[0_-10px_24px_rgba(15,23,42,0.12)]">
            <div className="grid grid-cols-4 gap-1.5">
              <TabButton active={activeTab === "pdf"} icon={<BookOpen className="h-4 w-4" />} label="PDF" onClick={() => setActiveTab("pdf")} />
              <TabButton active={activeTab === "ocr"} icon={<FileText className="h-4 w-4" />} label="OCR" onClick={() => ensureWorkspaceTab("ocr")} />
              <TabButton active={activeTab === "notes"} icon={<PencilLine className="h-4 w-4" />} label="笔记" onClick={() => ensureWorkspaceTab("notes")} />
              <TabButton active={activeTab === "results"} icon={<Sparkles className="h-4 w-4" />} label="助手" onClick={() => ensureWorkspaceTab("results")} />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
