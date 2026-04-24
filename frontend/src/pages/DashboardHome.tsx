import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Button, Card } from "@/components/ui";
import { useConversationCtx } from "@/contexts/ConversationContext";
import {
  dashboardApi,
  type DashboardHomeSnapshot,
  type ArxivTrendDirection,
  type ArxivTrendSubdomainOption,
  type LibraryFeaturedTopic,
} from "@/services/api";
import { cn } from "@/lib/utils";
import {
  ArrowRight,
  BarChart3,
  Bot,
  FileStack,
  FolderKanban,
  GitBranch,
  Layers3,
  Loader2,
  Quote,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  TrendingUp,
} from "lucide-react";

const MODULE_LINKS = [
  { title: "研究助手", subtitle: "继续对话与执行", to: "/assistant", icon: Bot },
  { title: "论文收集", subtitle: "检索与订阅入口", to: "/collect", icon: Search },
  { title: "论文库", subtitle: "筛选、阅读与沉淀", to: "/papers", icon: FileStack },
  { title: "项目工作区", subtitle: "项目与流程推进", to: "/projects", icon: FolderKanban },
];

function buildLinePath(values: number[], width: number, height: number, padding = 14) {
  if (!values.length) return "";
  const innerWidth = width - padding * 2;
  const innerHeight = height - padding * 2;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  return values
    .map((value, index) => {
      const x = padding + (innerWidth / Math.max(values.length - 1, 1)) * index;
      const y = height - padding - ((value - min) / range) * innerHeight;
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

function buildAreaPath(values: number[], width: number, height: number, padding = 14) {
  if (!values.length) return "";
  const line = buildLinePath(values, width, height, padding);
  const baselineY = height - padding;
  const innerWidth = width - padding * 2;
  const startX = padding;
  const endX = padding + innerWidth;
  return `${line} L${endX.toFixed(2)},${baselineY.toFixed(2)} L${startX.toFixed(2)},${baselineY.toFixed(2)} Z`;
}

function formatCount(value: number | null | undefined) {
  return new Intl.NumberFormat("zh-CN").format(Math.max(0, Number(value || 0)));
}

type ArxivKeyword = {
  keyword?: string;
  term?: string;
  count: number;
  example_title?: string | null;
};

const DATA_ACCENTS = [
  "var(--color-primary)",
  "var(--color-success)",
  "var(--color-warning)",
  "var(--color-error)",
  "var(--color-info)",
];

function dataAccent(index: number) {
  return DATA_ACCENTS[index % DATA_ACCENTS.length];
}

function accentBorder(accent: string, strength = 18) {
  return `color-mix(in srgb, ${accent} ${strength}%, var(--color-border))`;
}

function accentSurface(accent: string, strength = 8, base = "var(--color-surface)") {
  return `color-mix(in srgb, ${accent} ${strength}%, ${base})`;
}

export default function DashboardHome() {
  const navigate = useNavigate();
  const { metas, activeConv, switchConversation } = useConversationCtx();
  const [snapshot, setSnapshot] = useState<DashboardHomeSnapshot>({
    today: null,
    folders: null,
    projects: [],
    tasks: [],
    graph: null,
    arxiv_trend: null,
    library_focus: null,
    topics: [],
    acp: null,
  });
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [trendSubdomain, setTrendSubdomain] = useState("all");

  const loadSnapshot = useCallback(async () => {
    setRefreshing(true);
    try {
      const result = await dashboardApi.home({ projectLimit: 4, taskLimit: 8, trendSubdomain });
      setSnapshot(result);
      setTrendSubdomain(result.arxiv_trend?.subdomain_key || trendSubdomain || "all");
      setLoadError("");
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "首页数据暂时不可用，请稍后重试。");
    } finally {
      setRefreshing(false);
    }
  }, [trendSubdomain]);

  useEffect(() => {
    void loadSnapshot();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const sortedConversations = useMemo(
    () => [...metas].sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()),
    [metas],
  );
  const currentConversation = activeConv || sortedConversations[0] || null;
  const arxivTrend = snapshot.arxiv_trend;
  const arxivSubdomains = (arxivTrend?.subdomains || []) as ArxivTrendSubdomainOption[];
  const arxivKeywords = ((arxivTrend?.keywords?.length ? arxivTrend.keywords : arxivTrend?.top_terms || []) as ArxivKeyword[]).slice(0, 12);
  const arxivDirections = (arxivTrend?.directions || []).slice(0, 8);
  const arxivSubmissionCount = arxivTrend?.available && typeof arxivTrend.total_submissions === "number"
    ? arxivTrend.total_submissions
    : null;
  const libraryFocus = snapshot.library_focus;
  const topicCards = libraryFocus?.topic_cards || [];
  const libraryKeywords = libraryFocus?.keywords || [];
  const graphDensity = snapshot.graph?.density || 0;
  const trendPoints = useMemo(
    () => [...(snapshot.folders?.by_date || [])]
      .sort((a, b) => a.date.localeCompare(b.date))
      .slice(-7),
    [snapshot.folders],
  );
  const trendValues = trendPoints.map((item) => item.count);
  const acpReady = snapshot.acp?.chat_ready === true;
  const acpStatusLabel = typeof snapshot.acp?.chat_status_label === "string"
    ? snapshot.acp.chat_status_label
    : "ACP 状态未知";

  const handleOpenConversation = useCallback((conversationId: string) => {
    switchConversation(conversationId);
    navigate(`/assistant/${conversationId}`);
  }, [navigate, switchConversation]);

  const handleContinueAssistant = useCallback(() => {
    if (currentConversation) {
      handleOpenConversation(currentConversation.id);
      return;
    }
    navigate("/assistant");
  }, [currentConversation, handleOpenConversation, navigate]);

  const handleSelectTrendSubdomain = useCallback(async (key: string) => {
    const normalized = String(key || "all").trim() || "all";
    if (normalized === trendSubdomain) return;
    setTrendSubdomain(normalized);
    setRefreshing(true);
    try {
      const trend = await dashboardApi.arxivTrend(normalized);
      setSnapshot((prev) => ({ ...prev, arxiv_trend: trend }));
      setLoadError("");
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "趋势切换失败，请稍后重试。");
    } finally {
      setRefreshing(false);
    }
  }, [trendSubdomain]);

  return (
    <div className="animate-fade-in space-y-4 pb-8 sm:space-y-5 sm:pb-10">
      <section className="page-hero rounded-[28px] p-4 sm:p-5 lg:p-6">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="trust-pill">
                <Sparkles className="h-3.5 w-3.5" />
                ResearchOS Home
              </span>
              <Badge variant={acpReady ? "success" : "warning"}>{acpStatusLabel}</Badge>
              <button
                type="button"
                onClick={() => navigate("/settings?section=acp")}
                className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1 text-[11px] font-medium text-ink-secondary transition hover:bg-hover hover:text-ink"
              >
                <Settings className="h-3.5 w-3.5" />
                {acpReady ? "管理 ACP" : "配置 ACP"}
              </button>
              {refreshing ? (
                <Badge variant="info">
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                  同步中
                </Badge>
              ) : null}
            </div>
            <h1 className="text-2xl font-semibold tracking-[-0.04em] text-ink sm:text-4xl">
              研究工作台主页
            </h1>
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <Button size="lg" onClick={handleContinueAssistant} icon={<Bot className="h-4 w-4" />} className="w-full sm:w-auto">
              研究助手
            </Button>
            <button
              type="button"
              onClick={() => navigate("/papers")}
              className="theme-control inline-flex h-11 items-center justify-center gap-2 rounded-md border border-border bg-surface px-4 text-sm font-medium text-ink-secondary transition-colors duration-150 hover:bg-hover hover:text-ink"
            >
              <FileStack className="h-4 w-4" />
              论文库
            </button>
          </div>
        </div>
      </section>

      {loadError ? (
        <Card className="rounded-[22px]">
          <div className="text-sm text-error">{loadError}</div>
        </Card>
      ) : null}

      <Card className="chart-card rounded-[26px] p-3 sm:p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="dashboard-kpi-label">arXiv CS Signal</p>
            <h2 className="mt-2 text-xl font-semibold tracking-[-0.03em] text-ink">
              arXiv {arxivTrend?.subdomain_label || "CS"} 今日趋势
            </h2>
            <p className="mt-1 text-xs text-ink-secondary">{arxivTrend?.window_label || "等待 arXiv 同步"}</p>
          </div>
          <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:flex-nowrap">
            <span className="inline-flex min-w-0 flex-1 flex-col items-start justify-center rounded-md border border-border bg-page px-3 py-2 sm:flex-none">
              <span className="inline-flex items-center gap-2 text-sm font-semibold text-ink">
              <FileStack className="h-4 w-4 text-primary" />
                {arxivSubmissionCount !== null ? `当日投稿 ${formatCount(arxivSubmissionCount)} 篇` : "等待统计"}
              </span>
            </span>
            <button
              type="button"
              onClick={() => void loadSnapshot()}
              className="inline-flex h-10 w-10 items-center justify-center rounded-md border border-border bg-surface text-ink-secondary transition-colors duration-150 hover:bg-hover hover:text-ink"
              aria-label="刷新首页"
              title="刷新首页"
            >
              <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
            </button>
          </div>
        </div>

        <div className="mt-4 grid gap-3">
          {arxivSubdomains.length > 0 ? (
            <div className="-mx-1 overflow-x-auto px-1 pb-1">
              <div className="flex min-w-max gap-2">
              {arxivSubdomains.map((item) => {
                const active = item.key === (arxivTrend?.subdomain_key || trendSubdomain);
                return (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => void handleSelectTrendSubdomain(item.key)}
                    className={cn(
                      "inline-flex h-9 items-center rounded-full border px-3 text-xs font-medium transition-colors",
                      active
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border bg-page text-ink-secondary hover:bg-hover hover:text-ink",
                    )}
                  >
                    {item.label}
                  </button>
                );
              })}
              </div>
            </div>
          ) : null}
          <DirectionGrid directions={arxivDirections} compact />
          <div className="grid gap-3 lg:grid-cols-[1fr_1fr]">
            <div className="rounded-2xl border border-border bg-page p-3">
              <div className="mb-2 flex items-center justify-between gap-3">
                <span className="text-sm font-semibold text-ink">方向关键词</span>
                <Badge variant={arxivTrend?.available ? "info" : "warning"}>{arxivTrend?.query_date || "未同步"}</Badge>
              </div>
              <KeywordCloud keywords={arxivKeywords.slice(0, 7)} />
            </div>
            <div className="rounded-2xl border border-border bg-page p-3">
              <div className="text-sm font-semibold text-ink">趋势洞察</div>
              <p className="mt-1 text-sm leading-6 text-ink-secondary">
                {arxivTrend?.direction || "暂无趋势洞察"}
              </p>
            </div>
          </div>
        </div>
      </Card>

      <section className="grid gap-4 xl:grid-cols-[1.08fr_0.92fr]">
        <Card className="rounded-[26px] p-4 sm:p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <SectionHeader title="主题详情" className="mb-0" />
            <Badge variant="info">{libraryFocus?.window_label || "论文库"}</Badge>
          </div>
          {topicCards.length > 0 ? (
            <div className="grid gap-2.5">
              {topicCards.map((topic, index) => (
                <TopicSummaryCard key={`${topic.label}-${index}`} topic={topic} accent={dataAccent(index)} />
              ))}
            </div>
          ) : (
            <EmptyHint text="本库当前还没有可展示的主题详情。" />
          )}
        </Card>

        <Card className="rounded-[26px] p-4 sm:p-5">
          <SectionHeader title="本库研究资产" />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <MiniMetric label="图谱节点" value={formatCount(snapshot.graph?.total_papers)} sub="已收录" icon={<GitBranch className="h-4 w-4" />} />
            <MiniMetric label="引用边" value={formatCount(snapshot.graph?.total_edges)} sub={`密度 ${(graphDensity * 100).toFixed(2)}%`} icon={<BarChart3 className="h-4 w-4" />} />
            <MiniMetric label="近 7 天摄入" value={formatCount(snapshot.today?.week_new)} sub="本库新增" icon={<Layers3 className="h-4 w-4" />} />
          </div>

          <div className="mt-4 rounded-2xl border border-border bg-page p-4">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-ink">本库摄入</h3>
              <span className="text-xs text-ink-secondary">{trendPoints.length > 0 ? `${trendPoints.length} 天` : "等待数据"}</span>
            </div>
            <TrendSparkline values={trendValues} />
          </div>

          <div className="mt-4">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-ink">本库关键词</h3>
              <Badge variant="default">论文库口径</Badge>
            </div>
            <KeywordCloud keywords={libraryKeywords.slice(0, 10)} />
          </div>
        </Card>
      </section>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {MODULE_LINKS.map((item) => (
          <button key={item.to} type="button" onClick={() => navigate(item.to)} className="module-link-card">
            <div className="feature-icon !h-10 !w-10">
              <item.icon className="h-4 w-4 text-primary" />
            </div>
            <div className="min-w-0 flex-1 text-left">
              <div className="text-sm font-semibold text-ink">{item.title}</div>
              <div className="mt-1 text-xs text-ink-secondary">{item.subtitle}</div>
            </div>
            <ArrowRight className="h-4 w-4 text-ink-tertiary" />
          </button>
        ))}
      </section>
    </div>
  );
}

function MiniMetric({ label, value, sub, icon }: { label: string; value: string; sub: string; icon: ReactNode }) {
  return (
    <div className="mini-stat-card">
      <div className="flex items-center justify-between gap-3">
        <span className="dashboard-kpi-label">{label}</span>
        <span className="text-primary">{icon}</span>
      </div>
      <strong className="mt-2 block text-2xl font-semibold tracking-[-0.04em] text-ink">{value}</strong>
      <span className="text-xs text-ink-secondary">{sub}</span>
    </div>
  );
}

function SectionHeader({ title, className }: { title: string; className?: string }) {
  return (
    <div className={cn("mb-4", className)}>
      <h2 className="text-xl font-semibold tracking-[-0.03em] text-ink">{title}</h2>
    </div>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-dashed border-border bg-page px-4 py-4 text-sm leading-6 text-ink-secondary">
      {text}
    </div>
  );
}

function DirectionGrid({ directions, compact = false }: { directions: ArxivTrendDirection[]; compact?: boolean }) {
  if (!directions.length) return <EmptyHint text="arXiv CS 方向暂时不可用。" />;
  return (
    <div className={cn("grid grid-cols-1 gap-2 sm:grid-cols-2", compact ? "" : "md:grid-cols-2")}>
      {directions.map((item, index) => {
        const accent = dataAccent(index);
        return (
        <div
          key={item.key}
          className={cn("rounded-xl border border-border bg-page", compact ? "px-2.5 py-2" : "px-3 py-2.5")}
          style={{
            borderColor: accentBorder(accent),
            background: `linear-gradient(180deg, ${accentSurface(accent, 7)}, ${accentSurface(accent, 3, "var(--color-page)")})`,
          }}
        >
          <div className="flex items-center justify-between gap-2">
            <span className={cn("font-semibold leading-5 text-ink", compact ? "text-xs" : "text-sm")}>{item.label}</span>
            <span className="shrink-0 text-[11px] text-ink-tertiary">
              {item.count} 篇 · {Math.round(item.sample_ratio * 100)}%
            </span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-border-light">
            <div className="h-full rounded-full transition-all duration-300" style={{ width: `${Math.max(5, item.sample_ratio * 100)}%`, backgroundColor: accent }} />
          </div>
          {item.summary ? (
            <div className="mt-2 text-[11px] leading-4 text-ink-secondary">
              {item.summary}
            </div>
          ) : null}
          {!compact && item.keywords && item.keywords.length > 0 ? (
            <div className="mt-2 text-[11px] leading-4 text-ink-secondary">
              {item.keywords.slice(0, 2).map((keyword) => keyword.keyword).join(" / ")}
            </div>
          ) : null}
        </div>
      );
      })}
    </div>
  );
}

function KeywordCloud({ keywords }: { keywords: ArxivKeyword[] }) {
  if (!keywords.length) return <EmptyHint text="关键词暂不可用。" />;
  return (
    <div className="flex flex-wrap gap-2">
      {keywords.map((item, index) => {
        const label = item.keyword || item.term || "";
        const accent = dataAccent(index);
        return (
          <span
            key={label}
            className="inline-flex items-center gap-2 rounded-full border px-3 py-2 text-xs text-ink-secondary"
            style={{
              borderColor: accentBorder(accent, 22),
              backgroundColor: accentSurface(accent, 8),
            }}
          >
            <TrendingUp className="h-3.5 w-3.5" style={{ color: accent }} />
            {label}
            <span className="text-ink-tertiary">{item.count}</span>
          </span>
        );
      })}
    </div>
  );
}

function TopicSummaryCard({ topic, accent }: { topic: LibraryFeaturedTopic; accent: string }) {
  const deepRead = topic.progress.deep_read;
  const skimmed = topic.progress.skimmed;
  const unread = topic.progress.unread;
  return (
    <div
      className="rounded-[18px] border border-border px-4 py-4 sm:px-5"
      style={{
        borderColor: accentBorder(accent, 16),
        background: `linear-gradient(180deg, ${accentSurface(accent, 4)}, var(--color-surface))`,
      }}
    >
      <div className="flex items-start justify-between gap-4">
        <h3 className="min-w-0 text-xl font-semibold tracking-[-0.03em] text-ink">{topic.label}</h3>
        <span className="shrink-0 rounded-full bg-page px-3.5 py-1.5 text-sm font-semibold tracking-[-0.02em] text-ink shadow-sm">
          {topic.paper_count} 篇
        </span>
      </div>

      <div className="mt-3.5 grid gap-4 sm:grid-cols-2">
        <div className="flex items-center gap-3">
          <span
            className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl"
            style={{ backgroundColor: accentSurface("var(--color-error)", 9) }}
          >
            <Quote className="h-4 w-4 text-error" />
          </span>
          <div>
            <div className="text-xl font-semibold tracking-[-0.03em] text-ink">{formatCount(topic.citation_count)}</div>
            <div className="text-xs text-ink-secondary">引用边</div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <span
            className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl"
            style={{ backgroundColor: accentSurface("var(--color-success)", 10) }}
          >
            <TrendingUp className="h-4 w-4 text-success" />
          </span>
          <div>
            <div className="text-xl font-semibold tracking-[-0.03em] text-ink">{formatCount(topic.active_30d)}</div>
            <div className="text-xs text-ink-secondary">30天活跃</div>
          </div>
        </div>
      </div>

      <div className="mt-4">
        <div className="flex items-center justify-between gap-4">
          <span className="text-sm font-medium text-ink-secondary">阅读进度</span>
          <span className="text-base font-semibold tracking-[-0.02em]" style={{ color: accent }}>
            {topic.progress.completion_pct}%
          </span>
        </div>
        <TopicProgressBar deepRead={deepRead} skimmed={skimmed} unread={unread} />
      </div>
    </div>
  );
}

function TopicProgressBar({
  deepRead,
  skimmed,
  unread,
}: {
  deepRead: number;
  skimmed: number;
  unread: number;
}) {
  const total = Math.max(1, deepRead + skimmed + unread);
  const segments = [
    { label: "精读", value: deepRead, accent: "var(--color-error)" },
    { label: "粗读", value: skimmed, accent: "var(--color-warning)" },
    { label: "未读", value: unread, accent: "var(--color-border-light)" },
  ];
  return (
    <div>
      <div className="mt-2 flex h-2.5 overflow-hidden rounded-full bg-border-light">
        {segments.map((segment) => (
          <div
            key={segment.label}
            className="h-full transition-all duration-300"
            style={{
              width: `${(segment.value / total) * 100}%`,
              backgroundColor: segment.accent,
            }}
          />
        ))}
      </div>
      <div className="mt-2.5 grid grid-cols-3 gap-2 text-xs text-ink-secondary">
        {segments.map((segment) => (
          <span key={segment.label} className="inline-flex items-center gap-2">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: segment.accent }} />
            {segment.label} {segment.value}
          </span>
        ))}
      </div>
    </div>
  );
}

function TrendSparkline({ values }: { values: number[] }) {
  const chartWidth = 320;
  const chartHeight = 86;
  const normalized = values.length > 0 ? values : [0, 0, 0, 0, 0, 0, 0];
  return (
    <svg viewBox={`0 0 ${chartWidth} ${chartHeight}`} className="h-[86px] w-full">
      <defs>
        <linearGradient id="homeTrendArea" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="var(--color-primary)" stopOpacity="0.22" />
          <stop offset="100%" stopColor="var(--color-primary)" stopOpacity="0.03" />
        </linearGradient>
      </defs>
      {[0, 1, 2].map((index) => (
        <line
          key={index}
          x1="14"
          x2={String(chartWidth - 14)}
          y1={String(18 + index * 22)}
          y2={String(18 + index * 22)}
          stroke="var(--color-border-light)"
          strokeDasharray="4 6"
        />
      ))}
      <path d={buildAreaPath(normalized, chartWidth, chartHeight)} fill="url(#homeTrendArea)" />
      <path
        d={buildLinePath(normalized, chartWidth, chartHeight)}
        fill="none"
        stroke="var(--color-primary)"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
