import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRight, FolderPlus, Loader2, Play, Sparkles } from "lucide-react";
import { Badge, Button, Input, Textarea } from "@/components/ui";
import { useToast } from "@/contexts/ToastContext";
import { cn } from "@/lib/utils";
import { assistantWorkspaceApi, llmConfigApi, projectApi } from "@/services/api";
import type {
  AssistantWorkspaceServer,
  LLMProviderConfig,
  Project,
  ProjectDeploymentTarget,
  ProjectEngineProfile,
  ProjectRun,
  ProjectWorkflowPreset,
  ProjectWorkflowType,
  ProjectWorkspaceContext,
} from "@/types";

const LAST_PROJECT_KEY = "researchos.assistant.workflowLauncher.projectId";
const LAST_WORKFLOW_KEY = "researchos.assistant.workflowLauncher.workflow";
const PRIMARY_PROJECT_WORKFLOW_TYPES: ProjectWorkflowType[] = [
  "idea_discovery",
  "run_experiment",
  "auto_review_loop",
  "paper_writing",
  "rebuttal",
  "full_pipeline",
];
const PRIMARY_PROJECT_WORKFLOW_LABELS: Record<ProjectWorkflowType, string> = {
  init_repo: "初始化仓库",
  autoresearch_claude_code: "自动研究（Claude Code）",
  literature_review: "文献综述",
  idea_discovery: "想法发现",
  novelty_check: "查新评估",
  research_review: "研究评审",
  run_experiment: "实验桥接",
  auto_review_loop: "自动评审循环",
  paper_plan: "论文规划",
  paper_figure: "图表规划",
  paper_write: "论文成稿",
  paper_compile: "编译稿件",
  paper_writing: "论文写作",
  rebuttal: "答辩回复",
  paper_improvement: "论文改进",
  full_pipeline: "科研流程",
  monitor_experiment: "监控实验",
  sync_workspace: "同步工作区",
  custom_run: "自定义运行",
};

function normalizeServerId(value: string | null | undefined) {
  return (value || "").trim() || "local";
}

function trimToUndefined(value: string) {
  const next = value.trim();
  return next || undefined;
}

function projectWorkspacePath(project: Pick<Project, "workspace_path" | "remote_workdir" | "workdir"> | null | undefined): string {
  return (project?.workspace_path || project?.remote_workdir || project?.workdir || "").trim();
}

function targetWorkspacePath(target: Pick<ProjectDeploymentTarget, "workspace_path" | "remote_workdir" | "workdir"> | null | undefined): string {
  return (target?.workspace_path || target?.remote_workdir || target?.workdir || "").trim();
}

function workflowLabel(preset: ProjectWorkflowPreset | null | undefined) {
  const workflowType = preset?.workflow_type;
  if (workflowType) {
    return PRIMARY_PROJECT_WORKFLOW_LABELS[workflowType] || (preset?.label || workflowType).trim();
  }
  return (preset?.label || preset?.workflow_type || "").trim();
}

function workflowEntryCommand(preset: ProjectWorkflowPreset | null | undefined) {
  return (preset?.entry_command || "").trim();
}

function workflowSourceSkills(preset: ProjectWorkflowPreset | null | undefined) {
  return Array.isArray(preset?.source_skills)
    ? preset.source_skills.map((item) => item.trim()).filter(Boolean)
    : [];
}

function workflowTaskExample(preset: ProjectWorkflowPreset | null | undefined) {
  return (preset?.sample_prompt || "").trim();
}

function workflowExecutionCommandExample(preset: ProjectWorkflowPreset | null | undefined) {
  return (preset?.sample_execution_command || "").trim();
}

function workflowRebuttalReviewExample(preset: ProjectWorkflowPreset | null | undefined) {
  return (preset?.sample_rebuttal_review_bundle || "").trim();
}

function localizeTargetLabel(value: string | null | undefined) {
  return (value || "").trim() || "默认目标";
}

function inferExecutionCommand(value: string): string {
  const prompt = value.trim();
  if (!prompt) return "";
  if (prompt.toLowerCase().startsWith("command:")) {
    return prompt.slice("command:".length).trim();
  }
  const firstLine = prompt.split(/\r?\n/, 1)[0]?.trim() || "";
  if (firstLine.startsWith("!")) {
    return firstLine.slice(1).trim();
  }
  return "";
}

function sortAndFilterPrimaryProjectWorkflows(items: ProjectWorkflowPreset[]): ProjectWorkflowPreset[] {
  const orderMap = new Map<ProjectWorkflowType, number>(
    PRIMARY_PROJECT_WORKFLOW_TYPES.map((workflowType, index) => [workflowType, index]),
  );
  return items
    .filter((item) => orderMap.has(item.workflow_type))
    .map((item) => ({
      ...item,
      label: workflowLabel(item),
    }))
    .sort((a, b) => (orderMap.get(a.workflow_type) ?? 999) - (orderMap.get(b.workflow_type) ?? 999));
}

function workflowGuideItems(items: string[] | null | undefined) {
  return Array.isArray(items) ? items.map((item) => item.trim()).filter(Boolean) : [];
}

function WorkflowGuideList({ title, items }: { title: string; items: string[] | null | undefined }) {
  const visibleItems = workflowGuideItems(items);
  if (!visibleItems.length) return null;
  return (
    <div className="rounded-2xl border border-border/60 bg-page/72 px-3.5 py-3">
      <div className="text-xs font-semibold uppercase tracking-[0.14em] text-ink-tertiary">{title}</div>
      <div className="mt-2 space-y-2">
        {visibleItems.map((item) => (
          <div key={item} className="flex gap-2 text-sm leading-6 text-ink-secondary">
            <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary/45" />
            <span>{item}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function WorkflowStageGuide({ preset }: { preset: ProjectWorkflowPreset | null }) {
  const stages = preset?.stages || [];
  if (!stages.length) return null;
  return (
    <div className="rounded-2xl border border-border/60 bg-page/72 px-3.5 py-3 lg:col-span-2">
      <div className="text-xs font-semibold uppercase tracking-[0.14em] text-ink-tertiary">阶段说明</div>
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        {stages.map((stage, index) => (
          <div key={stage.id} className="rounded-2xl border border-border/55 bg-surface px-3 py-2.5">
            <div className="flex items-center gap-2">
              <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">
                {index + 1}
              </span>
              <span className="text-sm font-medium text-ink">{stage.label}</span>
            </div>
            <div className="mt-2 text-xs leading-5 text-ink-secondary">{stage.description}</div>
            {stage.deliverable ? (
              <div className="mt-2 rounded-full bg-page px-2.5 py-1 text-[11px] text-ink-tertiary">
                产出：{stage.deliverable}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

type ModelOption = {
  value: string;
  label: string;
  model: string;
};

export interface WorkflowLaunchResult {
  projectId: string;
  runId: string;
  run: ProjectRun;
}

interface ResearchWorkflowLauncherProps {
  initialProjectId?: string | null;
  workspacePath?: string | null;
  workspaceTitle?: string | null;
  workspaceServerId?: string | null;
  initialPaperIds?: string[];
  className?: string;
  compact?: boolean;
  surface?: "card" | "drawer";
  onLaunch?: (result: WorkflowLaunchResult) => void;
}

export default function ResearchWorkflowLauncher({
  initialProjectId,
  workspacePath,
  workspaceTitle,
  workspaceServerId,
  initialPaperIds = [],
  className,
  compact = false,
  surface = "card",
  onLaunch,
}: ResearchWorkflowLauncherProps) {
  const { toast } = useToast();
  const [bootstrapping, setBootstrapping] = useState(true);
  const [contextLoading, setContextLoading] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [creatingProject, setCreatingProject] = useState(false);
  const [showCreateProject, setShowCreateProject] = useState(false);

  const [projects, setProjects] = useState<Project[]>([]);
  const [workflowPresets, setWorkflowPresets] = useState<ProjectWorkflowPreset[]>([]);
  const [engineProfiles, setEngineProfiles] = useState<ProjectEngineProfile[]>([]);
  const [defaultExecutorEngineId, setDefaultExecutorEngineId] = useState("");
  const [defaultReviewerEngineId, setDefaultReviewerEngineId] = useState("");
  const [workspaceServers, setWorkspaceServers] = useState<AssistantWorkspaceServer[]>([]);
  const [llmConfigs, setLlmConfigs] = useState<LLMProviderConfig[]>([]);

  const [projectContext, setProjectContext] = useState<ProjectWorkspaceContext | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [selectedTargetId, setSelectedTargetId] = useState("");
  const [selectedWorkflowType, setSelectedWorkflowType] = useState<ProjectWorkflowType>("idea_discovery");
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [executionCommand, setExecutionCommand] = useState("");
  const [rebuttalReviewBundle, setRebuttalReviewBundle] = useState("");
  const [rebuttalVenue, setRebuttalVenue] = useState("ICML");
  const [rebuttalCharacterLimit, setRebuttalCharacterLimit] = useState("5000");
  const [rebuttalRound, setRebuttalRound] = useState("initial");
  const [rebuttalQuickMode, setRebuttalQuickMode] = useState(false);
  const [executorEngineId, setExecutorEngineId] = useState("");
  const [reviewerEngineId, setReviewerEngineId] = useState("");
  const [executorModel, setExecutorModel] = useState("");
  const [reviewerModel, setReviewerModel] = useState("");
  const [autoProceed, setAutoProceed] = useState(true);

  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectDescription, setNewProjectDescription] = useState("");
  const [newProjectServerId, setNewProjectServerId] = useState(normalizeServerId(workspaceServerId));
  const [newProjectPath, setNewProjectPath] = useState((workspacePath || "").trim());

  const selectedProject = useMemo(
    () => projects.find((item) => item.id === selectedProjectId) || null,
    [projects, selectedProjectId],
  );
  const selectedWorkflowPreset = useMemo(
    () => workflowPresets.find((item) => item.workflow_type === selectedWorkflowType) || workflowPresets[0] || null,
    [selectedWorkflowType, workflowPresets],
  );
  const selectedTarget = useMemo(
    () => projectContext?.targets.find((item) => item.id === selectedTargetId) || projectContext?.targets[0] || null,
    [projectContext?.targets, selectedTargetId],
  );
  const selectedWorkspacePath = targetWorkspacePath(selectedTarget) || projectWorkspacePath(projectContext?.project || selectedProject);
  const usingEngineProfiles = engineProfiles.length > 0;
  const llmModelOptions = useMemo<ModelOption[]>(() => {
    return llmConfigs.flatMap((config) => ([
      config.model_deep
        ? {
            value: config.model_deep,
            model: config.model_deep,
            label: `${config.name} · 深度 · ${config.model_deep}`,
          }
        : null,
      config.model_skim
        ? {
            value: config.model_skim,
            model: config.model_skim,
            label: `${config.name} · 轻量 · ${config.model_skim}`,
          }
        : null,
      config.model_fallback
        ? {
            value: config.model_fallback,
            model: config.model_fallback,
            label: `${config.name} · 兜底 · ${config.model_fallback}`,
          }
        : null,
    ].filter((item): item is ModelOption => Boolean(item))));
  }, [llmConfigs]);
  const selectedWorkflowSkills = useMemo(
    () => workflowSourceSkills(selectedWorkflowPreset),
    [selectedWorkflowPreset],
  );
  const selectedWorkflowCommand = useMemo(
    () => workflowEntryCommand(selectedWorkflowPreset),
    [selectedWorkflowPreset],
  );
  const selectedTaskExample = useMemo(
    () => workflowTaskExample(selectedWorkflowPreset),
    [selectedWorkflowPreset],
  );
  const selectedExecutionCommandExample = useMemo(
    () => workflowExecutionCommandExample(selectedWorkflowPreset),
    [selectedWorkflowPreset],
  );
  const selectedRebuttalReviewExample = useMemo(
    () => workflowRebuttalReviewExample(selectedWorkflowPreset),
    [selectedWorkflowPreset],
  );
  const normalizedInitialPaperIds = useMemo(
    () => Array.from(new Set((initialPaperIds || []).map((item) => item.trim()).filter(Boolean))),
    [initialPaperIds],
  );
  const isRebuttalWorkflow = selectedWorkflowPreset?.workflow_type === "rebuttal";
  const requiresExecutionCommand = selectedWorkflowPreset?.workflow_type === "run_experiment"
    || selectedWorkflowPreset?.workflow_type === "full_pipeline";

  const refreshProjects = useCallback(async () => {
    try {
      const result = await projectApi.companionOverview({ project_limit: 100, task_limit: 8 });
      const items = result.items || [];
      setProjects(items);
      return items;
    } catch {
      const fallback = await projectApi.list();
      const items = fallback.items || [];
      setProjects(items);
      return items;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setBootstrapping(true);
      try {
        const [presetResp, projectsResp, serversResp, llmResp] = await Promise.all([
          projectApi.workflowPresets(),
          refreshProjects(),
          assistantWorkspaceApi.servers().catch(() => ({
            items: [{ id: "local", label: "本地工作区", kind: "native", available: true }] as AssistantWorkspaceServer[],
          })),
          llmConfigApi.list().catch(() => ({ items: [] as LLMProviderConfig[] })),
        ]);
        if (cancelled) return;

        const presets = sortAndFilterPrimaryProjectWorkflows(presetResp.items || []);
        setWorkflowPresets(presets);
        setEngineProfiles(presetResp.engine_profiles || []);
        setDefaultExecutorEngineId(presetResp.default_engine_bindings?.executor_engine_id || "");
        setDefaultReviewerEngineId(presetResp.default_engine_bindings?.reviewer_engine_id || "");
        setWorkspaceServers(serversResp.items || []);
        setLlmConfigs(llmResp.items || []);

        const storedProjectId = typeof window !== "undefined" ? localStorage.getItem(LAST_PROJECT_KEY) || "" : "";
        const storedWorkflow = typeof window !== "undefined" ? localStorage.getItem(LAST_WORKFLOW_KEY) || "" : "";
        const nextProjectId =
          (initialProjectId && projectsResp.some((item) => item.id === initialProjectId) && initialProjectId)
          || (storedProjectId && projectsResp.some((item) => item.id === storedProjectId) && storedProjectId)
          || projectsResp[0]?.id
          || "";
        const nextWorkflow =
          (storedWorkflow && presets.some((item) => item.workflow_type === storedWorkflow) && storedWorkflow)
          || presets[0]?.workflow_type
          || "idea_discovery";

        setSelectedProjectId(nextProjectId);
        setSelectedWorkflowType(nextWorkflow as ProjectWorkflowType);
        const initialPreset = presets.find((item) => item.workflow_type === nextWorkflow) || presets[0];
        setPrompt((current) => current.trim() || initialPreset?.prefill_prompt || "");
        if (!projectsResp.length) {
          setShowCreateProject(true);
        }
        setNewProjectName((current) => current.trim() || (workspaceTitle || "").trim() || "研究项目");
      } catch (error) {
        if (!cancelled) {
          toast("error", error instanceof Error ? error.message : "工作流启动器加载失败");
        }
      } finally {
        if (!cancelled) {
          setBootstrapping(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [initialProjectId, refreshProjects, toast, workspaceTitle]);

  useEffect(() => {
    if (!selectedProjectId) {
      setProjectContext(null);
      setSelectedTargetId("");
      return;
    }
    let cancelled = false;
    void (async () => {
      setContextLoading(true);
      try {
        const result = await projectApi.workspaceContext(selectedProjectId);
        if (cancelled) return;
        const context = result.item;
        setProjectContext(context);
        setSelectedTargetId((current) => {
          if (context.targets.some((item) => item.id === current)) return current;
          return context.default_selections.target_id || context.targets[0]?.id || "";
        });
        setExecutorEngineId((current) => current || context.default_selections.executor_engine_id || defaultExecutorEngineId);
        setReviewerEngineId((current) => current || context.default_selections.reviewer_engine_id || defaultReviewerEngineId);
        setTitle((currentTitle) => currentTitle || workflowLabel(selectedWorkflowPreset));
      } catch (error) {
        if (!cancelled) {
          toast("error", error instanceof Error ? error.message : "读取项目上下文失败");
          setProjectContext(null);
        }
      } finally {
        if (!cancelled) {
          setContextLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [defaultExecutorEngineId, defaultReviewerEngineId, selectedProjectId, selectedWorkflowPreset, toast]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (selectedProjectId) {
      localStorage.setItem(LAST_PROJECT_KEY, selectedProjectId);
    }
  }, [selectedProjectId]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (selectedWorkflowType) {
      localStorage.setItem(LAST_WORKFLOW_KEY, selectedWorkflowType);
    }
  }, [selectedWorkflowType]);

  useEffect(() => {
    if (!selectedWorkflowPreset) return;
    setTitle(workflowLabel(selectedWorkflowPreset));
    setPrompt(selectedWorkflowPreset.prefill_prompt || "");
  }, [
    selectedWorkflowPreset?.label,
    selectedWorkflowPreset?.prefill_prompt,
    selectedWorkflowPreset?.workflow_type,
  ]);

  useEffect(() => {
    if (!usingEngineProfiles && llmModelOptions.length > 0) {
      setExecutorModel((current) => current || llmModelOptions[0].model);
      setReviewerModel((current) => current || llmModelOptions[0].model);
    }
  }, [llmModelOptions, usingEngineProfiles]);

  const handleCreateProject = useCallback(async () => {
    const name = newProjectName.trim();
    const serverId = normalizeServerId(newProjectServerId);
    const pathValue = newProjectPath.trim() || (workspacePath || "").trim();
    if (!name) {
      toast("warning", "请先填写项目名称");
      return null;
    }

    setCreatingProject(true);
    try {
      const result = await projectApi.create({
        name,
        description: trimToUndefined(newProjectDescription),
        workspace_server_id: serverId === "local" ? undefined : serverId,
        workdir: serverId === "local" ? trimToUndefined(pathValue) : undefined,
        remote_workdir: serverId === "local" ? undefined : trimToUndefined(pathValue),
      });
      const nextProject = result.item;
      const items = await refreshProjects();
      setSelectedProjectId(nextProject.id);
      setShowCreateProject(false);
      if (!items.some((item) => item.id === nextProject.id)) {
        setProjects((current) => [nextProject, ...current]);
      }
      toast("success", "项目已创建");
      return nextProject;
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "创建项目失败");
      return null;
    } finally {
      setCreatingProject(false);
    }
  }, [
    newProjectDescription,
    newProjectName,
    newProjectPath,
    newProjectServerId,
    refreshProjects,
    toast,
    workspacePath,
  ]);

  const handleApplyExample = useCallback(() => {
    if (selectedTaskExample) {
      setPrompt(selectedTaskExample);
    }
    if (requiresExecutionCommand && selectedExecutionCommandExample) {
      setExecutionCommand(selectedExecutionCommandExample);
    }
    if (isRebuttalWorkflow && selectedRebuttalReviewExample) {
      setRebuttalReviewBundle(selectedRebuttalReviewExample);
    }
  }, [
    isRebuttalWorkflow,
    requiresExecutionCommand,
    selectedExecutionCommandExample,
    selectedRebuttalReviewExample,
    selectedTaskExample,
  ]);

  const handleLaunch = useCallback(async () => {
    const preset = selectedWorkflowPreset;
    if (!preset) {
      toast("warning", "当前没有可用工作流");
      return;
    }

    let projectId = selectedProjectId;
    if (!projectId) {
      const created = await handleCreateProject();
      if (!created) return;
      projectId = created.id;
    }

    const parsedRebuttalCharacterLimit = Number.parseInt(rebuttalCharacterLimit.trim(), 10);
    const metadata: Record<string, unknown> = {
      launched_from: "assistant_workflow_launcher",
    };
    if (selectedWorkflowCommand) {
      metadata.entry_command = selectedWorkflowCommand;
    }
    if (preset.workflow_type === "rebuttal") {
      metadata.rebuttal_review_bundle = trimToUndefined(rebuttalReviewBundle);
      metadata.rebuttal_venue = trimToUndefined(rebuttalVenue) || "ICML";
      metadata.rebuttal_round = trimToUndefined(rebuttalRound) || "initial";
      metadata.rebuttal_quick_mode = rebuttalQuickMode;
      if (Number.isFinite(parsedRebuttalCharacterLimit) && parsedRebuttalCharacterLimit > 0) {
        metadata.rebuttal_character_limit = parsedRebuttalCharacterLimit;
      }
    }

    const body = {
      target_id: trimToUndefined(selectedTargetId),
      workflow_type: preset.workflow_type,
      title: trimToUndefined(title),
      prompt: prompt.trim() || preset.prefill_prompt || preset.label,
      paper_ids: normalizedInitialPaperIds,
      execution_command:
        preset.workflow_type === "run_experiment" || preset.workflow_type === "full_pipeline"
          ? trimToUndefined(executionCommand) || trimToUndefined(inferExecutionCommand(prompt))
          : undefined,
      executor_engine_id: usingEngineProfiles ? trimToUndefined(executorEngineId) : undefined,
      reviewer_engine_id: usingEngineProfiles ? trimToUndefined(reviewerEngineId) : undefined,
      executor_model: usingEngineProfiles ? undefined : trimToUndefined(executorModel),
      reviewer_model: usingEngineProfiles ? undefined : trimToUndefined(reviewerModel),
      auto_proceed: autoProceed,
      human_checkpoint_enabled: !autoProceed,
      metadata,
    };

    if (!body.prompt.trim()) {
      toast("warning", "请先填写任务说明");
      return;
    }
    if ((preset.workflow_type === "run_experiment" || preset.workflow_type === "full_pipeline") && !body.execution_command) {
      toast("warning", preset.workflow_type === "full_pipeline" ? "Research Pipeline 需要填写主实验命令" : "Experiment Bridge 需要填写实验命令");
      return;
    }
    if (preset.workflow_type === "rebuttal" && !trimToUndefined(rebuttalReviewBundle)) {
      toast("warning", "Rebuttal 需要填写审稿意见原文");
      return;
    }
    if (preset.workflow_type === "rebuttal" && (!Number.isFinite(parsedRebuttalCharacterLimit) || parsedRebuttalCharacterLimit <= 0)) {
      toast("warning", "Rebuttal 需要有效的字符限制");
      return;
    }

    setLaunching(true);
    try {
      const result = await projectApi.createRun(projectId, body);
      const launchResult: WorkflowLaunchResult = {
        projectId,
        runId: result.item.id,
        run: result.item,
      };
      toast("success", result.item.status === "paused" ? "运行已提交，等待确认" : "运行已启动");
      onLaunch?.(launchResult);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "启动运行失败");
    } finally {
      setLaunching(false);
    }
  }, [
    autoProceed,
    executorEngineId,
    executorModel,
    handleCreateProject,
    onLaunch,
    prompt,
    executionCommand,
    rebuttalCharacterLimit,
    rebuttalQuickMode,
    rebuttalReviewBundle,
    rebuttalRound,
    rebuttalVenue,
    reviewerEngineId,
    reviewerModel,
    normalizedInitialPaperIds,
    selectedProjectId,
    selectedTargetId,
    selectedWorkflowCommand,
    selectedWorkflowPreset,
    title,
    toast,
    usingEngineProfiles,
  ]);

  const headerBadges = useMemo(() => {
    const items = [];
    if (selectedProject) items.push(selectedProject.name);
    if (selectedTarget) items.push(localizeTargetLabel(selectedTarget.label));
    if (selectedWorkspacePath) items.push(selectedWorkspacePath);
    return items.slice(0, compact ? 2 : 3);
  }, [compact, selectedProject, selectedTarget, selectedWorkspacePath]);
  const isDrawer = surface === "drawer";

  return (
    <section
      className={cn(
        isDrawer
          ? "flex min-h-full flex-col rounded-[28px] border border-border/70 bg-surface/76 p-4 shadow-[0_28px_60px_-44px_rgba(15,23,35,0.18)] backdrop-blur-xl lg:p-5"
          : "rounded-[28px] border border-border/70 bg-surface/92 p-4 shadow-[0_28px_60px_-40px_rgba(15,23,35,0.22)] backdrop-blur-xl lg:p-5",
        compact && !isDrawer && "rounded-[24px] p-4",
        className,
      )}
    >
      <div className={cn("flex flex-wrap items-start justify-between gap-3", isDrawer && "border-b border-border/55 pb-4")}>
        <div className="min-w-0">
          <div className={cn(
            "inline-flex items-center gap-2 rounded-full px-3 py-1 text-[11px] font-medium",
            isDrawer
              ? "border border-border/70 bg-surface text-ink-secondary"
              : "border border-primary/15 bg-primary/8 text-primary",
          )}>
            <Sparkles className="h-3.5 w-3.5" />
            科研流程
          </div>
          <h3 className={cn(
            "mt-3 text-left font-semibold tracking-[-0.04em] text-ink",
            isDrawer ? "text-xl" : "text-lg",
          )}>
            {isDrawer ? "配置项目流程" : "在研究助手里启动项目流程"}
          </h3>
          {!isDrawer && headerBadges.length > 0 ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {headerBadges.map((item) => (
                <Badge key={item} className="max-w-full truncate">{item}</Badge>
              ))}
            </div>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            icon={<FolderPlus className="h-3.5 w-3.5" />}
            onClick={() => setShowCreateProject((current) => !current)}
          >
            {showCreateProject ? "收起项目" : "新建项目"}
          </Button>
        </div>
      </div>

      {showCreateProject ? (
        <div className="mt-4 rounded-[24px] border border-border/60 bg-page/72 p-4">
          <div className="grid gap-3 lg:grid-cols-2">
            <Input
              label="项目名称"
              value={newProjectName}
              onChange={(event) => setNewProjectName(event.target.value)}
              placeholder="输入项目名称"
            />
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-ink">工作区</label>
              <select
                value={newProjectServerId}
                onChange={(event) => setNewProjectServerId(event.target.value)}
                className="h-10 w-full rounded-lg border border-border bg-surface px-3.5 text-sm text-ink outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
              >
                {(workspaceServers.length > 0 ? workspaceServers : [{ id: "local", label: "本地工作区" }]).map((server) => (
                  <option key={server.id} value={server.id}>{server.label}</option>
                ))}
              </select>
            </div>
            <div className="lg:col-span-2">
              <Input
                label="目录路径"
                value={newProjectPath}
                onChange={(event) => setNewProjectPath(event.target.value)}
                placeholder="输入目录路径"
              />
            </div>
            <div className="lg:col-span-2">
              <Textarea
                label="项目说明"
                rows={3}
                value={newProjectDescription}
                onChange={(event) => setNewProjectDescription(event.target.value)}
                placeholder="输入项目说明"
              />
            </div>
          </div>
          <div className="mt-3 flex justify-end">
            <Button
              type="button"
              size="sm"
              loading={creatingProject}
              onClick={() => void handleCreateProject()}
            >
              创建项目
            </Button>
          </div>
        </div>
      ) : null}

      {bootstrapping ? (
        <div className="mt-5 flex items-center gap-2 rounded-2xl border border-border/60 bg-page/80 px-4 py-3 text-sm text-ink-secondary">
          <Loader2 className="h-4 w-4 animate-spin" />
          加载工作流配置...
        </div>
      ) : (
        <>
          <div className={cn(
            "mt-5 grid gap-4",
            isDrawer ? "grid-cols-1" : "xl:grid-cols-[minmax(0,1.1fr),minmax(0,0.9fr)]",
          )}>
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <label className="block text-sm font-medium text-ink">项目</label>
                  <select
                    value={selectedProjectId}
                    onChange={(event) => setSelectedProjectId(event.target.value)}
                    className="h-10 w-full rounded-lg border border-border bg-surface px-3.5 text-sm text-ink outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
                  >
                    {!projects.length && <option value="">先新建项目</option>}
                    {projects.map((project) => (
                      <option key={project.id} value={project.id}>{project.name}</option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1.5">
                  <label className="block text-sm font-medium text-ink">流程</label>
                  <select
                    value={selectedWorkflowType}
                    onChange={(event) => setSelectedWorkflowType(event.target.value as ProjectWorkflowType)}
                    className="h-10 w-full rounded-lg border border-border bg-surface px-3.5 text-sm text-ink outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
                  >
                    {workflowPresets.map((preset) => (
                      <option key={preset.id} value={preset.workflow_type}>{workflowLabel(preset)}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <label className="block text-sm font-medium text-ink">执行模型</label>
                  <select
                    value={usingEngineProfiles ? executorEngineId : executorModel}
                    onChange={(event) => {
                      if (usingEngineProfiles) {
                        setExecutorEngineId(event.target.value);
                        return;
                      }
                      setExecutorModel(event.target.value);
                    }}
                    className="h-10 w-full rounded-lg border border-border bg-surface px-3.5 text-sm text-ink outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
                  >
                    {usingEngineProfiles ? (
                      <>
                        <option value="">跟随默认绑定</option>
                        {engineProfiles.map((profile) => (
                          <option key={profile.id} value={profile.id}>
                            {profile.label} · {profile.model}
                          </option>
                        ))}
                      </>
                    ) : (
                      llmModelOptions.map((option) => (
                        <option key={`executor-${option.value}`} value={option.model}>{option.label}</option>
                      ))
                    )}
                  </select>
                </div>
                <div className="space-y-1.5">
                  <label className="block text-sm font-medium text-ink">评审模型</label>
                  <select
                    value={usingEngineProfiles ? reviewerEngineId : reviewerModel}
                    onChange={(event) => {
                      if (usingEngineProfiles) {
                        setReviewerEngineId(event.target.value);
                        return;
                      }
                      setReviewerModel(event.target.value);
                    }}
                    className="h-10 w-full rounded-lg border border-border bg-surface px-3.5 text-sm text-ink outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
                  >
                    {usingEngineProfiles ? (
                      <>
                        <option value="">跟随默认绑定</option>
                        {engineProfiles.map((profile) => (
                          <option key={profile.id} value={profile.id}>
                            {profile.label} · {profile.model}
                          </option>
                        ))}
                      </>
                    ) : (
                      llmModelOptions.map((option) => (
                        <option key={`reviewer-${option.value}`} value={option.model}>{option.label}</option>
                      ))
                    )}
                  </select>
                </div>
              </div>

              {projectContext?.targets.length ? (
                <div className="space-y-1.5">
                  <label className="block text-sm font-medium text-ink">部署目标</label>
                  <select
                    value={selectedTargetId}
                    onChange={(event) => setSelectedTargetId(event.target.value)}
                    className="h-10 w-full rounded-lg border border-border bg-surface px-3.5 text-sm text-ink outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
                  >
                    {projectContext.targets.map((target) => (
                      <option key={target.id} value={target.id}>{localizeTargetLabel(target.label)}</option>
                    ))}
                  </select>
                </div>
              ) : null}

              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr),180px]">
                <Input
                  label="运行标题"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="输入运行标题"
                />
                <button
                  type="button"
                  onClick={() => setAutoProceed((current) => !current)}
                  className={cn(
                    "mt-7 inline-flex h-10 items-center justify-center rounded-2xl border px-3.5 text-sm font-medium transition",
                    autoProceed
                      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                      : "border-amber-200 bg-amber-50 text-amber-700",
                  )}
                >
                  {autoProceed ? "自动继续" : "人工确认"}
                </button>
              </div>

              <div className="space-y-1.5">
                <div className="flex items-center justify-between gap-2">
                  <label className="block text-sm font-medium text-ink">任务说明</label>
                  {selectedWorkflowPreset?.prefill_prompt ? (
                    <button
                      type="button"
                      onClick={() => setPrompt(selectedWorkflowPreset.prefill_prompt)}
                      className="text-xs text-primary transition hover:text-primary-hover"
                    >
                      使用模板
                    </button>
                  ) : null}
                </div>
                <Textarea
                  rows={compact ? 5 : 7}
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                  placeholder={selectedTaskExample || "输入这次流程的目标"}
                />
              </div>

              {requiresExecutionCommand ? (
                <Input
                  label={selectedWorkflowPreset?.workflow_type === "full_pipeline" ? "主实验命令" : "实验命令"}
                  value={executionCommand}
                  onChange={(event) => setExecutionCommand(event.target.value)}
                  placeholder={selectedExecutionCommandExample || "输入实验命令"}
                />
              ) : null}

              {isRebuttalWorkflow ? (
                <>
                  <div className="grid gap-3 sm:grid-cols-3">
                    <Input
                      label="Venue"
                      value={rebuttalVenue}
                      onChange={(event) => setRebuttalVenue(event.target.value)}
                      placeholder="输入 Venue"
                    />
                    <Input
                      label="字符限制"
                      value={rebuttalCharacterLimit}
                      onChange={(event) => setRebuttalCharacterLimit(event.target.value)}
                      placeholder="输入限制"
                    />
                    <div className="space-y-1.5">
                      <label className="block text-sm font-medium text-ink">轮次</label>
                      <select
                        value={rebuttalRound}
                        onChange={(event) => setRebuttalRound(event.target.value)}
                        className="h-10 w-full rounded-lg border border-border bg-surface px-3.5 text-sm text-ink outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
                      >
                        <option value="initial">Initial</option>
                        <option value="followup">Follow-up</option>
                      </select>
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <label className="block text-sm font-medium text-ink">审稿意见原文</label>
                      <button
                        type="button"
                        onClick={() => setRebuttalQuickMode((current) => !current)}
                        className={cn(
                          "inline-flex h-8 items-center justify-center rounded-full border px-3 text-xs font-medium transition",
                          rebuttalQuickMode
                            ? "border-amber-200 bg-amber-50 text-amber-700"
                            : "border-emerald-200 bg-emerald-50 text-emerald-700",
                        )}
                      >
                        {rebuttalQuickMode ? "Quick Mode" : "Full Draft"}
                      </button>
                    </div>
                    <Textarea
                      rows={compact ? 6 : 8}
                      value={rebuttalReviewBundle}
                      onChange={(event) => setRebuttalReviewBundle(event.target.value)}
                      placeholder={selectedRebuttalReviewExample || "输入审稿意见"}
                    />
                  </div>
                </>
              ) : null}
            </div>

            {!isDrawer ? (
              <div className="space-y-4">
              <div className="rounded-[24px] border border-border/60 bg-page/72 p-4">
                <div className="text-sm font-semibold text-ink">当前绑定</div>
                <div className="mt-3 space-y-2 text-sm text-ink-secondary">
                  <div className="flex items-start justify-between gap-3">
                    <span>项目</span>
                    <span className="max-w-[68%] text-right text-ink">{selectedProject?.name || "未选择"}</span>
                  </div>
                  <div className="flex items-start justify-between gap-3">
                    <span>工作区</span>
                    <span className="max-w-[68%] text-right text-ink">{selectedWorkspacePath || "未绑定"}</span>
                  </div>
                  <div className="flex items-start justify-between gap-3">
                    <span>流程</span>
                    <span className="max-w-[68%] text-right text-ink">{workflowLabel(selectedWorkflowPreset) || "未选择"}</span>
                  </div>
                  {selectedWorkflowCommand ? (
                    <div className="flex items-start justify-between gap-3">
                      <span>入口命令</span>
                      <span className="max-w-[68%] text-right font-mono text-ink">{selectedWorkflowCommand}</span>
                    </div>
                  ) : null}
                  <div className="flex items-start justify-between gap-3">
                    <span>阶段数</span>
                    <span className="text-ink">{selectedWorkflowPreset?.stages.length || 0}</span>
                  </div>
                  {requiresExecutionCommand ? (
                    <div className="flex items-start justify-between gap-3">
                      <span>{selectedWorkflowPreset?.workflow_type === "full_pipeline" ? "主实验命令" : "实验命令"}</span>
                      <span className="max-w-[68%] break-all text-right font-mono text-ink">
                        {executionCommand || inferExecutionCommand(prompt) || "未填写"}
                      </span>
                    </div>
                  ) : null}
                  {isRebuttalWorkflow ? (
                    <>
                      <div className="flex items-start justify-between gap-3">
                        <span>Venue</span>
                        <span className="max-w-[68%] text-right text-ink">{rebuttalVenue || "ICML"}</span>
                      </div>
                      <div className="flex items-start justify-between gap-3">
                        <span>字符限制</span>
                        <span className="text-ink">{rebuttalCharacterLimit || "未填写"}</span>
                      </div>
                      <div className="flex items-start justify-between gap-3">
                        <span>轮次</span>
                        <span className="text-ink">{rebuttalRound}</span>
                      </div>
                    </>
                  ) : null}
                  {contextLoading ? (
                    <div className="flex items-center gap-2 text-xs text-ink-tertiary">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      读取项目上下文...
                    </div>
                  ) : null}
                </div>
              </div>

              {selectedWorkflowPreset?.stages?.length ? (
              <div className="rounded-[24px] border border-border/60 bg-surface/85 p-4">
                  <div className="text-sm font-semibold text-ink">
                    {selectedWorkflowSkills.length > 0 ? "技能链" : "阶段"}
                  </div>
                  {selectedWorkflowSkills.length > 0 ? (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {selectedWorkflowSkills.map((skill) => (
                        <Badge key={skill} variant="info" className="font-mono text-[11px]">
                          /{skill}
                        </Badge>
                      ))}
                    </div>
                  ) : null}
                  <div className="mt-3 space-y-2">
                    {selectedWorkflowPreset.stages.slice(0, compact ? 4 : selectedWorkflowPreset.stages.length).map((stage, index) => (
                      <div
                        key={stage.id}
                        className="flex items-center gap-3 rounded-2xl border border-border/55 bg-page/72 px-3 py-2.5 text-sm"
                      >
                        <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">
                          {index + 1}
                        </span>
                        <div className="min-w-0">
                          <div className="truncate font-medium text-ink">{stage.label}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
              </div>
            ) : null}
          </div>

          {selectedWorkflowPreset ? (
            <div className="mt-5 rounded-[24px] border border-border/60 bg-surface/82 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-semibold text-ink">流程说明与任务示例</div>
                  <div className="mt-2 text-sm leading-6 text-ink-secondary">
                    {selectedWorkflowPreset.intro || selectedWorkflowPreset.description}
                  </div>
                </div>
                {(selectedTaskExample || selectedExecutionCommandExample || selectedRebuttalReviewExample) ? (
                  <Button type="button" variant="secondary" size="sm" onClick={handleApplyExample}>
                    套用示例
                  </Button>
                ) : null}
              </div>

              <div className="mt-4 grid gap-3 lg:grid-cols-2">
                <WorkflowGuideList title="什么时候用" items={selectedWorkflowPreset.when_to_use} />
                <WorkflowGuideList title="需要输入" items={selectedWorkflowPreset.required_inputs} />
                <WorkflowGuideList title="操作步骤" items={selectedWorkflowPreset.usage_steps} />
                <WorkflowGuideList title="预期产出" items={selectedWorkflowPreset.expected_outputs} />
                <WorkflowStageGuide preset={selectedWorkflowPreset} />
              </div>

              {(selectedTaskExample || selectedExecutionCommandExample || selectedRebuttalReviewExample) ? (
                <div className="mt-4 rounded-[20px] border border-primary/15 bg-primary/8 px-4 py-3 text-sm">
                  <div className="font-semibold text-ink">任务示例</div>
                  {selectedTaskExample ? (
                    <p className="mt-2 leading-6 text-ink-secondary">{selectedTaskExample}</p>
                  ) : null}
                  {requiresExecutionCommand && selectedExecutionCommandExample ? (
                    <div className="mt-3 rounded-2xl border border-border/60 bg-surface px-3 py-2">
                      <div className="text-[11px] font-medium text-ink-tertiary">
                        {selectedWorkflowPreset.workflow_type === "full_pipeline" ? "主实验命令示例" : "实验命令示例"}
                      </div>
                      <div className="mt-1 break-all font-mono text-xs text-ink">{selectedExecutionCommandExample}</div>
                    </div>
                  ) : null}
                  {isRebuttalWorkflow && selectedRebuttalReviewExample ? (
                    <div className="mt-3 rounded-2xl border border-border/60 bg-surface px-3 py-2">
                      <div className="text-[11px] font-medium text-ink-tertiary">审稿意见示例</div>
                      <div className="mt-1 whitespace-pre-line text-xs leading-5 text-ink-secondary">
                        {selectedRebuttalReviewExample}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-border/60 pt-4">
            <div className="flex flex-wrap gap-2">
              {!autoProceed ? <Badge variant="warning">本轮会停在检查点</Badge> : null}
            </div>
            <Button
              type="button"
              loading={launching || creatingProject}
              icon={launching ? undefined : <Play className="h-4 w-4" />}
              onClick={() => void handleLaunch()}
            >
              启动运行
              {!launching ? <ArrowRight className="h-4 w-4" /> : null}
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
