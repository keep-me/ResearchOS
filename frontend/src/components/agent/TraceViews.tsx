import { lazy, memo, Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import DOMPurify from "dompurify";
import SignedAssetImage from "@/components/SignedAssetImage";
import { cn } from "@/lib/utils";
import { useAssistantInstance, type ChatItem, type StepItem } from "@/contexts/AssistantInstanceContext";
import { useToast } from "@/contexts/ToastContext";
import { normalizeReasoningDisplay } from "@/features/assistantInstance/reasoningText";
import { ingestApi, paperApi } from "@/services/api";
import { deriveProjectName, getToolMeta, type WorkspaceFileTreeNode } from "./agentPageShared";
import {
  AlertTriangle,
  BadgeCheck,
  BookOpen,
  Brain,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  FileText,
  FolderOpen,
  FolderTree,
  Globe,
  Hash,
  Link2,
  Loader2,
  Newspaper,
  PanelRightOpen,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Server,
  Shield,
  Sparkles,
  Square,
  Star,
  TerminalSquare,
  TrendingUp,
  Upload,
  X,
  XCircle,
} from "@/lib/lucide";

const Markdown = lazy(() => import("@/components/Markdown"));

function SanitizedHtmlPreview({ content, className }: { content: string; className?: string }) {
  const sanitized = useMemo(
    () => DOMPurify.sanitize(String(content || ""), { USE_PROFILES: { html: true, svg: true, svgFilters: true } }),
    [content],
  );
  return <div className={className} dangerouslySetInnerHTML={{ __html: sanitized }} />;
}

function safeHttpUrl(value: unknown): string | undefined {
  const raw = String(value || "").trim();
  if (!raw) return undefined;
  try {
    const url = new URL(raw);
    if (url.protocol === "http:" || url.protocol === "https:") {
      return url.toString();
    }
  } catch {
    return undefined;
  }
  return undefined;
}

export const EmptyState = memo(function EmptyState({
  mountedPaperSummary,
  mountedPaperCount,
  sessionTitle,
  workspaceName,
  assistantDirectory,
  onDraftPrompt,
  onStartConversation,
  onOpenWorkspace,
  children,
}: {
  mountedPaperSummary?: string;
  mountedPaperCount: number;
  sessionTitle: string;
  workspaceName?: string;
  assistantDirectory?: string;
  onDraftPrompt: (prompt: string) => void;
  onStartConversation?: (() => void) | null;
  onOpenWorkspace?: (() => void) | null;
  children?: ReactNode;
}) {
  const heading = sessionTitle && sessionTitle !== "新对话" ? sessionTitle : "ResearchOS";
  const quickActions = [
    {
      icon: Search,
      title: "检索一个方向",
      description: "把主题拆成代表论文、主要流派和建议阅读顺序。",
      prompt: "请帮我检索一个研究主题的代表论文，按方向分组并给出阅读顺序。",
    },
    {
      icon: Brain,
      title: mountedPaperCount > 0 ? "分析当前论文" : "建立研究问题",
      description: mountedPaperCount > 0
        ? "围绕当前挂载论文提炼问题、差距和下一步动作。"
        : "从问题定义、关键词和检索策略开始搭一个研究骨架。",
      prompt: mountedPaperCount > 0
        ? "请基于当前挂载的论文，先提炼研究问题，再给出下一步阅读与实验建议。"
        : "我想启动一个新研究主题，请先帮我定义问题、关键词和检索策略。",
    },
    {
      icon: FolderTree,
      title: assistantDirectory ? "扫一遍工作区" : "整理研究流程",
      description: assistantDirectory
        ? "快速检查当前目录，告诉我最值得做的 3 个下一步动作。"
        : "把收集、阅读、分析和写作串成最短执行路径。",
      prompt: assistantDirectory
        ? "请先检查当前工作区，总结已有材料，并给出最值得做的 3 个下一步动作。"
        : "请帮我把论文收集、阅读、分析和写作整理成一个可执行研究流程。",
    },
  ] as const;

  return (
    <div className="flex h-full items-center">
      <div className="mx-auto flex w-full max-w-[940px] flex-col items-center px-5 pb-12 pt-8 text-center lg:px-6 lg:pb-16 lg:pt-12">
        <div className="inline-flex items-center gap-2 rounded-full border border-primary/12 bg-primary/8 px-3 py-1.5 text-[11px] font-medium text-primary">
          <Sparkles className="h-3.5 w-3.5" />
          <span>ResearchOS · 研究助手</span>
        </div>
        <div className="mt-5 flex h-16 w-16 items-center justify-center rounded-[22px] border border-border/70 bg-white shadow-[0_22px_60px_-40px_rgba(15,23,35,0.34)]">
          <Sparkles className="h-7 w-7 text-primary" />
        </div>
        <h2 className="mt-4 text-[1.9rem] font-semibold tracking-[-0.06em] text-ink lg:text-[2.15rem]">
          {heading}
        </h2>
        <div className="mt-5 flex max-w-full flex-wrap items-center justify-center gap-2 text-[11px] text-ink-tertiary">
          <span className="rounded-full border border-border/70 bg-white px-3 py-1">
            角色：ResearchOS
          </span>
          <span className="rounded-full border border-border/70 bg-white px-3 py-1">
            工作区：{workspaceName || (assistantDirectory ? "已绑定" : "未绑定")}
          </span>
          <span className="rounded-full border border-border/70 bg-white px-3 py-1">
            论文上下文：{mountedPaperCount}
          </span>
          {onOpenWorkspace ? (
            <button
              type="button"
              onClick={onOpenWorkspace}
              className="rounded-full border border-border/70 bg-white px-3 py-1 text-primary transition-colors duration-150 hover:bg-hover"
            >
              打开工作区
            </button>
          ) : null}
        </div>
        {mountedPaperSummary ? (
          <div
            className="mt-4 inline-flex max-w-full items-center gap-2 rounded-[18px] border border-primary/15 bg-primary/8 px-3 py-2 text-[11px] text-primary"
            title={mountedPaperSummary}
          >
            <Link2 className="h-3.5 w-3.5 shrink-0" />
            <span className="max-w-[min(92vw,560px)] break-words whitespace-normal text-left">
              当前已挂载论文：{mountedPaperSummary}
            </span>
          </div>
        ) : null}
        {onStartConversation ? (
          <button
            type="button"
            onClick={onStartConversation}
            className="mt-6 inline-flex items-center justify-center rounded-full bg-primary px-5 py-2.5 text-sm font-medium text-white transition-colors duration-150 hover:bg-primary-hover"
          >
            发起研究对话
          </button>
        ) : null}
        <div className="mt-8 grid w-full gap-3 text-left sm:grid-cols-3">
          {quickActions.map((action) => (
            <button
              key={action.title}
              type="button"
              onClick={() => onDraftPrompt(action.prompt)}
              className="group rounded-[22px] border border-border bg-white p-4 text-left transition-colors duration-150 hover:bg-hover"
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                <action.icon className="h-5 w-5" />
              </div>
              <p className="mt-4 text-sm font-semibold text-ink">{action.title}</p>
              <div className="mt-4 text-[11px] font-medium text-primary transition-colors duration-150 group-hover:text-primary-hover">
                填入输入框
              </div>
            </button>
          ))}
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-center gap-2 text-[11px] text-ink-tertiary">
          {["论文检索", "PDF 阅读", "三轮分析", "工作区检查"].map((capability) => (
            <span key={capability} className="rounded-full border border-border/70 bg-white px-3 py-1">
              {capability}
            </span>
          ))}
        </div>
        {children}
      </div>
    </div>
  );
});

export function CanvasPanel({
  title,
  content,
  isHtml,
  onClose,
  onNavigate,
  mobile = false,
}: {
  title: string;
  content: string;
  isHtml?: boolean;
  onClose: () => void;
  onNavigate: (paperId: string) => void;
  mobile?: boolean;
}) {
  return (
    <div className={cn(
      "flex h-full flex-col overflow-hidden rounded-xl border border-border bg-white",
      mobile && "rounded-none border-none shadow-none",
    )}>
      <div className="flex items-center justify-between border-b border-border bg-white px-5 py-4">
        <div className="flex items-center gap-2">
          <PanelRightOpen className="h-4 w-4 text-primary" />
          <span className="text-sm font-semibold text-ink">{title}</span>
        </div>
        <button
          aria-label="关闭面板"
          onClick={onClose}
          className="flex h-9 w-9 items-center justify-center rounded-md text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-ink"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div
        className="flex-1 overflow-y-auto bg-[#fcfbf8] px-5 py-5"
        onClick={(e) => {
          const card = (e.target as HTMLElement).closest<HTMLElement>("[data-paper-id]");
          if (card?.dataset.paperId) onNavigate(card.dataset.paperId);
        }}
      >
	        {isHtml ? (
	          <SanitizedHtmlPreview
	            className="prose-custom brief-html-preview brief-content"
	            content={content}
	          />
	        ) : (
          <div className="prose-custom">
            <Suspense fallback={<div className="h-4 animate-pulse rounded bg-surface" />}>
              <Markdown>{content}</Markdown>
            </Suspense>
          </div>
        )}
      </div>
    </div>
  );
}

/* ========== 消息块 ========== */

export const ChatBlock = memo(function ChatBlock({
  item, mountedPrimaryPaperId, isPending, isConfirming, onConfirm, onReject, onQuestionSubmit, onOpenArtifact, onRetry,
}: {
  item: ChatItem; mountedPrimaryPaperId?: string | null; isPending: boolean; isConfirming: boolean;
  onConfirm: (id: string) => void; onReject: (id: string) => void;
  onQuestionSubmit: (id: string, answers: string[][]) => void;
  onOpenArtifact: (title: string, content: string, isHtml?: boolean) => void;
  onRetry?: () => void;
}) {
  switch (item.type) {
    case "user": return <UserMessage content={item.content} />;
    case "assistant": return <AssistantMessage content={item.content} streaming={!!item.streaming} mode={item.messageMode} mountedPrimaryPaperId={mountedPrimaryPaperId} />;
    case "reasoning": return <ReasoningMessage content={item.content} />;
    case "step_group": return <StepGroupCard steps={item.steps || []} />;
    case "action_confirm": return <ActionConfirmCard actionId={item.actionId || ""} description={item.actionDescription || ""} tool={item.actionTool || ""} args={item.toolArgs} isPending={isPending} isConfirming={isConfirming} onConfirm={onConfirm} onReject={onReject} />;
    case "question": return <QuestionCard actionId={item.actionId || ""} description={item.actionDescription || ""} questions={item.questionItems || []} isPending={isPending} isConfirming={isConfirming} onSubmit={onQuestionSubmit} onReject={onReject} />;
    case "artifact": return <ArtifactCard title={item.artifactTitle || ""} content={item.artifactContent || ""} isHtml={item.artifactIsHtml} onOpen={() => onOpenArtifact(item.artifactTitle || "", item.artifactContent || "", item.artifactIsHtml)} />;
    case "error": return <ErrorCard content={item.content} onRetry={onRetry} />;
    default: return null;
  }
});

/**
 * 用户消息 - Claude 风格：无头像，右对齐浅色气泡
 */
export const UserMessage = memo(function UserMessage({ content }: { content: string }) {
  return (
    <div className="flex justify-end py-3">
      <div className="max-w-[82%] rounded-xl border border-border bg-white px-4 py-3.5 text-sm leading-7 text-ink">
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-primary/85">
          You
        </div>
        <div className="whitespace-pre-wrap">{content}</div>
      </div>
    </div>
  );
});

/**
 * Assistant 消息 - Claude 风格：无头像，无气泡背景，纯文字流
 */
export const AssistantMessage = memo(function AssistantMessage({
  content,
  streaming,
  mode,
  mountedPrimaryPaperId,
}: {
  content: string;
  streaming: boolean;
  mode?: string;
  mountedPrimaryPaperId?: string | null;
}) {
  const [copied, setCopied] = useState(false);
  const isPlanMode = mode === "plan";
  const renderedContent = useMemo(() => {
    const text = String(content || "");
    const paperId = String(mountedPrimaryPaperId || "").trim();
    if (!text || !paperId) return text;
    if (/\!\[[^\]]*\]\((?:https?:\/\/|\/)?[^)]*\/papers\/[^)]+\/figures\/[^)]+\)/i.test(text)) {
      return text;
    }

    const figureRefPattern = /figure_ref:\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/g;
    const figureIds: string[] = [];
    let match: RegExpExecArray | null = null;
    while ((match = figureRefPattern.exec(text)) !== null) {
      const figureId = String(match[1] || "").trim();
      if (figureId && !figureIds.includes(figureId)) figureIds.push(figureId);
      if (figureIds.length >= 4) break;
    }
    if (figureIds.length <= 0) return text;

    const previews = figureIds
      .map((figureId) => `![原图 ${figureId.slice(0, 8)}](${paperApi.figureImageUrl(paperId, figureId)})`)
      .join("\n\n");
    return `${text}\n\n---\n\n原图预览：\n\n${previews}`;
  }, [content, mountedPrimaryPaperId]);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(renderedContent).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [renderedContent]);

  return (
    <div className="group py-3">
      <div className="rounded-xl border border-border bg-white px-4 py-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <span className="inline-flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-primary">
            <Sparkles className="h-3.5 w-3.5" />
            {isPlanMode ? "ResearchOS Plan" : "ResearchOS"}
          </span>
          {streaming ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-primary/8 px-2 py-1 text-[10px] font-medium text-primary">
              <Loader2 className="h-3 w-3 animate-spin" />
              输出中
            </span>
          ) : (
            <button
              onClick={handleCopy}
              className="flex items-center gap-1 rounded-md border border-border bg-page px-2.5 py-1 text-[11px] text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-ink-secondary"
            >
              {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
              {copied ? "已复制" : "复制"}
            </button>
          )}
        </div>
        <div className="prose-custom text-sm leading-relaxed text-ink">
          <Suspense fallback={<div className="h-4 animate-pulse rounded bg-surface" />}>
            <Markdown>{renderedContent}</Markdown>
          </Suspense>
          {streaming ? (
            <div className="mt-2">
              <span className="inline-block h-4 w-[2px] animate-pulse rounded-full bg-primary align-middle" />
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
});

/* ========== 步骤组 ========== */

export const StepGroupCard = memo(function StepGroupCard({ steps }: { steps: StepItem[] }) {
  const primaryStep = steps[0];
  const singleStep = steps.length === 1;
  return (
    <div className="py-3">
      <div className="overflow-hidden rounded-xl border border-border bg-white">
        <div className="flex items-center gap-2 border-b border-border-light bg-page/75 px-3.5 py-2.5">
          <Play className="h-3 w-3 text-primary" />
          <span className="text-xs font-medium text-ink-secondary">
            {singleStep ? getToolMeta(primaryStep.toolName).label : "工具调用"}
          </span>
          {singleStep ? (
            <span className={cn(
              "ml-auto rounded-md px-2 py-0.5 text-[10px]",
              primaryStep.status === "done"
                ? "bg-emerald-100 text-emerald-700"
                : primaryStep.status === "error"
                  ? "bg-red-100 text-red-700"
                  : "bg-amber-100 text-amber-700",
            )}>
              {primaryStep.status}
            </span>
          ) : (
            <span className="ml-auto text-[11px] text-ink-tertiary">
              {steps.filter((s) => s.status === "done").length}/{steps.length}
            </span>
          )}
        </div>
        <div className="divide-y divide-border-light">
          {steps.map((step, idx) => <StepRow key={step.id || idx} step={step} siblingSteps={steps} />)}
        </div>
      </div>
    </div>
  );
});

export function StepRow({ step, siblingSteps }: { step: StepItem; siblingSteps: StepItem[] }) {
  const isIngest = step.toolName === "ingest_arxiv";
  const [expanded, setExpanded] = useState(false);
  const meta = getToolMeta(step.toolName);
  const Icon = meta.icon;
  const hasData = step.data && Object.keys(step.data).length > 0;
  const hasProgress = step.status === "running" && step.progressTotal && step.progressTotal > 0;
  const progressPct = hasProgress ? Math.round(((step.progressCurrent || 0) / step.progressTotal!) * 100) : 0;

  const showExpanded = expanded;

  const statusIcon =
    step.status === "running" ? <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
    : step.status === "done" ? <CheckCircle2 className="h-3.5 w-3.5 text-success" />
    : <XCircle className="h-3.5 w-3.5 text-error" />;

  return (
    <div>
      <button
        onClick={() => {
          if (!hasData) return;
          setExpanded((current) => !current);
        }}
        className={cn("flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-xs transition-colors", hasData && "hover:bg-hover")}
      >
        {statusIcon}
        <Icon className="h-3.5 w-3.5 shrink-0 text-ink-tertiary" />
        <span className="font-medium text-ink">{meta.label}</span>
        {step.toolArgs && Object.keys(step.toolArgs).length > 0 && !hasProgress && (
          <span className="truncate text-ink-tertiary">
            {Object.entries(step.toolArgs).slice(0, 2).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(" · ")}
          </span>
        )}
        {hasProgress && !isIngest && (
          <span className="truncate text-ink-secondary">{step.progressMessage}</span>
        )}
        {step.summary && <span className={cn("ml-auto shrink-0 font-medium", step.success ? "text-success" : "text-error")}>{step.summary}</span>}
        {hasData && <span className="ml-1 shrink-0 text-ink-tertiary">{showExpanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}</span>}
      </button>

      {/* 入库进度面板 - 独立的可视化区域 */}
      {isIngest && hasProgress && (
        <div className="mx-3.5 mb-2.5 overflow-hidden rounded-lg border border-primary/20 bg-primary/5">
          <div className="flex items-center gap-2 px-3 py-2">
            <div className="relative h-8 w-8 shrink-0">
              <svg className="h-8 w-8 -rotate-90" viewBox="0 0 32 32">
                <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeWidth="3" className="text-border" />
                <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeWidth="3" className="text-primary transition-all duration-500" strokeDasharray={`${progressPct * 0.8168} 81.68`} strokeLinecap="round" />
              </svg>
              <span className="absolute inset-0 flex items-center justify-center text-[9px] font-bold text-primary">{progressPct}%</span>
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[11px] font-medium text-ink">{step.progressMessage}</p>
              <p className="text-[10px] text-ink-tertiary">{step.progressCurrent ?? 0} / {step.progressTotal ?? 0} 篇</p>
            </div>
            <Loader2 className="h-4 w-4 animate-spin text-primary/60" />
          </div>
          <div className="h-1 bg-border/50">
            <div className="h-full bg-primary transition-all duration-500 ease-out" style={{ width: `${progressPct}%` }} />
          </div>
        </div>
      )}

      {/* 非入库工具的简单进度条 */}
      {!isIngest && hasProgress && (
        <div className="mx-3.5 mb-2 h-1.5 overflow-hidden rounded-full bg-border">
          <div className="h-full rounded-full bg-primary transition-all duration-300 ease-out" style={{ width: `${progressPct}%` }} />
        </div>
      )}

      {showExpanded && step.data && (
        <div className="border-t border-border-light bg-page px-3.5 py-2.5">
          <StepDataView data={step.data} toolName={step.toolName} siblingSteps={siblingSteps} />
        </div>
      )}
    </div>
  );
}

/**
 * 论文列表卡片（search_papers / search_arxiv 共用）
 */
export const PaperListView = memo(function PaperListView({
  papers, label,
}: {
  papers: Array<Record<string, unknown>>; label: string;
}) {
  return (
    <div className="space-y-1.5">
      <p className="text-[11px] font-medium text-ink-secondary">{label}</p>
      <div className="max-h-56 space-y-1 overflow-y-auto">
        {papers.slice(0, 30).map((p, i) => (
          <div key={i} className="flex items-start gap-2 rounded-lg bg-surface px-2.5 py-2 text-[11px] transition-colors hover:bg-hover">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-bold text-primary">{i + 1}</span>
            <div className="min-w-0 flex-1">
              <p className="font-medium leading-snug text-ink">{String(p.title ?? "")}</p>
              <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-ink-tertiary">
                {p.arxiv_id ? <span className="font-mono">{String(p.arxiv_id)}</span> : null}
                {p.publication_date ? <span>{String(p.publication_date)}</span> : null}
                {!p.publication_date && p.publication_year ? <span>{String(p.publication_year)}</span> : null}
                {p.read_status ? <span className="rounded bg-primary/10 px-1 py-0.5 text-primary">{String(p.read_status)}</span> : null}
                {p.venue ? <span>{String(p.venue)}</span> : null}
                {p.citation_count !== undefined ? <span>引用 {String(p.citation_count)}</span> : null}
              </div>
              {Array.isArray(p.authors) && (p.authors as string[]).length > 0 && (
                <p className="mt-0.5 truncate text-[10px] text-ink-tertiary">{(p.authors as string[]).slice(0, 3).join(", ")}{(p.authors as string[]).length > 3 ? " ..." : ""}</p>
              )}
              {Array.isArray(p.categories) && (p.categories as string[]).length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {(p.categories as string[]).slice(0, 3).map((c) => (
                    <span key={c} className="rounded bg-hover px-1.5 py-0.5 text-[9px] text-ink-tertiary">{c}</span>
                  ))}
                </div>
              )}
              <div className="mt-1 flex flex-wrap gap-1">
                {p.source ? (
                  <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[9px] text-primary">
                    {String(p.source)}
                  </span>
                ) : null}
                {p.venue_tier ? (
                  <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[9px] text-amber-700 dark:text-amber-300">
                    {String(p.venue_tier).toUpperCase()}
                  </span>
                ) : null}
                {p.venue_type ? (
                  <span className="rounded bg-hover px-1.5 py-0.5 text-[9px] text-ink-tertiary">
                    {String(p.venue_type)}
                  </span>
                ) : null}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
});

export function externalLiteratureEntryKey(entry: Record<string, unknown>, index: number): string {
  const arxivId = String(entry.arxiv_id ?? "").trim();
  const openalexId = String(entry.openalex_id ?? "").trim();
  const title = String(entry.title ?? "").trim();
  const source = String(entry.source ?? "").trim();
  return `${source}::${arxivId || openalexId || title || index}`;
}

/**
 * 入库结果卡片
 */
export const IngestResultView = memo(function IngestResultView({ data }: { data: Record<string, unknown> }) {
  const total = Number(data.total ?? 0);
  const embedded = Number(data.embedded ?? 0);
  const skimmed = Number(data.skimmed ?? 0);
  const topic = String(data.topic ?? "");
  const ingested = Array.isArray(data.ingested) ? data.ingested as Array<Record<string, unknown>> : [];
  const failed = Array.isArray(data.failed) ? data.failed as Array<Record<string, unknown>> : [];
  const suggestSub = !!data.suggest_subscribe;

  return (
    <div className="space-y-2.5">
      {/* 统计条 */}
      <div className="grid grid-cols-4 gap-2">
        {[
          { label: "入库", value: total, color: "text-primary", bg: "bg-primary/10" },
          { label: "向量化", value: embedded, color: "text-success", bg: "bg-success/10" },
          { label: "粗读", value: skimmed, color: "text-blue-600 dark:text-blue-400", bg: "bg-blue-500/10" },
          { label: "失败", value: failed.length, color: failed.length > 0 ? "text-error" : "text-ink-tertiary", bg: failed.length > 0 ? "bg-error/10" : "bg-hover" },
        ].map((s) => (
          <div key={s.label} className={cn("flex flex-col items-center rounded-lg py-2", s.bg)}>
            <span className={cn("text-base font-bold", s.color)}>{s.value}</span>
            <span className="text-[10px] text-ink-tertiary">{s.label}</span>
          </div>
        ))}
      </div>

      {topic && (
        <div className="flex items-center gap-1.5 text-[11px]">
          <Hash className="h-3 w-3 text-primary" />
          <span className="text-ink-secondary">主题：</span>
          <span className="rounded-md bg-primary/10 px-1.5 py-0.5 font-medium text-primary">{topic}</span>
        </div>
      )}

      {/* 入库论文列表 */}
      {ingested.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] font-medium text-success">已入库 ({ingested.length})</p>
          <div className="max-h-32 space-y-0.5 overflow-y-auto">
            {ingested.map((p, i) => (
              <div key={i} className="flex items-center gap-1.5 rounded px-2 py-1 text-[11px]">
                <CheckCircle2 className="h-3 w-3 shrink-0 text-success" />
                <span className="truncate text-ink">{String(p.title ?? p.arxiv_id ?? "")}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 失败列表 */}
      {failed.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] font-medium text-error">失败 ({failed.length})</p>
          <div className="max-h-24 space-y-0.5 overflow-y-auto">
            {failed.map((p, i) => (
              <div key={i} className="flex items-center gap-1.5 rounded bg-error/5 px-2 py-1 text-[11px]">
                <XCircle className="h-3 w-3 shrink-0 text-error" />
                <span className="truncate text-ink">{String(p.title ?? p.arxiv_id ?? "")}</span>
                {p.error ? <span className="ml-auto shrink-0 text-[10px] text-error">{String(p.error).slice(0, 40)}</span> : null}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
});

export const ExternalLiteratureSelector = memo(function ExternalLiteratureSelector({
  papers,
  query,
}: {
  papers: Array<Record<string, unknown>>;
  query: string;
}) {
  const {
    ensureConversation,
    loading,
    mountedPaperIds,
    mountedPaperTitles,
    setMountedPapers,
  } = useAssistantInstance();
  const { toast } = useToast();
  const [submitted, setSubmitted] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(
    () => new Set(papers.map((paper, index) => externalLiteratureEntryKey(paper, index))),
  );
  const allSelected = selectedKeys.size === papers.length;

  const toggle = (key: string) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const handleSubmit = async () => {
    if (selectedKeys.size === 0 || submitted) return;
    const selectedEntries = papers.filter((paper, index) => selectedKeys.has(externalLiteratureEntryKey(paper, index)));
    if (selectedEntries.length === 0) return;
    setSubmitted(true);
    try {
      const result = await ingestApi.importLiterature({
        entries: selectedEntries.map((entry) => ({
          title: String(entry.title ?? ""),
          abstract: String(entry.abstract ?? ""),
          publication_year: entry.publication_year as number | null | undefined,
          publication_date: entry.publication_date as string | null | undefined,
          citation_count: entry.citation_count as number | null | undefined,
          venue: entry.venue as string | null | undefined,
          venue_type: entry.venue_type as string | null | undefined,
          venue_tier: entry.venue_tier as string | null | undefined,
          authors: Array.isArray(entry.authors) ? entry.authors as string[] : [],
          categories: Array.isArray(entry.categories) ? entry.categories as string[] : [],
          arxiv_id: entry.arxiv_id as string | null | undefined,
          openalex_id: entry.openalex_id as string | null | undefined,
          source_url: entry.source_url as string | null | undefined,
          pdf_url: entry.pdf_url as string | null | undefined,
          source: entry.source as string | null | undefined,
        })),
      });
      const importedPapers = result.papers || [];
      if (importedPapers.length > 0) {
        void ensureConversation();
        setMountedPapers({
          paperIds: [...mountedPaperIds, ...importedPapers.map((paper) => paper.id)],
          paperTitles: [...mountedPaperTitles, ...importedPapers.map((paper) => paper.title)],
          primaryPaperId: importedPapers[0]?.id || mountedPaperIds[0] || null,
        });
      }
      toast(
        importedPapers.length > 0 ? "success" : "warning",
        importedPapers.length > 0
          ? `已导入 ${importedPapers.length} 篇论文${query ? `：${query}` : ""}`
          : "未导入新论文，可能都已存在于库中",
      );
    } catch (error) {
      setSubmitted(false);
      toast("error", error instanceof Error ? error.message : "导入外部文献失败");
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-medium text-ink-secondary">
          {papers.length} 篇外部文献候选
        </p>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => setSelectedKeys(allSelected ? new Set() : new Set(papers.map((paper, index) => externalLiteratureEntryKey(paper, index))))}
            className="rounded-md px-2 py-0.5 text-[10px] font-medium text-primary transition-colors hover:bg-primary/10"
            type="button"
          >
            {allSelected ? "取消全选" : "全选"}
          </button>
          <span className="text-[10px] text-ink-tertiary">
            已选 {selectedKeys.size}/{papers.length}
          </span>
        </div>
      </div>
      <div className="max-h-64 space-y-1 overflow-y-auto">
        {papers.map((paper, index) => {
          const key = externalLiteratureEntryKey(paper, index);
          const selected = selectedKeys.has(key);
          const categories = Array.isArray(paper.categories) ? (paper.categories as string[]) : [];
          const authors = Array.isArray(paper.authors) ? (paper.authors as string[]) : [];
          return (
            <label
              key={key}
              className={cn(
                "flex cursor-pointer items-start gap-2.5 rounded-lg px-2.5 py-2 text-[11px] transition-colors",
                selected ? "border border-primary/20 bg-primary/5" : "border border-transparent bg-surface hover:bg-hover",
              )}
            >
              <input
                type="checkbox"
                checked={selected}
                onChange={() => toggle(key)}
                disabled={submitted}
                className="mt-1 h-3.5 w-3.5 shrink-0 rounded border-border text-primary"
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-start gap-1.5">
                  <p className="flex-1 font-medium leading-snug text-ink">{String(paper.title ?? "")}</p>
                  {paper.source ? (
                    <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[9px] font-medium text-primary">
                      {String(paper.source)}
                    </span>
                  ) : null}
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-ink-tertiary">
                  {paper.arxiv_id ? <span className="font-mono">{String(paper.arxiv_id)}</span> : null}
                  {!paper.arxiv_id && paper.openalex_id ? <span className="font-mono">{String(paper.openalex_id)}</span> : null}
                  {paper.publication_date ? <span>{String(paper.publication_date)}</span> : null}
                  {!paper.publication_date && paper.publication_year ? <span>{String(paper.publication_year)}</span> : null}
                  {paper.venue ? <span>{String(paper.venue)}</span> : null}
                  {paper.citation_count !== undefined ? <span>引用 {String(paper.citation_count)}</span> : null}
                </div>
                {authors.length > 0 ? (
                  <p className="mt-0.5 truncate text-[10px] text-ink-tertiary">{authors.slice(0, 4).join(", ")}</p>
                ) : null}
                {categories.length > 0 ? (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {categories.slice(0, 3).map((category) => (
                      <span key={category} className="rounded bg-hover px-1.5 py-0.5 text-[9px] text-ink-tertiary">
                        {category}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            </label>
          );
        })}
      </div>
      {!submitted ? (
        <button
          onClick={handleSubmit}
          disabled={selectedKeys.size === 0 || loading}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-white transition-all hover:bg-primary-hover disabled:opacity-50"
          type="button"
        >
          <Download className="h-4 w-4" />
          导入选中 ({selectedKeys.size} 篇)
        </button>
      ) : (
        <div className="flex items-center justify-center gap-2 rounded-xl bg-primary/10 px-4 py-2.5 text-sm font-medium text-primary">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在导入并挂载到当前聊天…
        </div>
      )}
    </div>
  );
});

/* ========== arXiv 候选论文选择器 ========== */

export const QUERY_TO_CATEGORIES: Record<string, string[]> = {
  "graphics": ["cs.GR"], "rendering": ["cs.GR", "cs.CV"], "vision": ["cs.CV"],
  "nlp": ["cs.CL"], "language": ["cs.CL"], "robot": ["cs.RO"], "learning": ["cs.LG", "cs.AI"],
  "neural": ["cs.LG", "cs.CV", "cs.AI"], "3d": ["cs.GR", "cs.CV"], "image": ["cs.CV"],
  "audio": ["cs.SD", "eess.AS"], "speech": ["cs.CL", "cs.SD"], "security": ["cs.CR"],
  "network": ["cs.NI"], "database": ["cs.DB"], "attention": ["cs.LG", "cs.CL"],
  "transformer": ["cs.LG", "cs.CL"], "diffusion": ["cs.CV", "cs.LG"],
  "gaussian": ["cs.GR", "cs.CV"], "nerf": ["cs.GR", "cs.CV"], "reconstruction": ["cs.GR", "cs.CV"],
  "detection": ["cs.CV"], "segmentation": ["cs.CV"], "generation": ["cs.CV", "cs.LG"],
  "llm": ["cs.CL", "cs.AI"], "agent": ["cs.AI", "cs.CL"], "rl": ["cs.LG", "cs.AI"],
  "reinforcement": ["cs.LG", "cs.AI"], "optimization": ["math.OC", "cs.LG"],
};

export function inferRelevantCategories(query: string): Set<string> {
  const qLower = query.toLowerCase();
  const cats = new Set<string>();
  for (const [kw, kwCats] of Object.entries(QUERY_TO_CATEGORIES)) {
    if (qLower.includes(kw)) kwCats.forEach(c => cats.add(c));
  }
  return cats;
}

export function isRelevantCandidate(cats: string[], relevantCats: Set<string>): boolean {
  if (relevantCats.size === 0) return true;
  return cats.some(c => relevantCats.has(c));
}

export function ArxivCandidateSelector({ candidates, query }: {
  candidates: Array<Record<string, unknown>>;
  query: string;
}) {
  const {
    ensureConversation,
    loading,
    mountedPaperIds,
    mountedPaperTitles,
    setMountedPapers,
  } = useAssistantInstance();
  const { toast } = useToast();
  const relevantCats = inferRelevantCategories(query);

  const [selected, setSelected] = useState<Set<string>>(() => {
    if (relevantCats.size === 0) return new Set(candidates.map(c => String(c.arxiv_id ?? "")));
    const relevant = new Set<string>();
    for (const c of candidates) {
      const cats = Array.isArray(c.categories) ? (c.categories as string[]) : [];
      if (isRelevantCandidate(cats, relevantCats)) relevant.add(String(c.arxiv_id ?? ""));
    }
    return relevant.size > 0 ? relevant : new Set(candidates.map(c => String(c.arxiv_id ?? "")));
  });
  const [submitted, setSubmitted] = useState(false);
  const allSelected = selected.size === candidates.length;
  const relevantCount = relevantCats.size > 0
    ? candidates.filter(c => isRelevantCandidate(Array.isArray(c.categories) ? (c.categories as string[]) : [], relevantCats)).length
    : candidates.length;

  const toggle = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const selectRelevant = () => {
    const relevant = new Set<string>();
    for (const c of candidates) {
      const cats = Array.isArray(c.categories) ? (c.categories as string[]) : [];
      if (isRelevantCandidate(cats, relevantCats)) relevant.add(String(c.arxiv_id ?? ""));
    }
    setSelected(relevant);
  };

  const handleSubmit = async () => {
    if (selected.size === 0 || submitted) return;
    setSubmitted(true);
    try {
      const result = await ingestApi.arxivIds(Array.from(selected));
      const importedPapers = result.papers || [];
      if (importedPapers.length > 0) {
        void ensureConversation();
        setMountedPapers({
          paperIds: [...mountedPaperIds, ...importedPapers.map((paper) => paper.id)],
          paperTitles: [...mountedPaperTitles, ...importedPapers.map((paper) => paper.title)],
          primaryPaperId: importedPapers[0]?.id || mountedPaperIds[0] || null,
        });
      }
      toast(
        importedPapers.length > 0 ? "success" : "warning",
        importedPapers.length > 0
          ? `已导入 ${importedPapers.length} 篇 arXiv 论文`
          : "未导入新论文，可能都已存在于库中",
      );
    } catch (error) {
      setSubmitted(false);
      toast("error", error instanceof Error ? error.message : "导入 arXiv 论文失败");
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-medium text-ink-secondary">
          {candidates.length} 篇候选论文
          {relevantCats.size > 0 && relevantCount < candidates.length && (
            <span className="ml-1 text-success">（{relevantCount} 篇高相关）</span>
          )}
        </p>
        <div className="flex items-center gap-1.5">
          {relevantCats.size > 0 && relevantCount < candidates.length && (
            <button
              onClick={selectRelevant}
              className="rounded-md px-2 py-0.5 text-[10px] font-medium text-success hover:bg-success/10 transition-colors"
            >
              仅选相关
            </button>
          )}
          <button
            onClick={() => setSelected(allSelected ? new Set() : new Set(candidates.map(c => String(c.arxiv_id ?? ""))))}
            className="rounded-md px-2 py-0.5 text-[10px] font-medium text-primary hover:bg-primary/10 transition-colors"
          >
            {allSelected ? "取消全选" : "全选"}
          </button>
          <span className="text-[10px] text-ink-tertiary">已选 {selected.size}/{candidates.length}</span>
        </div>
      </div>
      <div className="max-h-64 space-y-1 overflow-y-auto">
        {candidates.map((p, i) => {
          const aid = String(p.arxiv_id ?? "");
          const isChecked = selected.has(aid);
          const cats = Array.isArray(p.categories) ? (p.categories as string[]) : [];
          const isRelevant = isRelevantCandidate(cats, relevantCats);
          return (
            <label key={aid || i} className={cn(
              "flex items-start gap-2.5 rounded-lg px-2.5 py-2 text-[11px] cursor-pointer transition-colors",
              isChecked ? "bg-primary/5 border border-primary/20" : "bg-surface hover:bg-hover border border-transparent",
              !isRelevant && relevantCats.size > 0 && "opacity-60",
            )}>
              <input
                type="checkbox"
                checked={isChecked}
                onChange={() => toggle(aid)}
                disabled={submitted}
                className="mt-1 h-3.5 w-3.5 rounded border-border text-primary focus:ring-primary/20 shrink-0"
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-start gap-1.5">
                  <p className="font-medium leading-snug text-ink flex-1">{String(p.title ?? "")}</p>
                  {isRelevant && relevantCats.size > 0 && (
                    <span className="shrink-0 rounded bg-success/10 px-1.5 py-0.5 text-[9px] font-medium text-success">相关</span>
                  )}
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-ink-tertiary">
                  {p.arxiv_id ? <span className="font-mono">{aid}</span> : null}
                  {p.publication_date ? <span>{String(p.publication_date)}</span> : null}
                  {cats.length > 0 && cats.slice(0, 3).map(c => (
                    <span key={c} className={cn(
                      "rounded px-1 py-px text-[9px] font-mono",
                      relevantCats.has(c) ? "bg-primary/10 text-primary" : "bg-ink/5 text-ink-tertiary",
                    )}>{c}</span>
                  ))}
                </div>
                {Array.isArray(p.authors) && (p.authors as string[]).length > 0 && (
                  <p className="mt-0.5 truncate text-[10px] text-ink-tertiary">{(p.authors as string[]).slice(0, 3).join(", ")}</p>
                )}
              </div>
            </label>
          );
        })}
      </div>
      {!submitted ? (
        <button
          onClick={handleSubmit}
          disabled={selected.size === 0 || loading}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-white transition-all hover:bg-primary-hover disabled:opacity-50"
        >
          <Download className="h-4 w-4" />
          入库选中 ({selected.size} 篇)
        </button>
      ) : (
        <div className="flex items-center justify-center gap-2 rounded-xl bg-primary/10 px-4 py-2.5 text-sm font-medium text-primary">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在导入并挂载到当前聊天…
        </div>
      )}
    </div>
  );
}

export const WorkspaceTreeNodeView = memo(function WorkspaceTreeNodeView({
  node,
  depth,
  expandedDirs,
  activeFile,
  onToggleDir,
  onOpenFile,
}: {
  node: WorkspaceFileTreeNode;
  depth: number;
  expandedDirs: Record<string, boolean>;
  activeFile: string | null;
  onToggleDir: (path: string) => void;
  onOpenFile: (path: string) => void;
}) {
  if (node.type === "dir") {
    const expanded = expandedDirs[node.path] ?? depth < 1;
    return (
      <div>
        <button
          type="button"
          onClick={() => onToggleDir(node.path)}
          className="flex w-full items-center gap-1 rounded-md px-2 py-1 text-left text-[11px] text-ink-secondary transition hover:bg-white/75 hover:text-ink"
          style={{ paddingLeft: `${8 + depth * 12}px` }}
        >
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          <FolderTree className="h-3.5 w-3.5 text-primary" />
          <span className="truncate">{node.name}</span>
        </button>
        {expanded && node.children.map((child) => (
          <WorkspaceTreeNodeView
            key={child.path}
            node={child}
            depth={depth + 1}
            expandedDirs={expandedDirs}
            activeFile={activeFile}
            onToggleDir={onToggleDir}
            onOpenFile={onOpenFile}
          />
        ))}
      </div>
    );
  }

  const selected = activeFile === node.path;
  return (
    <button
      type="button"
      onClick={() => onOpenFile(node.path)}
      className={cn(
        "flex w-full items-center gap-1 rounded-md px-2 py-1 text-left text-[11px] transition",
        selected
          ? "bg-primary/10 text-primary"
          : "text-ink-secondary hover:bg-white/75 hover:text-ink",
      )}
      style={{ paddingLeft: `${20 + depth * 12}px` }}
      title={node.path}
    >
      <FileText className="h-3.5 w-3.5" />
      <span className="truncate">{node.name}</span>
    </button>
  );
});

export function asObjectRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? value as Record<string, unknown> : null;
}

export function mergeToolEnvelope(
  envelope: Record<string, unknown>,
  nested: Record<string, unknown>,
): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...nested };
  const passthroughKeys = ["success", "summary", "progress", "current", "total", "message", "error", "title"];
  for (const key of passthroughKeys) {
    if (merged[key] === undefined && envelope[key] !== undefined) {
      merged[key] = envelope[key];
    }
  }
  return merged;
}

export function normalizeToolStepData(rawData: Record<string, unknown>): Record<string, unknown> {
  let payload = asObjectRecord(rawData) || {};
  for (let depth = 0; depth < 6; depth += 1) {
    const nestedData = asObjectRecord(payload.data);
    const nestedResult = asObjectRecord(payload.result);
    const nestedPayload = asObjectRecord(payload.payload);
    const nested = nestedData || nestedResult || nestedPayload;
    if (!nested) break;

    const keys = Object.keys(payload);
    const isEnvelope =
      Object.prototype.hasOwnProperty.call(payload, "success")
      || Object.prototype.hasOwnProperty.call(payload, "summary")
      || Object.prototype.hasOwnProperty.call(payload, "progress")
      || Object.prototype.hasOwnProperty.call(payload, "current")
      || Object.prototype.hasOwnProperty.call(payload, "total")
      || Object.prototype.hasOwnProperty.call(payload, "error");
    const isThinWrapper = keys.length <= 2 && (nestedData !== null || nestedResult !== null || nestedPayload !== null);
    if (!isEnvelope && !isThinWrapper) break;
    payload = mergeToolEnvelope(payload, nested);
  }
  return payload;
}

export function resolveToolFigureImageUrl(
  paperId: string | undefined,
  figure: Record<string, unknown>,
): string | null {
  const figureId = String(figure.id ?? "").trim();
  if (figureId && paperId) {
    return paperApi.figureImageUrl(paperId, figureId);
  }
  if (typeof figure.image_url === "string" && figure.image_url.trim()) {
    return figure.image_url.trim();
  }
  return null;
}

export function ToolFigureLightbox({
  imageUrl,
  alt,
  caption,
  onClose,
}: {
  imageUrl: string;
  alt: string;
  caption?: string;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-[120] flex items-center justify-center bg-black/80"
      onClick={onClose}
    >
      <button
        className="absolute right-4 top-4 rounded-full bg-white/10 p-2 text-white transition-colors hover:bg-white/20"
        onClick={onClose}
      >
        <X className="h-5 w-5" />
      </button>
      <SignedAssetImage
        src={imageUrl}
        alt={alt}
        className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      />
      {caption ? (
        <div className="absolute bottom-6 left-1/2 max-w-xl -translate-x-1/2 rounded-lg bg-black/60 px-4 py-2 text-center text-sm text-white/90">
          {caption}
        </div>
      ) : null}
    </div>
  );
}

export function ToolFigureGallery({
  paperId,
  figures,
}: {
  paperId?: string;
  figures: Array<Record<string, unknown>>;
}) {
  const [lightbox, setLightbox] = useState<{ imageUrl: string; alt: string; caption?: string } | null>(null);
  if (figures.length === 0) return null;
  const visible = figures.slice(0, 4);
  return (
    <>
      <div className="mt-2 space-y-2">
        <div className="text-[10px] text-ink-tertiary">关联图表 {figures.length} 项</div>
        <div className="grid gap-2 md:grid-cols-2">
          {visible.map((figure, index) => {
            const figureId = String(figure.id ?? "").trim();
            const figureLabel = String(figure.figure_label ?? figure.label ?? "").trim();
            const caption = String(figure.caption ?? "").trim();
            const pageNumber = String(figure.page_number ?? figure.page ?? "?");
            const imageType = String(figure.image_type ?? figure.figure_type ?? "图表");
            const description = String(figure.description ?? figure.analysis ?? "").trim();
            const imageUrl = resolveToolFigureImageUrl(paperId, figure);
            const alt = caption || `${imageType} p.${pageNumber}`;
            return (
              <div key={figureId || `${index}-${caption}`} className="overflow-hidden rounded-lg border border-border bg-surface">
                {imageUrl ? (
                  <button
                    type="button"
                    className="block w-full bg-white"
                    onClick={() => setLightbox({ imageUrl, alt, caption: caption || undefined })}
                  >
                    <SignedAssetImage
                      src={imageUrl}
                      alt={alt}
                      className="h-28 w-full object-cover transition-transform hover:scale-[1.01]"
                    />
                  </button>
                ) : (
                  <div className="flex h-28 items-center justify-center bg-page text-[10px] text-ink-tertiary">
                    无图像预览
                  </div>
                )}
                <div className="space-y-1 px-2.5 py-2 text-[11px]">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-ink">{figureLabel || imageType}</span>
                    <span className="text-[10px] text-ink-tertiary">p.{pageNumber}</span>
                  </div>
                  {figureLabel && figureLabel !== imageType ? (
                    <div className="text-[10px] text-ink-tertiary">{imageType}</div>
                  ) : null}
                  {caption ? <p className="line-clamp-2 text-ink">{caption}</p> : null}
                  {description ? <p className="line-clamp-3 text-[10px] text-ink-tertiary">{description}</p> : null}
                </div>
              </div>
            );
          })}
        </div>
        {figures.length > visible.length ? (
          <div className="text-[10px] text-ink-tertiary">另有 {figures.length - visible.length} 项未展开显示</div>
        ) : null}
      </div>
      {lightbox ? (
        <ToolFigureLightbox
          imageUrl={lightbox.imageUrl}
          alt={lightbox.alt}
          caption={lightbox.caption}
          onClose={() => setLightbox(null)}
        />
      ) : null}
    </>
  );
}

export function ToolFigureReferences({
  paperId,
  refs,
  totalCount,
}: {
  paperId?: string;
  refs: Array<Record<string, unknown>>;
  totalCount?: unknown;
}) {
  const [lightbox, setLightbox] = useState<{ imageUrl: string; alt: string; caption?: string } | null>(null);
  if (refs.length === 0) return null;
  const visible = refs.slice(0, 4);
  const resolvedTotal = Math.max(Number(totalCount ?? 0) || 0, refs.length);
  return (
    <>
      <div className="mt-2 space-y-2">
        <div className="text-[10px] text-ink-tertiary">
          原图引用 {visible.length}
          {resolvedTotal > visible.length ? ` / ${resolvedTotal}` : ""}
          {" "}项
        </div>
        <div className="grid gap-2">
          {visible.map((figure, index) => {
            const figureId = String(figure.id ?? "").trim();
            const figureLabel = String(figure.figure_label ?? figure.label ?? "").trim();
            const caption = String(figure.caption ?? "").trim();
            const pageNumber = String(figure.page_number ?? figure.page ?? "?");
            const imageType = String(figure.image_type ?? figure.figure_type ?? "图表");
            const imageUrl = resolveToolFigureImageUrl(paperId, figure);
            const alt = caption || `${imageType} p.${pageNumber}`;
            return (
              <div
                key={figureId || `${index}-${caption}`}
                className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2"
              >
                {imageUrl ? (
                  <button
                    type="button"
                    className="shrink-0 overflow-hidden rounded-md border border-border/70 bg-white"
                    onClick={() => setLightbox({ imageUrl, alt, caption: caption || undefined })}
                  >
                    <SignedAssetImage
                      src={imageUrl}
                      alt={alt}
                      className="h-14 w-14 object-cover transition-transform hover:scale-[1.03]"
                    />
                  </button>
                ) : (
                  <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-md border border-dashed border-border bg-page text-[10px] text-ink-tertiary">
                    原图
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-[10px] text-ink-tertiary">
                    <span>{figureLabel || imageType}</span>
                    <span>p.{pageNumber}</span>
                  </div>
                  {figureLabel && figureLabel !== imageType ? (
                    <div className="mt-0.5 text-[10px] text-ink-tertiary">{imageType}</div>
                  ) : null}
                  <p className="mt-1 line-clamp-2 text-[11px] text-ink">
                    {caption || `未命名${imageType}`}
                  </p>
                </div>
                {imageUrl ? (
                  <button
                    type="button"
                    onClick={() => setLightbox({ imageUrl, alt, caption: caption || undefined })}
                    className="shrink-0 rounded-md border border-border/70 bg-white px-2 py-1 text-[10px] font-medium text-primary transition-colors hover:bg-primary/5"
                  >
                    查看原图
                  </button>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
      {lightbox ? (
        <ToolFigureLightbox
          imageUrl={lightbox.imageUrl}
          alt={lightbox.alt}
          caption={lightbox.caption}
          onClose={() => setLightbox(null)}
        />
      ) : null}
    </>
  );
}

export function PaperAnalysisBundleView({ bundle }: { bundle: Record<string, unknown> }) {
  const rounds = [
    { key: "round_1", fallbackTitle: "第 1 轮：鸟瞰扫描" },
    { key: "round_2", fallbackTitle: "第 2 轮：内容理解" },
    { key: "round_3", fallbackTitle: "第 3 轮：深度分析" },
    { key: "final_notes", fallbackTitle: "最终结构化笔记" },
  ]
    .map(({ key, fallbackTitle }) => {
      const payload = asObjectRecord(bundle[key]);
      const markdown = String(payload?.markdown ?? "").trim();
      if (!markdown) return null;
      return {
        key,
        title: String(payload?.title ?? fallbackTitle).trim() || fallbackTitle,
        markdown,
        updatedAt: String(payload?.updated_at ?? "").trim(),
      };
    })
    .filter((item): item is { key: string; title: string; markdown: string; updatedAt: string } => !!item);

  if (rounds.length === 0) {
    return <div className="text-ink-secondary">暂无三轮分析</div>;
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5 text-[10px] text-ink-tertiary">
        {rounds.map((round) => (
          <span key={round.key} className="rounded-full border border-border/70 bg-surface px-2 py-0.5">
            {round.title}
          </span>
        ))}
      </div>
      {rounds.map((round) => (
        <details
          key={round.key}
          open={round.key === "final_notes" || rounds.length === 1}
          className="overflow-hidden rounded-lg border border-border/70 bg-surface"
        >
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-[11px] font-medium text-ink">
            <span>{round.title}</span>
            {round.updatedAt ? <span className="text-[10px] font-normal text-ink-tertiary">{round.updatedAt}</span> : null}
          </summary>
          <div className="border-t border-border/60 px-3 py-2.5">
            <div className="prose prose-sm max-h-56 max-w-none overflow-y-auto text-[12px]">
              <Suspense fallback={<div className="h-4 animate-pulse rounded bg-surface" />}>
                <Markdown>{round.markdown}</Markdown>
              </Suspense>
            </div>
          </div>
        </details>
      ))}
    </div>
  );
}

export const StepDataView = memo(function StepDataView({
  data: rawData,
  toolName,
  siblingSteps = [],
}: {
  data: Record<string, unknown>;
  toolName: string;
  siblingSteps?: StepItem[];
}) {
  const navigate = useNavigate();
  const data = useMemo(() => normalizeToolStepData(rawData), [rawData]);

  const normalizeToolFigures = useCallback((payload: Record<string, unknown>) => {
    const raw = Array.isArray(payload.figures)
      ? payload.figures
      : Array.isArray(payload.items)
        ? payload.items
        : [];
    return raw.filter((item): item is Record<string, unknown> => !!item && typeof item === "object");
  }, []);

  const normalizeToolFigureRefs = useCallback((payload: Record<string, unknown>) => {
    const raw = Array.isArray(payload.figure_refs)
      ? payload.figure_refs
      : Array.isArray(payload.figureRefs)
        ? payload.figureRefs
        : [];
    return raw
      .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
      .map((item) => {
        const figureId = String(item.id ?? item.figure_ref ?? item.figure_id ?? "").trim();
        const normalized: Record<string, unknown> = { ...item };
        if (figureId) normalized.id = figureId;
        if (normalized.figure_label === undefined && normalized.label !== undefined) normalized.figure_label = normalized.label;
        if (normalized.figure_label === undefined && normalized.title !== undefined) normalized.figure_label = normalized.title;
        if (normalized.page_number === undefined && normalized.page !== undefined) normalized.page_number = normalized.page;
        if (normalized.image_type === undefined && normalized.figure_type !== undefined) normalized.image_type = normalized.figure_type;
        return normalized;
      });
  }, []);
  const nestedPaper = data.paper && typeof data.paper === "object"
    ? data.paper as Record<string, unknown>
    : null;
  const toolPaperId = String(data.paper_id ?? data.id ?? nestedPaper?.id ?? "").trim();
  const hasSiblingFigureGallery = siblingSteps.some((candidate) => {
    if (candidate.toolName !== "analyze_figures" && candidate.toolName !== "paper_figures") return false;
    const candidateData = candidate.data && typeof candidate.data === "object"
      ? candidate.data as Record<string, unknown>
      : null;
    const candidatePaper = candidateData?.paper && typeof candidateData.paper === "object"
      ? candidateData.paper as Record<string, unknown>
      : null;
    const candidatePaperId = String(
      candidateData?.paper_id
      ?? candidateData?.id
      ?? candidatePaper?.id
      ?? candidate.toolArgs?.paper_id
      ?? "",
    ).trim();
    return candidatePaperId === toolPaperId || !candidatePaperId || !toolPaperId;
  });
  const hasFreshRoundAnalysis = siblingSteps.some((candidate) => {
    if (candidate.toolName !== "analyze_paper_rounds") return false;
    const candidateData = candidate.data && typeof candidate.data === "object"
      ? candidate.data as Record<string, unknown>
      : null;
    const candidatePaperId = String(candidateData?.paper_id ?? candidateData?.id ?? candidate.toolArgs?.paper_id ?? "").trim();
    return candidatePaperId === toolPaperId || !candidatePaperId || !toolPaperId;
  });

  const renderFigureGallery = useCallback((
    paperId: string | undefined,
    figures: Array<Record<string, unknown>>,
  ) => <ToolFigureGallery paperId={paperId} figures={figures} />, []);

  const renderFigureReferences = useCallback((
    paperId: string | undefined,
    refs: Array<Record<string, unknown>>,
    totalCount?: unknown,
  ) => <ToolFigureReferences paperId={paperId} refs={refs} totalCount={totalCount} />, []);

  if (toolName === "inspect_workspace" && data.tree) {
    return <WorkspaceInspectView data={data} />;
  }
  if (toolName === "read_workspace_file" && data.content !== undefined) {
    return <WorkspaceFileView data={data} />;
  }
  if ((toolName === "write_workspace_file" || toolName === "replace_workspace_text") && data.relative_path) {
    return <WorkspaceWriteView data={data} toolName={toolName} />;
  }
  if (toolName === "run_workspace_command" && data.command) {
    return <WorkspaceCommandView data={data} />;
  }
  if (toolName === "get_workspace_task_status" && (data.status || data.result)) {
    return <WorkspaceTaskStatusView data={data} />;
  }
  if (toolName === "search_web" && Array.isArray(data.items)) {
    return <WebSearchView data={data} />;
  }
  if (toolName === "search_papers" && Array.isArray(data.papers)) {
    return <PaperListView papers={data.papers as Array<Record<string, unknown>>} label={`找到 ${(data.papers as unknown[]).length} 篇论文`} />;
  }
  if (toolName === "search_literature" && Array.isArray(data.papers)) {
    return (
      <ExternalLiteratureSelector
        papers={data.papers as Array<Record<string, unknown>>}
        query={String(data.query ?? "")}
      />
    );
  }
  if (toolName === "preview_external_paper_head" && (data.title || data.abstract)) {
    const sections = Array.isArray(data.sections) ? (data.sections as Array<Record<string, unknown>>) : [];
    return (
      <div className="space-y-3">
        <div className="rounded-xl border border-border bg-surface px-3 py-3">
          <div className="text-sm font-semibold text-ink">{String(data.title || `arXiv:${String(data.arxiv_id || "")}`)}</div>
          {data.submission_info ? <div className="mt-1 text-[11px] text-ink-tertiary">{String(data.submission_info)}</div> : null}
          {Array.isArray(data.authors) && data.authors.length > 0 ? (
            <div className="mt-2 text-[11px] text-ink-secondary">{(data.authors as string[]).slice(0, 8).join(" · ")}</div>
          ) : null}
          {data.abstract ? <div className="mt-3 text-[12px] leading-6 text-ink-secondary">{String(data.abstract)}</div> : null}
        </div>
        <div className="rounded-xl border border-border bg-page/72 px-3 py-3">
          <div className="text-[11px] font-medium uppercase tracking-[0.14em] text-ink-tertiary">章节目录</div>
          {sections.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-2">
              {sections.slice(0, 16).map((section, index) => (
                <span key={`${String(section.anchor || "")}-${index}`} className="rounded-full border border-border/70 bg-surface px-2.5 py-1 text-[11px] text-ink-secondary">
                  {String(section.title || "")}
                </span>
              ))}
            </div>
          ) : (
            <div className="mt-2 text-[11px] text-ink-tertiary">暂无章节目录</div>
          )}
        </div>
      </div>
    );
  }
  if (toolName === "preview_external_paper_section" && data.markdown) {
    return (
      <div className="space-y-3">
        <div className="rounded-xl border border-border bg-surface px-3 py-3">
          <div className="text-sm font-semibold text-ink">{String(data.matched_section || data.requested_section || "章节预读")}</div>
          {data.arxiv_id ? <div className="mt-1 text-[11px] text-ink-tertiary">arXiv:{String(data.arxiv_id)}</div> : null}
          {Array.isArray(data.child_sections) && data.child_sections.length > 0 ? (
            <div className="mt-2 text-[11px] text-ink-secondary">
              子章节：{(data.child_sections as string[]).slice(0, 6).join(" · ")}
            </div>
          ) : null}
        </div>
        <div className="rounded-xl border border-border bg-page/72 px-4 py-3">
          <Markdown autoMath>{String(data.markdown)}</Markdown>
        </div>
      </div>
    );
  }
  if (toolName === "search_arxiv" && Array.isArray(data.candidates)) {
    return <ArxivCandidateSelector candidates={data.candidates as Array<Record<string, unknown>>} query={String(data.query ?? "")} />;
  }
  if (toolName === "ingest_arxiv" && data.total !== undefined) {
    return <IngestResultView data={data} />;
  }
  if (toolName === "get_system_status") {
    return (
      <div className="grid grid-cols-3 gap-2">
        {[
          { label: "论文", value: data.paper_count, color: "text-primary" },
          { label: "已向量化", value: data.embedded_count, color: "text-success" },
          { label: "主题", value: data.topic_count, color: "text-blue-600 dark:text-blue-400" },
        ].map((s) => (
          <div key={s.label} className="flex flex-col items-center rounded-lg bg-surface py-2">
            <span className={cn("text-base font-bold", s.color)}>{String(s.value ?? 0)}</span>
            <span className="text-[10px] text-ink-tertiary">{s.label}</span>
          </div>
        ))}
      </div>
    );
  }
  /* list_topics — 主题列表 */
  if (toolName === "list_topics" && Array.isArray(data.topics)) {
    const topics = data.topics as Array<Record<string, unknown>>;
    return (
      <div className="space-y-1">
        {topics.map((t, i) => (
          <div key={i} className="flex items-center gap-2 rounded-lg bg-surface px-2.5 py-1.5 text-[11px]">
            <Hash className="h-3 w-3 text-primary shrink-0" />
            <span className="font-medium text-ink">{String(t.name ?? "")}</span>
            {t.paper_count !== undefined && <span className="text-ink-tertiary">{String(t.paper_count)} 篇</span>}
            {t.enabled !== undefined && (
              <span className={cn("ml-auto rounded px-1.5 py-0.5 text-[9px]", t.enabled ? "bg-success/10 text-success" : "bg-ink/5 text-ink-tertiary")}>
                {t.enabled ? "已订阅" : "未订阅"}
              </span>
            )}
          </div>
        ))}
      </div>
    );
  }
  /* get_timeline — 时间线 */
  if (toolName === "get_timeline" && Array.isArray(data.timeline)) {
    const items = data.timeline as Array<Record<string, unknown>>;
    return (
      <div className="space-y-1 max-h-48 overflow-y-auto">
        {items.map((p, i) => (
          <button
            key={i}
            onClick={() => p.paper_id && navigate(`/papers/${String(p.paper_id)}`)}
            className="flex items-center gap-2 w-full text-left rounded-lg bg-surface px-2.5 py-1.5 text-[11px] hover:bg-hover transition-colors"
          >
            <span className="shrink-0 font-mono text-[10px] text-primary">{String(p.year ?? "?")}</span>
            <span className="truncate text-ink">{String(p.title ?? "")}</span>
          </button>
        ))}
      </div>
    );
  }
  /* get_similar_papers — 相似论文 */
  if (toolName === "get_similar_papers") {
    const items = Array.isArray(data.items) ? (data.items as Array<Record<string, unknown>>) : [];
    const ids = Array.isArray(data.similar_ids) ? (data.similar_ids as string[]) : [];
    if (items.length > 0) {
      return (
        <div className="space-y-1">
          {items.map((p, i) => (
            <button
              key={i}
              onClick={() => p.id && navigate(`/papers/${String(p.id)}`)}
              className="flex items-center gap-2 w-full text-left rounded-lg bg-surface px-2.5 py-1.5 text-[11px] hover:bg-hover transition-colors"
            >
              <Star className="h-3 w-3 text-amber-500 shrink-0" />
              <span className="truncate text-ink">{String(p.title ?? "")}</span>
            </button>
          ))}
        </div>
      );
    }
    if (ids.length > 0) {
      return <p className="text-[11px] text-ink-secondary">找到 {ids.length} 篇相似论文</p>;
    }
  }
  /* get_citation_tree — 引用树统计 */
  if (toolName === "get_citation_tree" && data.nodes) {
    const nodes = Array.isArray(data.nodes) ? data.nodes.length : 0;
    const edges = Array.isArray(data.edges) ? data.edges.length : 0;
    return (
      <div className="flex items-center gap-3 text-[11px]">
        <span className="font-medium text-ink">{nodes} 个节点</span>
        <span className="text-ink-tertiary">{edges} 条引用关系</span>
      </div>
    );
  }
  /* suggest_keywords — 关键词建议 */
  if (toolName === "suggest_keywords" && Array.isArray(data.suggestions)) {
    const suggestions = data.suggestions as Array<Record<string, unknown>>;
    return (
      <div className="space-y-1.5">
        {suggestions.map((s, i) => (
          <div key={i} className="rounded-lg bg-surface px-2.5 py-2 text-[11px]">
            <p className="font-medium text-ink">{String(s.name ?? "")}</p>
            <p className="mt-0.5 font-mono text-[10px] text-primary">{String(s.query ?? "")}</p>
            {s.reason !== undefined && <p className="mt-0.5 text-[10px] text-ink-tertiary">{String(s.reason)}</p>}
          </div>
        ))}
      </div>
    );
  }
  if (
    (toolName === "paper_skim"
      || toolName === "paper_deep_read"
      || toolName === "paper_reasoning"
      || toolName === "paper_embed"
      || toolName === "paper_extract_figures")
    && data.task_id
  ) {
    return (
      <div className="space-y-1 text-[11px]">
        <p className="font-medium text-ink">{String(data.message || "后台任务已提交")}</p>
        <p className="text-ink-tertiary">任务 ID: {String(data.task_id)}</p>
      </div>
    );
  }
  if (toolName === "task_status" && data.status && typeof data.status === "object") {
    const status = data.status as Record<string, unknown>;
    const finished = Boolean(status.finished);
    const success = Boolean(status.success);
    const progress = Number(status.progress ?? 0);
    const statusLabel = finished ? (success ? "已完成" : "失败") : "进行中";
    const rawResult = asObjectRecord(data.result);
    const normalizedResult = rawResult ? normalizeToolStepData(rawResult) : null;
    const skimResult = asObjectRecord(normalizedResult?.skim_report) || normalizedResult;
    const deepResult = asObjectRecord(normalizedResult?.deep_report) || normalizedResult;
    const oneLiner = String(skimResult?.one_liner ?? "").trim();
    const novelty = String(skimResult?.novelty ?? "").trim();
    const methodology = String(skimResult?.methodology ?? "").trim();
    const methodSummary = String(deepResult?.method_summary ?? "").trim();
    const experimentsSummary = String(deepResult?.experiments_summary ?? "").trim();
    const ablationSummary = String(deepResult?.ablation_summary ?? "").trim();
    const analysisBundle = asObjectRecord(normalizedResult?.analysis_rounds);
    const resultSummary = String(rawResult?.summary ?? normalizedResult?.summary ?? "").trim();
    return (
      <div className="space-y-1.5 text-[11px]">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-ink">任务状态</span>
          <span className={cn(
            "rounded-full px-2 py-0.5 text-[10px]",
            finished
              ? (success ? "bg-success/10 text-success" : "bg-error/10 text-error")
              : "bg-primary/10 text-primary",
          )}>
            {statusLabel}
          </span>
          {Number.isFinite(progress) ? <span className="text-ink-tertiary">{Math.max(0, Math.min(100, progress))}%</span> : null}
        </div>
        {status.title ? <p className="text-ink-secondary">{String(status.title)}</p> : null}
        {finished && normalizedResult ? (
          <div className="mt-1.5 space-y-1 rounded-lg border border-border/70 bg-surface px-2.5 py-2">
            {oneLiner ? <p className="font-medium text-ink">{oneLiner}</p> : null}
            {novelty ? <p className="text-ink-secondary"><span className="font-medium text-ink">创新点:</span> {novelty}</p> : null}
            {methodology ? <p className="text-ink-secondary"><span className="font-medium text-ink">方法:</span> {methodology}</p> : null}
            {methodSummary ? <p className="text-ink-secondary"><span className="font-medium text-ink">方法:</span> {methodSummary}</p> : null}
            {experimentsSummary ? <p className="text-ink-secondary"><span className="font-medium text-ink">实验:</span> {experimentsSummary}</p> : null}
            {ablationSummary ? <p className="text-ink-secondary"><span className="font-medium text-ink">消融:</span> {ablationSummary}</p> : null}
            {analysisBundle ? <PaperAnalysisBundleView bundle={analysisBundle} /> : null}
            {!oneLiner && !methodSummary && !experimentsSummary && !ablationSummary && !analysisBundle && resultSummary ? (
              <p className="text-ink-secondary">{resultSummary}</p>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  }
  if (toolName === "task_list" && Array.isArray(data.items)) {
    const items = (data.items as Array<Record<string, unknown>>).slice(0, 5);
    return (
      <div className="space-y-1">
        {items.map((task, index) => {
          const finished = Boolean(task.finished);
          const success = Boolean(task.success);
          return (
            <div key={String(task.task_id || task.id || index)} className="flex items-center justify-between gap-2 rounded-lg bg-surface px-2.5 py-1.5 text-[11px]">
              <span className="truncate text-ink">{String(task.title || task.task_type || "任务")}</span>
              <span className={cn(
                "shrink-0 rounded-full px-2 py-0.5 text-[10px]",
                finished
                  ? (success ? "bg-success/10 text-success" : "bg-error/10 text-error")
                  : "bg-primary/10 text-primary",
              )}>
                {finished ? (success ? "完成" : "失败") : "运行中"}
              </span>
            </div>
          );
        })}
      </div>
    );
  }
  const skimData = asObjectRecord(data.skim_report) || data;
  /* skim_paper — 报告摘要 */
  if ((toolName === "skim_paper" || toolName === "paper_skim") && skimData.one_liner) {
    return (
      <div className="text-[11px]">
        <p className="font-medium text-ink">{String(skimData.one_liner)}</p>
        {skimData.novelty !== undefined && <p className="mt-1 text-ink-secondary"><span className="font-medium">创新点:</span> {String(skimData.novelty)}</p>}
        {skimData.methodology !== undefined && <p className="mt-0.5 text-ink-secondary"><span className="font-medium">方法:</span> {String(skimData.methodology)}</p>}
      </div>
    );
  }
  const deepData = asObjectRecord(data.deep_report) || data;
  if (
    (toolName === "deep_read_paper" || toolName === "paper_deep_read")
    && (deepData.method_summary || deepData.experiments_summary || deepData.ablation_summary || Array.isArray(deepData.reviewer_risks))
  ) {
    const risks = Array.isArray(deepData.reviewer_risks)
      ? (deepData.reviewer_risks as unknown[]).slice(0, 3).map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    return (
      <div className="space-y-1 text-[11px]">
        {deepData.method_summary ? <p className="text-ink-secondary"><span className="font-medium text-ink">方法:</span> {String(deepData.method_summary)}</p> : null}
        {deepData.experiments_summary ? <p className="text-ink-secondary"><span className="font-medium text-ink">实验:</span> {String(deepData.experiments_summary)}</p> : null}
        {deepData.ablation_summary ? <p className="text-ink-secondary"><span className="font-medium text-ink">消融:</span> {String(deepData.ablation_summary)}</p> : null}
        {risks.length > 0 ? (
          <p className="text-ink-secondary">
            <span className="font-medium text-ink">风险:</span> {risks.join("；")}
          </p>
        ) : null}
      </div>
    );
  }
  /* reasoning_analysis — 推理链 */
  if (toolName === "reasoning_analysis" && data.reasoning_steps) {
    const steps = Array.isArray(data.reasoning_steps) ? (data.reasoning_steps as Array<Record<string, unknown>>) : [];
    return (
      <div className="space-y-1.5 max-h-48 overflow-y-auto">
        {steps.slice(0, 6).map((s, i) => (
          <div key={i} className="rounded-lg bg-surface px-2.5 py-1.5 text-[11px]">
            <p className="font-medium text-ink">{String(s.step_name ?? s.claim ?? `步骤 ${i + 1}`)}</p>
            {s.evidence !== undefined && <p className="mt-0.5 text-[10px] text-ink-tertiary truncate">{String(s.evidence)}</p>}
          </div>
        ))}
      </div>
    );
  }
  /* analyze_figures — 图表列表 */
  if (toolName === "analyze_figures") {
    const figs = normalizeToolFigures(data);
    if (figs.length === 0) return null;
    const paperId = String(data.paper_id ?? data.id ?? "").trim() || undefined;
    return (
      <div className="text-[11px]">
        {data.title ? <div className="mb-2 font-medium text-ink">{String(data.title)}</div> : null}
        {renderFigureGallery(paperId, figs)}
      </div>
    );
  }
  if (toolName === "paper_figures") {
    const figs = normalizeToolFigures(data);
    if (figs.length === 0) {
      return <div className="text-[11px] text-ink-secondary">暂无图表</div>;
    }
    const paperId = String(data.paper_id ?? nestedPaper?.id ?? "").trim() || undefined;
    const title = String(data.title ?? nestedPaper?.title ?? "").trim();
    return (
      <div className="text-[11px]">
        {title ? <div className="mb-2 font-medium text-ink">{title}</div> : null}
        {renderFigureGallery(paperId, figs)}
      </div>
    );
  }
  /* identify_research_gaps — 研究空白 */
  if (toolName === "identify_research_gaps" && data.analysis) {
    const analysis = data.analysis as Record<string, unknown>;
    const gaps = Array.isArray(analysis.research_gaps) ? (analysis.research_gaps as Array<Record<string, unknown>>) : [];
    return (
      <div className="space-y-1.5">
        {gaps.slice(0, 5).map((g, i) => (
          <div key={i} className="rounded-lg bg-surface px-2.5 py-1.5 text-[11px]">
            <p className="font-medium text-ink">{String(g.gap_title ?? g.title ?? `空白 ${i + 1}`)}</p>
            <p className="mt-0.5 text-[10px] text-ink-tertiary truncate">{String(g.description ?? g.evidence ?? "")}</p>
          </div>
        ))}
      </div>
    );
  }
  /* get_paper_detail — 论文详情卡片 */
  if ((toolName === "get_paper_detail" || toolName === "paper_detail") && data.title) {
    const detailMetadata = data.metadata && typeof data.metadata === "object"
      ? data.metadata as Record<string, unknown>
      : null;
    const detailVenue = String(data.venue ?? detailMetadata?.venue ?? detailMetadata?.citation_venue ?? "").trim();
    return (
      <div className="text-[11px]">
        <button
          onClick={() => data.id && navigate(`/papers/${String(data.id)}`)}
          className="font-medium text-primary hover:underline"
        >
          {String(data.title)}
        </button>
        <div className="mt-1 flex flex-wrap gap-1.5 text-[10px] text-ink-tertiary">
          {data.arxiv_id ? <span className="font-mono">{String(data.arxiv_id)}</span> : null}
          {data.read_status ? <span>{String(data.read_status)}</span> : null}
          {detailVenue ? <span>{detailVenue}</span> : null}
          {(data.has_analysis_rounds ?? data.analysis_rounds) ? <span>已含三轮分析</span> : null}
          {(data.has_skim_report ?? data.skim_report) ? <span>已有粗读</span> : null}
          {(data.has_deep_report ?? data.deep_report) ? <span>已有精读</span> : null}
          {data.figure_count ? <span>图表 {String(data.figure_count)}</span> : null}
        </div>
        {(data.abstract_zh !== undefined || data.abstract !== undefined) ? (
          <p className="text-ink-secondary line-clamp-3">
            {String(data.abstract_zh ?? data.abstract ?? "")}
          </p>
        ) : null}
      </div>
    );
  }
  if ((toolName === "get_paper_analysis" || toolName === "analyze_paper_rounds") && data.analysis_rounds) {
    const bundle = data.analysis_rounds as Record<string, unknown>;
    const paperId = String(data.paper_id ?? data.id ?? "").trim() || undefined;
    const figureRefs = normalizeToolFigureRefs(data);
    const suppressFigureRefs = hasSiblingFigureGallery || (toolName === "get_paper_analysis" && hasFreshRoundAnalysis);
    const figureReferences = suppressFigureRefs ? null : renderFigureReferences(paperId, figureRefs, data.figure_count);
    return (
      <div className="text-[11px]">
        <div className="mb-2 flex flex-wrap gap-1.5 text-[10px] text-ink-tertiary">
          {data.title ? <span className="font-medium text-ink">{String(data.title)}</span> : null}
          {bundle.detail_level ? <span>详略: {String(bundle.detail_level)}</span> : null}
          {bundle.reasoning_level ? <span>推理: {String(bundle.reasoning_level)}</span> : null}
          {data.figure_count ? <span>图表 {String(data.figure_count)}</span> : null}
        </div>
        {figureReferences}
        <div className={cn(figureReferences && "mt-3")}>
          <PaperAnalysisBundleView bundle={bundle} />
        </div>
      </div>
    );
  }
  if (toolName === "ingest_external_literature" && (data.requested !== undefined || data.ingested !== undefined)) {
    return (
      <div className="text-[11px]">
        <div className="flex flex-wrap gap-1.5 text-[10px] text-ink-tertiary">
          <span>请求 {String(data.requested ?? 0)}</span>
          <span>新增 {String(data.ingested ?? 0)}</span>
          <span>重复 {String(data.duplicates ?? 0)}</span>
        </div>
        {Array.isArray(data.papers) && data.papers.length > 0 ? (
          <div className="mt-2 space-y-1">
            {data.papers.slice(0, 4).map((paper: any, index: number) => (
              <div key={`${paper.id || paper.arxiv_id || index}`} className="truncate text-ink-secondary">
                {String(paper.title || paper.arxiv_id || `paper-${index + 1}`)}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    );
  }
  /* writing_assist — 写作助手结果 */
  if (toolName === "writing_assist" && data.content) {
    return (
      <div className="prose prose-sm dark:prose-invert max-w-none text-[12px] max-h-48 overflow-y-auto">
        <Suspense fallback={<div className="h-4 animate-pulse rounded bg-surface" />}>
          <Markdown>{String(data.content)}</Markdown>
        </Suspense>
      </div>
    );
  }
  if ((toolName === "inspect_workspace" || toolName === "ls") && (data.tree || data.entries)) {
    return <WorkspaceInspectView data={data} />;
  }
  if ((toolName === "read_workspace_file" || toolName === "read") && data.content) {
    return <WorkspaceFileView data={data} />;
  }
  if ((toolName === "write_workspace_file" || toolName === "replace_workspace_text" || toolName === "write" || toolName === "edit")
    && (data.preview || data.diff_preview || data.path || data.relative_path)) {
    return <WorkspaceWriteView data={data} toolName={toolName} />;
  }
  if (toolName === "glob" && Array.isArray(data.matches)) {
    return <WorkspaceMatchListView data={data} label="Glob 命中" />;
  }
  if (toolName === "grep" && Array.isArray(data.matches)) {
    return <WorkspaceMatchListView data={data} label="Grep 命中" />;
  }
  if ((toolName === "run_workspace_command" || toolName === "bash") && data.task_id) {
    return <WorkspaceTaskStatusView data={data} />;
  }
  if ((toolName === "run_workspace_command" || toolName === "bash") && (data.command || data.shell_command)) {
    return <WorkspaceCommandView data={data} />;
  }
  if (toolName === "get_workspace_task_status" && data.status) {
    return <WorkspaceTaskStatusView data={data} />;
  }
  if ((toolName === "todoread" || toolName === "todowrite") && Array.isArray(data.todos)) {
    return <TodoListView data={data} />;
  }
  if (toolName === "task" && data.content) {
    return <TaskResultView data={data} />;
  }
  /* 兜底：原始 JSON */
  return (
    <pre className="max-h-40 overflow-auto rounded-lg bg-surface p-2.5 text-[11px] text-ink-secondary">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
});

export const ReasoningMessage = memo(function ReasoningMessage({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false);
  const normalizedContent = useMemo(() => normalizeReasoningDisplay(content), [content]);
  const previewText = normalizedContent || "模型正在整理思路...";

  return (
    <div className="py-3">
      <div className="overflow-hidden rounded-[22px] border border-primary/12 bg-primary/5">
        <button
          type="button"
          onClick={() => setExpanded((prev) => !prev)}
          className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
        >
          <div className="flex min-w-0 items-center gap-2">
            <Brain className="h-4 w-4 shrink-0 text-primary" />
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary/80">思考过程</p>
              <p className="mt-1 line-clamp-1 text-sm text-ink-secondary">
                {previewText}
              </p>
            </div>
          </div>
          {expanded ? <ChevronDown className="h-4 w-4 text-ink-tertiary" /> : <ChevronRight className="h-4 w-4 text-ink-tertiary" />}
        </button>
        {expanded && (
          <div className="border-t border-primary/10 bg-white/70 px-4 py-4">
            <div className="prose-custom text-sm leading-relaxed text-ink">
              <Suspense fallback={<div className="h-4 animate-pulse rounded bg-surface" />}>
                <Markdown>{normalizedContent}</Markdown>
              </Suspense>
            </div>
          </div>
        )}
      </div>
    </div>
  );
});

export const WebSearchView = memo(function WebSearchView({
  data,
}: {
  data: Record<string, unknown>;
}) {
  const items = Array.isArray(data.items) ? (data.items as Array<Record<string, unknown>>) : [];
  const instantAnswer = data.instant_answer && typeof data.instant_answer === "object"
    ? (data.instant_answer as Record<string, unknown>)
    : null;

  return (
    <div className="space-y-2">
      {instantAnswer && (
        <div className="rounded-xl border border-primary/15 bg-primary/8 px-3 py-2">
          <div className="flex items-center gap-2 text-[11px] font-medium text-primary">
            <Globe className="h-3.5 w-3.5" />
            即时答案
          </div>
          <p className="mt-1 text-[11px] font-medium text-ink">{String(instantAnswer.title || "")}</p>
          {Boolean(instantAnswer.snippet) && (
            <p className="mt-1 text-[10px] leading-5 text-ink-secondary">{String(instantAnswer.snippet)}</p>
          )}
        </div>
      )}
      <div className="space-y-1.5">
        {items.map((item, index) => {
          const href = safeHttpUrl(item.url);
          const content = (
            <div className="flex items-start gap-2">
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-bold text-primary">
                {index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <p className="font-medium leading-snug text-ink">{String(item.title || "")}</p>
                {Boolean(item.snippet) && (
                  <p className="mt-0.5 line-clamp-3 text-[10px] leading-5 text-ink-secondary">
                    {String(item.snippet)}
                  </p>
                )}
                <p className="mt-1 text-[10px] text-primary">{String(item.display_url || item.url || "")}</p>
              </div>
            </div>
          );
          return href ? (
            <a
              key={`${href}_${index}`}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="block rounded-lg bg-surface px-2.5 py-2 text-[11px] transition-colors hover:bg-hover"
            >
              {content}
            </a>
          ) : (
            <div
              key={`${String(item.url || "")}_${index}`}
              className="block rounded-lg bg-surface px-2.5 py-2 text-[11px]"
            >
              {content}
            </div>
          );
        })}
      </div>
    </div>
  );
});

export function WorkspaceInspectView({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-secondary">
        <span className="rounded-full border border-border/70 bg-white px-2 py-0.5">
          {String(data.workspace_path || data.directory_path || data.path || "")}
        </span>
        {data.total_entries !== undefined && (
          <span>{String(data.total_entries)} 个条目</span>
        )}
      </div>
      <TraceCodeBlock label="目录结构" content={String(data.tree || "")} />
    </div>
  );
}

export function WorkspaceFileView({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-secondary">
        <span className="rounded-full border border-border/70 bg-white px-2 py-0.5">
          {String(data.relative_path || data.path || "")}
        </span>
        {data.size_bytes !== undefined && <span>{String(data.size_bytes)} bytes</span>}
        {Boolean(data.truncated) && <span className="text-warning">内容已截断</span>}
      </div>
      <TraceCodeBlock label="文件内容" content={String(data.content || "")} />
    </div>
  );
}

export function WorkspaceWriteView({
  data,
  toolName,
}: {
  data: Record<string, unknown>;
  toolName: string;
}) {
  const isReplace = toolName === "replace_workspace_text" || toolName === "edit";
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-secondary">
        <span className="rounded-full border border-border/70 bg-white px-2 py-0.5">
          {String(data.relative_path || data.path || "")}
        </span>
        {Boolean(data.created) && <TraceBadge tone="success" text="新建文件" />}
        {Boolean(data.overwritten) && <TraceBadge tone="info" text="覆盖写入" />}
        {data.changed === false && <TraceBadge tone="neutral" text="无内容变化" />}
        {isReplace && data.replaced_occurrences !== undefined && (
          <TraceBadge tone="info" text={`替换 ${String(data.replaced_occurrences)} 处`} />
        )}
      </div>
      {Boolean(data.diff_preview) && (
        <TraceCodeBlock label="改动预览" content={String(data.diff_preview)} />
      )}
      {Boolean(data.preview) && (
        <TraceCodeBlock label={isReplace ? "修改后内容" : "写入内容"} content={String(data.preview)} />
      )}
    </div>
  );
}

export function WorkspaceCommandView({ data }: { data: Record<string, unknown> }) {
  const shellCommand = Array.isArray(data.shell_command)
    ? (data.shell_command as string[]).join(" ")
    : String(data.command || "");
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-secondary">
        <span className="rounded-full border border-border/70 bg-white px-2 py-0.5">
          {String(data.workspace_path || data.path || "")}
        </span>
        <TraceBadge tone={Number(data.exit_code || 0) === 0 ? "success" : "error"} text={`exit ${String(data.exit_code ?? "?")}`} />
      </div>
      <TraceCodeBlock label="执行命令" content={shellCommand} />
      {Boolean(data.stdout) && (
        <TraceCodeBlock label="stdout" content={String(data.stdout)} />
      )}
      {Boolean(data.stderr) && (
        <TraceCodeBlock label="stderr" content={String(data.stderr)} danger />
      )}
    </div>
  );
}

export function WorkspaceTaskStatusView({ data }: { data: Record<string, unknown> }) {
  const result = data.result && typeof data.result === "object"
    ? (data.result as Record<string, unknown>)
    : null;
  return (
    <div className="space-y-2 text-[11px]">
      <div className="flex flex-wrap items-center gap-2 text-ink-secondary">
        <TraceBadge tone="info" text={`状态 ${String(data.status || "unknown")}`} />
        {data.progress_pct !== undefined && <span>{String(data.progress_pct)}%</span>}
      </div>
      {result && Boolean(result.command) && (
        <WorkspaceCommandView data={result} />
      )}
      {!result && (
        <pre className="max-h-40 overflow-auto rounded-lg bg-surface p-2.5 text-[11px] text-ink-secondary">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function WorkspaceMatchListView({
  data,
  label,
}: {
  data: Record<string, unknown>;
  label: string;
}) {
  const matches = Array.isArray(data.matches) ? (data.matches as Array<Record<string, unknown>>) : [];
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-secondary">
        <span className="rounded-full border border-border/70 bg-white px-2 py-0.5">
          {String(data.path || "")}
        </span>
        <span>{String(data.count ?? matches.length)} 条结果</span>
      </div>
      <div className="max-h-44 space-y-1 overflow-y-auto">
        {matches.slice(0, 60).map((item, index) => (
          <div key={index} className="rounded-lg bg-surface px-2.5 py-1.5 text-[11px]">
            <p className="font-medium text-ink">{String(item.relative_path || item.path || "")}</p>
            {item.line !== undefined && (
              <p className="mt-0.5 text-[10px] text-primary">Line {String(item.line)}</p>
            )}
            {item.text !== undefined && (
              <p className="mt-0.5 text-[10px] text-ink-tertiary break-all">{String(item.text)}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function TodoListView({ data }: { data: Record<string, unknown> }) {
  const todos = Array.isArray(data.todos) ? (data.todos as Array<Record<string, unknown>>) : [];
  return (
    <div className="space-y-1.5">
      {todos.length === 0 ? (
        <p className="text-[11px] text-ink-secondary">暂无待办</p>
      ) : (
        todos.map((todo, index) => (
          <div key={index} className="rounded-lg bg-surface px-2.5 py-1.5 text-[11px]">
            <div className="flex items-center gap-2">
              <span className="font-medium text-ink">{String(todo.content ?? "")}</span>
              <TraceBadge tone="info" text={String(todo.status ?? "pending")} />
              <TraceBadge tone="neutral" text={String(todo.priority ?? "medium")} />
            </div>
          </div>
        ))
      )}
    </div>
  );
}

export function TaskResultView({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="space-y-2 text-[11px]">
      <div className="flex flex-wrap items-center gap-2 text-ink-secondary">
        <TraceBadge tone="info" text={`task ${String(data.task_id ?? "")}`} />
        <TraceBadge tone="neutral" text={String(data.mode ?? "build")} />
      </div>
      <p className="font-medium text-ink">{String(data.description ?? "")}</p>
      <div className="prose-custom max-h-56 overflow-y-auto text-[12px]">
        <Suspense fallback={<div className="h-4 animate-pulse rounded bg-surface" />}>
          <Markdown>{String(data.content ?? "")}</Markdown>
        </Suspense>
      </div>
    </div>
  );
}

export function TraceCodeBlock({
  label,
  content,
  danger = false,
}: {
  label: string;
  content: string;
  danger?: boolean;
}) {
  return (
    <div>
      <p className={cn("mb-1 text-[10px] font-semibold uppercase tracking-[0.16em]", danger ? "text-error" : "text-ink-tertiary")}>
        {label}
      </p>
      <pre className={cn(
        "max-h-56 overflow-auto rounded-xl border p-3 text-[11px] leading-6",
        danger
          ? "border-error/20 bg-error-light text-error"
          : "border-border/70 bg-white text-ink-secondary",
      )}>
        {content}
      </pre>
    </div>
  );
}

export function TraceBadge({
  tone,
  text,
}: {
  tone: "success" | "error" | "info" | "neutral";
  text: string;
}) {
  const toneClass = {
    success: "border-success/20 bg-success/10 text-success",
    error: "border-error/20 bg-error-light text-error",
    info: "border-primary/20 bg-primary/8 text-primary",
    neutral: "border-border/70 bg-page/70 text-ink-secondary",
  }[tone];

  return (
    <span className={cn("rounded-full border px-2 py-0.5", toneClass)}>
      {text}
    </span>
  );
}

export const QuestionCard = memo(function QuestionCard({
  actionId,
  description,
  questions,
  isPending,
  isConfirming,
  onSubmit,
  onReject,
}: {
  actionId: string;
  description: string;
  questions: NonNullable<ChatItem["questionItems"]>;
  isPending: boolean;
  isConfirming: boolean;
  onSubmit: (id: string, answers: string[][]) => void;
  onReject: (id: string) => void;
}) {
  const [selectedAnswers, setSelectedAnswers] = useState<string[][]>(() => questions.map(() => []));
  const [customAnswers, setCustomAnswers] = useState<string[]>(() => questions.map(() => ""));

  useEffect(() => {
    setSelectedAnswers(questions.map(() => []));
    setCustomAnswers(questions.map(() => ""));
  }, [actionId, questions.length]);

  const mergedAnswers = useMemo(
    () => questions.map((question, index) => {
      const base = selectedAnswers[index] || [];
      const custom = question.custom === false ? "" : (customAnswers[index] || "").trim();
      if (!custom || base.includes(custom)) return base;
      return [...base, custom];
    }),
    [customAnswers, questions, selectedAnswers],
  );

  const canSubmit = useMemo(
    () => mergedAnswers.every((answers) => answers.length > 0),
    [mergedAnswers],
  );

  const toggleOption = useCallback((questionIndex: number, label: string, multiple: boolean) => {
    setSelectedAnswers((current) => current.map((answers, index) => {
      if (index !== questionIndex) return answers;
      if (!multiple) return [label];
      return answers.includes(label)
        ? answers.filter((item) => item !== label)
        : [...answers, label];
    }));
  }, []);

  return (
    <div className="py-3">
      <div className={cn(
        "overflow-hidden rounded-[24px] border bg-white/88 shadow-[0_14px_34px_-28px_rgba(15,23,35,0.18)] transition-all",
        isPending ? "border-primary/40 shadow-md shadow-primary/10" : "border-border",
      )}>
        <div className="flex items-center gap-2 bg-page px-3.5 py-2.5">
          <Sparkles className="h-3.5 w-3.5 text-primary" />
          <span className="text-xs font-semibold text-ink">需要你补充信息</span>
        </div>
        <div className="space-y-4 px-3.5 py-3">
          {description ? (
            <div className="rounded-2xl border border-primary/10 bg-primary/5 px-3 py-2.5 text-sm text-ink">
              {description}
            </div>
          ) : null}
          {questions.map((question, questionIndex) => {
            const answers = mergedAnswers[questionIndex] || [];
            const multiple = question.multiple === true;
            const customEnabled = question.custom !== false;
            return (
              <div key={`${actionId}_${questionIndex}`} className="rounded-2xl border border-border/70 bg-page/55 px-3 py-3">
                <div className="mb-2 flex items-center gap-2">
                  <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-primary">
                    {question.header}
                  </span>
                  <span className="text-[11px] text-ink-tertiary">
                    {multiple ? "可多选" : "单选"}
                  </span>
                </div>
                <p className="mb-3 text-sm font-medium text-ink">{question.question}</p>
                <div className="space-y-2">
                  {question.options.map((option) => {
                    const selected = answers.includes(option.label);
                    return (
                      <button
                        key={`${question.header}_${option.label}`}
                        type="button"
                        disabled={!isPending || isConfirming}
                        onClick={() => toggleOption(questionIndex, option.label, multiple)}
                        className={cn(
                          "flex w-full items-start gap-2 rounded-2xl border px-3 py-2.5 text-left transition-colors",
                          selected
                            ? "border-primary/45 bg-primary/8"
                            : "border-border/70 bg-white hover:bg-hover",
                          (!isPending || isConfirming) && "cursor-not-allowed opacity-70",
                        )}
                      >
                        <div className={cn(
                          "mt-0.5 h-4 w-4 shrink-0 rounded-full border",
                          selected ? "border-primary bg-primary" : "border-border",
                        )} />
                        <div className="min-w-0">
                          <div className="text-sm font-medium text-ink">{option.label}</div>
                        </div>
                      </button>
                    );
                  })}
                </div>
                {customEnabled && (
                  <div className="mt-3">
                    <textarea
                      value={customAnswers[questionIndex] || ""}
                      disabled={!isPending || isConfirming}
                      onChange={(event) => {
                        const nextValue = event.target.value;
                        setCustomAnswers((current) => current.map((value, index) => (
                          index === questionIndex ? nextValue : value
                        )));
                      }}
                      rows={2}
                      placeholder="或填写你自己的回答"
                      className="w-full resize-none rounded-2xl border border-border bg-white px-3 py-2 text-sm text-ink outline-none transition-colors focus:border-primary/50 focus:ring-2 focus:ring-primary/10 disabled:cursor-not-allowed disabled:bg-surface"
                    />
                  </div>
                )}
              </div>
            );
          })}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => onSubmit(actionId, mergedAnswers)}
              disabled={!isPending || isConfirming || !canSubmit}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-primary py-2 text-xs font-medium text-white transition-all hover:bg-primary-hover disabled:opacity-50"
            >
              {isConfirming ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
              提交回答
            </button>
            <button
              type="button"
              onClick={() => onReject(actionId)}
              disabled={!isPending || isConfirming}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-border bg-surface py-2 text-xs font-medium text-ink-secondary transition-all hover:bg-hover disabled:opacity-50"
            >
              <XCircle className="h-3.5 w-3.5" />
              暂不回答
            </button>
          </div>
        </div>
      </div>
    </div>
  );
});

/* ========== 确认卡片 ========== */

export const ActionConfirmCard = memo(function ActionConfirmCard({
  actionId, description, tool, args, isPending, isConfirming, onConfirm, onReject,
}: {
  actionId: string; description: string; tool: string; args?: Record<string, unknown>;
  isPending: boolean; isConfirming: boolean; onConfirm: (id: string) => void; onReject: (id: string) => void;
}) {
  const meta = getToolMeta(tool);
  const Icon = meta.icon;
  return (
    <div className="py-3">
      <div className={cn(
        "overflow-hidden rounded-[24px] border bg-white/86 shadow-[0_14px_34px_-28px_rgba(15,23,35,0.18)] transition-all",
        isPending ? "border-warning/60 shadow-md shadow-warning/10 animate-[confirm-glow_2s_ease-in-out_infinite]" : "border-border",
      )}>
        <div className={cn(
          "flex items-center gap-2 px-3.5 py-2.5",
          isPending ? "bg-warning-light" : "bg-page",
        )}>
          <AlertTriangle className={cn("h-3.5 w-3.5", isPending ? "text-warning animate-pulse" : "text-ink-tertiary")} />
          <span className="text-xs font-semibold text-ink">{isPending ? "⚠️ 需要你的确认" : "已处理"}</span>
        </div>
        <div className="space-y-3 px-3.5 py-3">
          <div className="flex items-start gap-2.5">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-warning-light">
              <Icon className="h-4 w-4 text-warning" />
            </div>
            <div>
              <p className="text-sm font-medium text-ink">{description}</p>
              {args && Object.keys(args).length > 0 && (
                <div className="mt-1.5 rounded-lg bg-page px-2.5 py-1.5">
                  {Object.entries(args).map(([k, v]) => (
                    <div key={k} className="flex gap-1.5 text-[11px]">
                      <span className="font-medium text-ink-secondary">{k}:</span>
                      <span className="text-ink-tertiary">{typeof v === "string" ? v : JSON.stringify(v)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
          {isPending && (
            <div className="flex gap-2">
              <button onClick={() => onConfirm(actionId)} disabled={isConfirming} className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-primary py-2 text-xs font-medium text-white transition-all hover:bg-primary-hover disabled:opacity-50">
                {isConfirming ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                确认执行
              </button>
              <button onClick={() => onReject(actionId)} disabled={isConfirming} className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-border bg-surface py-2 text-xs font-medium text-ink-secondary transition-all hover:bg-hover disabled:opacity-50">
                <XCircle className="h-3.5 w-3.5" />
                跳过
              </button>
            </div>
          )}
          {!isPending && (
            <div className="flex items-center gap-1 text-[11px] text-success">
              <CheckCircle2 className="h-3 w-3" />
              已处理
            </div>
          )}
        </div>
      </div>
    </div>
  );
});

export const ErrorCard = memo(function ErrorCard({ content, onRetry }: { content: string; onRetry?: () => void }) {
  return (
    <div className="py-2">
      <div className="flex items-start gap-2 rounded-xl border border-error/30 bg-error-light px-3.5 py-2.5">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-error" />
        <p className="flex-1 text-sm text-error">{content}</p>
        {onRetry && (
          <button
            onClick={onRetry}
            className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-error transition-colors hover:bg-error/10"
          >
            <RotateCcw className="h-3 w-3" />
            重试
          </button>
        )}
      </div>
    </div>
  );
});

/* ========== 嵌入式内容卡片（Artifact） ========== */

export const ArtifactCard = memo(function ArtifactCard({
  title, content, isHtml, onOpen,
}: {
  title: string; content: string; isHtml?: boolean; onOpen: () => void;
}) {
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState(false);
  const isWiki = !isHtml;
  const iconColor = isWiki ? "text-primary" : "text-amber-500";
  const borderColor = isWiki ? "border-primary/30" : "border-amber-400/30";
  const bgAccent = isWiki ? "bg-primary/5" : "bg-amber-50 dark:bg-amber-900/10";
  const IconComp = isWiki ? FileText : Newspaper;

  const preview = (isHtml
    ? content.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ")
    : content.replace(/[#*_`\[\]()>-]/g, "").replace(/\s+/g, " ")
  ).trim().slice(0, 200);

  return (
    <div className="py-3">
      <div className={cn("overflow-hidden rounded-[24px] border transition-all", borderColor, "bg-white/86 shadow-[0_14px_34px_-28px_rgba(15,23,35,0.18)] hover:shadow-md")}>
        <button
          onClick={onOpen}
          className={cn("flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-hover", bgAccent)}
        >
          <div className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg", isWiki ? "bg-primary/10" : "bg-amber-100 dark:bg-amber-900/20")}>
            <IconComp className={cn("h-4.5 w-4.5", iconColor)} />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-ink">{title}</p>
            <p className="mt-0.5 truncate text-xs text-ink-tertiary">{preview}...</p>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <span className="rounded-md bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
              点击查看
            </span>
            <PanelRightOpen className="h-4 w-4 text-ink-tertiary" />
          </div>
        </button>

        <div className="flex items-center gap-1 border-t border-border-light px-4 py-1.5">
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1 text-[11px] text-ink-tertiary hover:text-ink-secondary"
          >
            {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
            {expanded ? "收起预览" : "展开预览"}
          </button>
        </div>

        {expanded && (
          <div
            className="max-h-80 overflow-y-auto border-t border-border-light px-5 py-4"
            onClick={(e) => {
              const card = (e.target as HTMLElement).closest<HTMLElement>("[data-paper-id]");
              if (card?.dataset.paperId) navigate(`/papers/${card.dataset.paperId}`);
            }}
          >
	            {isHtml ? (
	              <SanitizedHtmlPreview
	                className="prose-custom brief-html-preview brief-content text-sm"
	                content={content}
	              />
	            ) : (
              <div className="prose-custom text-sm">
                <Suspense fallback={<div className="h-4 animate-pulse rounded bg-surface" />}>
                  <Markdown>{content}</Markdown>
                </Suspense>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
});
