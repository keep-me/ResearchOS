/**
 * 领域洞察面板 — 一键查询: 时间线 + 演化 + 质量 + 研究空白
 * @author Color2333
 */
import { useState, useCallback, useMemo, useEffect } from "react";
import { Button, Badge } from "@/components/ui";
import AIPromptHelper from "@/components/AIPromptHelper";
import { useToast } from "@/contexts/ToastContext";
import { generatedApi, graphApi, paperApi, tasksApi, topicApi, type PaperKeywordFacet } from "@/services/api";
import {
  Search, Network, Clock, BarChart3, TrendingUp, Star,
  ArrowDown, ArrowRight, Layers, Lightbulb, HelpCircle,
  Tag, Target, AlertTriangle, Zap,
  ChevronDown, ChevronRight, SlidersHorizontal, Compass, RotateCw, History, Trash2, Flame,
} from "@/lib/lucide";
import type {
  TimelineResponse, GraphQuality, EvolutionResponse, ResearchGapsResponse, GeneratedContent, GeneratedContentListItem,
} from "@/types";
import { Section, PaperLink, NetStat, StrengthBadge, GapCard, LoadingHint } from "./shared";

const LIMIT_OPTIONS = [
  { value: 30, label: "30 篇" },
  { value: 50, label: "50 篇" },
  { value: 100, label: "100 篇" },
  { value: 200, label: "200 篇" },
  { value: 500, label: "500 篇" },
] as const;

function parseKeywordTokens(value: string): string[] {
  const seen = new Set<string>();
  return value
    .split(/[\n,，;；]+/)
    .map((item) => item.trim())
    .filter((item) => {
      const normalized = item.toLowerCase();
      if (!normalized || seen.has(normalized)) return false;
      seen.add(normalized);
      return true;
    });
}

export default function InsightPanel() {
  const { toast } = useToast();
  const [keyword, setKeyword] = useState("");
  const [limit, setLimit] = useState(100);
  const [loading, setLoading] = useState(false);
  const [aiDesc, setAiDesc] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [suggestions, setSuggestions] = useState<Array<{ name: string; query: string; reason: string }>>([]);

  const [timelineData, setTimelineData] = useState<TimelineResponse | null>(null);
  const [qualityData, setQualityData] = useState<GraphQuality | null>(null);
  const [evolutionData, setEvolutionData] = useState<EvolutionResponse | null>(null);
  const [gapsData, setGapsData] = useState<ResearchGapsResponse | null>(null);
  const [taskMessage, setTaskMessage] = useState("");
  const [history, setHistory] = useState<GeneratedContentListItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyDetailLoading, setHistoryDetailLoading] = useState(false);
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null);

  /* 推荐关键词 */
  const [libraryKeywords, setLibraryKeywords] = useState<PaperKeywordFacet[]>([]);
  const [activeQuery, setActiveQuery] = useState<string>("");
  const selectedKeywords = useMemo(() => parseKeywordTokens(keyword), [keyword]);

  const hydrateInsightDetail = useCallback((detail: Pick<GeneratedContent, "keyword" | "metadata_json">) => {
    const metadata = detail.metadata_json && typeof detail.metadata_json === "object" && !Array.isArray(detail.metadata_json)
      ? detail.metadata_json as Record<string, unknown>
      : {};
    const resolvedKeyword = String(detail.keyword || metadata.keyword || "").trim();
    setTimelineData((metadata.timeline as TimelineResponse | null) || null);
    setEvolutionData((metadata.evolution as EvolutionResponse | null) || null);
    setQualityData((metadata.quality as GraphQuality | null) || null);
    setGapsData((metadata.gaps as ResearchGapsResponse | null) || null);
    if (resolvedKeyword) {
      setKeyword(resolvedKeyword);
      setActiveQuery(resolvedKeyword);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const result = await generatedApi.list("graph_insight", 40);
      const items = Array.isArray(result.items) ? result.items : [];
      setHistory(items);
      return items;
    } catch {
      return [];
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    (async () => {
      const keywordRes = await paperApi.keywordStats({ limit: 18 }).catch(
        () => ({ items: [] as PaperKeywordFacet[] }),
      );
      setLibraryKeywords(keywordRes.items || []);
    })();
  }, []);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  const suggestedKeywords = libraryKeywords
    .map((item) => ({
      keyword: String(item.keyword || "").trim(),
      count: Number(item.count || 0),
    }))
    .filter((item) => item.keyword);

  const pollInsightTask = useCallback(async (taskId: string) => {
    const timeoutAt = Date.now() + 10 * 60 * 1000;
    while (Date.now() < timeoutAt) {
      const status = await tasksApi.getStatus(taskId);
      setTaskMessage(String(status.message || "").trim());
      if (status.finished) {
        if (!status.success || status.status === "failed" || status.status === "cancelled") {
          throw new Error(status.error || status.message || "领域洞察任务失败");
        }
        return await tasksApi.getResult(taskId) as {
          timeline?: TimelineResponse | null;
          evolution?: EvolutionResponse | null;
          quality?: GraphQuality | null;
          gaps?: ResearchGapsResponse | null;
        };
      }
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
    throw new Error("领域洞察任务超时，请稍后重试");
  }, []);

  /* 一键聚合查四项 */
  const runInsight = useCallback(async (kw: string) => {
    const normalizedQuery = parseKeywordTokens(kw).join(", ") || kw.trim();
    if (!normalizedQuery) return;
    setKeyword(normalizedQuery);
    setActiveQuery(normalizedQuery);
    setSelectedHistoryId(null);
    setLoading(true);
    setTaskMessage("创建领域洞察任务...");
    try {
      const kickoff = await graphApi.insightAsync(normalizedQuery, limit);
      if (!kickoff.task_id) throw new Error("领域洞察任务启动失败");
      const result = await pollInsightTask(kickoff.task_id);
      setTimelineData((result.timeline as TimelineResponse | null) || null);
      setEvolutionData((result.evolution as EvolutionResponse | null) || null);
      setQualityData((result.quality as GraphQuality | null) || null);
      setGapsData((result.gaps as ResearchGapsResponse | null) || null);
      const savedId = typeof (result as Record<string, unknown>).content_id === "string"
        ? String((result as Record<string, unknown>).content_id)
        : null;
      void loadHistory();
      setSelectedHistoryId(savedId);
    } catch (error) {
      try {
        const [tl, ev, qa, gp] = await Promise.all([
          graphApi.timeline(normalizedQuery, limit).catch(() => null),
          graphApi.evolution(normalizedQuery, limit).catch(() => null),
          graphApi.quality(normalizedQuery, limit).catch(() => null),
          graphApi.researchGaps(normalizedQuery, limit).catch(() => null),
        ]);
        if (tl) setTimelineData(tl);
        if (ev) setEvolutionData(ev);
        if (qa) setQualityData(qa);
        if (gp) setGapsData(gp);
        if (!tl && !ev && !qa && !gp) {
          throw error;
        }
      } catch {
        toast("error", error instanceof Error ? error.message : "查询失败，请重试");
      }
    } finally {
      setLoading(false);
      setTaskMessage("");
    }
  }, [limit, pollInsightTask, toast]);

  const handleSubmit = useCallback(() => {
    runInsight(keyword);
  }, [keyword, runInsight]);

  const hasResults = timelineData || qualityData || evolutionData || gapsData;

  const handleAiSuggest = useCallback(async () => {
    const description = aiDesc.trim() || keyword.trim();
    if (!description) {
      toast("warning", "请先描述想分析的研究方向");
      return;
    }
    setAiLoading(true);
    try {
      const result = await topicApi.suggestKeywords(description, {
        source_scope: "hybrid",
        search_field: "all",
      });
      setSuggestions(result.suggestions || []);
    } catch {
      toast("error", "AI 建议生成失败");
    } finally {
      setAiLoading(false);
    }
  }, [aiDesc, keyword, toast]);

  const applySuggestion = useCallback((suggestion: { name: string; query: string; reason: string }) => {
    const nextKeyword = suggestion.query.trim() || suggestion.name.trim();
    setAiDesc(suggestion.name);
    setKeyword(nextKeyword);
    setSuggestions([]);
  }, []);

  const toggleSuggestedKeyword = useCallback((nextKeyword: string) => {
    const normalized = nextKeyword.trim();
    if (!normalized) return;
    const exists = selectedKeywords.some((item) => item.toLowerCase() === normalized.toLowerCase());
    const next = exists
      ? selectedKeywords.filter((item) => item.toLowerCase() !== normalized.toLowerCase())
      : [...selectedKeywords, normalized];
    setKeyword(next.join(", "));
  }, [selectedKeywords]);

  /* 单项刷新 */
  const refreshTimeline = useCallback(async () => { if (activeQuery) setTimelineData(await graphApi.timeline(activeQuery, limit)); }, [activeQuery, limit]);
  const refreshEvolution = useCallback(async () => { if (activeQuery) setEvolutionData(await graphApi.evolution(activeQuery, limit)); }, [activeQuery, limit]);
  const refreshQuality = useCallback(async () => { if (activeQuery) setQualityData(await graphApi.quality(activeQuery, limit)); }, [activeQuery, limit]);
  const refreshGaps = useCallback(async () => { if (activeQuery) setGapsData(await graphApi.researchGaps(activeQuery, limit)); }, [activeQuery, limit]);

  const handleViewHistory = useCallback(async (item: GeneratedContentListItem) => {
    setHistoryDetailLoading(true);
    try {
      const detail = await generatedApi.detail(item.id);
      hydrateInsightDetail(detail);
      setSelectedHistoryId(item.id);
    } catch {
      toast("error", "加载领域洞察历史失败");
    } finally {
      setHistoryDetailLoading(false);
    }
  }, [hydrateInsightDetail, toast]);

  const handleDeleteHistory = useCallback(async (item: GeneratedContentListItem, event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    try {
      await generatedApi.delete(item.id);
      setHistory((prev) => prev.filter((entry) => entry.id !== item.id));
      if (selectedHistoryId === item.id) {
        setSelectedHistoryId(null);
      }
    } catch {
      toast("error", "删除历史记录失败");
    }
  }, [selectedHistoryId, toast]);

  const formatHistoryTime = useCallback((value: string) => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString("zh-CN", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "Asia/Shanghai",
    });
  }, []);

  return (
    <div className="space-y-5">
      {/* 搜索区 */}
      <div className="rounded-2xl border border-border bg-surface p-5 shadow-sm">
        <div className="flex gap-3">
          <div className="relative flex-1">
            <Search className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-tertiary" />
            <input
              placeholder="输入关键词: transformer, reinforcement learning..."
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleSubmit(); }}
              className="h-11 w-full rounded-xl border border-border bg-page pl-10 pr-4 text-sm text-ink placeholder:text-ink-placeholder focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
          </div>
          <div className="relative flex items-center">
            <SlidersHorizontal className="absolute left-3 h-3.5 w-3.5 text-ink-tertiary pointer-events-none" />
            <select
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="h-11 appearance-none rounded-xl border border-border bg-page pl-9 pr-8 text-sm text-ink focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 cursor-pointer"
            >
              {LIMIT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <ChevronDown className="absolute right-2.5 h-3.5 w-3.5 text-ink-tertiary pointer-events-none" />
          </div>
          <Button icon={<Search className="h-4 w-4" />} onClick={handleSubmit} loading={loading}>分析</Button>
        </div>
      </div>

      <AIPromptHelper
        value={aiDesc}
        onChange={setAiDesc}
        onSubmit={handleAiSuggest}
        onApply={applySuggestion}
        suggestions={suggestions}
        loading={aiLoading}
        placeholder="输入探索主题"
      />

      {/* 推荐关键词 */}
      {suggestedKeywords.length > 0 && (
        <div className="rounded-2xl border border-border bg-surface p-4 shadow-sm">
          <div className="mb-3 flex items-center gap-2">
            <Tag className="h-3.5 w-3.5 text-primary" />
            <span className="text-xs font-medium text-ink-secondary">快速探索 · 当前论文库关键词</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {suggestedKeywords.map((item) => (
              <button
                key={item.keyword}
                onClick={() => toggleSuggestedKeyword(item.keyword)}
                className={`group flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-medium transition-all ${
                  selectedKeywords.some((selected) => selected.toLowerCase() === item.keyword.toLowerCase())
                    ? "bg-primary text-white shadow-sm"
                    : "bg-page text-ink-secondary hover:bg-primary/8 hover:text-primary"
                }`}
              >
                {item.keyword}
                {item.count > 0 && (
                  <span className={`rounded-full px-1.5 text-[10px] ${
                    selectedKeywords.some((selected) => selected.toLowerCase() === item.keyword.toLowerCase()) ? "bg-white/20 text-white" : "bg-border-light text-ink-tertiary"
                  }`}>{item.count}</span>
                )}
              </button>
            ))}
          </div>
          {selectedKeywords.length > 0 && (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px]">
              <Badge>{selectedKeywords.length} 个已选关键词</Badge>
              <button
                type="button"
                onClick={() => setKeyword("")}
                className="rounded-full border border-border/70 bg-white px-2.5 py-1 text-ink-tertiary transition-colors hover:text-ink"
              >
                清空
              </button>
              <button
                type="button"
                onClick={handleSubmit}
                className="rounded-full bg-primary px-2.5 py-1 font-medium text-white transition-opacity hover:opacity-90"
              >
                分析已选关键词
              </button>
            </div>
          )}
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="space-y-4">
          <div className="rounded-2xl border border-border bg-surface shadow-sm">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div className="flex items-center gap-2">
                <History className="h-4 w-4 text-primary" />
                <span className="text-sm font-semibold text-ink">历史记录</span>
              </div>
              <span className="text-xs text-ink-tertiary">{history.length} 条</span>
            </div>
            {historyLoading ? (
              <div className="px-4 py-6 text-sm text-ink-tertiary">加载中...</div>
            ) : history.length === 0 ? (
              <div className="px-4 py-8 text-sm text-ink-tertiary">暂无历史</div>
            ) : (
              <div className="max-h-[70vh] space-y-1 overflow-y-auto p-2">
                {history.map((item) => {
                  const isActive = selectedHistoryId === item.id;
                  return (
                    <div
                      key={item.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => { void handleViewHistory(item); }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          void handleViewHistory(item);
                        }
                      }}
                      className={`group w-full cursor-pointer rounded-xl border px-3 py-3 text-left transition-all ${
                        isActive
                          ? "border-primary/25 bg-primary/8 shadow-sm"
                          : "border-transparent bg-page/70 hover:border-border hover:bg-page"
                      }`}
                    >
                      <div className="flex items-start gap-2">
                        <div className="min-w-0 flex-1">
                          <p className={`line-clamp-2 text-sm font-medium ${isActive ? "text-primary" : "text-ink"}`}>
                            {item.keyword || item.title}
                          </p>
                          <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-ink-tertiary">
                            <span>{formatHistoryTime(item.created_at)}</span>
                            {item.keyword ? <Badge>{item.keyword}</Badge> : null}
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={(event) => { void handleDeleteHistory(item, event); }}
                          className="rounded-lg p-1.5 text-ink-tertiary opacity-0 transition-all hover:bg-error/10 hover:text-error group-hover:opacity-100"
                          title="删除历史记录"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </aside>

        <div className="space-y-5">
          {(loading || historyDetailLoading) && (
            <div className="space-y-2">
              <LoadingHint tab="insight" isInit={false} />
              {taskMessage ? (
                <div className="rounded-xl border border-border bg-page px-4 py-3 text-sm text-ink-secondary">
                  {taskMessage}
                </div>
              ) : null}
            </div>
          )}

          {!loading && !historyDetailLoading && !hasResults && (
            <div className="flex flex-col items-center rounded-2xl border border-dashed border-border py-16 text-center">
              <Compass className="h-8 w-8 text-ink-tertiary/30" />
            </div>
          )}

          {!loading && !historyDetailLoading && hasResults && (
            <div className="space-y-5">
              {timelineData && (
                <CollapsibleSection title="时间线" icon={<Clock className="h-4 w-4 text-primary" />} onRefresh={refreshTimeline} defaultOpen>
                  <TimelineContent data={timelineData} />
                </CollapsibleSection>
              )}

              {evolutionData && (
                <CollapsibleSection title="演化趋势" icon={<TrendingUp className="h-4 w-4 text-primary" />} onRefresh={refreshEvolution} defaultOpen>
                  <EvolutionContent data={evolutionData} />
                </CollapsibleSection>
              )}

              {qualityData && (
                <CollapsibleSection title="质量分析" icon={<BarChart3 className="h-4 w-4 text-primary" />} onRefresh={refreshQuality}>
                  <QualityContent data={qualityData} />
                </CollapsibleSection>
              )}

              {gapsData && (
                <CollapsibleSection title="研究空白" icon={<Target className="h-4 w-4 text-warning" />} onRefresh={refreshGaps} defaultOpen>
                  <ResearchGapsContent data={gapsData} />
                </CollapsibleSection>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ==================== 可折叠区块 ==================== */
function CollapsibleSection({ title, icon, onRefresh, defaultOpen = false, children }: {
  title: string; icon: React.ReactNode; onRefresh?: () => void;
  defaultOpen?: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [refreshing, setRefreshing] = useState(false);

  const handleRefresh = async () => {
    if (!onRefresh) return;
    setRefreshing(true);
    try { await onRefresh(); } catch { /* refresh failed silently */ }
    finally { setRefreshing(false); }
  };

  return (
    <div className="animate-fade-in rounded-2xl border border-border bg-surface shadow-sm">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-5 py-4 text-left"
      >
        {icon}
        <span className="flex-1 text-sm font-semibold text-ink">{title}</span>
        {onRefresh && (
          <span
            onClick={(e) => { e.stopPropagation(); handleRefresh(); }}
            className="rounded-lg p-1.5 text-ink-tertiary hover:bg-hover hover:text-primary transition-colors"
          >
            <RotateCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
          </span>
        )}
        {open ? <ChevronDown className="h-4 w-4 text-ink-tertiary" /> : <ChevronRight className="h-4 w-4 text-ink-tertiary" />}
      </button>
      {open && <div className="border-t border-border px-5 pb-5 pt-4">{children}</div>}
    </div>
  );
}

/* ==================== 时间线内容 ==================== */
function TimelineContent({ data }: { data: TimelineResponse }) {
  return (
    <div className="space-y-6">
      {data.seminal.length > 0 && (
        <div>
          <p className="mb-3 flex items-center gap-1.5 text-xs font-semibold text-ink-secondary">
            <Star className="h-3.5 w-3.5 text-warning" /> 开创性论文
          </p>
          <div className="space-y-2">
            {data.seminal.map((e) => (
              <div key={e.paper_id} className="flex items-center justify-between rounded-xl border border-warning/20 bg-warning-light p-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <Star className="h-4 w-4 shrink-0 text-warning" />
                    <PaperLink id={e.paper_id} title={e.title} />
                  </div>
                  {e.why_seminal && <p className="mt-1 pl-6 text-xs text-ink-secondary">{e.why_seminal}</p>}
                </div>
                <div className="shrink-0 pl-4 text-right">
                  <span className="text-lg font-bold text-warning">{e.seminal_score.toFixed(2)}</span>
                  <p className="text-xs text-ink-tertiary">{e.year}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div>
        <p className="mb-3 flex items-center gap-1.5 text-xs font-semibold text-ink-secondary">
          <Clock className="h-3.5 w-3.5 text-primary" /> 时间轴 ({data.timeline.length} 篇)
        </p>
        <div className="relative ml-3 border-l-2 border-border-light pl-5 space-y-1">
          {data.timeline.map((e) => (
            <div key={e.paper_id} className="relative rounded-xl px-3 py-2 transition-colors hover:bg-hover">
              <span className="absolute -left-[1.625rem] top-1/2 h-2.5 w-2.5 -translate-y-1/2 rounded-full border-2 border-primary bg-surface" />
              <div className="flex items-center gap-3">
                <span className="w-10 shrink-0 text-xs font-semibold text-primary">{e.year}</span>
                <PaperLink id={e.paper_id} title={e.title} className="min-w-0 flex-1 truncate" />
                <div className="flex shrink-0 gap-2 text-[10px] text-ink-tertiary">
                  <span>↓{e.indegree}</span><span>↑{e.outdegree}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {data.milestones.length > 0 && (
        <div>
          <p className="mb-3 flex items-center gap-1.5 text-xs font-semibold text-ink-secondary">
            <Lightbulb className="h-3.5 w-3.5 text-info" /> 里程碑
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            {data.milestones.map((m) => (
              <div key={m.paper_id} className="flex items-center gap-3 rounded-xl bg-info-light p-3">
                <Lightbulb className="h-4 w-4 shrink-0 text-info" />
                <PaperLink id={m.paper_id} title={m.title} className="flex-1 truncate" />
                <span className="text-xs font-medium text-info">{m.year}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ==================== 演化内容 ==================== */
function EvolutionContent({ data }: { data: EvolutionResponse }) {
  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-xl bg-page p-4">
          <p className="mb-1 text-[10px] font-medium uppercase tracking-widest text-ink-tertiary">趋势总结</p>
          <p className="text-sm leading-relaxed text-ink-secondary">{data.summary.trend_summary}</p>
        </div>
        <div className="rounded-xl bg-page p-4">
          <p className="mb-1 text-[10px] font-medium uppercase tracking-widest text-ink-tertiary">阶段转变</p>
          <p className="text-sm leading-relaxed text-ink-secondary">{data.summary.phase_shift_signals}</p>
        </div>
        <div className="rounded-xl bg-primary/5 p-4">
          <p className="mb-1 text-[10px] font-medium uppercase tracking-widest text-primary">下周关注</p>
          <p className="text-sm font-medium leading-relaxed text-ink">{data.summary.next_week_focus}</p>
        </div>
      </div>

      <div>
        <p className="mb-3 flex items-center gap-1.5 text-xs font-semibold text-ink-secondary">
          <BarChart3 className="h-3.5 w-3.5 text-info" /> 年度分布
        </p>
        <div className="space-y-2">
          {data.year_buckets.map((b) => {
            const maxCount = Math.max(...data.year_buckets.map((x) => x.paper_count), 1);
            const pct = Math.max((b.paper_count / maxCount) * 100, 3);
            return (
              <div key={b.year} className="flex items-center gap-4 rounded-xl px-3 py-2 transition-colors hover:bg-hover">
                <span className="w-12 shrink-0 text-sm font-bold text-ink">{b.year}</span>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-page">
                      <div className="bar-animate h-full rounded-full bg-gradient-to-r from-primary to-primary/60" style={{ width: `${pct}%` }} />
                    </div>
                    <span className="w-10 text-right text-xs font-medium text-ink-secondary">{b.paper_count}</span>
                  </div>
                  {b.top_titles[0] && <p className="mt-0.5 truncate text-[10px] text-ink-tertiary">{b.top_titles[0]}</p>}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ==================== 质量分析内容 ==================== */
function QualityContent({ data }: { data: GraphQuality }) {
  const metrics = [
    { label: "节点数", value: data.node_count, icon: Layers, color: "primary" },
    { label: "边数", value: data.edge_count, icon: Network, color: "info" },
    { label: "密度", value: data.density.toFixed(4), icon: BarChart3, color: "warning" },
    { label: "连通比例", value: `${(data.connected_node_ratio * 100).toFixed(1)}%`, icon: TrendingUp, color: "success" },
    { label: "日期覆盖", value: `${(data.publication_date_coverage * 100).toFixed(1)}%`, icon: Clock, color: "info" },
  ] as const;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
      {metrics.map((m) => (
        <div key={m.label} className={`stat-gradient-${m.color} rounded-2xl border border-border p-4`}>
          <m.icon className={`h-4 w-4 text-${m.color} mb-2`} />
          <p className="text-xl font-bold text-ink">{m.value}</p>
          <p className="text-xs text-ink-tertiary">{m.label}</p>
        </div>
      ))}
    </div>
  );
}

/* ==================== 研究空白内容 ==================== */
function ResearchGapsContent({ data }: { data: ResearchGapsResponse }) {
  const { network_stats, analysis } = data;
  const { research_gaps, method_comparison, trend_analysis, overall_summary } = analysis;

  return (
    <div className="space-y-6">
      {/* 网络统计 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <NetStat label="总论文" value={network_stats.total_papers} />
        <NetStat label="引用边" value={network_stats.edge_count} />
        <NetStat label="密度" value={network_stats.density.toFixed(4)} />
        <NetStat label="连通率" value={`${(network_stats.connected_ratio * 100).toFixed(1)}%`} />
        <NetStat label="孤立论文" value={network_stats.isolated_count} highlight />
      </div>

      {/* 总结 */}
      {overall_summary && (
        <div className="rounded-xl bg-page p-5">
          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-ink-secondary">
            <Target className="h-3.5 w-3.5" /> 分析总结
          </p>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink-secondary">{overall_summary}</p>
        </div>
      )}

      {/* 研究空白列表 */}
      {research_gaps.length > 0 && (
        <div>
          <p className="mb-3 flex items-center gap-1.5 text-xs font-semibold text-ink-secondary">
            <AlertTriangle className="h-3.5 w-3.5 text-warning" /> 识别到 {research_gaps.length} 个研究空白
          </p>
          <div className="space-y-3">
            {research_gaps.map((gap, i) => <GapCard key={i} gap={gap} index={i} />)}
          </div>
        </div>
      )}

      {/* 方法对比矩阵 */}
      {method_comparison.methods.length > 0 && (
        <div>
          <p className="mb-3 flex items-center gap-1.5 text-xs font-semibold text-ink-secondary">
            <Layers className="h-3.5 w-3.5 text-info" /> 方法对比矩阵
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="px-3 py-2 text-left font-medium text-ink-tertiary">方法</th>
                  {method_comparison.dimensions.map((dim) => (
                    <th key={dim} className="px-3 py-2 text-center font-medium text-ink-tertiary">{dim}</th>
                  ))}
                  <th className="px-3 py-2 text-left font-medium text-ink-tertiary">来源</th>
                </tr>
              </thead>
              <tbody>
                {method_comparison.methods.map((m, i) => (
                  <tr key={i} className="border-b border-border/50 transition-colors hover:bg-hover">
                    <td className="px-3 py-2 font-medium text-ink">{m.name}</td>
                    {method_comparison.dimensions.map((dim) => (
                      <td key={dim} className="px-3 py-2 text-center">
                        <StrengthBadge value={m.scores[dim]} />
                      </td>
                    ))}
                    <td className="px-3 py-2 text-xs text-ink-tertiary">{m.papers.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {method_comparison.underexplored_combinations.length > 0 && (
            <div className="mt-4 rounded-xl bg-warning/5 p-4">
              <p className="mb-2 text-xs font-semibold text-warning">未被探索的方法组合</p>
              <ul className="space-y-1">
                {method_comparison.underexplored_combinations.map((c, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-ink-secondary">
                    <Zap className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />{c}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* 趋势分析 */}
      <div className="grid gap-4 sm:grid-cols-3">
        {trend_analysis.hot_directions.length > 0 && (
          <div className="rounded-xl bg-error/5 p-4">
            <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-error">
              <Flame className="h-3.5 w-3.5" /> 热门方向
            </p>
            <ul className="space-y-1">
              {trend_analysis.hot_directions.map((d, i) => (
                <li key={i} className="text-sm text-ink-secondary">• {d}</li>
              ))}
            </ul>
          </div>
        )}
        {trend_analysis.declining_areas.length > 0 && (
          <div className="rounded-xl bg-page p-4">
            <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-ink-tertiary">
              <ArrowDown className="h-3.5 w-3.5" /> 式微方向
            </p>
            <ul className="space-y-1">
              {trend_analysis.declining_areas.map((d, i) => (
                <li key={i} className="text-sm text-ink-secondary">• {d}</li>
              ))}
            </ul>
          </div>
        )}
        {trend_analysis.emerging_opportunities.length > 0 && (
          <div className="rounded-xl bg-success/5 p-4">
            <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-success">
              <Zap className="h-3.5 w-3.5" /> 新兴机会
            </p>
            <ul className="space-y-1">
              {trend_analysis.emerging_opportunities.map((d, i) => (
                <li key={i} className="text-sm text-ink-secondary">• {d}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
