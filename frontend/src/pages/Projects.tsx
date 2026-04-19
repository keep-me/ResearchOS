import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowRight,
  BookOpen,
  ChevronRight,
  Clock3,
  Download,
  Eye,
  FolderKanban,
  FolderOpen,
  Link2,
  Loader2,
  Pencil,
  Play,
  RefreshCw,
  RotateCcw,
  Trash2,
} from "lucide-react";
import ArtifactPreviewModal from "@/components/ArtifactPreviewModal";
import { Badge, Button, Empty, Input, Modal, Textarea } from "@/components/ui";
import { Drawer } from "@/components/ui/Drawer";
import ResearchWorkflowLauncher, { type WorkflowLaunchResult } from "@/components/assistant/ResearchWorkflowLauncher";
import { useToast } from "@/contexts/ToastContext";
import { useConversationCtx } from "@/contexts/ConversationContext";
import { getErrorMessage } from "@/lib/errorHandler";
import { cn, formatDateTime, timeAgo, truncate } from "@/lib/utils";
import { getConversationWorkspaceKey, isUntouchedConversation } from "@/hooks/useConversations";
import { joinWorkspaceRootPath, validateWorkspaceDirectoryName } from "@/lib/workspaceRoots";
import {
  buildArtifactReadCandidates,
  fileNameFromPath,
  isMarkdownArtifact,
  isPreviewableArtifact,
  normalizeServerId,
  type ArtifactPreviewState,
} from "@/lib/workspaceArtifacts";
import { assistantWorkspaceApi, projectApi, workspaceRootApi } from "@/services/api";
import type {
  AssistantWorkspaceServer,
  Project,
  ProjectArtifactRef,
  ProjectCompanionOverviewItem,
  ProjectDeploymentTarget,
  ProjectEngineProfile,
  ProjectPaperRef,
  ProjectRun,
  ProjectRunActionPreset,
  ProjectRunActionType,
  ProjectRunPendingCheckpoint,
  ProjectWorkflowStageTrace,
  ProjectWorkspaceContext,
} from "@/types";
import ConfirmDialog from "@/components/ConfirmDialog";

const Markdown = lazy(() => import("@/components/Markdown"));

interface ProjectFormState {
  name: string;
  description: string;
  dirName: string;
  workspace_server_id: string;
  path: string;
}

type ProjectModalMode = "create" | "import" | "edit";

const DEFAULT_PROJECT_FORM: ProjectFormState = {
  name: "",
  description: "",
  dirName: "",
  workspace_server_id: "local",
  path: "",
};

const FALLBACK_ACTIONS: ProjectRunActionPreset[] = [
  { id: "continue", action_type: "continue", label: "继续到下一步" },
  { id: "experiment_bridge", action_type: "run_experiment", label: "实验桥接", workflow_type: "run_experiment" },
  { id: "auto_review_loop", action_type: "review", label: "自动评审循环", workflow_type: "auto_review_loop" },
  { id: "sync_workspace", action_type: "sync_workspace", label: "同步工作区", workflow_type: "sync_workspace" },
  { id: "custom_run", action_type: "custom", label: "自定义流程", workflow_type: "custom_run" },
];

function projectWorkspacePath(project: Pick<Project, "workspace_path" | "remote_workdir" | "workdir"> | null | undefined): string {
  return (project?.workspace_path || project?.remote_workdir || project?.workdir || "").trim();
}

function targetWorkspacePath(target: Pick<ProjectDeploymentTarget, "workspace_path" | "remote_workdir" | "workdir"> | null | undefined): string {
  return (target?.workspace_path || target?.remote_workdir || target?.workdir || "").trim();
}

function runWorkspacePath(run: Pick<ProjectRun, "workspace_path" | "remote_workdir" | "workdir"> | null | undefined): string {
  return (run?.workspace_path || run?.remote_workdir || run?.workdir || "").trim();
}

function localizeLabel(value: string | null | undefined) {
  return (value || "").trim() || "未命名";
}

function isProjectNotFoundError(error: unknown) {
  const message = getErrorMessage(error);
  return message.includes("项目不存在") || message.includes("404");
}

function isRunNotFoundError(error: unknown) {
  const message = getErrorMessage(error);
  return message.includes("项目运行不存在") || message.includes("404");
}

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
      return "未知状态";
  }
}

function statusVariant(status: string | null | undefined): "default" | "success" | "warning" | "error" | "info" {
  switch (status) {
    case "succeeded":
    case "completed":
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

function stageStatusLabel(status: string | null | undefined) {
  switch (status) {
    case "running":
      return "执行中";
    case "completed":
      return "已完成";
    case "failed":
      return "失败";
    case "cancelled":
      return "已取消";
    default:
      return "待执行";
  }
}

function actionTypeLabel(value: string | null | undefined) {
  switch (value) {
    case "continue":
      return "继续执行";
    case "review":
      return "补充评审";
    case "run_experiment":
      return "补充实验";
    case "sync_workspace":
      return "同步工作区";
    case "retry":
      return "重新运行";
    case "monitor":
      return "监控";
    default:
      return value?.trim() || "自定义动作";
  }
}

function runEngineSummary(label: string | null | undefined, model: string | null | undefined) {
  const cleanLabel = (label || "").trim();
  const cleanModel = (model || "").trim();
  if (cleanLabel && cleanModel) return `${cleanLabel} · ${cleanModel}`;
  return cleanLabel || cleanModel || "系统默认";
}

function paperSourceLabel(value: string | null | undefined) {
  switch ((value || "").trim()) {
    case "selected":
      return "显式选择";
    case "project_linked":
      return "项目论文";
    case "library_match":
      return "论文库匹配";
    case "external_arxiv":
      return "外部 arXiv";
    case "workspace_pdf":
      return "工作区 PDF";
    default:
      return value?.trim() || "候选";
  }
}

function paperRefYear(ref: ProjectPaperRef) {
  return ref.year || ref.publication_year || (ref.publication_date || "").slice(0, 4) || null;
}

function paperRefActionLabel(ref: ProjectPaperRef) {
  if (ref.project_linked) return "已关联";
  if (ref.status === "imported" || ref.imported_paper_id) return "已导入";
  if (ref.paper_id) return "关联项目";
  if (ref.importable) return "导入并关联";
  return "";
}

function paperAssetLabels(ref: ProjectPaperRef) {
  const assets = ref.asset_status || {};
  const labels: string[] = [];
  if (assets.pdf) labels.push("PDF");
  if (assets.embedding) labels.push("向量");
  if (assets.skim) labels.push("粗读");
  if (assets.deep) labels.push("精读");
  if (Array.isArray(assets.analysis_rounds) && assets.analysis_rounds.length > 0) {
    labels.push(`三轮分析 ${assets.analysis_rounds.length}`);
  }
  return labels;
}

function serverLabelOf(serverId: string | null | undefined, servers: AssistantWorkspaceServer[]) {
  const normalized = normalizeServerId(serverId);
  if (normalized === "local") return "本地工作区";
  return servers.find((item) => item.id === normalized)?.label || normalized;
}

function engineOptionLabel(profile: Pick<ProjectEngineProfile, "label" | "model"> | null | undefined) {
  if (!profile) return "系统默认";
  return `${profile.label} · ${profile.model}`;
}

function checkpointTitle(checkpoint: ProjectRunPendingCheckpoint) {
  return checkpoint.type === "stage_transition" ? "阶段确认" : "运行前确认";
}

function buildWorkflowOutputPreview(
  artifact: ProjectArtifactRef,
  serverId: string,
  outputMarkdown: string,
  resultPath?: string | null,
): ArtifactPreviewState {
  const previewPath = artifact.path || artifact.relative_path || resultPath || "workflow_output_markdown.md";
  return {
    title: fileNameFromPath(previewPath),
    path: previewPath,
    serverId,
    content: outputMarkdown,
    truncated: false,
    markdown: true,
  };
}

export default function Projects() {
  const navigate = useNavigate();
  const { projectId } = useParams<{ projectId?: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const { toast } = useToast();
  const { metas, activeConv, createConversation, switchConversation, patchConversation } = useConversationCtx();

  const [projects, setProjects] = useState<ProjectCompanionOverviewItem[]>([]);
  const [servers, setServers] = useState<AssistantWorkspaceServer[]>([]);
  const [workspaceContext, setWorkspaceContext] = useState<ProjectWorkspaceContext | null>(null);
  const [selectedRunDetail, setSelectedRunDetail] = useState<ProjectRun | null>(null);
  const [selectedRunId, setSelectedRunId] = useState("");
  const selectedRunIdRef = useRef("");
  const deletingProjectRef = useRef<string | null>(null);

  const [loadingProjects, setLoadingProjects] = useState(true);
  const [loadingContext, setLoadingContext] = useState(false);
  const [loadingRunDetail, setLoadingRunDetail] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const [projectModalOpen, setProjectModalOpen] = useState(false);
  const [projectModalMode, setProjectModalMode] = useState<ProjectModalMode>("create");
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
  const [projectForm, setProjectForm] = useState<ProjectFormState>(DEFAULT_PROJECT_FORM);
  const [defaultProjectsRoot, setDefaultProjectsRoot] = useState("");
  const [savingProject, setSavingProject] = useState(false);
  const [deletingProjectId, setDeletingProjectId] = useState<string | null>(null);
  const [deleteProjectTarget, setDeleteProjectTarget] = useState<Project | null>(null);

  const [revealingPath, setRevealingPath] = useState<string | null>(null);
  const [previewLoadingPath, setPreviewLoadingPath] = useState<string | null>(null);
  const [artifactPreview, setArtifactPreview] = useState<ArtifactPreviewState | null>(null);

  const [checkpointComment, setCheckpointComment] = useState("");
  const [respondingCheckpoint, setRespondingCheckpoint] = useState<"approve" | "reject" | null>(null);
  const [retryingRunId, setRetryingRunId] = useState<string | null>(null);
  const [deletingRunId, setDeletingRunId] = useState<string | null>(null);
  const [deleteRunTarget, setDeleteRunTarget] = useState<ProjectRun | null>(null);
  const [submittingAction, setSubmittingAction] = useState(false);
  const [selectedActionPresetId, setSelectedActionPresetId] = useState("continue");
  const [runActionPrompt, setRunActionPrompt] = useState("");
  const [workflowDrawerOpen, setWorkflowDrawerOpen] = useState(false);
  const [importingCandidateRefId, setImportingCandidateRefId] = useState<string | null>(null);

  const activeWorkspaceContext = workspaceContext?.project?.id === projectId ? workspaceContext : null;
  const queryRunId = searchParams.get("run") || "";
  const selectedProject = activeWorkspaceContext?.project || projects.find((item) => item.id === projectId) || null;
  const targets = activeWorkspaceContext?.targets || [];
  const runs = activeWorkspaceContext?.runs || [];
  const actionItems = activeWorkspaceContext?.action_items || [];
  const engineProfiles = activeWorkspaceContext?.engine_profiles || [];
  const selectedRunSummary = useMemo(
    () => (activeWorkspaceContext ? runs.find((item) => item.id === selectedRunId) || runs[0] || null : null),
    [activeWorkspaceContext, runs, selectedRunId],
  );
  const selectedRun = activeWorkspaceContext
    ? (selectedRunDetail && selectedRunDetail.id === selectedRunId
      ? selectedRunDetail
      : selectedRunSummary)
    : null;
  const selectedTarget = useMemo(
    () => targets.find((item) => item.id === (selectedRun?.target_id || activeWorkspaceContext?.default_selections.target_id || "")) || targets[0] || null,
    [activeWorkspaceContext?.default_selections.target_id, selectedRun?.target_id, targets],
  );
  const assistantTarget = useMemo(
    () =>
      targets.find((item) => item.id === (activeWorkspaceContext?.default_selections.target_id || "")) ||
      targets.find((item) => item.is_primary) ||
      targets[0] ||
      null,
    [activeWorkspaceContext?.default_selections.target_id, targets],
  );
  const selectedRunWorkspaceRoot = runWorkspacePath(selectedRun) || targetWorkspacePath(selectedTarget) || projectWorkspacePath(selectedProject);
  const selectedRunServerId = normalizeServerId(selectedRun?.workspace_server_id || selectedTarget?.workspace_server_id || selectedProject?.workspace_server_id);
  const selectedArtifacts = selectedRun?.artifact_refs || [];
  const paperIndex = selectedRun?.paper_index || [];
  const literatureCandidates = selectedRun?.literature_candidates || [];
  const selectedRunLogs = selectedRun?.recent_logs || activeWorkspaceContext?.recent_logs || [];
  const outputMarkdown = typeof selectedRun?.metadata?.workflow_output_markdown === "string"
    ? selectedRun.metadata.workflow_output_markdown.trim()
    : "";
  const actionOptions = (selectedRun?.next_actions?.length ? selectedRun.next_actions : actionItems.length > 0 ? actionItems : FALLBACK_ACTIONS) as ProjectRunActionPreset[];
  const selectedActionPreset = actionOptions.find((item) => item.id === selectedActionPresetId) || actionOptions[0] || null;
  const reportArtifactCount = useMemo(
    () => selectedArtifacts.filter((artifact) => artifact.kind === "report").length,
    [selectedArtifacts],
  );
  const pendingCheckpoint = selectedRun?.pending_checkpoint?.status === "pending"
    ? selectedRun.pending_checkpoint
    : null;

  const loadServers = useCallback(async () => {
    try {
      const result = await assistantWorkspaceApi.servers();
      setServers(result.items || []);
      return result.items || [];
    } catch {
      const fallback = [{ id: "local", label: "本地工作区", kind: "native", available: true }] as AssistantWorkspaceServer[];
      setServers(fallback);
      return fallback;
    }
  }, []);

  const loadDefaultProjectsRoot = useCallback(async () => {
    try {
      const result = await workspaceRootApi.list();
      setDefaultProjectsRoot(result.default_projects_root || "");
      return result.default_projects_root || "";
    } catch {
      setDefaultProjectsRoot("");
      return "";
    }
  }, []);

  const loadProjects = useCallback(async (silent = false) => {
    if (!silent) setLoadingProjects(true);
    try {
      const result = await projectApi.companionOverview({
        project_limit: 100,
        task_limit: 32,
      });
      const items = result.items || [];
      setProjects(items);
      return items;
    } catch (error) {
      try {
        const fallback = await projectApi.list();
        const items = (fallback.items || []) as ProjectCompanionOverviewItem[];
        setProjects(items);
        return items;
      } catch {
        if (!silent) {
          toast("error", getErrorMessage(error));
        }
        return [];
      }
    } finally {
      if (!silent) setLoadingProjects(false);
    }
  }, [toast]);

  const loadWorkspaceContext = useCallback(async (targetProjectId: string, silent = false) => {
    if (!targetProjectId) {
      setWorkspaceContext(null);
      return null;
    }
    if (!silent) setLoadingContext(true);
    try {
      const result = await projectApi.workspaceContext(targetProjectId);
      setWorkspaceContext(result.item);
      setProjects((current) => current.map((item) => (
        item.id === targetProjectId
          ? {
              ...item,
              ...result.item.project,
              latest_run: result.item.runs[0] || item.latest_run || null,
            }
          : item
      )));
      return result.item;
    } catch (error) {
      if (deletingProjectRef.current === targetProjectId && isProjectNotFoundError(error)) {
        return null;
      }
      if (!silent) {
        toast("error", getErrorMessage(error));
      }
      return null;
    } finally {
      if (!silent) setLoadingContext(false);
    }
  }, [toast]);

  const loadRunDetail = useCallback(async (runId: string, silent = false) => {
    if (!runId) {
      setSelectedRunDetail(null);
      return null;
    }
    if (!silent) setLoadingRunDetail(true);
    try {
      const result = await projectApi.getRun(runId);
      setSelectedRunDetail(result.item);
      return result.item;
    } catch (error) {
      if ((deletingProjectRef.current && isProjectNotFoundError(error)) || isRunNotFoundError(error)) {
        return null;
      }
      if (!silent) {
        toast("error", getErrorMessage(error));
      }
      return null;
    } finally {
      if (!silent) setLoadingRunDetail(false);
    }
  }, [toast]);

  const refreshCurrentProject = useCallback(async (silent = false) => {
    if (!projectId) return;
    if (silent) setRefreshing(true);
    try {
      const context = await loadWorkspaceContext(projectId, silent);
      if (!context) return;
      const nextRunId =
        (selectedRunIdRef.current && context.runs.some((item) => item.id === selectedRunIdRef.current) && selectedRunIdRef.current)
        || context.default_selections.run_id
        || context.runs[0]?.id
        || "";
      const runChanged = nextRunId !== selectedRunIdRef.current;
      if (runChanged) {
        setSelectedRunId(nextRunId);
      }
      if (!runChanged && nextRunId) {
        await loadRunDetail(nextRunId, silent);
      } else {
        setSelectedRunDetail(null);
      }
    } finally {
      if (silent) setRefreshing(false);
    }
  }, [loadRunDetail, loadWorkspaceContext, projectId]);

  useEffect(() => {
    void Promise.all([loadProjects(), loadServers(), loadDefaultProjectsRoot()]);
  }, [loadDefaultProjectsRoot, loadProjects, loadServers]);

  useEffect(() => {
    if (loadingProjects) return;
    if (!projects.length) return;
    if (!projectId) {
      navigate(`/projects/${projects[0].id}`, { replace: true });
      return;
    }
    if (!projects.some((item) => item.id === projectId)) {
      navigate(`/projects/${projects[0].id}`, { replace: true });
    }
  }, [loadingProjects, navigate, projectId, projects]);

  useEffect(() => {
    if (deletingProjectRef.current && deletingProjectRef.current !== projectId) {
      deletingProjectRef.current = null;
    }
  }, [projectId]);

  useEffect(() => {
    selectedRunIdRef.current = selectedRunId;
  }, [selectedRunId]);

  useEffect(() => {
    setWorkspaceContext((current) => (current?.project?.id === projectId ? current : null));
    setSelectedRunDetail(null);
    setSelectedRunId("");
  }, [projectId]);

  useEffect(() => {
    if (!projectId) return;
    void refreshCurrentProject();
    void projectApi.touch(projectId).catch(() => undefined);
  }, [projectId, refreshCurrentProject]);

  useEffect(() => {
    if (queryRunId && queryRunId !== selectedRunId && runs.some((item) => item.id === queryRunId)) {
      setSelectedRunId(queryRunId);
      return;
    }
    if (queryRunId !== selectedRunId) {
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        if (selectedRunId) {
          next.set("run", selectedRunId);
        } else {
          next.delete("run");
        }
        return next;
      }, { replace: true });
    }
  }, [queryRunId, runs, selectedRunId, setSearchParams]);

  useEffect(() => {
    if (!selectedRunId) {
      setSelectedRunDetail(null);
      return;
    }
    void loadRunDetail(selectedRunId);
  }, [loadRunDetail, selectedRunId]);

  useEffect(() => {
    if (!projectId || !selectedRun?.id) return;
    if (!["queued", "running"].includes(selectedRun.status)) return;
    const timer = window.setInterval(() => {
      void refreshCurrentProject(true);
    }, 12000);
    return () => window.clearInterval(timer);
  }, [projectId, refreshCurrentProject, selectedRun?.id, selectedRun?.status]);

  useEffect(() => {
    if (!actionOptions.length) {
      setSelectedActionPresetId("");
      return;
    }
    if (!actionOptions.some((item) => item.id === selectedActionPresetId)) {
      setSelectedActionPresetId(actionOptions[0].id);
    }
  }, [actionOptions, selectedActionPresetId]);

  const openCreateProject = useCallback(() => {
    void loadDefaultProjectsRoot();
    setProjectModalMode("create");
    setEditingProjectId(null);
    setProjectForm(DEFAULT_PROJECT_FORM);
    setProjectModalOpen(true);
  }, [loadDefaultProjectsRoot]);

  const openImportProject = useCallback(() => {
    void loadDefaultProjectsRoot();
    setProjectModalMode("import");
    setEditingProjectId(null);
    setProjectForm(DEFAULT_PROJECT_FORM);
    setProjectModalOpen(true);
  }, [loadDefaultProjectsRoot]);

  const openEditProject = useCallback((project: Project) => {
    setProjectModalMode("edit");
    setEditingProjectId(project.id);
    setProjectForm({
      name: project.name,
      description: project.description || "",
      dirName: "",
      workspace_server_id: normalizeServerId(project.workspace_server_id),
      path: projectWorkspacePath(project),
    });
    setProjectModalOpen(true);
  }, []);

  const handleSaveProject = useCallback(async () => {
    const name = projectForm.name.trim();
    if (!name) {
      toast("warning", "项目名称不能为空");
      return;
    }
    let payload: {
      name: string;
      description?: string;
      workspace_server_id?: string;
      workdir?: string;
      remote_workdir?: string;
    };

    if (projectModalMode === "create") {
      const rootPath = defaultProjectsRoot.trim();
      const dirName = projectForm.dirName.trim();
      const dirError = validateWorkspaceDirectoryName(dirName);
      if (!rootPath) {
        toast("warning", "请先在设置中配置默认项目根目录");
        return;
      }
      if (dirError) {
        toast("warning", dirError);
        return;
      }
      payload = {
        name,
        description: projectForm.description.trim() || undefined,
        workdir: joinWorkspaceRootPath(rootPath, dirName),
      };
    } else {
      const serverId = normalizeServerId(projectForm.workspace_server_id);
      const path = projectForm.path.trim();
      if (projectModalMode === "import" && !path) {
        toast("warning", "目录路径不能为空");
        return;
      }
      payload = {
        name,
        description: projectForm.description.trim() || undefined,
        workspace_server_id: serverId === "local" ? undefined : serverId,
        workdir: serverId === "local" ? (path || undefined) : undefined,
        remote_workdir: serverId === "local" ? undefined : (path || undefined),
      };
    }

    setSavingProject(true);
    try {
      const result = editingProjectId
        ? await projectApi.update(editingProjectId, payload)
        : await projectApi.create(payload);
      const items = await loadProjects(true);
      setProjectModalOpen(false);
      toast("success", editingProjectId ? "项目已更新" : projectModalMode === "import" ? "项目已导入" : "项目已创建");
      const nextProjectId = result.item.id || items[0]?.id;
      if (nextProjectId) {
        navigate(`/projects/${nextProjectId}`);
      }
    } catch (error) {
      toast("error", getErrorMessage(error));
    } finally {
      setSavingProject(false);
    }
  }, [defaultProjectsRoot, editingProjectId, loadProjects, navigate, projectForm, projectModalMode, toast]);

  const handleDeleteProject = useCallback(async () => {
    if (!deleteProjectTarget) return;
    const targetId = deleteProjectTarget.id;
    deletingProjectRef.current = targetId;
    setDeletingProjectId(targetId);
    try {
      if (projectId === targetId) {
        setWorkspaceContext(null);
        setSelectedRunDetail(null);
        setSelectedRunId("");
        setSearchParams((current) => {
          const next = new URLSearchParams(current);
          next.delete("run");
          return next;
        }, { replace: true });
      }
      await projectApi.delete(targetId);
      const items = await loadProjects(true);
      setDeleteProjectTarget(null);
      toast("success", "项目已删除");
      const nextProjectId = items[0]?.id;
      if (nextProjectId) {
        navigate(`/projects/${nextProjectId}`, { replace: true });
      } else {
        navigate("/projects", { replace: true });
        setWorkspaceContext(null);
        setSelectedRunDetail(null);
        setSelectedRunId("");
      }
    } catch (error) {
      toast("error", getErrorMessage(error));
    } finally {
      setDeletingProjectId(null);
    }
  }, [deleteProjectTarget, loadProjects, navigate, projectId, setSearchParams, toast]);

  const handleOpenAssistant = useCallback(() => {
    if (!selectedProject) return;
    const workspacePath = targetWorkspacePath(assistantTarget) || projectWorkspacePath(selectedProject);
    const workspace = workspacePath
      ? {
          path: workspacePath,
          effectivePath: workspacePath,
          title: selectedProject.name,
          serverId: normalizeServerId(assistantTarget?.workspace_server_id || selectedProject.workspace_server_id),
          serverLabel: serverLabelOf(assistantTarget?.workspace_server_id || selectedProject.workspace_server_id, servers),
        }
      : null;
    const workspaceKey = getConversationWorkspaceKey(workspace);
    const reusableWorkspaceConversationId =
      (workspaceKey
        ? metas.find((meta) => getConversationWorkspaceKey(meta) === workspaceKey && isUntouchedConversation(meta))?.id
        : undefined)
      || (workspaceKey
        ? metas.find((meta) => getConversationWorkspaceKey(meta) === workspaceKey)?.id
        : undefined);
    const reusableConversationId =
      reusableWorkspaceConversationId
      || (
        activeConv
        && isUntouchedConversation(activeConv)
          ? activeConv.id
          : undefined
      )
      || "";

    if (reusableConversationId) {
      switchConversation(reusableConversationId);
      if (workspace) {
        patchConversation(reusableConversationId, {
          workspacePath: workspace.path,
          effectiveWorkspacePath: workspace.effectivePath,
          workspaceTitle: workspace.title,
          workspaceServerId: workspace.serverId,
          workspaceServerLabel: workspace.serverLabel,
        });
      }
      navigate(`/assistant/${reusableConversationId}?project=${selectedProject.id}`);
      return;
    }

    const conversationId = createConversation(workspace, { persist: true });
    navigate(`/assistant/${conversationId}?project=${selectedProject.id}`);
  }, [activeConv, assistantTarget, createConversation, metas, navigate, patchConversation, selectedProject, servers, switchConversation]);

  const handleRevealPath = useCallback(async (path: string | null | undefined, serverId?: string | null) => {
    const targetPath = String(path || "").trim();
    if (!targetPath) {
      toast("warning", "当前没有可打开的路径");
      return;
    }
    setRevealingPath(targetPath);
    try {
      await assistantWorkspaceApi.reveal(targetPath, normalizeServerId(serverId));
    } catch (error) {
      toast("error", getErrorMessage(error));
    } finally {
      setRevealingPath(null);
    }
  }, [toast]);

  const handlePreviewArtifact = useCallback(async (artifact: ProjectArtifactRef) => {
    const artifactPath = (artifact.path || artifact.relative_path || "").trim();
    if (!artifactPath || !isPreviewableArtifact(artifactPath)) {
      toast("warning", "当前文件类型暂不支持预览");
      return;
    }
    const resultPath = String(selectedRun?.result_path || "").trim();
    const artifactName = fileNameFromPath(artifactPath);
    const resultName = fileNameFromPath(resultPath);
    const useWorkflowOutputPreview = Boolean(
      outputMarkdown
      && isMarkdownArtifact(artifactPath)
      && (
        (resultName && artifactName === resultName)
        || (!resultName && artifact.kind === "report" && reportArtifactCount === 1)
      ),
    );
    setPreviewLoadingPath(artifactPath);
    try {
      if (useWorkflowOutputPreview) {
        setArtifactPreview(buildWorkflowOutputPreview(artifact, selectedRunServerId, outputMarkdown, resultPath));
        return;
      }
      const readCandidates = buildArtifactReadCandidates(
        [selectedRun?.run_directory, selectedRunWorkspaceRoot],
        artifact,
      );
      if (!readCandidates.length) {
        throw new Error("当前文件缺少可用路径，暂时无法预览");
      }
      let result: Awaited<ReturnType<typeof assistantWorkspaceApi.readFile>> | null = null;
      let previewError: unknown = null;
      for (const candidate of readCandidates) {
        try {
          result = await assistantWorkspaceApi.readFile(
            candidate.workspacePath,
            candidate.relativePath,
            120000,
            selectedRunServerId,
          );
          break;
        } catch (error) {
          previewError = error;
        }
      }
      if (!result) {
        throw previewError instanceof Error ? previewError : new Error("当前文件暂时无法预览");
      }
      setArtifactPreview({
        title: fileNameFromPath(artifactPath),
        path: artifactPath,
        serverId: selectedRunServerId,
        content: result.content,
        truncated: result.truncated,
        markdown: isMarkdownArtifact(artifactPath),
      });
    } catch (error) {
      if (useWorkflowOutputPreview) {
        setArtifactPreview(buildWorkflowOutputPreview(artifact, selectedRunServerId, outputMarkdown, resultPath));
        return;
      }
      toast("error", getErrorMessage(error));
    } finally {
      setPreviewLoadingPath(null);
    }
  }, [outputMarkdown, reportArtifactCount, selectedRun?.result_path, selectedRun?.run_directory, selectedRunServerId, selectedRunWorkspaceRoot, toast]);

  const handleRetryRun = useCallback(async (run: ProjectRun) => {
    setRetryingRunId(run.id);
    try {
      const result = await projectApi.retryRun(run.id);
      toast("success", result.item.status === "paused" ? "已创建重试运行，等待确认" : "已创建重试运行");
      navigate(`/projects/${result.item.project_id}?run=${result.item.id}`);
      await refreshCurrentProject(true);
    } catch (error) {
      toast("error", getErrorMessage(error));
    } finally {
      setRetryingRunId(null);
    }
  }, [navigate, refreshCurrentProject, toast]);

  const handleDeleteRun = useCallback(async () => {
    if (!deleteRunTarget) return;
    setDeletingRunId(deleteRunTarget.id);
    try {
      await projectApi.deleteRun(deleteRunTarget.id, { deleteArtifacts: false });
      setDeleteRunTarget(null);
      toast("success", "运行记录已删除");
      await refreshCurrentProject(true);
    } catch (error) {
      toast("error", getErrorMessage(error));
    } finally {
      setDeletingRunId(null);
    }
  }, [deleteRunTarget, refreshCurrentProject, toast]);

  const handleCheckpointResponse = useCallback(async (action: "approve" | "reject") => {
    if (!selectedRun) return;
    setRespondingCheckpoint(action);
    try {
      const result = await projectApi.respondRunCheckpoint(selectedRun.id, {
        action,
        comment: checkpointComment.trim() || undefined,
      });
      setSelectedRunDetail(result.item);
      await refreshCurrentProject(true);
      toast("success", action === "approve" ? "已批准继续" : "已拒绝继续");
      setCheckpointComment("");
    } catch (error) {
      toast("error", getErrorMessage(error));
    } finally {
      setRespondingCheckpoint(null);
    }
  }, [checkpointComment, refreshCurrentProject, selectedRun, toast]);

  const handleSubmitRunAction = useCallback(async () => {
    if (!selectedRun) return;
    if (!selectedActionPreset) {
      toast("warning", "当前没有可用的后续流程");
      return;
    }
    const prompt = runActionPrompt.trim();
    if (!prompt) {
      toast("warning", "请填写要追加的动作说明");
      return;
    }
    setSubmittingAction(true);
    try {
      await projectApi.createRunAction(selectedRun.id, {
        action_type: selectedActionPreset.action_type,
        prompt,
        workflow_type: selectedActionPreset.workflow_type || undefined,
      });
      toast("success", "已启动后续流程");
      setRunActionPrompt("");
      await refreshCurrentProject(true);
    } catch (error) {
      toast("error", getErrorMessage(error));
    } finally {
      setSubmittingAction(false);
    }
  }, [refreshCurrentProject, runActionPrompt, selectedActionPreset, selectedRun, toast]);

  const handleWorkflowLaunched = useCallback((result: WorkflowLaunchResult) => {
    setWorkflowDrawerOpen(false);
    setSelectedRunId(result.runId);
    setSelectedRunDetail(result.run);
    navigate(`/projects/${result.projectId}?run=${result.runId}`);
    void refreshCurrentProject(true);
  }, [navigate, refreshCurrentProject]);

  const handleImportLiteratureCandidate = useCallback(async (candidate: ProjectPaperRef) => {
    if (!selectedRun?.id) return;
    const refId = candidate.ref_id?.trim();
    if (!refId) return;
    setImportingCandidateRefId(refId);
    try {
      const result = await projectApi.importRunLiteratureCandidates(selectedRun.id, {
        candidate_ref_ids: [refId],
        link_to_project: true,
      });
      setSelectedRunDetail(result.item);
      await refreshCurrentProject(true);
      const imported = result.imported_paper_ids.length;
      const linked = result.linked_paper_ids.length;
      toast("success", imported > 0 ? "候选论文已导入并关联项目" : linked > 0 ? "论文已关联项目" : "候选已更新");
    } catch (error) {
      toast("error", getErrorMessage(error));
    } finally {
      setImportingCandidateRefId(null);
    }
  }, [refreshCurrentProject, selectedRun?.id, toast]);

  const projectStats = useMemo(() => {
    if (!selectedProject) return [];
    return [
      { label: "目标", value: String(targets.length) },
      { label: "运行", value: String(runs.length) },
      { label: "论文", value: String(selectedProject.paper_count || 0) },
    ];
  }, [runs.length, selectedProject, targets.length]);

  const runStats = useMemo(() => {
    if (!selectedRun) return [];
    const stages = selectedRun.stage_trace || [];
    const actions = selectedRun.actions || [];
    return [
      { label: "阶段", value: String(stages.length) },
      { label: "动作", value: String(actions.length) },
      { label: "产物", value: String(selectedArtifacts.length) },
    ];
  }, [selectedArtifacts.length, selectedRun]);

  const projectListItems = useMemo(
    () => projects.map((project) => (
      activeWorkspaceContext?.project?.id === project.id
        ? {
            ...project,
            ...activeWorkspaceContext.project,
            latest_run: runs[0] || project.latest_run || null,
            run_count: runs.length,
          }
        : project
    )),
    [activeWorkspaceContext?.project, projects, runs],
  );

  return (
    <>
      <div className="grid gap-5 xl:grid-cols-[320px,minmax(0,1fr)] xl:gap-6">
        <aside className="space-y-5">
          <section
            data-testid="projects-list-panel"
            className="rounded-[24px] border border-border/70 bg-surface/92 p-4 shadow-[0_24px_48px_-36px_rgba(15,23,35,0.24)] backdrop-blur-xl sm:rounded-[28px]"
          >
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-primary">项目工作区</div>
                <div className="mt-1 text-lg font-semibold tracking-[-0.04em] text-ink">项目列表</div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" icon={<FolderKanban className="h-4 w-4" />} onClick={openCreateProject}>
                  新建
                </Button>
                <Button size="sm" variant="secondary" icon={<FolderOpen className="h-4 w-4" />} onClick={openImportProject}>
                  导入
                </Button>
              </div>
            </div>

            <div className="mt-4 space-y-2">
              {loadingProjects && projects.length === 0 ? (
                <div className="flex items-center gap-2 rounded-2xl border border-border/60 bg-page/75 px-3 py-3 text-sm text-ink-secondary">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  加载项目...
                </div>
              ) : null}

              {!loadingProjects && projects.length === 0 ? (
                <Empty
                  className="py-10"
                  icon={<FolderKanban className="h-10 w-10" />}
                  title="还没有项目"
                  action={(
                    <div className="flex flex-wrap justify-center gap-2">
                      <Button onClick={openCreateProject}>新建项目</Button>
                      <Button variant="secondary" onClick={openImportProject}>导入项目</Button>
                    </div>
                  )}
                />
              ) : null}

              {projectListItems.map((project) => {
                const active = project.id === projectId;
                return (
                  <button
                    key={project.id}
                    type="button"
                    onClick={() => navigate(`/projects/${project.id}`)}
                    className={cn(
                      "w-full rounded-[22px] border px-3.5 py-3 text-left transition",
                      active
                        ? "border-primary/25 bg-primary/8 shadow-[0_18px_36px_-30px_rgba(37,99,235,0.32)]"
                        : "border-border/60 bg-page/72 hover:border-primary/15 hover:bg-surface",
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold text-ink">{project.name}</div>
                        <div className="mt-1 flex flex-wrap gap-1.5">
                          <Badge className="px-2 py-0.5 text-[10px]">{typeof project.run_count === "number" ? project.run_count : 0} Runs</Badge>
                          <Badge className="px-2 py-0.5 text-[10px]">{typeof project.paper_count === "number" ? project.paper_count : 0} Papers</Badge>
                        </div>
                      </div>
                      <ChevronRight className={cn("h-4 w-4 shrink-0", active ? "text-primary" : "text-ink-tertiary")} />
                    </div>
                    {project.latest_run ? (
                      <div className="mt-3 text-xs text-ink-secondary">
                        <div className="font-medium text-ink">{project.latest_run.title || localizeLabel(project.latest_run.workflow_label)}</div>
                        <div className="mt-1 flex items-center gap-1.5 text-ink-tertiary">
                          <Clock3 className="h-3.5 w-3.5" />
                          <span>{timeAgo(project.latest_run.updated_at)}</span>
                        </div>
                      </div>
                    ) : null}
                  </button>
                );
              })}
            </div>
          </section>
        </aside>

        <section data-testid="projects-workbench" className="space-y-6">
          {selectedProject ? (
            <section
              data-testid="project-companion-card"
              className="rounded-[24px] border border-border/70 bg-surface/92 p-4 shadow-[0_28px_56px_-42px_rgba(15,23,35,0.24)] backdrop-blur-xl sm:rounded-[28px] lg:p-5"
            >
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-primary">项目工作区</div>
                  <h2 className="mt-1.5 text-xl font-semibold tracking-[-0.04em] text-ink">{selectedProject.name}</h2>
                  {selectedProject.description ? (
                    <p className="mt-1.5 max-w-3xl text-sm leading-6 text-ink-secondary">{selectedProject.description}</p>
                  ) : null}
                  <div className="mt-2.5 flex flex-wrap gap-2">
                    <Badge>{serverLabelOf(selectedProject.workspace_server_id, servers)}</Badge>
                    {projectWorkspacePath(selectedProject) ? <Badge>{projectWorkspacePath(selectedProject)}</Badge> : null}
                  </div>
                </div>

                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" icon={<RefreshCw className="h-4 w-4" />} loading={refreshing} onClick={() => void refreshCurrentProject(true)}>
                    刷新
                  </Button>
                  <Button icon={<Play className="h-4 w-4" />} onClick={() => setWorkflowDrawerOpen(true)}>
                    启动工作流
                  </Button>
                  <Button variant="secondary" icon={<Play className="h-4 w-4" />} onClick={handleOpenAssistant}>
                    打开助手
                  </Button>
                  <Button variant="secondary" icon={<Pencil className="h-4 w-4" />} onClick={() => openEditProject(selectedProject)}>
                    编辑
                  </Button>
                  <Button variant="danger" icon={<Trash2 className="h-4 w-4" />} loading={deletingProjectId === selectedProject.id} onClick={() => setDeleteProjectTarget(selectedProject)}>
                    删除项目
                  </Button>
                </div>
              </div>

              <div className="mt-4 grid gap-2 sm:grid-cols-3">
                {projectStats.map((item) => (
                  <div key={item.label} className="rounded-[20px] border border-border/60 bg-page/72 px-3.5 py-3">
                    <div className="text-[11px] uppercase tracking-[0.12em] text-ink-tertiary">{item.label}</div>
                    <div className="mt-1.5 text-xl font-semibold tracking-[-0.03em] text-ink">{item.value}</div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {selectedProject && runs.length > 0 ? (
            <section className="rounded-[24px] border border-border/70 bg-surface/88 p-4 shadow-[0_24px_48px_-36px_rgba(15,23,35,0.18)] backdrop-blur-xl sm:rounded-[28px]">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div className="text-sm font-semibold text-ink">运行列表</div>
                <div className="text-xs text-ink-tertiary">{runs.length} 条记录</div>
              </div>
              <div className="mt-3 grid gap-2 xl:grid-cols-2">
                {runs.map((run) => {
                  const active = run.id === selectedRunId;
                  return (
                    <button
                      key={run.id}
                      type="button"
                      onClick={() => {
                        setSelectedRunId(run.id);
                        setSearchParams((current) => {
                          const next = new URLSearchParams(current);
                          next.set("run", run.id);
                          return next;
                        }, { replace: true });
                      }}
                      className={cn(
                        "rounded-[22px] border px-3.5 py-3 text-left transition",
                        active
                          ? "border-primary/25 bg-primary/8"
                          : "border-border/60 bg-page/72 hover:border-primary/15 hover:bg-surface",
                      )}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-semibold text-ink">
                            {run.title || localizeLabel(run.workflow_label)}
                          </div>
                          <div className="mt-1 truncate text-xs text-ink-secondary">
                            {truncate(run.summary || run.prompt || "暂无摘要", 88)}
                          </div>
                        </div>
                        <Badge variant={statusVariant(run.status)}>{runStatusLabel(run.status, run.active_phase)}</Badge>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-ink-tertiary">
                        <span>{localizeLabel(run.target_label) || "默认目标"}</span>
                        <span>{formatDateTime(run.updated_at)}</span>
                      </div>
                    </button>
                  );
                })}
              </div>
            </section>
          ) : null}

          {loadingContext && !activeWorkspaceContext ? (
            <section className="rounded-[28px] border border-border/70 bg-surface/92 px-5 py-10 shadow-[0_22px_42px_-34px_rgba(15,23,35,0.18)]">
              <div className="flex items-center justify-center gap-2 text-sm text-ink-secondary">
                <Loader2 className="h-4 w-4 animate-spin" />
                加载运行上下文...
              </div>
            </section>
          ) : null}

          {!loadingContext && selectedProject && runs.length === 0 ? (
            <section className="rounded-[28px] border border-border/70 bg-surface/92 shadow-[0_22px_42px_-34px_rgba(15,23,35,0.18)]">
              <Empty
                className="py-16"
                icon={<Play className="h-12 w-12" />}
                title="这个项目还没有运行记录"
                action={
                  <Button icon={<ArrowRight className="h-4 w-4" />} onClick={() => setWorkflowDrawerOpen(true)}>
                    启动工作流
                  </Button>
                }
              />
            </section>
          ) : null}

          {selectedRun ? (
            <>
              <section className="rounded-[28px] border border-border/70 bg-surface/92 p-4 shadow-[0_28px_56px_-40px_rgba(15,23,35,0.22)] backdrop-blur-xl lg:p-5">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={statusVariant(selectedRun.status)}>{runStatusLabel(selectedRun.status, selectedRun.active_phase)}</Badge>
                      <Badge>{localizeLabel(selectedRun.workflow_label)}</Badge>
                      <Badge>{localizeLabel(selectedRun.target_label) || "默认目标"}</Badge>
                    </div>
                    <h3 className="mt-2 text-xl font-semibold tracking-[-0.04em] text-ink">
                      {selectedRun.title || localizeLabel(selectedRun.workflow_label)}
                    </h3>
                    <p className="mt-1.5 max-w-4xl text-sm leading-6 text-ink-secondary">
                      {selectedRun.summary || selectedRun.prompt || "暂无摘要"}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Badge>{runEngineSummary(selectedRun.executor_engine_label, selectedRun.executor_model)}</Badge>
                      <Badge>{runEngineSummary(selectedRun.reviewer_engine_label, selectedRun.reviewer_model)}</Badge>
                      <Badge>{serverLabelOf(selectedRun.workspace_server_id || selectedTarget?.workspace_server_id || selectedProject?.workspace_server_id, servers)}</Badge>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="secondary"
                      icon={<RotateCcw className="h-4 w-4" />}
                      loading={retryingRunId === selectedRun.id}
                      onClick={() => void handleRetryRun(selectedRun)}
                    >
                      重试
                    </Button>
                    <Button
                      variant="secondary"
                      icon={<FolderOpen className="h-4 w-4" />}
                      loading={revealingPath === (selectedRun.run_directory || selectedRunWorkspaceRoot)}
                      onClick={() => void handleRevealPath(selectedRun.run_directory || selectedRunWorkspaceRoot, selectedRunServerId)}
                    >
                      打开目录
                    </Button>
                    <Button
                      variant="danger"
                      icon={<Trash2 className="h-4 w-4" />}
                      loading={deletingRunId === selectedRun.id}
                      onClick={() => setDeleteRunTarget(selectedRun)}
                    >
                      删除
                    </Button>
                  </div>
                </div>

                <div className="mt-4 grid gap-2 md:grid-cols-3">
                  {runStats.map((item) => (
                    <div key={item.label} className="rounded-[20px] border border-border/60 bg-page/72 px-3.5 py-3">
                      <div className="text-[11px] uppercase tracking-[0.12em] text-ink-tertiary">{item.label}</div>
                      <div className="mt-1.5 text-xl font-semibold tracking-[-0.03em] text-ink">{item.value}</div>
                    </div>
                  ))}
                </div>

                <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr),340px]">
                  <div className="rounded-[24px] border border-border/60 bg-page/72 p-4">
                    <div className="text-sm font-semibold text-ink">任务说明</div>
                    <div className="mt-3 whitespace-pre-wrap text-sm leading-6 text-ink-secondary">
                      {selectedRun.prompt || "暂无说明"}
                    </div>
                    <div className="mt-4 grid gap-2 text-xs text-ink-tertiary">
                      <div>创建于 {formatDateTime(selectedRun.created_at)}</div>
                      <div>更新于 {formatDateTime(selectedRun.updated_at)}</div>
                      {selectedRun.finished_at ? <div>结束于 {formatDateTime(selectedRun.finished_at)}</div> : null}
                      {selectedRunWorkspaceRoot ? <div>工作区 {selectedRunWorkspaceRoot}</div> : null}
                    </div>
                  </div>

                  <div className="rounded-[24px] border border-border/60 bg-surface p-4">
                    <div className="text-sm font-semibold text-ink">后续流程</div>
                    <div className="mt-3 space-y-3">
                      <div className="space-y-1.5">
                        <label className="block text-sm font-medium text-ink">下一步</label>
                        <select
                          value={selectedActionPresetId}
                          onChange={(event) => setSelectedActionPresetId(event.target.value)}
                          className="h-10 w-full rounded-lg border border-border bg-surface px-3.5 text-sm text-ink outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
                        >
                          {actionOptions.map((item) => (
                            <option key={item.id} value={item.id}>{item.label}</option>
                          ))}
                        </select>
                      </div>
                      <Textarea
                        label="补充说明"
                        rows={5}
                        value={runActionPrompt}
                        onChange={(event) => setRunActionPrompt(event.target.value)}
                        placeholder="输入补充说明"
                      />
                      <Button
                        className="w-full"
                        loading={submittingAction}
                        icon={<Play className="h-4 w-4" />}
                        onClick={() => void handleSubmitRunAction()}
                      >
                        启动后续流程
                      </Button>
                    </div>
                  </div>
                </div>
              </section>

              {pendingCheckpoint ? (
                <section className="rounded-[28px] border border-amber-200 bg-amber-50/85 p-5 shadow-[0_20px_36px_-28px_rgba(120,53,15,0.18)]">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-amber-700">{checkpointTitle(pendingCheckpoint)}</div>
                      <div className="mt-2 text-lg font-semibold text-amber-950">{pendingCheckpoint.message || "等待人工确认"}</div>
                      {pendingCheckpoint.stage_summary ? (
                        <div className="mt-3 rounded-2xl bg-amber-100/85 px-3 py-2 text-sm text-amber-900">
                          {pendingCheckpoint.stage_summary}
                        </div>
                      ) : null}
                    </div>
                    <Badge variant="warning">待审批</Badge>
                  </div>
                  <div className="mt-4">
                    <Textarea
                      label="审批备注"
                      rows={3}
                      value={checkpointComment}
                      onChange={(event) => setCheckpointComment(event.target.value)}
                      placeholder="可以填写批准说明或拒绝原因"
                    />
                  </div>
                  <div className="mt-4 flex flex-wrap justify-end gap-2">
                    <Button
                      variant="secondary"
                      loading={respondingCheckpoint === "reject"}
                      onClick={() => void handleCheckpointResponse("reject")}
                    >
                      拒绝继续
                    </Button>
                    <Button
                      loading={respondingCheckpoint === "approve"}
                      onClick={() => void handleCheckpointResponse("approve")}
                    >
                      批准并继续
                    </Button>
                  </div>
                </section>
              ) : null}

              <div className="grid gap-6 2xl:grid-cols-[minmax(0,1fr),360px]">
                <div className="space-y-6">
                  <section className="rounded-[28px] border border-border/70 bg-surface/90 p-5 shadow-[0_22px_42px_-34px_rgba(15,23,35,0.18)]">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-semibold text-ink">阶段轨迹</div>
                      <div className="text-xs text-ink-tertiary">{selectedRun.stage_trace?.length || 0} 个阶段</div>
                    </div>
                    <div className="mt-4 space-y-3">
                      {(selectedRun.stage_trace || []).length === 0 ? (
                        <div className="rounded-2xl border border-border/60 bg-page/72 px-4 py-4 text-sm text-ink-secondary">
                          暂无阶段轨迹
                        </div>
                      ) : (
                        (selectedRun.stage_trace || []).map((stage) => (
                          <StageTraceCard
                            key={`${stage.stage_id}-${stage.started_at || stage.completed_at || stage.label}`}
                            stage={stage}
                            engineProfiles={engineProfiles}
                          />
                        ))
                      )}
                    </div>
                  </section>

                  {(selectedRun.actions || []).length > 0 ? (
                    <section className="rounded-[28px] border border-border/70 bg-surface/90 p-5 shadow-[0_22px_42px_-34px_rgba(15,23,35,0.18)]">
                      <div className="flex items-center justify-between gap-3">
                        <div className="text-sm font-semibold text-ink">动作历史</div>
                        <div className="text-xs text-ink-tertiary">{selectedRun.actions?.length || 0} 条</div>
                      </div>
                      <div className="mt-4 space-y-3">
                        {(selectedRun.actions || []).map((action) => (
                          <div key={action.id} className="rounded-[22px] border border-border/60 bg-page/72 px-4 py-3">
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <div className="text-sm font-semibold text-ink">{action.action_label || actionTypeLabel(action.action_type)}</div>
                                <div className="mt-1 text-xs text-ink-secondary">{truncate(action.summary || action.prompt || "暂无说明", 120)}</div>
                              </div>
                              <Badge variant={statusVariant(action.status)}>{runStatusLabel(action.status, action.active_phase)}</Badge>
                            </div>
                            <div className="mt-3 flex flex-wrap gap-3 text-[11px] text-ink-tertiary">
                              <span>{formatDateTime(action.updated_at)}</span>
                              {action.result_path ? <span>结果 {action.result_path}</span> : null}
                              {action.log_path ? <span>日志 {action.log_path}</span> : null}
                            </div>
                            {typeof action.metadata?.spawned_run_id === "string" && action.metadata.spawned_run_id.trim() ? (
                              <div className="mt-3 flex justify-end">
                                <Button
                                  size="sm"
                                  variant="secondary"
                                  icon={<ArrowRight className="h-4 w-4" />}
                                  onClick={() => setSelectedRunId(action.metadata?.spawned_run_id as string)}
                                >
                                  打开子运行
                                </Button>
                              </div>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    </section>
                  ) : null}

                  {outputMarkdown ? (
                    <section className="rounded-[28px] border border-border/70 bg-surface/90 p-5 shadow-[0_22px_42px_-34px_rgba(15,23,35,0.18)]">
                      <div className="flex items-center justify-between gap-3">
                        <div className="text-sm font-semibold text-ink">正文输出</div>
                        <Badge variant="info">Markdown</Badge>
                      </div>
                      <div className="mt-4 max-h-[520px] overflow-y-auto rounded-[22px] border border-border/60 bg-page/72 px-5 py-4">
                        <div className="prose-custom text-sm leading-7 text-ink">
                          <Suspense fallback={<div className="h-4 animate-pulse rounded bg-surface" />}>
                            <Markdown>{outputMarkdown}</Markdown>
                          </Suspense>
                        </div>
                      </div>
                    </section>
                  ) : null}

                  <section className="rounded-[28px] border border-border/70 bg-surface/90 p-5 shadow-[0_22px_42px_-34px_rgba(15,23,35,0.18)]">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-semibold text-ink">最近日志</div>
                      <div className="text-xs text-ink-tertiary">{selectedRunLogs.length} 行</div>
                    </div>
                    <div className="theme-console-block mt-4 rounded-[22px] border border-border/60 px-4 py-4">
                      {selectedRunLogs.length > 0 ? (
                        <pre className="theme-console-fg max-h-[360px] overflow-y-auto whitespace-pre-wrap break-words text-xs leading-6">
                          {selectedRunLogs.join("\n")}
                        </pre>
                      ) : (
                        <div className="theme-console-muted text-sm">暂无日志</div>
                      )}
                    </div>
                  </section>
                </div>

                <div className="space-y-6">
                  <section className="rounded-[28px] border border-border/70 bg-surface/90 p-5 shadow-[0_22px_42px_-34px_rgba(15,23,35,0.18)]">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-semibold text-ink">论文索引</div>
                      <div className="text-xs text-ink-tertiary">{paperIndex.length} 篇</div>
                    </div>
                    <div className="mt-4 space-y-3">
                      {paperIndex.length === 0 ? (
                        <div className="rounded-[22px] border border-border/60 bg-page/72 px-4 py-4 text-sm text-ink-secondary">
                          当前运行还没有显式论文索引。
                        </div>
                      ) : (
                        paperIndex.map((ref) => {
                          const assetLabels = paperAssetLabels(ref);
                          return (
                            <div key={`${ref.ref_id}:${ref.paper_id || ref.external_id || ref.title}`} className="rounded-[22px] border border-border/60 bg-page/72 px-4 py-3">
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                  <div className="truncate text-sm font-semibold text-ink">{ref.title || "未命名论文"}</div>
                                  <div className="mt-1 flex flex-wrap gap-1.5 text-[11px] text-ink-tertiary">
                                    <span>{ref.ref_id}</span>
                                    <span>{paperSourceLabel(ref.source)}</span>
                                    {paperRefYear(ref) ? <span>{paperRefYear(ref)}</span> : null}
                                    {ref.paper_id ? <span>{ref.paper_id.slice(0, 8)}</span> : null}
                                  </div>
                                  {assetLabels.length > 0 ? (
                                    <div className="mt-2 flex flex-wrap gap-1.5">
                                      {assetLabels.map((label) => (
                                        <span key={label} className="rounded-full border border-border/70 bg-white/70 px-2 py-0.5 text-[11px] text-ink-secondary">
                                          {label}
                                        </span>
                                      ))}
                                    </div>
                                  ) : null}
                                </div>
                                {ref.paper_id ? (
                                  <Button
                                    size="sm"
                                    variant="secondary"
                                    icon={<BookOpen className="h-4 w-4" />}
                                    onClick={() => navigate(`/papers/${ref.paper_id}`)}
                                  >
                                    查看
                                  </Button>
                                ) : null}
                              </div>
                            </div>
                          );
                        })
                      )}
                    </div>
                  </section>

                  <section className="rounded-[28px] border border-border/70 bg-surface/90 p-5 shadow-[0_22px_42px_-34px_rgba(15,23,35,0.18)]">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-semibold text-ink">补充论文候选</div>
                      <div className="text-xs text-ink-tertiary">{literatureCandidates.length} 条</div>
                    </div>
                    <div className="mt-4 space-y-3">
                      {literatureCandidates.length === 0 ? (
                        <div className="rounded-[22px] border border-border/60 bg-page/72 px-4 py-4 text-sm text-ink-secondary">
                          工作流检索到的库内匹配或外部论文会显示在这里。
                        </div>
                      ) : (
                        literatureCandidates.map((ref) => {
                          const actionLabel = paperRefActionLabel(ref);
                          const disabled = !actionLabel || actionLabel.startsWith("已") || importingCandidateRefId === ref.ref_id || ref.status === "failed";
                          const assetLabels = paperAssetLabels(ref);
                          return (
                            <div key={`${ref.ref_id}:${ref.paper_id || ref.external_id || ref.title}`} className="rounded-[22px] border border-border/60 bg-page/72 px-4 py-3">
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                  <div className="truncate text-sm font-semibold text-ink">{ref.title || "未命名候选"}</div>
                                  <div className="mt-1 flex flex-wrap gap-1.5 text-[11px] text-ink-tertiary">
                                    <span>{ref.ref_id}</span>
                                    <span>{paperSourceLabel(ref.source)}</span>
                                    {paperRefYear(ref) ? <span>{paperRefYear(ref)}</span> : null}
                                    {typeof ref.citation_count === "number" ? <span>{ref.citation_count} cites</span> : null}
                                  </div>
                                  {ref.match_reason ? (
                                    <div className="mt-2 line-clamp-2 text-xs leading-5 text-ink-secondary">{ref.match_reason}</div>
                                  ) : null}
                                  {assetLabels.length > 0 ? (
                                    <div className="mt-2 flex flex-wrap gap-1.5">
                                      {assetLabels.map((label) => (
                                        <span key={label} className="rounded-full border border-border/70 bg-white/70 px-2 py-0.5 text-[11px] text-ink-secondary">
                                          {label}
                                        </span>
                                      ))}
                                    </div>
                                  ) : null}
                                  {ref.error ? (
                                    <div className="mt-2 text-xs text-error">{ref.error}</div>
                                  ) : null}
                                </div>
                                <Badge variant={ref.status === "failed" ? "error" : ref.project_linked ? "success" : "default"}>
                                  {ref.project_linked ? "已关联" : ref.status || "candidate"}
                                </Badge>
                              </div>
                              <div className="mt-3 flex flex-wrap gap-2">
                                {ref.paper_id ? (
                                  <Button
                                    size="sm"
                                    variant="secondary"
                                    icon={<BookOpen className="h-4 w-4" />}
                                    onClick={() => navigate(`/papers/${ref.paper_id}`)}
                                  >
                                    查看论文
                                  </Button>
                                ) : null}
                                {actionLabel ? (
                                  <Button
                                    size="sm"
                                    variant="secondary"
                                    icon={ref.paper_id ? <Link2 className="h-4 w-4" /> : <Download className="h-4 w-4" />}
                                    loading={importingCandidateRefId === ref.ref_id}
                                    disabled={disabled}
                                    onClick={() => void handleImportLiteratureCandidate(ref)}
                                  >
                                    {actionLabel}
                                  </Button>
                                ) : null}
                              </div>
                            </div>
                          );
                        })
                      )}
                    </div>
                  </section>

                  <section className="rounded-[28px] border border-border/70 bg-surface/90 p-5 shadow-[0_22px_42px_-34px_rgba(15,23,35,0.18)]">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-semibold text-ink">产物</div>
                      <div className="text-xs text-ink-tertiary">{selectedArtifacts.length} 个</div>
                    </div>
                    <div className="mt-4 space-y-3">
                      {selectedArtifacts.length === 0 ? (
                        <div className="rounded-[22px] border border-border/60 bg-page/72 px-4 py-4 text-sm text-ink-secondary">
                          暂无产物
                        </div>
                      ) : (
                        selectedArtifacts.map((artifact) => (
                          <div key={`${artifact.kind}:${artifact.path}`} className="rounded-[22px] border border-border/60 bg-page/72 px-4 py-3">
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <div className="truncate text-sm font-semibold text-ink">{fileNameFromPath(artifact.path)}</div>
                                <div className="mt-1 text-xs text-ink-secondary">{artifact.kind}</div>
                              </div>
                              <Badge>{artifact.updated_at ? timeAgo(artifact.updated_at) : "未记录时间"}</Badge>
                            </div>
                            <div className="mt-3 break-all text-xs text-ink-tertiary">{artifact.path}</div>
                            <div className="mt-3 flex flex-wrap gap-2">
                              <Button
                                size="sm"
                                variant="secondary"
                                icon={<FolderOpen className="h-4 w-4" />}
                                loading={revealingPath === artifact.path}
                                onClick={() => void handleRevealPath(artifact.path, selectedRunServerId)}
                              >
                                打开
                              </Button>
                              {isPreviewableArtifact(artifact.path) ? (
                                <Button
                                  size="sm"
                                  variant="secondary"
                                  icon={<Eye className="h-4 w-4" />}
                                  loading={previewLoadingPath === artifact.path}
                                  onClick={() => void handlePreviewArtifact(artifact)}
                                >
                                  预览
                                </Button>
                              ) : null}
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </section>

                  <section className="rounded-[28px] border border-border/70 bg-surface/90 p-5 shadow-[0_22px_42px_-34px_rgba(15,23,35,0.18)]">
                    <div className="text-sm font-semibold text-ink">模型绑定</div>
                    <div className="mt-4 space-y-3">
                      <BindingCard label="执行模型" value={runEngineSummary(selectedRun.executor_engine_label, selectedRun.executor_model)} />
                      <BindingCard label="评审模型" value={runEngineSummary(selectedRun.reviewer_engine_label, selectedRun.reviewer_model)} />
                      <BindingCard label="工作区" value={selectedRunWorkspaceRoot || "未记录"} />
                      <BindingCard label="运行目录" value={selectedRun.run_directory || "未记录"} />
                    </div>
                  </section>
                </div>
              </div>
            </>
          ) : null}
        </section>
      </div>

      {selectedProject ? (
        <Drawer
          open={workflowDrawerOpen}
          onClose={() => setWorkflowDrawerOpen(false)}
          title="启动项目工作流"
          width="lg"
        >
          <ResearchWorkflowLauncher
            initialProjectId={selectedProject.id}
            workspacePath={targetWorkspacePath(assistantTarget) || projectWorkspacePath(selectedProject)}
            workspaceTitle={selectedProject.name}
            workspaceServerId={normalizeServerId(assistantTarget?.workspace_server_id || selectedProject.workspace_server_id)}
            initialPaperIds={(selectedProject.papers || []).map((paper) => paper.id)}
            compact
            surface="drawer"
            onLaunch={handleWorkflowLaunched}
          />
        </Drawer>
      ) : null}

      <Modal
        open={projectModalOpen}
        onClose={() => !savingProject && setProjectModalOpen(false)}
        title={editingProjectId ? "编辑项目" : projectModalMode === "import" ? "导入项目" : "新建项目"}
        maxWidth="lg"
      >
        <div className="space-y-4">
          <Input
            label="显示名称"
            value={projectForm.name}
            onChange={(event) => setProjectForm((current) => ({ ...current, name: event.target.value }))}
            placeholder="输入显示名称"
          />
          <Textarea
            label="项目说明"
            rows={4}
            value={projectForm.description}
            onChange={(event) => setProjectForm((current) => ({ ...current, description: event.target.value }))}
            placeholder="输入项目说明"
          />
          {projectModalMode === "create" ? (
            <>
              <Input
                label="目录名称"
                value={projectForm.dirName}
                onChange={(event) => setProjectForm((current) => ({ ...current, dirName: event.target.value }))}
                placeholder="输入目录名称"
              />
            </>
          ) : (
            <>
              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-ink">工作区</label>
                <select
                  value={projectForm.workspace_server_id}
                  onChange={(event) => setProjectForm((current) => ({ ...current, workspace_server_id: event.target.value }))}
                  className="h-10 w-full rounded-lg border border-border bg-surface px-3.5 text-sm text-ink outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
                >
                  {(servers.length > 0 ? servers : [{ id: "local", label: "本地工作区" }]).map((server) => (
                    <option key={server.id} value={server.id}>{server.label}</option>
                  ))}
                </select>
              </div>
              <Input
                label="目录路径"
                value={projectForm.path}
                onChange={(event) => setProjectForm((current) => ({ ...current, path: event.target.value }))}
                placeholder="本地路径或远程工作目录"
              />
            </>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setProjectModalOpen(false)} disabled={savingProject}>取消</Button>
            <Button
              loading={savingProject}
              disabled={projectModalMode === "create" && !defaultProjectsRoot.trim()}
              onClick={() => void handleSaveProject()}
            >
              保存
            </Button>
          </div>
        </div>
      </Modal>

      <ArtifactPreviewModal preview={artifactPreview} onClose={() => setArtifactPreview(null)} />
      <ConfirmDialog
        open={!!deleteProjectTarget}
        title="删除项目"
        description={deleteProjectTarget ? `删除后无法恢复，确定删除“${deleteProjectTarget.name}”吗？` : undefined}
        variant="danger"
        confirmLabel="删除项目"
        onConfirm={() => void handleDeleteProject()}
        onCancel={() => setDeleteProjectTarget(null)}
      />
      <ConfirmDialog
        open={!!deleteRunTarget}
        title="删除运行"
        description={deleteRunTarget ? `确定删除运行“${deleteRunTarget.title || localizeLabel(deleteRunTarget.workflow_label)}”吗？` : undefined}
        variant="danger"
        confirmLabel="删除运行"
        onConfirm={() => void handleDeleteRun()}
        onCancel={() => setDeleteRunTarget(null)}
      />
    </>
  );
}

function BindingCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[22px] border border-border/60 bg-page/72 px-4 py-3">
      <div className="text-[11px] uppercase tracking-[0.12em] text-ink-tertiary">{label}</div>
      <div className="mt-1 break-all text-sm text-ink">{value}</div>
    </div>
  );
}

function StageTraceCard({
  stage,
  engineProfiles,
}: {
  stage: ProjectWorkflowStageTrace;
  engineProfiles: ProjectEngineProfile[];
}) {
  const profile = engineProfiles.find((item) => item.id === stage.engine_id) || null;
  return (
    <div className="rounded-[22px] border border-border/60 bg-page/72 px-4 py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-sm font-semibold text-ink">{stage.label}</div>
            <Badge variant={statusVariant(stage.status)}>{stageStatusLabel(stage.status)}</Badge>
          </div>
          {stage.description ? (
            <div className="mt-1 text-xs text-ink-secondary">{stage.description}</div>
          ) : null}
        </div>
        {stage.progress_pct != null ? (
          <div className="rounded-full bg-primary/10 px-2.5 py-1 text-[11px] font-medium text-primary">
            {Math.round(stage.progress_pct)}%
          </div>
        ) : null}
      </div>
      <div className="mt-3 grid gap-2 text-xs text-ink-tertiary">
        {stage.message ? <div>{stage.message}</div> : null}
        <div className="flex flex-wrap gap-2">
          {stage.model_role ? <Badge>{stage.model_role === "executor" ? "执行" : "评审"}</Badge> : null}
          {stage.engine_label || profile ? <Badge>{stage.engine_label || engineOptionLabel(profile)}</Badge> : null}
          {stage.model ? <Badge>{stage.model}</Badge> : null}
          {stage.execution_target ? <Badge>{stage.execution_target}</Badge> : null}
        </div>
        <div>
          {stage.started_at ? `开始 ${formatDateTime(stage.started_at)}` : "开始时间未记录"}
          {stage.completed_at ? ` · 完成 ${formatDateTime(stage.completed_at)}` : ""}
        </div>
        {stage.error ? <div className="text-error">{stage.error}</div> : null}
      </div>
    </div>
  );
}
