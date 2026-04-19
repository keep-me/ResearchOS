/**
 * 全局任务进度条 — 固定在页面底部
 * @author Color2333
 */
import { useGlobalTasks, type ActiveTask } from "@/contexts/GlobalTaskContext";
import { Loader2, CheckCircle2, XCircle, ChevronUp, ChevronDown } from "lucide-react";
import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { useLocation, useNavigate } from "react-router-dom";

function TaskItem({ task }: { task: ActiveTask }) {
  const pct = task.progress_pct;
  return (
    <div className="flex items-center gap-3 px-4 py-2 text-xs">
      {task.finished ? (
        task.success ? (
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" />
        ) : (
          <XCircle className="h-3.5 w-3.5 shrink-0 text-error" />
        )
      ) : (
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between">
          <span className="truncate font-medium text-ink">{task.title}</span>
          <span className="ml-2 shrink-0 text-[10px] text-ink-tertiary">
            {task.total > 0 ? `${task.current}/${task.total}` : ""}
            {task.finished ? "" : ` · ${task.elapsed_seconds}s`}
          </span>
        </div>
        {task.message && (
          <p className="truncate text-[10px] text-ink-tertiary">{task.message}</p>
        )}
        {!task.finished && task.total > 0 && (
          <div className="mt-1 h-1 overflow-hidden rounded-full bg-border">
            <div
              className="h-full rounded-full bg-primary transition-all duration-500 ease-out"
              style={{ width: `${pct}%` }}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export default function GlobalTaskBar() {
  const { tasks, hasRunning } = useGlobalTasks();
  const [expanded, setExpanded] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  const hiddenOnRoute = location.pathname.startsWith("/tasks");
  const running = useMemo(() => tasks.filter((t) => !t.finished), [tasks]);
  const recent = useMemo(() => tasks.filter((t) => t.finished).slice(0, 3), [tasks]);
  const leadTask = running[0] || recent[0] || null;
  const displayTasks = expanded ? [...running, ...recent].slice(0, 6) : [];

  if (tasks.length === 0 || hiddenOnRoute) return null;
  if (running.length === 0 && !expanded) return null;

  return (
    <div className={cn("pointer-events-none fixed bottom-4 left-4 right-4 z-30 transition-all duration-200", "lg:left-auto lg:right-6 lg:w-[380px]")}>
      <div className="pointer-events-auto overflow-hidden rounded-[24px] border border-border/80 bg-white/96 shadow-[0_18px_40px_-26px_rgba(15,23,35,0.24)] backdrop-blur-xl">
        <div className={cn("flex items-center justify-between gap-3 px-4 py-3", expanded && "border-b border-border/70")}>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-xs font-semibold text-primary">
              {hasRunning ? <Loader2 className="h-4 w-4 animate-spin text-primary" /> : null}
              <span>{running.length > 0 ? `${running.length} 个任务进行中` : "最近任务"}</span>
            </div>
            {leadTask ? (
              <div className="mt-1 truncate text-[11px] text-ink-tertiary">
                {leadTask.title}
                {leadTask.message ? ` · ${leadTask.message}` : ""}
              </div>
            ) : null}
          </div>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              className="rounded-lg px-2 py-1 text-[10px] text-ink-tertiary transition hover:bg-page hover:text-ink"
              onClick={() => navigate("/tasks")}
            >
              任务中心
            </button>
            <button
              type="button"
              onClick={() => setExpanded(!expanded)}
              className="rounded-lg p-1.5 text-primary transition hover:bg-page"
              aria-label={expanded ? "收起任务条" : "展开任务条"}
            >
              {expanded ? <ChevronDown className="h-4 w-4 text-primary" /> : <ChevronUp className="h-4 w-4 text-primary" />}
            </button>
          </div>
        </div>
        {displayTasks.length > 0 ? (
          <div className="max-h-72 divide-y divide-border-light overflow-y-auto bg-white">
            {displayTasks.map((t) => (
              <TaskItem key={t.task_id} task={t} />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
