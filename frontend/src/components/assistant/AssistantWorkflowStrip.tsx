import { ExternalLink, Loader2, Play, RefreshCw, X } from "lucide-react";
import { Badge, Button } from "@/components/ui";
import type { ProjectRun } from "@/types";

function runStatusLabel(status: string | null | undefined, activePhase?: string | null) {
  switch (status) {
    case "queued":
      return "排队中";
    case "paused":
      return activePhase === "awaiting_checkpoint" ? "等待确认" : "已暂停";
    case "running":
      return activePhase?.trim() ? `运行中 · ${activePhase}` : "运行中";
    case "succeeded":
      return "已完成";
    case "failed":
      return "失败";
    case "cancelled":
      return "已取消";
    default:
      return "同步中";
  }
}

function statusVariant(status: string | null | undefined): "default" | "success" | "warning" | "error" | "info" {
  switch (status) {
    case "succeeded":
      return "success";
    case "running":
      return "info";
    case "queued":
    case "paused":
      return "warning";
    case "failed":
      return "error";
    default:
      return "default";
  }
}

interface AssistantWorkflowStripProps {
  run?: ProjectRun | null;
  loading?: boolean;
  error?: string | null;
  onOpenConfig: () => void;
  onOpenDetail: () => void;
  onRefresh: () => void;
  onDismiss: () => void;
}

export default function AssistantWorkflowStrip({
  run,
  loading = false,
  error,
  onOpenConfig,
  onOpenDetail,
  onRefresh,
  onDismiss,
}: AssistantWorkflowStripProps) {
  if (!run && !loading && !error) return null;

  return (
    <div className="mx-auto mt-3 max-w-[1040px]">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-[24px] border border-border/60 bg-surface/92 px-4 py-3 shadow-[0_22px_48px_-40px_rgba(15,23,35,0.26)]">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center rounded-full border border-border/70 bg-page/72 px-2.5 py-1 text-[11px] font-medium text-ink-secondary">
              流程
            </span>
            {run ? <Badge variant={statusVariant(run.status)}>{runStatusLabel(run.status, run.active_phase)}</Badge> : null}
            {run?.workflow_label ? <Badge>{run.workflow_label}</Badge> : null}
            {error ? <Badge variant="error">{error}</Badge> : null}
          </div>
          <div className="mt-2 truncate text-sm font-semibold text-ink">
            {run?.title?.trim() || run?.workflow_label || "同步流程状态..."}
          </div>
          {run?.active_phase ? (
            <div className="mt-1 text-[11px] text-ink-secondary">当前阶段：{run.active_phase}</div>
          ) : null}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {run ? (
            <>
              <Button
                type="button"
                size="sm"
                variant="secondary"
                icon={<ExternalLink className="h-3.5 w-3.5" />}
                onClick={onOpenDetail}
              >
                打开运行
              </Button>
              <button
                type="button"
                onClick={onRefresh}
                className="inline-flex h-9 w-9 items-center justify-center rounded-2xl border border-border/70 bg-surface text-ink-secondary transition hover:border-primary/20 hover:text-primary"
                aria-label="刷新流程状态"
              >
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              </button>
              <button
                type="button"
                onClick={onDismiss}
                className="inline-flex h-9 w-9 items-center justify-center rounded-2xl border border-border/70 bg-surface text-ink-tertiary transition hover:text-ink"
                aria-label="关闭流程状态"
              >
                <X className="h-4 w-4" />
              </button>
            </>
          ) : null}
          <Button
            type="button"
            size="sm"
            icon={<Play className="h-3.5 w-3.5" />}
            onClick={onOpenConfig}
          >
            {run ? "新建流程" : "开始流程"}
          </Button>
        </div>
      </div>
    </div>
  );
}
