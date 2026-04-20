import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import Markdown from "@/components/Markdown";
import { cn } from "@/lib/utils";
import { paperApi } from "@/services/api";
import type { PaperReaderAction, PaperReaderQueryResponse, PaperReaderScope } from "@/types";
import {
  BookOpen,
  Check,
  Copy,
  FileText,
  Highlighter,
  Image as ImageIcon,
  Languages,
  Lightbulb,
  Loader2,
  Maximize2,
  MessageSquareText,
  Minimize2,
  RefreshCw,
  ScanSearch,
  SendHorizontal,
  Sparkles,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

interface PdfReaderProps {
  paperId: string;
  paperTitle: string;
  paperArxivId?: string;
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

const ACTION_LABEL: Record<PaperReaderAction, string> = {
  analyze: "分析",
  explain: "分析",
  translate: "翻译",
  summarize: "分析",
  ask: "问答",
};

function clamp(v: number, min: number, max: number) {
  return Math.min(Math.max(v, min), max);
}

function normalizeText(v: string) {
  return String(v || "").replace(/\s+/g, " ").trim();
}

function preview(v: string, max = 120) {
  const text = normalizeText(v);
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

function ActionButton({
  onClick,
  disabled,
  children,
  tone = "default",
}: {
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
  tone?: "default" | "primary";
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "rounded-lg px-3 py-2 text-xs transition-colors disabled:opacity-50",
        tone === "primary" ? "bg-primary/20 text-primary hover:bg-primary/30" : "bg-surface/72 text-ink-secondary hover:bg-hover",
      )}
    >
      {children}
    </button>
  );
}

export default function PdfReader({ paperId, paperTitle, paperArxivId, onClose }: PdfReaderProps) {
  const [numPages, setNumPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [scale, setScale] = useState(1.2);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const [isMobileViewport, setIsMobileViewport] = useState(false);

  const [selectedText, setSelectedText] = useState("");
  const [selectedPage, setSelectedPage] = useState<number | null>(null);
  const [paperQuestion, setPaperQuestion] = useState("");
  const [selectionQuestion, setSelectionQuestion] = useState("");
  const [regionQuestion, setRegionQuestion] = useState("");

  const [results, setResults] = useState<ReaderResult[]>([]);
  const [loadingLabel, setLoadingLabel] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const [regionMode, setRegionMode] = useState(false);
  const [regionDrag, setRegionDrag] = useState<RegionDrag | null>(null);
  const [regionSelection, setRegionSelection] = useState<RegionSelection | null>(null);

  const [pageInput, setPageInput] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const pdfUrl = useMemo(() => paperApi.pdfUrl(paperId, paperArxivId), [paperArxivId, paperId]);
  const pages = useMemo(() => Array.from({ length: numPages }, (_, i) => i + 1), [numPages]);

  const setCopied = useCallback((key: string) => {
    setCopiedKey(key);
    window.setTimeout(() => setCopiedKey((current) => (current === key ? null : current)), 1500);
  }, []);

  const appendResult = useCallback((response: PaperReaderQueryResponse, fallbackText?: string, fallbackQuestion?: string) => {
    setResults((prev) => [
      {
        ...response,
        id: `reader-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        label: resultLabel(response),
        excerpt: preview(String(response.caption || fallbackQuestion || response.text || fallbackText || "")),
      },
      ...prev,
    ].slice(0, 24));
    setPanelOpen(true);
  }, []);

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
      appendResult({ scope: body.scope, action: body.action, result: `错误: ${error instanceof Error ? error.message : String(error)}` }, body.text, body.question);
      return null;
    } finally {
      setLoadingLabel(null);
    }
  }, [appendResult, paperId]);

  const setPageRef = useCallback((page: number, el: HTMLDivElement | null) => {
    if (el) pageRefs.current.set(page, el);
    else pageRefs.current.delete(page);
  }, []);

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
    setPanelOpen(true);
  }, [cropRegion]);

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

  useEffect(() => {
    if (typeof window === "undefined") return;
    const media = window.matchMedia("(max-width: 767px)");
    const sync = () => setIsMobileViewport(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    if (isMobileViewport) {
      setScale((value) => (value > 1 ? 0.9 : value));
    }
  }, [isMobileViewport]);

  useEffect(() => {
    const onLoadState = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onLoadState);
    return () => document.removeEventListener("fullscreenchange", onLoadState);
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
      setPanelOpen(true);
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

  const scrollToPage = useCallback((page: number) => {
    const target = Math.max(1, Math.min(page, numPages || 1));
    pageRefs.current.get(target)?.scrollIntoView({ behavior: "smooth", block: "start" });
    setCurrentPage(target);
  }, [numPages]);

  return (
    <div ref={containerRef} className="fixed inset-0 z-50 flex bg-page/95 backdrop-blur-sm">
      <div className="theme-terminal-header absolute left-0 right-0 top-0 z-20 border-b px-3 py-2 sm:px-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-ink">
            <BookOpen className="h-4 w-4 text-primary" />
            <span className="truncate text-sm">{paperTitle}</span>
          </div>
          <div className="mt-1 flex gap-2 text-[11px] text-ink-tertiary">
            {selectedPage ? <span>选区 p.{selectedPage}</span> : null}
            {regionSelection ? <span>区域 p.{regionSelection.page}</span> : null}
            {regionMode ? <span className="text-primary">框选中</span> : null}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-1 sm:justify-end">
          <div className="flex items-center gap-1 rounded-md border border-border/60 bg-surface/72 px-2 py-1">
            <input
              value={pageInput}
              onChange={(event) => setPageInput(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && (scrollToPage(Number.parseInt(pageInput, 10)), setPageInput(""))}
              placeholder={String(currentPage)}
              className="w-8 bg-transparent text-center text-xs text-ink outline-none placeholder:text-ink-placeholder"
            />
            <span className="text-xs text-ink-tertiary">/ {numPages}</span>
          </div>
          <button onClick={() => setScale((value) => Math.max(value - 0.2, 0.5))} className="toolbar-btn"><ZoomOut className="h-4 w-4" /></button>
          <button onClick={() => setScale(1.2)} className="toolbar-btn-text">{Math.round(scale * 100)}%</button>
          <button onClick={() => setScale((value) => Math.min(value + 0.2, 3))} className="toolbar-btn"><ZoomIn className="h-4 w-4" /></button>
          <button onClick={() => { setRegionMode((value) => !value); setRegionDrag(null); }} className={cn("toolbar-btn", regionMode && "bg-primary/30 text-primary")}><ImageIcon className="h-4 w-4" /></button>
          <button onClick={() => !isFullscreen ? containerRef.current?.requestFullscreen?.() : document.exitFullscreen?.()} className="toolbar-btn">
            {isFullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </button>
          <button onClick={() => setPanelOpen((value) => !value)} className={cn("toolbar-btn", panelOpen && "bg-primary/30 text-primary")}><MessageSquareText className="h-4 w-4" /></button>
          <button onClick={onClose} className="toolbar-btn hover:bg-red-500/20 hover:text-red-300"><X className="h-4 w-4" /></button>
        </div>
        </div>
      </div>

      <div
        ref={scrollRef}
        className={cn(
          "mt-[72px] flex-1 overflow-auto pb-10 sm:mt-12",
          isMobileViewport && panelOpen && "pb-[min(60dvh,34rem)]",
        )}
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
            <div className="flex flex-col items-center gap-4 px-2 py-4 sm:py-6">
              {pages.map((page) => {
                const nearby = Math.abs(page - currentPage) <= 3;
                const activeRect = regionSelection?.page === page ? regionSelection : null;
                const dragRect = regionDrag?.page === page ? regionDrag : null;
                return (
                  <div
                    key={page}
                    data-page={page}
                    ref={(el) => setPageRef(page, el)}
                    className="relative max-w-full"
                    style={!nearby ? { minHeight: `${Math.round(792 * scale)}px`, width: `${Math.round(612 * scale)}px`, maxWidth: "100%" } : undefined}
                  >
                    <div className="absolute left-1/2 top-0 z-10 -translate-x-1/2 -translate-y-full pb-1"><span className="rounded-full border border-border/60 bg-surface/72 px-2 py-0.5 text-[10px] text-ink-tertiary">{page}</span></div>
                    {nearby ? (
                      <div className={cn("relative inline-block max-w-full", regionMode && "pdf-region-mode")}>
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
                        {activeRect ? <div className="pointer-events-none absolute z-30 border-2 border-primary shadow-[0_0_0_9999px_rgba(0,0,0,0.18)]" style={{ left: activeRect.x, top: activeRect.y, width: activeRect.width, height: activeRect.height }} /> : null}
                        {dragRect ? <div className="pointer-events-none absolute z-30 border-2 border-primary bg-primary/10" style={{ left: dragRect.x, top: dragRect.y, width: dragRect.width, height: dragRect.height }} /> : null}
                      </div>
                    ) : (
                      <div className="rounded bg-surface/10" style={{ width: Math.round(612 * scale), height: Math.round(792 * scale) }} />
                    )}
                  </div>
                );
              })}
            </div>
          </Document>
        )}
      </div>

      <div
        className={cn(
          "overflow-hidden bg-surface transition-all duration-300",
          isMobileViewport
            ? "absolute inset-x-0 bottom-0 z-30 border-t border-border shadow-[0_-14px_28px_rgba(15,23,42,0.16)]"
            : "relative mt-12 border-l border-border",
          panelOpen
            ? isMobileViewport
              ? "h-[min(60dvh,34rem)]"
              : "w-[420px]"
            : isMobileViewport
              ? "h-0"
              : "w-0",
        )}
      >
        <div className={cn("flex h-full flex-col", isMobileViewport ? "w-full" : "w-[420px]")}>
          <div className="border-b border-border px-3 py-3 text-sm text-ink sm:px-4">阅读助手</div>
          <div className="flex-1 space-y-4 overflow-auto px-3 py-3 sm:px-4 sm:py-4">
            <section className="rounded-xl border border-border/70 bg-surface/72 p-3">
              <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-ink-tertiary"><FileText className="h-3.5 w-3.5" />整篇论文</div>
              <div className="grid grid-cols-1 gap-2">
                <ActionButton onClick={() => void runReaderQuery({ scope: "paper", action: "analyze" }, "正在分析整篇论文...")}>分析</ActionButton>
              </div>
              <textarea value={paperQuestion} onChange={(event) => setPaperQuestion(event.target.value)} placeholder="基于整篇论文提问" className="mt-3 min-h-[84px] w-full resize-none rounded-lg border border-border bg-page/72 px-3 py-2 text-sm text-ink outline-none placeholder:text-ink-placeholder" />
              <ActionButton tone="primary" disabled={!paperQuestion.trim() || !!loadingLabel} onClick={() => { void runReaderQuery({ scope: "paper", action: "ask", question: paperQuestion.trim() }, "正在基于整篇论文回答问题..."); setPaperQuestion(""); }}>
                <span className="inline-flex items-center gap-2"><SendHorizontal className="h-4 w-4" />提问</span>
              </ActionButton>
            </section>

            <section className="rounded-xl border border-border/70 bg-surface/72 p-3">
              <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-ink-tertiary"><Highlighter className="h-3.5 w-3.5" />选中文本</div>
              {selectedText ? (
                <>
                  <div className="rounded-lg bg-page/72 p-2.5 text-xs leading-relaxed text-ink-secondary">{selectedText}</div>
                  <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <ActionButton disabled={!!loadingLabel} onClick={() => void runReaderQuery({ scope: "selection", action: "translate", text: selectedText, page_number: selectedPage || undefined }, "正在翻译选中文本...")}>翻译</ActionButton>
                    <ActionButton disabled={!!loadingLabel} onClick={() => void runReaderQuery({ scope: "selection", action: "analyze", text: selectedText, page_number: selectedPage || undefined }, "正在分析选中文本...")}>分析</ActionButton>
                  </div>
                  <div className="mt-3 flex flex-col gap-2 rounded-lg border border-border bg-page/72 p-2 sm:flex-row">
                    <input value={selectionQuestion} onChange={(event) => setSelectionQuestion(event.target.value)} placeholder="针对选区提问" className="h-9 min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-placeholder" />
                    <ActionButton tone="primary" disabled={!selectionQuestion.trim() || !!loadingLabel} onClick={() => { void runReaderQuery({ scope: "selection", action: "ask", text: selectedText, question: selectionQuestion.trim(), page_number: selectedPage || undefined }, "正在回答选区问题..."); setSelectionQuestion(""); }}>
                      <SendHorizontal className="h-4 w-4" />
                    </ActionButton>
                  </div>
                </>
              ) : <div className="rounded-lg border border-dashed border-border px-3 py-5 text-center text-xs text-ink-tertiary">暂无选区</div>}
            </section>

            <section className="rounded-xl border border-border/70 bg-surface/72 p-3">
              <div className="mb-3 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-ink-tertiary"><ScanSearch className="h-3.5 w-3.5" />框选区域</div>
                <div className="flex gap-2">
                  <ActionButton onClick={() => { setRegionMode((value) => !value); setRegionDrag(null); }}>{regionMode ? "取消" : "开始框选"}</ActionButton>
                  {regionSelection ? <ActionButton onClick={() => { setRegionSelection(null); setRegionQuestion(""); }}>清除</ActionButton> : null}
                </div>
              </div>
              {regionSelection ? (
                <>
                  <div className="overflow-hidden rounded-lg border border-border bg-page/72"><img src={regionSelection.previewUrl} alt="selected region" className="max-h-44 w-full object-contain" /></div>
                  <div className="mt-2 text-[11px] text-ink-tertiary">第 {regionSelection.page} 页</div>
                  <div className="mt-3 grid grid-cols-1 gap-2">
                    <ActionButton disabled={!!loadingLabel} onClick={() => void runReaderQuery({ scope: "figure", action: "analyze", image_base64: regionSelection.imageBase64, page_number: regionSelection.page }, "正在分析框选区域...")}>分析</ActionButton>
                  </div>
                  <div className="mt-3 flex flex-col gap-2 rounded-lg border border-border bg-page/72 p-2 sm:flex-row">
                    <input value={regionQuestion} onChange={(event) => setRegionQuestion(event.target.value)} placeholder="针对区域提问" className="h-9 min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-placeholder" />
                    <ActionButton tone="primary" disabled={!regionQuestion.trim() || !!loadingLabel} onClick={() => { void runReaderQuery({ scope: "figure", action: "ask", image_base64: regionSelection.imageBase64, page_number: regionSelection.page, question: regionQuestion.trim() }, "正在分析框选区域..."); setRegionQuestion(""); }}>
                      <SendHorizontal className="h-4 w-4" />
                    </ActionButton>
                  </div>
                </>
              ) : <div className="rounded-lg border border-dashed border-border px-3 py-5 text-center text-xs text-ink-tertiary">{regionMode ? "在页面上拖拽框选" : "暂无区域"}</div>}
            </section>

            {loadingLabel ? <div className="flex items-center gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-sm text-primary"><Loader2 className="h-4 w-4 animate-spin" />{loadingLabel}</div> : null}

            <section className="rounded-xl border border-border/70 bg-surface/72 p-3">
              <div className="mb-3 flex items-center justify-between gap-2 text-xs uppercase tracking-[0.16em] text-ink-tertiary">
                <span className="inline-flex items-center gap-2"><Sparkles className="h-3.5 w-3.5" />结果</span>
                <button onClick={() => setResults([])} className="rounded-md p-1 text-ink-tertiary transition hover:bg-hover hover:text-ink-secondary"><RefreshCw className="h-3.5 w-3.5" /></button>
              </div>
              {results.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-xs text-ink-tertiary">暂无结果</div>
              ) : (
                <div className="space-y-3">
                  {results.map((item) => {
                    const copyKey = `result-${item.id}`;
                    return (
                      <div key={item.id} className="rounded-lg border border-border/70 bg-page/72 p-3">
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
        </div>
      </div>
    </div>
  );
}
