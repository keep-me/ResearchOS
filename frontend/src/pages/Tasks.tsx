import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Button, Empty, Spinner } from "@/components/ui";
import {
  assistantWorkspaceApi,
  metricsApi,
  projectApi,
  tasksApi,
  type TaskTokenUsage,
  type TaskStatus,
} from "@/services/api";
import { useToast } from "@/contexts/ToastContext";
import { useDocumentVisible } from "@/hooks/useDocumentVisible";
import { timeAgo } from "@/lib/utils";
import type { CostMetrics, ProjectArtifactRef, ProjectRun } from "@/types";
import ArtifactPreviewModal from "@/components/ArtifactPreviewModal";
import {
  buildArtifactReadCandidates,
  type ArtifactPreviewState,
  fileNameFromPath,
  isMarkdownArtifact,
  isPreviewableArtifact,
  normalizeServerId,
} from "@/lib/workspaceArtifacts";
import { Activity, CheckCircle2, Clock3, Eye, FileText, FolderOpen, ListTodo, PieChart, RefreshCw, StopCircle, XCircle } from "lucide-react";

const FILTERS = [
  { key: "all", label: "全部" },
  { key: "running", label: "运行中" },
  { key: "completed", label: "已完成" },
  { key: "cancelled", label: "已终止" },
  { key: "failed", label: "失败" },
] as const;

function formatElapsedSeconds(value: number | null | undefined): string {
  const totalSeconds = Math.max(0, Math.round(Number(value || 0)));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}小时 ${minutes}分 ${seconds}秒`;
  if (minutes > 0) return `${minutes}分 ${seconds}秒`;
  return `${seconds}秒`;
}

function formatTokens(value: number | null | undefined): string {
  const total = Math.max(0, Number(value || 0));
  if (total >= 1_000_000) return `${(total / 1_000_000).toFixed(1)}M`;
  if (total >= 10_000) return `${Math.round(total / 1000)}k`;
  if (total >= 1000) return `${(total / 1000).toFixed(1)}k`;
  return new Intl.NumberFormat("zh-CN").format(total);
}

function formatCost(value: number | null | undefined): string {
  const amount = Math.max(0, Number(value || 0));
  if (amount <= 0) return "$0.00";
  if (amount < 0.01) return `$${amount.toFixed(4)}`;
  return `$${amount.toFixed(2)}`;
}

function tokenCategoryForStage(stage: string): string {
  const text = stage.toLowerCase();
  if (text.includes("skim")) return "论文粗读";
  if (text.includes("deep") || text.includes("paper_round")) return "论文精读";
  if (text.includes("embed")) return "向量化";
  if (text.includes("vision") || text.includes("figure")) return "图表理解";
  if (text.includes("wiki") || text.includes("writing")) return "写作生成";
  if (text.includes("graph") || text.includes("reasoning")) return "研究洞察";
  if (text.includes("keyword") || text.includes("topic")) return "主题发现";
  return "其他任务";
}

type TokenCategory = {
  label: string;
  tokens: number;
  inputTokens: number;
  outputTokens: number;
  calls: number;
  costUsd: number;
  color: string;
};

const TOKEN_COLORS = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2", "#64748b"];

function buildTokenCategories(metrics: CostMetrics | null): TokenCategory[] {
  const buckets = new Map<string, Omit<TokenCategory, "label" | "color">>();
  for (const stage of metrics?.by_stage || []) {
    const label = tokenCategoryForStage(stage.stage);
    const current = buckets.get(label) || { tokens: 0, inputTokens: 0, outputTokens: 0, calls: 0, costUsd: 0 };
    const inputTokens = Number(stage.input_tokens || 0);
    const outputTokens = Number(stage.output_tokens || 0);
    buckets.set(label, {
      tokens: current.tokens + inputTokens + outputTokens,
      inputTokens: current.inputTokens + inputTokens,
      outputTokens: current.outputTokens + outputTokens,
      calls: current.calls + Number(stage.calls || 0),
      costUsd: current.costUsd + Number(stage.total_cost_usd || 0),
    });
  }
  return [...buckets.entries()]
    .map(([label, item], index) => ({ label, ...item, color: TOKEN_COLORS[index % TOKEN_COLORS.length] }))
    .sort((a, b) => b.tokens - a.tokens);
}

function buildConicGradient(categories: TokenCategory[]): string {
  const total = categories.reduce((sum, item) => sum + item.tokens, 0);
  if (total <= 0) return "conic-gradient(#e5e7eb 0deg 360deg)";
  let cursor = 0;
  const segments = categories.map((item) => {
    const start = cursor;
    const end = cursor + (item.tokens / total) * 360;
    cursor = end;
    return `${item.color} ${start.toFixed(1)}deg ${end.toFixed(1)}deg`;
  });
  return `conic-gradient(${segments.join(", ")})`;
}

function usableTaskTokenUsage(task: TaskStatus): TaskTokenUsage {
  return task.token_usage || {
    input_tokens: 0,
    output_tokens: 0,
    reasoning_tokens: 0,
    total_tokens: 0,
    total_cost_usd: 0,
    calls: 0,
    source: "unavailable",
    category: tokenCategoryForStage(task.task_type),
  };
}

function StatusBadge({ task }: { task: TaskStatus }) {
  if (!task.finished) {
    if (task.status === "paused") {
      return <Badge variant="warning">等待审批</Badge>;
    }
    return <Badge variant="info">{task.cancel_requested ? "终止中" : "运行中"}</Badge>;
  }
  if (task.status === "cancelled" || task.cancelled) {
    return <Badge variant="warning">已终止</Badge>;
  }
  return task.success ? <Badge variant="success">已完成</Badge> : <Badge variant="error">失败</Badge>;
}

function TokenOverviewStrip({
  categories,
  totalTokens,
  metrics,
}: {
  categories: TokenCategory[];
  totalTokens: number;
  metrics: CostMetrics | null;
}) {
  const hasData = totalTokens > 0 && categories.length > 0;
  return (
    <div className="glass-card glass-card-soft rounded-[26px] p-4">
      <div className="grid gap-4 xl:grid-cols-[1fr_112px_1.6fr] xl:items-center">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-2">
          <TokenStat label="7 天 Token" value={formatTokens(totalTokens)} />
          <TokenStat label="估算费用" value={formatCost(metrics?.total_cost_usd)} />
          <TokenStat label="调用次数" value={String(metrics?.calls || 0)} />
          <TokenStat label="输入 / 输出" value={`${formatTokens(metrics?.input_tokens)} / ${formatTokens(metrics?.output_tokens)}`} />
        </div>

        <div className="mx-auto flex h-28 w-28 items-center justify-center rounded-full p-2.5" style={{ background: buildConicGradient(categories) }}>
          <div className="flex h-[76px] w-[76px] flex-col items-center justify-center rounded-full border border-border bg-page text-center shadow-sm">
            <PieChart className="h-4 w-4 text-primary" />
            <strong className="mt-1 text-sm font-semibold text-ink">{formatTokens(totalTokens)}</strong>
          </div>
        </div>

        <div className="space-y-2">
          {hasData ? categories.slice(0, 6).map((item) => {
            const ratio = totalTokens > 0 ? Math.round((item.tokens / totalTokens) * 100) : 0;
            return (
              <div key={item.label} className="grid gap-2 sm:grid-cols-[128px_1fr_92px] sm:items-center">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: item.color }} />
                  <span className="truncate text-xs font-medium text-ink">{item.label}</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-border-light">
                  <div className="h-full rounded-full" style={{ width: `${Math.max(4, ratio)}%`, backgroundColor: item.color }} />
                </div>
                <div className="flex justify-between gap-2 text-[11px] text-ink-tertiary sm:justify-end">
                  <span>{ratio}%</span>
                  <span>{formatTokens(item.tokens)}</span>
                </div>
              </div>
            );
          }) : (
            <div className="rounded-xl border border-dashed border-border bg-page px-4 py-5 text-sm text-ink-secondary">
              最近 7 天还没有可统计的模型调用。
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function TokenStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border bg-page px-3 py-2.5">
      <div className="text-[11px] text-ink-secondary">{label}</div>
      <div className="mt-1 truncate text-base font-semibold text-ink">{value}</div>
    </div>
  );
}

function TokenUsageLine({ usage, sourceLabel }: { usage: TaskTokenUsage; sourceLabel: string }) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-ink-secondary">
      <span className="rounded-full border border-border bg-page px-2.5 py-1">
        Token {formatTokens(usage.total_tokens)}
      </span>
      <span className="rounded-full border border-border bg-page px-2.5 py-1">
        输入 {formatTokens(usage.input_tokens)} / 输出 {formatTokens(usage.output_tokens)}
      </span>
      <span className="rounded-full border border-border bg-page px-2.5 py-1">
        {formatCost(usage.total_cost_usd)}
      </span>
      <span className="rounded-full border border-border bg-page px-2.5 py-1">
        {usage.category || "其他任务"}
      </span>
      <span className="rounded-full border border-border bg-page px-2.5 py-1">
        {sourceLabel}
      </span>
    </div>
  );
}

export default function TasksPage() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [tasks, setTasks] = useState<TaskStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("all");
  const [costMetrics, setCostMetrics] = useState<CostMetrics | null>(null);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [taskLogs, setTaskLogs] = useState<Record<string, Array<{ timestamp: number; level: string; message: string }>>>({});
  const [taskResults, setTaskResults] = useState<Record<string, Record<string, unknown>>>({});
  const [loadingLogsId, setLoadingLogsId] = useState<string | null>(null);
  const [loadingResultId, setLoadingResultId] = useState<string | null>(null);
  const [runDetails, setRunDetails] = useState<Record<string, ProjectRun>>({});
  const [revealingPath, setRevealingPath] = useState<string | null>(null);
  const [previewLoadingPath, setPreviewLoadingPath] = useState<string | null>(null);
  const [artifactPreview, setArtifactPreview] = useState<ArtifactPreviewState | null>(null);
  const loadingTasksRef = useRef(false);
  const documentVisible = useDocumentVisible();

  const loadTasks = useCallback(async (silent = false) => {
    if (loadingTasksRef.current) {
      return;
    }
    if (silent && !documentVisible) {
      return;
    }
    loadingTasksRef.current = true;
    if (!silent) setLoading(true);
    try {
      const res = await tasksApi.list(undefined, 30);
      setTasks(res.tasks || []);
    } finally {
      loadingTasksRef.current = false;
      if (!silent) setLoading(false);
    }
  }, [documentVisible]);

  const loadCostMetrics = useCallback(async () => {
    try {
      const res = await metricsApi.costs(7);
      setCostMetrics(res);
    } catch (error) {
      console.warn("load token cost metrics failed", error);
    }
  }, []);

  const hasRunning = useMemo(() => tasks.some((task) => !task.finished), [tasks]);

  useEffect(() => {
    void loadTasks();
    void loadCostMetrics();
  }, [loadCostMetrics, loadTasks]);

  useEffect(() => {
    if (!documentVisible) return undefined;
    const timer = setInterval(() => {
      void loadTasks(true);
    }, hasRunning ? 3000 : 10000);
    return () => clearInterval(timer);
  }, [documentVisible, hasRunning, loadTasks]);

  useEffect(() => {
    if (documentVisible) {
      void loadTasks(true);
    }
  }, [documentVisible, loadTasks]);

  useEffect(() => {
    if (!documentVisible) return undefined;
    const timer = setInterval(() => {
      void loadCostMetrics();
    }, hasRunning ? 15000 : 45000);
    return () => clearInterval(timer);
  }, [documentVisible, hasRunning, loadCostMetrics]);

  const filteredTasks = useMemo(() => {
    if (filter === "all") return tasks;
    if (filter === "cancelled") return tasks.filter((task) => task.status === "cancelled" || task.cancelled);
    if (filter === "running") return tasks.filter((task) => !task.finished);
    return tasks.filter((task) => task.status === filter);
  }, [filter, tasks]);

  const counts = useMemo(
    () => ({
      all: tasks.length,
      running: tasks.filter((task) => !task.finished).length,
      completed: tasks.filter((task) => task.finished && task.success).length,
      cancelled: tasks.filter((task) => task.status === "cancelled" || task.cancelled).length,
      failed: tasks.filter((task) => task.finished && !task.success && task.status !== "cancelled" && !task.cancelled).length,
    }),
    [tasks],
  );

  const tokenCategories = useMemo(() => buildTokenCategories(costMetrics), [costMetrics]);
  const totalWindowTokens = Number(costMetrics?.input_tokens || 0) + Number(costMetrics?.output_tokens || 0);

  const handleCancel = useCallback(async (taskId: string) => {
    setCancellingId(taskId);
    try {
      await tasksApi.cancel(taskId);
      await loadTasks(true);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "终止任务失败");
    } finally {
      setCancellingId(null);
    }
  }, [loadTasks, toast]);

  const handleRetry = useCallback(async (taskId: string) => {
    setRetryingId(taskId);
    try {
      await tasksApi.retry(taskId);
      await loadTasks(true);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "重试任务失败");
    } finally {
      setRetryingId(null);
    }
  }, [loadTasks, toast]);

  const handleLoadLogs = useCallback(async (taskId: string) => {
    if (taskLogs[taskId]) {
      setTaskLogs((current) => {
        const next = { ...current };
        delete next[taskId];
        return next;
      });
      return;
    }
    setLoadingLogsId(taskId);
    try {
      const res = await tasksApi.getLogs(taskId, 80);
      setTaskLogs((current) => ({ ...current, [taskId]: res.items || [] }));
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "加载任务日志失败");
    } finally {
      setLoadingLogsId(null);
    }
  }, [taskLogs, toast]);

  const handleLoadResult = useCallback(async (taskId: string) => {
    if (taskResults[taskId]) {
      setTaskResults((current) => {
        const next = { ...current };
        delete next[taskId];
        return next;
      });
      return;
    }
    setLoadingResultId(taskId);
    try {
      const res = await tasksApi.getResult(taskId);
      setTaskResults((current) => ({ ...current, [taskId]: res || {} }));
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "加载任务结果失败");
    } finally {
      setLoadingResultId(null);
    }
  }, [taskResults, toast]);

  const loadRunDetail = useCallback(async (runId: string) => {
    const cached = runDetails[runId];
    if (cached) return cached;
    const res = await projectApi.getRun(runId);
    setRunDetails((current) => ({ ...current, [runId]: res.item }));
    return res.item;
  }, [runDetails]);

  const resolveTaskWorkspace = useCallback(async (task: TaskStatus) => {
    const taskMetadata = task.metadata || {};
    const metadataWorkspace = String(taskMetadata.workspace_path || "").trim();
    const metadataRunDirectory = String(taskMetadata.run_directory || "").trim();
    const metadataServerId = normalizeServerId(
      typeof taskMetadata.workspace_server_id === "string" ? taskMetadata.workspace_server_id : undefined,
    );
    const metadataRoots = [metadataRunDirectory, metadataWorkspace].filter(Boolean);
    if (metadataRoots.length > 0) {
      return {
        workspacePath: metadataRunDirectory || metadataWorkspace,
        roots: metadataRoots,
        serverId: metadataServerId,
      };
    }
    if (!task.run_id) {
      return null;
    }
    const run = await loadRunDetail(task.run_id);
    const roots = [
      run.run_directory,
      run.workspace_path,
      run.remote_workdir,
      run.workdir,
    ].map((item) => String(item || "").trim()).filter(Boolean);
    return {
      workspacePath: roots[0] || "",
      roots,
      serverId: normalizeServerId(run.workspace_server_id),
    };
  }, [loadRunDetail]);

  const handleRevealPath = useCallback(async (task: TaskStatus, path: string) => {
    const targetPath = String(path || "").trim();
    if (!targetPath) return;
    setRevealingPath(targetPath);
    try {
      const workspace = await resolveTaskWorkspace(task);
      await assistantWorkspaceApi.reveal(targetPath, workspace?.serverId || "local");
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "打开路径失败");
    } finally {
      setRevealingPath(null);
    }
  }, [resolveTaskWorkspace, toast]);

  const handlePreviewArtifact = useCallback(async (task: TaskStatus, artifact: ProjectArtifactRef) => {
    const artifactPath = artifact.path || artifact.relative_path || "";
    if (!isPreviewableArtifact(artifactPath)) {
      return;
    }
    setPreviewLoadingPath(artifactPath);
    try {
      const workspace = await resolveTaskWorkspace(task);
      if (!workspace?.workspacePath) {
        throw new Error("当前任务未记录可预览的工作区路径");
      }
      const readCandidates = buildArtifactReadCandidates(workspace.roots || [workspace.workspacePath], artifact);
      if (!readCandidates.length) {
        throw new Error("当前产物缺少可用路径，暂时无法预览");
      }
      let result: Awaited<ReturnType<typeof assistantWorkspaceApi.readFile>> | null = null;
      let previewError: unknown = null;
      for (const candidate of readCandidates) {
        try {
          result = await assistantWorkspaceApi.readFile(
            candidate.workspacePath,
            candidate.relativePath,
            120000,
            workspace.serverId,
          );
          break;
        } catch (error) {
          previewError = error;
        }
      }
      if (!result) {
        throw previewError instanceof Error ? previewError : new Error("当前产物暂时无法预览");
      }
      setArtifactPreview({
        title: artifact.relative_path || fileNameFromPath(artifact.path),
        path: artifact.path,
        serverId: workspace.serverId,
        content: result.content,
        truncated: result.truncated,
        markdown: isMarkdownArtifact(artifactPath),
      });
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "预览产物失败");
    } finally {
      setPreviewLoadingPath(null);
    }
  }, [resolveTaskWorkspace, toast]);

  return (
    <div className="animate-fade-in space-y-7">
      <div className="page-hero flex flex-col gap-5 rounded-[34px] p-6 lg:flex-row lg:items-center lg:justify-between lg:p-7">
        <div className="flex items-center gap-3">
          <div className="glass-segment flex h-12 w-12 items-center justify-center rounded-[20px]">
            <ListTodo className="h-5 w-5 text-primary" />
          </div>
          <div>
            <h1 className="text-2xl font-bold tracking-[-0.045em] text-ink">任务后台</h1>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" size="sm" icon={<RefreshCw className="h-3.5 w-3.5" />} onClick={() => void loadTasks()}>
            刷新
          </Button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <div className="metric-tile rounded-[30px] p-5">
          <div className="flex items-center gap-2 text-ink-secondary">
            <Activity className="h-4 w-4 text-primary" />
            <span className="text-sm font-medium">运行中</span>
          </div>
          <p className="mt-3 text-3xl font-bold text-ink">{counts.running}</p>
        </div>
        <div className="metric-tile rounded-[30px] p-5">
          <div className="flex items-center gap-2 text-ink-secondary">
            <CheckCircle2 className="h-4 w-4 text-success" />
            <span className="text-sm font-medium">已完成</span>
          </div>
          <p className="mt-3 text-3xl font-bold text-ink">{counts.completed}</p>
        </div>
        <div className="metric-tile rounded-[30px] p-5">
          <div className="flex items-center gap-2 text-ink-secondary">
            <XCircle className="h-4 w-4 text-error" />
            <span className="text-sm font-medium">失败</span>
          </div>
          <p className="mt-3 text-3xl font-bold text-ink">{counts.failed}</p>
        </div>
      </div>

      <TokenOverviewStrip categories={tokenCategories} totalTokens={totalWindowTokens} metrics={costMetrics} />

      <div className="glass-segment flex flex-wrap gap-1.5 rounded-[26px] p-1.5">
        {FILTERS.map((item) => (
          <button
            key={item.key}
            onClick={() => setFilter(item.key)}
            className={`flex min-w-[96px] flex-1 items-center justify-center gap-1.5 rounded-[18px] py-2.5 text-xs font-medium transition-all ${
              filter === item.key
                ? "border border-border bg-page text-ink"
                : "text-ink-secondary hover:bg-surface/72 hover:text-ink"
            }`}
          >
            {item.label}
            <span className="rounded-full border border-border/70 bg-page/82 px-1.5 py-0.5 text-[10px] text-ink-tertiary">
              {counts[item.key]}
            </span>
          </button>
        ))}
      </div>

      {loading ? (
        <Spinner text="加载任务列表..." />
      ) : filteredTasks.length === 0 ? (
        <Empty
          icon={<Clock3 className="h-14 w-14" />}
          title="暂无任务记录"
        />
      ) : (
        <div className="space-y-3">
          {filteredTasks.map((task) => {
            const tokenUsage = usableTaskTokenUsage(task);
            const tokenSourceLabel = tokenUsage.source === "prompt_trace"
              ? "Trace 匹配"
              : tokenUsage.source === "metadata"
                ? "任务记录"
                : "暂无记录";
            return (
            <section key={task.task_id} className="glass-card glass-card-soft rounded-[30px] p-5">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge task={task} />
                    <Badge variant="default">{task.task_type}</Badge>
                    <span className="text-xs text-ink-tertiary">{task.task_id}</span>
                  </div>
                  <h2 className="mt-3 text-base font-semibold text-ink">{task.title}</h2>
                  <p className="mt-1 text-sm text-ink-secondary">
                    {task.message || (task.finished ? (task.success ? "任务已完成" : "任务执行失败") : "任务正在运行")}
                  </p>
                  <TokenUsageLine usage={tokenUsage} sourceLabel={tokenSourceLabel} />
                  <div className="mt-3 flex flex-wrap gap-2">
                    {!task.finished ? (
                      <Button
                        size="sm"
                        variant="danger"
                        icon={<StopCircle className="h-3.5 w-3.5" />}
                        onClick={() => void handleCancel(task.task_id)}
                        loading={cancellingId === task.task_id}
                      >
                        终止任务
                      </Button>
                    ) : null}
                    {task.retry_supported ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        icon={<RefreshCw className="h-3.5 w-3.5" />}
                        onClick={() => void handleRetry(task.task_id)}
                        loading={retryingId === task.task_id}
                      >
                        {task.retry_label || "重试"}
                      </Button>
                    ) : null}
                    {task.project_id ? (
                      <Button size="sm" variant="secondary" onClick={() => navigate(`/projects/${task.project_id}`)}>
                        打开项目
                      </Button>
                    ) : null}
                    {task.paper_id ? (
                      <Button size="sm" variant="secondary" onClick={() => navigate(`/papers/${task.paper_id}`)}>
                        打开论文
                      </Button>
                    ) : null}
                    {(task.log_count || taskLogs[task.task_id]) ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => void handleLoadLogs(task.task_id)}
                        loading={loadingLogsId === task.task_id}
                      >
                        {taskLogs[task.task_id] ? "收起日志" : "查看日志"}
                      </Button>
                    ) : null}
                    {task.has_result ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => void handleLoadResult(task.task_id)}
                        loading={loadingResultId === task.task_id}
                      >
                        {taskResults[task.task_id] ? "收起结果" : "查看结果"}
                      </Button>
                    ) : null}
                  </div>
                  {task.error && (
                    <div className="mt-3 rounded-xl border border-error/20 bg-error/5 px-3 py-2 text-sm text-error">
                      {task.error}
                    </div>
                  )}
                  {task.artifact_refs && task.artifact_refs.length > 0 ? (
                    <div className="mt-3 rounded-xl bg-surface/60 px-3 py-3">
                      <div className="flex items-center justify-between gap-3">
                        <div className="text-xs font-medium text-ink">关联产物</div>
                        <div className="text-[11px] text-ink-tertiary">{task.artifact_refs.length} 个</div>
                      </div>
                      <div className="mt-2 space-y-2">
                        {task.artifact_refs.slice(0, 4).map((artifact, index) => {
                          const relative = typeof artifact.relative_path === "string" ? artifact.relative_path : "";
                          const absolute = typeof artifact.path === "string" ? artifact.path : "";
                          const artifactPath = absolute || relative;
                          const previewable = isPreviewableArtifact(artifactPath);
                          return (
                            <div key={`${artifactPath}-${index}`} className="rounded-lg border border-border/70 bg-surface/70 px-3 py-2">
                              <div className="break-all text-xs font-medium text-ink">{relative || absolute}</div>
                              {absolute ? (
                                <div className="mt-1 break-all text-[11px] text-ink-tertiary">{absolute}</div>
                              ) : null}
                              <div className="mt-2 flex flex-wrap gap-2">
                                {previewable ? (
                                  <Button
                                    size="sm"
                                    variant="secondary"
                                    icon={<Eye className="h-3.5 w-3.5" />}
                                    onClick={() => void handlePreviewArtifact(task, artifact as unknown as ProjectArtifactRef)}
                                    loading={previewLoadingPath === artifactPath}
                                  >
                                    预览
                                  </Button>
                                ) : null}
                                {absolute ? (
                                  <Button
                                    size="sm"
                                    variant="secondary"
                                    icon={<FolderOpen className="h-3.5 w-3.5" />}
                                    onClick={() => void handleRevealPath(task, absolute)}
                                    loading={revealingPath === absolute}
                                  >
                                    定位
                                  </Button>
                                ) : null}
                              </div>
                            </div>
                          );
                        })}
                        {task.artifact_refs.length > 4 ? (
                          <div className="text-[11px] text-ink-tertiary">其余 {task.artifact_refs.length - 4} 个产物</div>
                        ) : null}
                      </div>
                    </div>
                  ) : null}
                  {task.log_path ? (
                    <div className="mt-3 rounded-xl bg-surface/60 px-3 py-3">
                      <div className="text-xs font-medium text-ink">日志文件</div>
                      <div className="mt-1 break-all text-[11px] text-ink-tertiary">{task.log_path}</div>
                      <div className="mt-2">
                        <Button
                          size="sm"
                          variant="secondary"
                          icon={<FileText className="h-3.5 w-3.5" />}
                          onClick={() => void handleRevealPath(task, task.log_path as string)}
                          loading={revealingPath === task.log_path}
                        >
                          定位日志
                        </Button>
                      </div>
                    </div>
                  ) : null}
                  {taskResults[task.task_id] ? (
                    <pre className="theme-console-block theme-console-fg mt-3 max-h-56 overflow-auto rounded-xl px-3 py-3 text-xs leading-6">
                      {JSON.stringify(taskResults[task.task_id], null, 2)}
                    </pre>
                  ) : null}
                  {taskLogs[task.task_id] && taskLogs[task.task_id].length > 0 ? (
                    <pre className="theme-console-block theme-console-fg mt-3 max-h-56 overflow-auto rounded-xl px-3 py-3 text-xs leading-6">
                      {taskLogs[task.task_id].map((item) => item.message).join("\n")}
                    </pre>
                  ) : null}
                </div>

                <div className="glass-segment w-full max-w-xs rounded-[24px] p-4">
                  <div className="flex items-center justify-between text-xs text-ink-secondary">
                    <span>进度</span>
                    <span>{task.progress_pct}%</span>
                  </div>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-border-light">
                    <div
                      className={`h-full rounded-full transition-all duration-300 ${
                        task.finished
                          ? task.success
                            ? "bg-success"
                            : "bg-error"
                          : "bg-gradient-to-r from-primary to-info"
                      }`}
                      style={{ width: `${Math.max(2, task.progress_pct)}%` }}
                    />
                  </div>
                  <div className="mt-3 flex items-center justify-between text-[11px] text-ink-tertiary">
                    <span>{task.current}/{task.total}</span>
                    <span>{task.finished ? "总耗时" : "运行时长"}</span>
                  </div>
                  <div className="mt-1 flex items-center justify-between text-xs text-ink-secondary">
                    <span>{formatElapsedSeconds(task.elapsed_seconds)}</span>
                    <span>{timeAgo(new Date(task.updated_at * 1000).toISOString())}</span>
                  </div>
                </div>
              </div>
            </section>
            );
          })}
        </div>
      )}

      <ArtifactPreviewModal preview={artifactPreview} onClose={() => setArtifactPreview(null)} />
    </div>
  );
}
