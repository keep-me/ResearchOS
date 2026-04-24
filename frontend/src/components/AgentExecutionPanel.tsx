import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  FolderOpen,
  Loader2,
  Play,
  Save,
  ShieldCheck,
  Sparkles,
  Trash2,
} from "@/lib/lucide";
import { cn } from "@/lib/utils";
import { useToast } from "@/contexts/ToastContext";
import { useDesktopRuntime } from "@/contexts/DesktopRuntimeContext";
import { getErrorMessage } from "@/lib/errorHandler";
import { openFolderDialog } from "@/lib/tauri";
import { assistantExecPolicyApi, workspaceRootApi } from "@/services/api";
import type { AssistantExecPolicy, WorkspaceRootItem } from "@/types";

type RunMode = "coding" | "experiment" | "benchmark" | "paper_ops";
type ReviewMode = "human_checkpoint" | "semi_auto" | "auto_loop";

interface AgentExecutionBrief {
  goal: string;
  workspacePath: string;
  runMode: RunMode;
  reviewMode: ReviewMode;
  outputs: string;
  acceptance: string;
  notes: string;
}

interface AgentExecutionPanelProps {
  compact?: boolean;
  disabled?: boolean;
  preferredWorkspacePath?: string;
  preferredWorkspaceTitle?: string;
  policySeed?: Partial<AssistantExecPolicy> | null;
  onWorkspacePathChange?: (path: string, title?: string) => void;
  onFillPrompt: (prompt: string) => void;
  onSendPrompt: (prompt: string) => Promise<void> | void;
}

const CONFIG_KEY = "researchos.openclaw.config";

const DEFAULT_BRIEF: AgentExecutionBrief = {
  goal: "",
  workspacePath: "D:\\Research\\openclaw-workspace",
  runMode: "experiment",
  reviewMode: "human_checkpoint",
  outputs: "代码改动说明、实验日志、核心结果摘要、下一步建议",
  acceptance: "关键命令可复现，结果记录清楚，失败原因可追踪",
  notes: "",
};

const DEFAULT_POLICY: AssistantExecPolicy = {
  workspace_access: "read",
  command_execution: "allowlist",
  approval_mode: "on_request",
  allowed_command_prefixes: [
    "python",
    "python -m",
    "py",
    "pip",
    "pytest",
    "uv",
    "uv run",
    "node",
    "npm",
    "pnpm",
    "yarn",
    "git status",
    "git diff",
    "git log",
    "git rev-parse",
    "Get-ChildItem",
    "dir",
  ],
};

const MODE_OPTIONS: { value: RunMode; label: string }[] = [
  { value: "experiment", label: "实验推进" },
  { value: "coding", label: "代码开发" },
  { value: "benchmark", label: "基准测试" },
  { value: "paper_ops", label: "论文支持" },
];

const REVIEW_OPTIONS: { value: ReviewMode; label: string }[] = [
  { value: "human_checkpoint", label: "关键步骤确认" },
  { value: "semi_auto", label: "半自动推进" },
  { value: "auto_loop", label: "自动循环" },
];

const WORKSPACE_ACCESS_OPTIONS: { value: AssistantExecPolicy["workspace_access"]; label: string }[] = [
  { value: "none", label: "禁止访问" },
  { value: "read", label: "只读" },
  { value: "read_write", label: "读写" },
];

const COMMAND_OPTIONS: { value: AssistantExecPolicy["command_execution"]; label: string }[] = [
  { value: "deny", label: "禁止执行" },
  { value: "allowlist", label: "白名单" },
  { value: "full", label: "完全执行" },
];

const APPROVAL_OPTIONS: { value: AssistantExecPolicy["approval_mode"]; label: string }[] = [
  { value: "always", label: "总是确认" },
  { value: "on_request", label: "高风险确认" },
  { value: "off", label: "自动执行" },
];

const WORKSPACE_ACCESS_LABELS: Record<AssistantExecPolicy["workspace_access"], string> = {
  none: "禁止访问",
  read: "只读",
  read_write: "读写",
};

const COMMAND_LABELS: Record<AssistantExecPolicy["command_execution"], string> = {
  deny: "禁止执行",
  allowlist: "白名单",
  full: "完全执行",
};

const APPROVAL_LABELS: Record<AssistantExecPolicy["approval_mode"], string> = {
  always: "总是确认",
  on_request: "高风险确认",
  off: "自动执行",
};

export default function AgentExecutionPanel({
  compact = false,
  disabled = false,
  preferredWorkspacePath,
  preferredWorkspaceTitle,
  policySeed,
  onWorkspacePathChange,
  onFillPrompt,
  onSendPrompt,
}: AgentExecutionPanelProps) {
  const { toast } = useToast();
  const { isDesktop: tauriMode } = useDesktopRuntime();
  const [open, setOpen] = useState(!compact);
  const [brief, setBrief] = useState<AgentExecutionBrief>(() => readBriefConfig());
  const [roots, setRoots] = useState<WorkspaceRootItem[]>([]);
  const [rootsLoading, setRootsLoading] = useState(true);
  const [workspaceSaving, setWorkspaceSaving] = useState(false);
  const [policy, setPolicy] = useState<AssistantExecPolicy>(DEFAULT_POLICY);
  const [policyDraft, setPolicyDraft] = useState<AssistantExecPolicy>(DEFAULT_POLICY);
  const [policyLoading, setPolicyLoading] = useState(true);
  const [policySaving, setPolicySaving] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem(CONFIG_KEY, JSON.stringify(brief));
    }
  }, [brief]);

  useEffect(() => {
    if (!preferredWorkspacePath) return;
    if (preferredWorkspacePath === brief.workspacePath) return;
    setBrief((prev) => ({
      ...prev,
      workspacePath: preferredWorkspacePath,
      goal: prev.goal || (preferredWorkspaceTitle ? `在 ${preferredWorkspaceTitle} 中推进实验或代码任务` : prev.goal),
    }));
  }, [preferredWorkspacePath, preferredWorkspaceTitle, brief.workspacePath]);

  useEffect(() => {
    if (!policySeed) return;
    setPolicy((prev) => mergePolicy(prev, policySeed));
    setPolicyDraft((prev) => mergePolicy(prev, policySeed));
  }, [
    policySeed?.workspace_access,
    policySeed?.command_execution,
    policySeed?.approval_mode,
    JSON.stringify(policySeed?.allowed_command_prefixes || []),
  ]);

  useEffect(() => {
    void loadRoots();
    void loadPolicy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const workspaceAccess = useMemo(
    () => getWorkspaceAccess(brief.workspacePath, roots),
    [brief.workspacePath, roots],
  );
  const policyDirty = useMemo(
    () => JSON.stringify(policyDraft) !== JSON.stringify(policy),
    [policyDraft, policy],
  );
  const canLaunch = brief.goal.trim().length > 0 && brief.workspacePath.trim().length > 0;

  async function loadRoots() {
    setRootsLoading(true);
    try {
      const result = await workspaceRootApi.list();
      setRoots(result.items || []);
    } catch (error) {
      toast("error", `加载工作区权限失败：${getErrorMessage(error)}`);
    } finally {
      setRootsLoading(false);
    }
  }

  async function loadPolicy() {
    setPolicyLoading(true);
    try {
      const result = await assistantExecPolicyApi.get();
      setPolicy(result);
      setPolicyDraft(result);
    } catch (error) {
      toast("warning", `加载执行策略失败，先使用默认值：${getErrorMessage(error)}`);
      setPolicy(DEFAULT_POLICY);
      setPolicyDraft(DEFAULT_POLICY);
    } finally {
      setPolicyLoading(false);
    }
  }

  async function savePolicy(silent = false): Promise<AssistantExecPolicy | null> {
    setPolicySaving(true);
    try {
      const result = await assistantExecPolicyApi.update(policyDraft);
      setPolicy(result);
      setPolicyDraft(result);
      if (!silent) {
        toast("success", "研究助手执行策略已更新");
      }
      return result;
    } catch (error) {
      toast("error", `保存执行策略失败：${getErrorMessage(error)}`);
      return null;
    } finally {
      setPolicySaving(false);
    }
  }

  async function ensurePolicySaved(): Promise<AssistantExecPolicy | null> {
    if (!policyDirty) return policyDraft;
    return savePolicy(true);
  }

  async function handlePickWorkspace() {
    if (!tauriMode) return;
    const folder = await openFolderDialog("选择本地工作区目录");
    if (!folder) return;
    setBrief((prev) => ({ ...prev, workspacePath: folder }));
    onWorkspacePathChange?.(folder, deriveProjectName(folder));
  }

  async function handleAuthorizeWorkspace() {
    const path = brief.workspacePath.trim();
    if (!path) {
      toast("warning", "请先填写工作区路径");
      return;
    }
    setWorkspaceSaving(true);
    try {
      const result = await workspaceRootApi.create(path);
      setRoots(result.items || []);
      toast("success", "工作区目录已加入允许访问列表");
    } catch (error) {
      toast("error", getErrorMessage(error));
    } finally {
      setWorkspaceSaving(false);
    }
  }

  async function handleRemoveRoot(path: string) {
    setWorkspaceSaving(true);
    try {
      const result = await workspaceRootApi.delete(path);
      setRoots(result.items || []);
      toast("success", "已移除工作区授权");
    } catch (error) {
      toast("error", getErrorMessage(error));
    } finally {
      setWorkspaceSaving(false);
    }
  }

  async function handleUsePrompt(mode: "fill" | "send") {
    if (!canLaunch || disabled) return;
    const nextPolicy = await ensurePolicySaved();
    if (!nextPolicy) return;
    const prompt = buildAgentPrompt(brief, nextPolicy);
    if (mode === "fill") {
      onFillPrompt(prompt);
      toast("success", "执行简报已填入输入框");
      return;
    }
    try {
      await onSendPrompt(prompt);
      toast("success", "执行简报已发送给研究助手");
      if (compact) {
        setOpen(false);
      }
    } catch (error) {
      toast("error", `发送执行简报失败：${getErrorMessage(error)}`);
    }
  }

  return (
    <div className="rounded-[26px] border border-border/75 bg-white/82 shadow-[0_28px_70px_-52px_rgba(15,23,35,0.34)] backdrop-blur-xl">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="flex w-full items-start justify-between gap-4 px-4 py-4 text-left"
      >
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-2 rounded-full border border-primary/15 bg-primary/8 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-primary">
              <Bot className="h-3.5 w-3.5" />
              执行控制
            </span>
            <span className="inline-flex items-center gap-2 rounded-full border border-border/75 bg-page/70 px-3 py-1 text-[11px] font-medium text-ink-secondary">
              工作区 {WORKSPACE_ACCESS_LABELS[policyDraft.workspace_access]}
            </span>
            <span className="inline-flex items-center gap-2 rounded-full border border-border/75 bg-page/70 px-3 py-1 text-[11px] font-medium text-ink-secondary">
              命令 {COMMAND_LABELS[policyDraft.command_execution]}
            </span>
            <span className="inline-flex items-center gap-2 rounded-full border border-border/75 bg-page/70 px-3 py-1 text-[11px] font-medium text-ink-secondary">
              审批 {APPROVAL_LABELS[policyDraft.approval_mode]}
            </span>
          </div>
        </div>
        <div className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-[14px] border border-border/75 bg-white/72 text-ink-secondary">
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </div>
      </button>

      {open && (
        <div className="border-t border-border/70 px-4 pb-4 pt-1">
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
            <section className="space-y-4 rounded-[22px] border border-border/70 bg-page/58 p-4">
              <Field label="研究目标">
                <textarea
                  value={brief.goal}
                  onChange={(event) => setBrief((prev) => ({ ...prev, goal: event.target.value }))}
                  placeholder="输入研究目标"
                  className="min-h-[112px] w-full rounded-[18px] border border-border/80 bg-white/82 px-4 py-3 text-sm leading-7 text-ink outline-none transition focus:border-primary/30"
                />
              </Field>

              <div className="grid gap-4 lg:grid-cols-2">
                <Field label="本地工作区">
                  <div className="space-y-2">
                    <div className="flex gap-2">
                    <div className="relative min-w-0 flex-1">
                        <FolderOpen className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-tertiary" />
                        <input
                          value={brief.workspacePath}
                          onChange={(event) => {
                            const nextPath = event.target.value;
                            setBrief((prev) => ({ ...prev, workspacePath: nextPath }));
                            onWorkspacePathChange?.(nextPath, deriveProjectName(nextPath));
                          }}
                          placeholder="输入工作区目录"
                          className="w-full rounded-[18px] border border-border/80 bg-white/82 py-3 pl-11 pr-4 text-sm text-ink outline-none transition focus:border-primary/30"
                        />
                      </div>
                      {tauriMode && (
                        <button
                          type="button"
                          onClick={() => void handlePickWorkspace()}
                          className="inline-flex shrink-0 items-center gap-2 rounded-[16px] border border-border/80 bg-white/82 px-3 py-2.5 text-xs font-semibold text-ink-secondary transition hover:border-primary/20 hover:text-primary"
                        >
                          <FolderOpen className="h-4 w-4" />
                          浏览
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => void handleAuthorizeWorkspace()}
                        disabled={workspaceSaving}
                        className="inline-flex shrink-0 items-center gap-2 rounded-[16px] border border-border/80 bg-white/82 px-3 py-2.5 text-xs font-semibold text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {workspaceSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
                        授权
                      </button>
                    </div>

                    <div
                      className={cn(
                        "rounded-[16px] border px-4 py-3 text-[12px] leading-6",
                        workspaceAccess.allowed
                          ? "border-success/15 bg-success/8 text-success"
                          : "border-warning/20 bg-warning-light text-warning",
                      )}
                    >
                      {workspaceAccess.allowed ? (
                        <span>
                          已授权
                          {workspaceAccess.rootPath ? ` · ${workspaceAccess.rootPath}` : ""}
                        </span>
                      ) : (
                        <span>未授权</span>
                      )}
                    </div>
                  </div>
                </Field>

                <Field label="验收标准">
                  <textarea
                    value={brief.acceptance}
                    onChange={(event) => setBrief((prev) => ({ ...prev, acceptance: event.target.value }))}
                    placeholder="输入验收标准"
                    className="min-h-[112px] w-full rounded-[18px] border border-border/80 bg-white/82 px-4 py-3 text-sm leading-7 text-ink outline-none transition focus:border-primary/30"
                  />
                </Field>
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <Field label="执行模式">
                  <div className="grid gap-2 sm:grid-cols-2">
                    {MODE_OPTIONS.map((option) => (
                      <ChoiceChip
                        key={option.value}
                        selected={brief.runMode === option.value}
                        title={option.label}
                        onClick={() => setBrief((prev) => ({ ...prev, runMode: option.value }))}
                      />
                    ))}
                  </div>
                </Field>

                <Field label="审阅方式">
                  <div className="grid gap-2 sm:grid-cols-2">
                    {REVIEW_OPTIONS.map((option) => (
                      <ChoiceChip
                        key={option.value}
                        selected={brief.reviewMode === option.value}
                        title={option.label}
                        onClick={() => setBrief((prev) => ({ ...prev, reviewMode: option.value }))}
                      />
                    ))}
                  </div>
                </Field>
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <Field label="预期产出">
                  <textarea
                    value={brief.outputs}
                    onChange={(event) => setBrief((prev) => ({ ...prev, outputs: event.target.value }))}
                    placeholder="输入预期产出"
                    className="min-h-[112px] w-full rounded-[18px] border border-border/80 bg-white/82 px-4 py-3 text-sm leading-7 text-ink outline-none transition focus:border-primary/30"
                  />
                </Field>

                <Field label="补充说明">
                  <textarea
                    value={brief.notes}
                    onChange={(event) => setBrief((prev) => ({ ...prev, notes: event.target.value }))}
                    placeholder="输入补充说明"
                    className="min-h-[112px] w-full rounded-[18px] border border-border/80 bg-white/82 px-4 py-3 text-sm leading-7 text-ink outline-none transition focus:border-primary/30"
                  />
                </Field>
              </div>
            </section>

            <section className="space-y-4 rounded-[22px] border border-border/70 bg-page/58 p-4">
              <div className="rounded-[18px] border border-primary/12 bg-primary/6 px-4 py-3">
                <div className="flex items-center gap-2 text-sm font-semibold text-ink">
                  <Sparkles className="h-4 w-4 text-primary" />
                  执行权限
                </div>
              </div>

              <Field label="工作区权限">
                <ChoiceGrid
                  options={WORKSPACE_ACCESS_OPTIONS}
                  value={policyDraft.workspace_access}
                  onChange={(value) => setPolicyDraft((prev) => ({ ...prev, workspace_access: value }))}
                />
              </Field>

              <Field label="命令执行">
                <ChoiceGrid
                  options={COMMAND_OPTIONS}
                  value={policyDraft.command_execution}
                  onChange={(value) => setPolicyDraft((prev) => ({ ...prev, command_execution: value }))}
                />
              </Field>

              <Field label="审批模式">
                <ChoiceGrid
                  options={APPROVAL_OPTIONS}
                  value={policyDraft.approval_mode}
                  onChange={(value) => setPolicyDraft((prev) => ({ ...prev, approval_mode: value }))}
                />
              </Field>

              {policyDraft.command_execution === "allowlist" && (
                <Field label="允许命令前缀">
                  <textarea
                    value={policyDraft.allowed_command_prefixes.join("\n")}
                    onChange={(event) =>
                      setPolicyDraft((prev) => ({
                        ...prev,
                        allowed_command_prefixes: parsePrefixes(event.target.value),
                      }))
                    }
                    placeholder={"python\npython -m\npytest\nuv run"}
                    className="min-h-[140px] w-full rounded-[18px] border border-border/80 bg-white/82 px-4 py-3 font-mono text-[12px] leading-6 text-ink outline-none transition focus:border-primary/30"
                  />
                </Field>
              )}

              <div className="grid gap-2 sm:grid-cols-3">
                <button
                  type="button"
                  onClick={() => void savePolicy()}
                  disabled={policySaving || policyLoading}
                  className="inline-flex items-center justify-center gap-2 rounded-[16px] border border-border/80 bg-white/82 px-3 py-3 text-xs font-semibold text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {policySaving || policyLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                  保存权限
                </button>
                <button
                  type="button"
                  onClick={() => void handleUsePrompt("fill")}
                  disabled={!canLaunch || disabled || policySaving}
                  className="inline-flex items-center justify-center gap-2 rounded-[16px] border border-border/80 bg-white/82 px-3 py-3 text-xs font-semibold text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Bot className="h-4 w-4" />
                  填入输入框
                </button>
                <button
                  type="button"
                  onClick={() => void handleUsePrompt("send")}
                  disabled={!canLaunch || disabled || policySaving}
                  className="inline-flex items-center justify-center gap-2 rounded-[16px] bg-[linear-gradient(135deg,var(--color-primary),var(--color-primary-hover))] px-3 py-3 text-xs font-semibold text-white shadow-lg shadow-primary/18 transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-55 disabled:hover:translate-y-0"
                >
                  <Play className="h-4 w-4" />
                  直接发送
                </button>
              </div>

              <div className="rounded-[18px] border border-border/70 bg-white/82 px-4 py-4">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-ink">
                  <ShieldCheck className="h-4 w-4 text-primary" />
                  已授权工作区根目录
                </div>
                {rootsLoading ? (
                  <div className="flex items-center gap-2 text-xs text-ink-tertiary">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    正在加载工作区授权...
                  </div>
                ) : roots.length === 0 ? (
                  <p className="text-xs leading-6 text-ink-tertiary">暂无</p>
                ) : (
                  <div className="space-y-2">
                    {roots.slice(0, 4).map((root) => (
                      <div key={root.path} className="flex items-start justify-between gap-3 rounded-[14px] border border-border/70 bg-page/64 px-3 py-3">
                        <div className="min-w-0">
                          <p className="break-all text-xs font-semibold text-ink">{root.path}</p>
                          <p className="mt-1 text-[11px] text-ink-tertiary">
                            {root.source === "config" ? "内置" : "自定义"}
                            {root.exists ? "" : " · 缺失"}
                          </p>
                        </div>
                        {root.removable ? (
                          <button
                            type="button"
                            onClick={() => void handleRemoveRoot(root.path)}
                            disabled={workspaceSaving}
                            className="rounded-[12px] border border-border/80 bg-white/82 p-2 text-ink-tertiary transition hover:text-error disabled:cursor-not-allowed disabled:opacity-60"
                            aria-label="移除工作区授权"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        ) : null}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>
          </div>
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-2 block text-[11px] font-bold uppercase tracking-[0.16em] text-ink-tertiary">{label}</span>
      {children}
    </label>
  );
}

function ChoiceChip({
  selected,
  title,
  onClick,
}: {
  selected: boolean;
  title: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-[16px] border px-3 py-3 text-left text-xs font-semibold transition",
        selected
          ? "border-primary/20 bg-primary/8 text-primary"
          : "border-border/80 bg-white/82 text-ink-secondary hover:border-primary/20 hover:text-primary",
      )}
    >
      {title}
    </button>
  );
}

function ChoiceGrid<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="grid gap-2">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={cn(
            "rounded-[16px] border px-4 py-3 text-left transition",
            value === option.value
              ? "border-primary/20 bg-primary/8"
              : "border-border/80 bg-white/82 hover:border-primary/20 hover:bg-primary/6",
          )}
        >
          <div className="text-sm font-semibold text-ink">{option.label}</div>
        </button>
      ))}
    </div>
  );
}

function readBriefConfig(): AgentExecutionBrief {
  if (typeof window === "undefined") return DEFAULT_BRIEF;
  try {
    const raw = localStorage.getItem(CONFIG_KEY);
    if (!raw) return DEFAULT_BRIEF;
    return { ...DEFAULT_BRIEF, ...(JSON.parse(raw) as Partial<AgentExecutionBrief>) };
  } catch {
    return DEFAULT_BRIEF;
  }
}

function parsePrefixes(value: string): string[] {
  const values = value.replace(/\r/g, "\n").split("\n").flatMap((line) => line.split(","));
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of values) {
    const normalized = item.trim().replace(/\s+/g, " ");
    if (!normalized) continue;
    const key = normalized.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(normalized);
  }
  return result;
}

function mergePolicy(
  base: AssistantExecPolicy,
  patch: Partial<AssistantExecPolicy>,
): AssistantExecPolicy {
  return {
    ...base,
    ...patch,
    allowed_command_prefixes: patch.allowed_command_prefixes ?? base.allowed_command_prefixes,
  };
}

function normalizePathForCompare(value: string): string {
  return (value || "").replace(/\\/g, "/").replace(/\/+$/, "").trim().toLowerCase();
}

function getWorkspaceAccess(workspacePath: string, roots: WorkspaceRootItem[]) {
  const target = normalizePathForCompare(workspacePath);
  if (!target) {
    return { allowed: false, rootPath: "" };
  }
  for (const root of roots) {
    const normalizedRoot = normalizePathForCompare(root.path);
    if (!normalizedRoot) continue;
    if (target === normalizedRoot || target.startsWith(`${normalizedRoot}/`)) {
      return { allowed: true, rootPath: root.path };
    }
  }
  return { allowed: false, rootPath: "" };
}

function buildAgentPrompt(brief: AgentExecutionBrief, policy: AssistantExecPolicy): string {
  return [
    "你现在作为 ResearchOS 的实验执行助手，请基于下面的执行简报推进任务。",
    "",
    `研究目标：${brief.goal || "未填写"}`,
    `本地工作目录：${brief.workspacePath || "未填写"}`,
    `执行模式：${MODE_OPTIONS.find((item) => item.value === brief.runMode)?.label ?? brief.runMode}`,
    `审阅方式：${REVIEW_OPTIONS.find((item) => item.value === brief.reviewMode)?.label ?? brief.reviewMode}`,
    `工作区权限：${WORKSPACE_ACCESS_LABELS[policy.workspace_access]}`,
    `命令执行：${COMMAND_LABELS[policy.command_execution]}`,
    `审批模式：${APPROVAL_LABELS[policy.approval_mode]}`,
    `命令白名单：${policy.command_execution === "allowlist" ? (policy.allowed_command_prefixes.join(", ") || "无") : "不使用"}`,
    `预期产出：${brief.outputs || "未填写"}`,
    `验收标准：${brief.acceptance || "未填写"}`,
    `补充说明：${brief.notes || "无"}`,
    "",
    "请按这个顺序推进：",
    "1. 先结合论文知识库，提炼与当前实验相关的论文、关键设定和评测指标。",
    "2. 再检查本地工作区目录结构、关键文件和已有日志，不要直接盲目改代码。",
    "3. 动手前先给出计划，明确要读哪些文件、改哪些文件、跑哪些命令。",
    "4. 每轮执行后回报结果、失败原因、下一步建议，以及是否还需要我确认。",
    "5. 如果权限策略或工作区授权不足，请明确指出该去哪里调整，而不是继续失败重试。",
  ].join("\n");
}

function deriveProjectName(path: string) {
  const normalized = (path || "").replace(/\\/g, "/").replace(/\/+$/, "");
  if (!normalized) return "未命名项目";
  const parts = normalized.split("/");
  return parts[parts.length - 1] || normalized;
}
