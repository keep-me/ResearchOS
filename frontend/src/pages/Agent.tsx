/**
 * Agent 对话页面 - 纯渲染壳，核心状态由 AssistantInstanceContext 管理
 * 切换页面不会丢失 SSE 流和进度
 * @author Bamzc
 */
import {
  useState,
  useRef,
  useEffect,
  useCallback,
  useMemo,
  memo,
  lazy,
  Suspense,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { useNavigate, useSearchParams } from "react-router-dom";
import { cn } from "@/lib/utils";
import type { AgentPermissionPreset } from "@/contexts/AgentWorkbenchContext";
import { useToast } from "@/contexts/ToastContext";
import { Modal } from "@/components/ui/Modal";
import { WorkspaceTerminal, type WorkspaceTerminalState } from "@/components/WorkspaceTerminal";
import AssistantWorkflowDrawer from "@/components/assistant/AssistantWorkflowDrawer";
import AssistantWorkflowStrip from "@/components/assistant/AssistantWorkflowStrip";
import type { WorkflowLaunchResult } from "@/components/assistant/ResearchWorkflowLauncher";
import type {
  AssistantWorkspaceDiffResponse,
  AssistantWorkspaceOverview,
  AssistantWorkspaceServer,
  AssistantWorkspaceServerPayload,
  AssistantSkillItem,
  AssistantSessionDiffEntry,
  AssistantSessionRevertInfo,
  AssistantWorkspaceTerminalSessionInfo,
  AgentMode,
  AgentReasoningLevel,
  AssistantExecPolicy,
  ActiveLLMConfig,
  LLMProviderConfig,
  McpRegistryConfig,
  McpRuntimeStatus,
  McpServerInfo,
  Paper,
  Project,
  ProjectRun,
  ProjectWorkflowPreset,
  ProjectWorkflowType,
  Topic,
} from "@/types";

// Markdown 含 katex，懒加载避免首屏拉取大 chunk
const Markdown = lazy(() => import("@/components/Markdown"));
import {
  Send,
  CheckCircle2,
  XCircle,
  Loader2,
  AlertTriangle,
  Sparkles,
  Search,
  Download,
  BookOpen,
  Brain,
  FileText,
  Newspaper,
  ChevronDown,
  ChevronRight,
  Circle,
  Play,
  Square,
  X,
  PanelRightOpen,
  TrendingUp,
  Star,
  Hash,
  Copy,
  Check,
  RotateCcw,
  FolderTree,
  FolderOpen,
  GitBranch,
  Shield,
  Server,
  TerminalSquare,
  RefreshCw,
  Upload,
  Link2,
  Globe,
  BadgeCheck,
  GripVertical,
} from "@/lib/lucide";
import { useAssistantInstance, type ChatItem, type StepItem } from "@/contexts/AssistantInstanceContext";
import {
  assistantExecPolicyApi,
  assistantWorkspaceApi,
  llmConfigApi,
  mcpApi,
  paperApi,
  projectApi,
  resolveApiAssetUrl,
  sessionApi,
  topicApi,
} from "@/services/api";
import { normalizeReasoningDisplay } from "@/features/assistantInstance/reasoningText";
import {
  AGENT_TERMINAL_PANEL_HEIGHT_KEY,
  AGENT_TERMINAL_PANEL_MAX_HEIGHT,
  AGENT_TERMINAL_PANEL_MIN_HEIGHT,
  AGENT_DRAFT_PROMPT_KEY,
  AGENT_WORKSPACE_OVERVIEW_DEPTH,
  AGENT_WORKSPACE_OVERVIEW_MAX_ENTRIES,
  BUILTIN_SLASH_COMMANDS,
  LEGACY_AGENT_DRAFT_PROMPT_KEY,
  MODE_OPTIONS,
  PERMISSION_POLICY_MAP,
  REASONING_LEVEL_OPTIONS,
  WritablePermissionPreset,
  WORKFLOW_RUN_STORAGE_KEY,
  WORKFLOW_SLASH_COMMANDS,
  WORKSPACE_PANEL_TABS,
  buildAssistantWorkspaceCandidates,
  buildTerminalSessionName,
  buildSessionPatchCheckpoints,
  buildSkillSlashTrigger,
  deriveProjectName,
  buildWorkspaceTreeFromOverview,
  extractSlashQuery,
  formatSessionReviewTimestamp,
  getSessionDiffIdentity,
  getSessionDiffStatusLabel,
  getSessionDiffStatusTone,
  getSessionDiffTarget,
  getToolMeta,
  inferPermissionPresetFromPolicy,
  inferWorkflowExecutionCommand,
  isRemoteWorkspaceServer,
  isTerminalProjectRunStatus,
  normalizeAssistantWorkspacePath,
  normalizeComparableServerId,
  normalizeComparableWorkspacePath,
  parseMultilineArgs,
  parseMultilineKeyValue,
  projectMatchesWorkspace,
  readAgentTerminalPanelHeight,
  resolveWorkflowIntentLaunchRequest,
  resolveWorkflowSlashLaunchRequest,
  sortSkillSlashItems,
  truncateText,
  type SessionPatchCheckpoint,
  type SlashCommandItem,
  type WorkspaceFileTreeNode,
  type WorkspacePanelTab,
  type WorkspaceTerminalSession,
} from "@/components/agent/agentPageShared";
import {
  CanvasPanel,
  ChatBlock,
  EmptyState,
  TraceBadge,
  WorkspaceTreeNodeView,
} from "@/components/agent/TraceViews";
import {
  useAgentRuntimeControls,
  useAgentWorkflowDrawerPersistence,
  useAgentWorkspacePanel,
  useMountedPapers,
} from "@/features/agentPage";

/* ========== 主组件 ========== */

export default function Agent() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { toast } = useToast();
  const {
    activeConversationId,
    activeConversation: activeConv,
    activeWorkspace,
    conversationTitle,
    activeSessionId,
    activeSession,
    activeStatus,
    permissionPreset,
    setPermissionPreset,
    agentMode,
    setAgentMode,
    reasoningLevel,
    setReasoningLevel,
    activeSkillIds,
    availableSkills,
    mountedPaperIds,
    mountedPaperTitleMap,
    mountedPaperSummary,
    mountedPrimaryPaperId,
    items, loading, pendingActions, confirmingActions, canvas,
    hasPendingConfirm, setCanvas, sendMessage, handleConfirm, handleReject, handleQuestionReply, stopGeneration,
    createConversationWithRuntime,
    ensureConversation,
    patchActiveConversation,
    setMountedPapers,
    removeMountedPaper,
    clearMountedPapers,
  } = useAssistantInstance();
  const activeId = activeConversationId;
  const isEmpty = items.length === 0;
  const preferredWorkspaceServerId = String(activeWorkspace?.serverId || "").trim() || "local";
  const runtimeControls = useAgentRuntimeControls(permissionPreset);
  const mountedPaperItems = useMountedPapers({ mountedPaperIds, mountedPaperTitleMap, mountedPrimaryPaperId });
  const { width: workspacePanelWidth, setWidth: setWorkspacePanelWidth } = useAgentWorkspacePanel();
  const {
    persistSelection: persistWorkflowLauncherSelection,
    readStoredProjectId: readStoredWorkflowProjectId,
  } = useAgentWorkflowDrawerPersistence();

  const [input, setInput] = useState(() => {
    if (typeof window === "undefined") return "";
    return sessionStorage.getItem(AGENT_DRAFT_PROMPT_KEY)
      || sessionStorage.getItem(LEGACY_AGENT_DRAFT_PROMPT_KEY)
      || "";
  });
  const [policySyncState, setPolicySyncState] = useState<"idle" | "saving" | "error">("idle");
  const [showImportModal, setShowImportModal] = useState(false);
  const [paperQuery, setPaperQuery] = useState("");
  const [paperScope, setPaperScope] = useState("all");
  const [paperLoading, setPaperLoading] = useState(false);
  const [topicLoading, setTopicLoading] = useState(false);
  const [paperItems, setPaperItems] = useState<Paper[]>([]);
  const [topicItems, setTopicItems] = useState<Topic[]>([]);
  const [paperTotal, setPaperTotal] = useState(0);
  const [selectedPaperIds, setSelectedPaperIds] = useState<string[]>([]);
  const [focusedPaperId, setFocusedPaperId] = useState<string | null>(null);
  const [importingPapers, setImportingPapers] = useState(false);
  const [uploadingPdf, setUploadingPdf] = useState(false);
  const [llmConfigs, setLlmConfigs] = useState<LLMProviderConfig[]>([]);
  const [activeLlm, setActiveLlm] = useState<ActiveLLMConfig | null>(null);
  const [modelLoading, setModelLoading] = useState(false);
  const [modelSwitching, setModelSwitching] = useState(false);
  const [showMcpModal, setShowMcpModal] = useState(false);
  const [workflowDrawerOpen, setWorkflowDrawerOpen] = useState(false);
  const [assistantWorkflowRun, setAssistantWorkflowRun] = useState<ProjectRun | null>(null);
  const [workflowRunLoading, setWorkflowRunLoading] = useState(false);
  const [workflowRunError, setWorkflowRunError] = useState<string | null>(null);
  const [mcpRuntime, setMcpRuntime] = useState<McpRuntimeStatus | null>(null);
  const [mcpServers, setMcpServers] = useState<McpServerInfo[]>([]);
  const [mcpLoading, setMcpLoading] = useState(false);
  const [mcpError, setMcpError] = useState<string | null>(null);
  const [mcpConfig, setMcpConfig] = useState<McpRegistryConfig | null>(null);
  const [mcpConfigLoading, setMcpConfigLoading] = useState(false);
  const [mcpConfigSaving, setMcpConfigSaving] = useState(false);
  const [customMcpName, setCustomMcpName] = useState("");
  const [customMcpTransport, setCustomMcpTransport] = useState<"stdio" | "http">("stdio");
  const [customMcpCommand, setCustomMcpCommand] = useState("");
  const [customMcpArgsText, setCustomMcpArgsText] = useState("");
  const [customMcpEnvText, setCustomMcpEnvText] = useState("");
  const [customMcpUrl, setCustomMcpUrl] = useState("");
  const [customMcpHeadersText, setCustomMcpHeadersText] = useState("");
  const [workspaceServers, setWorkspaceServers] = useState<AssistantWorkspaceServer[]>([]);
  const [workspaceServerId, setWorkspaceServerId] = useState(() => preferredWorkspaceServerId);
  const [workspacePanelOpen, setWorkspacePanelOpen] = useState(false);
  const [workspacePanelTab, setWorkspacePanelTab] = useState<WorkspacePanelTab>("files");
  const lastAutoOpenedCanvasRef = useRef("");
  const [terminalDrawerOpen, setTerminalDrawerOpen] = useState(false);
  const [terminalPanelSize, setTerminalPanelSize] = useState(readAgentTerminalPanelHeight);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [workspaceOverview, setWorkspaceOverview] = useState<AssistantWorkspaceOverview | null>(null);
  const [workspaceExpandedDirs, setWorkspaceExpandedDirs] = useState<Record<string, boolean>>({});
  const [activeWorkspaceFile, setActiveWorkspaceFile] = useState<string | null>(null);
  const [workspaceFileContent, setWorkspaceFileContent] = useState("");
  const [workspaceFileDirty, setWorkspaceFileDirty] = useState(false);
  const [workspaceFileLoading, setWorkspaceFileLoading] = useState(false);
  const [workspaceFileSaving, setWorkspaceFileSaving] = useState(false);
  const [workspaceFileError, setWorkspaceFileError] = useState<string | null>(null);
  const [selectedDiffFile, setSelectedDiffFile] = useState<string | null>(null);
  const [gitDiff, setGitDiff] = useState<AssistantWorkspaceDiffResponse | null>(null);
  const [gitDiffLoading, setGitDiffLoading] = useState(false);
  const [sessionReviewLoading, setSessionReviewLoading] = useState(false);
  const [sessionReviewError, setSessionReviewError] = useState<string | null>(null);
  const [sessionDiffEntries, setSessionDiffEntries] = useState<AssistantSessionDiffEntry[]>([]);
  const [sessionPatchCheckpoints, setSessionPatchCheckpoints] = useState<SessionPatchCheckpoint[]>([]);
  const [sessionRevertInfo, setSessionRevertInfo] = useState<AssistantSessionRevertInfo | null>(null);
  const [selectedSessionDiffId, setSelectedSessionDiffId] = useState<string | null>(null);
  const [sessionReviewActionKey, setSessionReviewActionKey] = useState<string | null>(null);
  const [terminalSessions, setTerminalSessions] = useState<WorkspaceTerminalSession[]>([]);
  const [activeTerminalSessionId, setActiveTerminalSessionId] = useState<string>("");
  const [gitBranchName, setGitBranchName] = useState("");
  const [gitCommitMessage, setGitCommitMessage] = useState("");
  const [gitActionKey, setGitActionKey] = useState<string | null>(null);
  const [showWorkspaceServerModal, setShowWorkspaceServerModal] = useState(false);
  const [workspaceServerDraft, setWorkspaceServerDraft] = useState<AssistantWorkspaceServerPayload>({
    label: "",
    host: "",
    port: 22,
    username: "",
    password: "",
    private_key: "",
    passphrase: "",
    workspace_root: "",
    enabled: true,
  });
  const [workspaceServerSaving, setWorkspaceServerSaving] = useState(false);
  const [workspaceServerDeletingId, setWorkspaceServerDeletingId] = useState<string | null>(null);
  const [workspaceServerEditingId, setWorkspaceServerEditingId] = useState<string | null>(null);
  const [workspaceServerProbeLoading, setWorkspaceServerProbeLoading] = useState(false);
  const [workspaceServerProbeResult, setWorkspaceServerProbeResult] = useState<string | null>(null);
  const [workspaceServerProbeSuccess, setWorkspaceServerProbeSuccess] = useState<boolean | null>(null);
  const [mountedPaperPanelOpen, setMountedPaperPanelOpen] = useState(false);
  const [isMobileViewport, setIsMobileViewport] = useState(false);
  const [mobileChromeCollapsed, setMobileChromeCollapsed] = useState(false);
  const [slashQuery, setSlashQuery] = useState("");
  const [slashActiveIndex, setSlashActiveIndex] = useState(0);
  const [selectedSlashCommand, setSelectedSlashCommand] = useState<SlashCommandItem | null>(null);
  const chatViewportRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const modelSelectRef = useRef<HTMLSelectElement>(null);
  const slashMenuRef = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef(true);
  const autoFollowRef = useRef(true);
  const workspaceOverviewRequestSeqRef = useRef(0);
  const gitDiffRequestSeqRef = useRef(0);
  const terminalSessionRequestSeqRef = useRef(0);
  const workspaceOverviewRefreshTimerRef = useRef<number | null>(null);
  const terminalSpawnModeRef = useRef<"append" | "replaceClosed" | null>(null);
  const terminalSessionsRef = useRef<WorkspaceTerminalSession[]>([]);
  const workspacePanelResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const terminalPanelResizeRef = useRef<{ startY: number; startHeight: number } | null>(null);
  const lastViewportScrollTopRef = useRef(0);
  const activeSessionDirectory = normalizeAssistantWorkspacePath(activeSession?.directory || "");
  const assistantSourceDirectory = normalizeAssistantWorkspacePath(activeWorkspace?.path || activeSessionDirectory || "");
  const assistantDirectory = normalizeAssistantWorkspacePath(
    activeWorkspace?.effectivePath || activeWorkspace?.path || activeSessionDirectory || "",
  );
  const terminalWorkspaceCandidates = useMemo(
    () => buildAssistantWorkspaceCandidates({
      primaryPath: assistantDirectory,
      secondaryPath: assistantSourceDirectory,
      sessionPath: activeSessionDirectory,
    }),
    [activeSessionDirectory, assistantDirectory, assistantSourceDirectory],
  );
  const workspaceDirectory = assistantDirectory || "";
  const gitEntries = workspaceOverview?.git?.entries || [];
  const selectedGitEntry = useMemo(
    () => gitEntries.find((entry) => entry.path === selectedDiffFile) || null,
    [gitEntries, selectedDiffFile],
  );
  const stagedGitCount = useMemo(
    () => gitEntries.filter((entry) => entry.code !== "??" && Boolean(entry.index_status.trim())).length,
    [gitEntries],
  );
  const unstagedGitCount = useMemo(
    () => gitEntries.filter((entry) => entry.code === "??" || Boolean(entry.worktree_status.trim())).length,
    [gitEntries],
  );
  const skillSlashCommands = useMemo<SlashCommandItem[]>(
    () => [...availableSkills]
      .filter((item) => String(item.name || "").trim())
      .sort(sortSkillSlashItems)
      .map((item) => {
        const name = String(item.name || "").trim();
        const relativePath = String(item.relative_path || "").trim();
        const sourceLabel = item.source === "project"
          ? "项目 Skill"
          : item.source === "codex"
            ? "Codex Skill"
            : "Agents Skill";
        return {
          id: `skill.${item.id}`,
          trigger: buildSkillSlashTrigger(item) || name.toLowerCase().replace(/\s+/g, "-"),
          description: `${sourceLabel} · ${name}${relativePath ? ` · ${relativePath}` : ""}`,
          insertText: `/skill ${name} `,
          source: "skill" as const,
        };
      }),
    [availableSkills],
  );
  const slashCommands = useMemo<SlashCommandItem[]>(() => {
    return [...BUILTIN_SLASH_COMMANDS, ...WORKFLOW_SLASH_COMMANDS, ...skillSlashCommands];
  }, [skillSlashCommands]);
  const parsedSlashQuery = useMemo(() => extractSlashQuery(input), [input]);
  const slashMenuOpen = useMemo(
    () => !selectedSlashCommand && parsedSlashQuery !== null,
    [parsedSlashQuery, selectedSlashCommand],
  );
  const slashFilteredCommands = useMemo(() => {
    const query = slashQuery.trim().toLowerCase();
    if (!query) return slashCommands;
    return slashCommands.filter((item) =>
      item.trigger.toLowerCase().includes(query)
      || item.description.toLowerCase().includes(query),
    );
  }, [slashCommands, slashQuery]);
  const workspaceFileTree = useMemo(
    () => buildWorkspaceTreeFromOverview(workspaceOverview),
    [workspaceOverview],
  );
  const builtinMcpServer = useMemo(
    () => mcpServers.find((item) => item.builtin || item.name === "researchos") || null,
    [mcpServers],
  );
  const builtinMcpToolCount = useMemo(
    () => Number(
      builtinMcpServer?.tool_count
      ?? builtinMcpServer?.tools?.length
      ?? mcpRuntime?.builtin_tool_count
      ?? 0,
    ) || 0,
    [builtinMcpServer?.tool_count, builtinMcpServer?.tools?.length, mcpRuntime?.builtin_tool_count],
  );
  const builtinMcpAvailable = useMemo(
    () => Boolean(builtinMcpServer?.connected || mcpRuntime?.builtin_ready || builtinMcpToolCount > 0),
    [builtinMcpServer?.connected, mcpRuntime?.builtin_ready, builtinMcpToolCount],
  );
  const configuredCustomMcpServers = useMemo(
    () => mcpServers.filter((item) => !item.builtin),
    [mcpServers],
  );
  const configuredMcpNames = useMemo(() => {
    const raw = mcpConfig?.servers ? Object.keys(mcpConfig.servers).filter((name) => name !== "researchos") : [];
    return raw.sort((a, b) => a.localeCompare(b, "zh-CN"));
  }, [mcpConfig]);
  const activeWorkspaceServer = useMemo(
    () => workspaceServers.find((item) => item.id === workspaceServerId) || null,
    [workspaceServerId, workspaceServers],
  );
  const workspaceServerDisplayLabel = useMemo(() => {
    return activeWorkspace?.serverLabel
      || activeWorkspaceServer?.label
      || (workspaceServerId === "local" ? "本地" : workspaceServerId);
  }, [activeWorkspace?.serverLabel, activeWorkspaceServer?.label, workspaceServerId]);
  const activeWorkspaceRef = useRef(activeWorkspace);
  const activeWorkspaceServerRef = useRef(activeWorkspaceServer);
  const activeIdRef = useRef(activeId);
  const previousReviewStatusRef = useRef(activeStatus.type);
  const recentStepItems = useMemo(() => {
    const steps: StepItem[] = [];
    for (const item of items) {
      if (item.type !== "step_group" || !item.steps || item.steps.length === 0) continue;
      for (const step of item.steps) {
        steps.push(step);
      }
    }
    return steps.slice(-24).reverse();
  }, [items]);
  const selectedSessionDiff = useMemo(
    () => sessionDiffEntries.find((entry) => getSessionDiffIdentity(entry) === selectedSessionDiffId)
      || sessionDiffEntries[0]
      || null,
    [selectedSessionDiffId, sessionDiffEntries],
  );
  const sessionDiffStats = useMemo(
    () => ({
      additions: sessionDiffEntries.reduce((sum, entry) => sum + Number(entry.additions || 0), 0),
      deletions: sessionDiffEntries.reduce((sum, entry) => sum + Number(entry.deletions || 0), 0),
      files: new Set(sessionDiffEntries.map((entry) => getSessionDiffTarget(entry))).size,
    }),
    [sessionDiffEntries],
  );
  const activeTerminalSession = useMemo(
    () => terminalSessions.find((item) => item.id === activeTerminalSessionId) || terminalSessions[0] || null,
    [activeTerminalSessionId, terminalSessions],
  );
  const hasLiveTerminalSession = useMemo(
    () => terminalSessions.some((item) => item.state === "connecting" || item.state === "ready"),
    [terminalSessions],
  );

  useEffect(() => {
    activeWorkspaceRef.current = activeWorkspace;
  }, [activeWorkspace]);

  useEffect(() => {
    activeWorkspaceServerRef.current = activeWorkspaceServer;
  }, [activeWorkspaceServer]);

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  useEffect(() => {
    if (sessionDiffEntries.length === 0) {
      setSelectedSessionDiffId(null);
      return;
    }
    setSelectedSessionDiffId((current) => (
      current && sessionDiffEntries.some((entry) => getSessionDiffIdentity(entry) === current)
        ? current
        : getSessionDiffIdentity(sessionDiffEntries[0])
    ));
  }, [sessionDiffEntries]);
  useEffect(() => {
    terminalSessionsRef.current = terminalSessions;
  }, [terminalSessions]);
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(AGENT_TERMINAL_PANEL_HEIGHT_KEY, String(terminalPanelSize));
  }, [terminalPanelSize]);
  useEffect(() => {
    const handlePointerMove = (event: MouseEvent) => {
      const workspaceResize = workspacePanelResizeRef.current;
      if (workspaceResize) {
        const nextWidth = workspaceResize.startWidth - (event.clientX - workspaceResize.startX);
        setWorkspacePanelWidth(nextWidth);
      }
      const terminalResize = terminalPanelResizeRef.current;
      if (terminalResize) {
        const nextHeight = terminalResize.startHeight - (event.clientY - terminalResize.startY);
        setTerminalPanelSize(
          Math.min(AGENT_TERMINAL_PANEL_MAX_HEIGHT, Math.max(AGENT_TERMINAL_PANEL_MIN_HEIGHT, nextHeight)),
        );
      }
      if (workspaceResize || terminalResize) {
        document.body.style.userSelect = "none";
      }
    };
    const handlePointerUp = () => {
      workspacePanelResizeRef.current = null;
      terminalPanelResizeRef.current = null;
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };
    window.addEventListener("mousemove", handlePointerMove);
    window.addEventListener("mouseup", handlePointerUp);
    return () => {
      window.removeEventListener("mousemove", handlePointerMove);
      window.removeEventListener("mouseup", handlePointerUp);
    };
  }, []);
  const mcpDirectory = workspaceDirectory || ".";
  const persistWorkflowRun = useCallback((run: ProjectRun | null) => {
    if (typeof window === "undefined") return;
    if (!run?.id) {
      localStorage.removeItem(WORKFLOW_RUN_STORAGE_KEY);
      return;
    }
    localStorage.setItem(WORKFLOW_RUN_STORAGE_KEY, JSON.stringify({
      runId: run.id,
      projectId: run.project_id,
    }));
  }, []);
  const syncAssistantWorkflowRun = useCallback(async (
    runId: string,
    options: {
      background?: boolean;
      toastOnError?: boolean;
    } = {},
  ) => {
    const { background = false, toastOnError = !background } = options;
    if (!runId.trim()) return null;
    if (!background) {
      setWorkflowRunLoading(true);
    }
    try {
      const result = await projectApi.getRun(runId);
      setAssistantWorkflowRun(result.item);
      setWorkflowRunError(null);
      persistWorkflowRun(result.item);
      return result.item;
    } catch (error) {
      const message = error instanceof Error ? error.message : "读取流程状态失败";
      setWorkflowRunError(message);
      if (toastOnError) {
        toast("error", message);
      }
      return null;
    } finally {
      if (!background) {
        setWorkflowRunLoading(false);
      }
    }
  }, [persistWorkflowRun, toast]);
  const handleWorkflowLaunched = useCallback((result: WorkflowLaunchResult) => {
    setAssistantWorkflowRun(result.run);
    setWorkflowRunError(null);
    persistWorkflowRun(result.run);
    void syncAssistantWorkflowRun(result.runId, {
      background: true,
      toastOnError: false,
    });
  }, [persistWorkflowRun, syncAssistantWorkflowRun]);
  const handleOpenWorkflowRunDetail = useCallback(() => {
    if (!assistantWorkflowRun?.id || !assistantWorkflowRun.project_id) return;
    navigate(`/projects/${assistantWorkflowRun.project_id}?run=${assistantWorkflowRun.id}`);
  }, [assistantWorkflowRun, navigate]);
  const handleRefreshWorkflowRun = useCallback(() => {
    if (!assistantWorkflowRun?.id) return;
    void syncAssistantWorkflowRun(assistantWorkflowRun.id);
  }, [assistantWorkflowRun, syncAssistantWorkflowRun]);
  const handleDismissWorkflowRun = useCallback(() => {
    setAssistantWorkflowRun(null);
    setWorkflowRunError(null);
    persistWorkflowRun(null);
  }, [persistWorkflowRun]);
  const resolveWorkflowProject = useCallback(async () => {
    if (!workspaceDirectory) {
      throw new Error("当前没有可用工作区，无法启动研究流程");
    }

    const projectList = await projectApi.list();
    const projects = projectList.items || [];
    const preferredProjectId = String(searchParams.get("project") || "").trim();
    const storedProjectId = readStoredWorkflowProjectId();
    const normalizedWorkspacePath = normalizeComparableWorkspacePath(workspaceDirectory);
    const normalizedServerId = normalizeComparableServerId(workspaceServerId === "local" ? null : workspaceServerId);

    for (const candidateId of [preferredProjectId, storedProjectId]) {
      if (!candidateId) continue;
      const matched = projects.find((item) => item.id === candidateId);
      if (matched) return matched;
    }

    const workspaceMatchedProject = projects.find((item) =>
      projectMatchesWorkspace(item, normalizedWorkspacePath, normalizedServerId),
    );
    if (workspaceMatchedProject) {
      return workspaceMatchedProject;
    }

    const inferredName = (
      activeWorkspace?.title
      || (conversationTitle === "新对话" ? "" : conversationTitle)
      || deriveProjectName(workspaceDirectory)
      || "研究项目"
    ).trim();

    const created = await projectApi.create({
      name: inferredName || "研究项目",
      workspace_server_id: normalizedServerId === "local" ? undefined : normalizedServerId,
      workdir: normalizedServerId === "local" ? workspaceDirectory : undefined,
      remote_workdir: normalizedServerId === "local" ? undefined : workspaceDirectory,
    });
    return created.item;
  }, [
    activeWorkspace?.title,
    conversationTitle,
    readStoredWorkflowProjectId,
    searchParams,
    workspaceDirectory,
    workspaceServerId,
  ]);
  const launchWorkflowFromChatCommand = useCallback(async (
    launchRequest: {
      command: SlashCommandItem;
      workflowType: ProjectWorkflowType;
      prompt: string;
      trigger: string;
    },
  ) => {
    const presetResponse = await projectApi.workflowPresets().catch(() => ({ items: [] as ProjectWorkflowPreset[] }));
    const preset = (presetResponse.items || []).find((item) => item.workflow_type === launchRequest.workflowType) || null;
    const workflowPrompt = launchRequest.prompt.trim() || preset?.prefill_prompt?.trim() || launchRequest.command.description;
    if (!workflowPrompt) {
      throw new Error("请补充流程说明后再启动");
    }

    const metadata: Record<string, unknown> = {
      launched_from: "assistant_chat_workflow_slash",
    };
    const entryCommand = String(preset?.entry_command || `/${launchRequest.trigger}`).trim();
    if (entryCommand) {
      metadata.entry_command = entryCommand;
    }

    const requiresExecutionCommand = launchRequest.workflowType === "run_experiment"
      || launchRequest.workflowType === "full_pipeline";
    const executionCommand = requiresExecutionCommand
      ? inferWorkflowExecutionCommand(launchRequest.prompt || workflowPrompt)
      : undefined;
    if (requiresExecutionCommand && !executionCommand) {
      throw new Error(
        launchRequest.workflowType === "full_pipeline"
          ? "Research Pipeline 需要实验命令。请用 `/research-pipeline !python train.py` 后再补充目标说明。"
          : "Experiment Bridge 需要实验命令。请用 `/experiment-bridge !python train.py` 后再补充目标说明。",
      );
    }

    if (launchRequest.workflowType === "rebuttal") {
      const reviewBundle = launchRequest.prompt.trim();
      if (!reviewBundle) {
        throw new Error("Rebuttal 需要附带审稿意见原文。可直接使用 `/rebuttal <reviews>` 启动。");
      }
      metadata.rebuttal_review_bundle = reviewBundle;
      metadata.rebuttal_venue = "ICML";
      metadata.rebuttal_round = "initial";
      metadata.rebuttal_character_limit = 5000;
    }

    setWorkflowRunLoading(true);
    try {
      const project = await resolveWorkflowProject();
      const result = await projectApi.createRun(project.id, {
        workflow_type: launchRequest.workflowType,
        prompt: workflowPrompt,
        paper_ids: mountedPaperIds,
        execution_command: executionCommand,
        auto_proceed: true,
        human_checkpoint_enabled: false,
        metadata,
      });

      persistWorkflowLauncherSelection(project.id, launchRequest.workflowType);
      handleWorkflowLaunched({
        projectId: project.id,
        runId: result.item.id,
        run: result.item,
      });

      const nextSearchParams = new URLSearchParams(searchParams);
      if (nextSearchParams.get("project") !== project.id) {
        nextSearchParams.set("project", project.id);
        navigate({
          pathname: "/assistant",
          search: `?${nextSearchParams.toString()}`,
        }, { replace: true });
      }

      toast("success", result.item.status === "paused" ? "流程已提交，等待确认" : "流程已启动");
    } finally {
      setWorkflowRunLoading(false);
    }
  }, [
    handleWorkflowLaunched,
    mountedPaperIds,
    navigate,
    persistWorkflowLauncherSelection,
    resolveWorkflowProject,
    searchParams,
    toast,
  ]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const media = window.matchMedia("(max-width: 1023px)");
    const sync = () => setIsMobileViewport(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const raw = localStorage.getItem(WORKFLOW_RUN_STORAGE_KEY);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw) as { runId?: string };
      const runId = String(parsed.runId || "").trim();
      if (!runId) {
        localStorage.removeItem(WORKFLOW_RUN_STORAGE_KEY);
        return;
      }
      void syncAssistantWorkflowRun(runId, { toastOnError: false });
    } catch {
      localStorage.removeItem(WORKFLOW_RUN_STORAGE_KEY);
    }
  }, [syncAssistantWorkflowRun]);

  useEffect(() => {
    const runId = assistantWorkflowRun?.id;
    if (!runId || isTerminalProjectRunStatus(assistantWorkflowRun?.status)) return;
    const timer = window.setInterval(() => {
      void syncAssistantWorkflowRun(runId, {
        background: true,
        toastOnError: false,
      });
    }, 4000);
    return () => {
      window.clearInterval(timer);
    };
  }, [assistantWorkflowRun?.id, assistantWorkflowRun?.status, syncAssistantWorkflowRun]);

  const refreshModelOptions = useCallback(async () => {
    setModelLoading(true);
    try {
      const [listResp, activeResp] = await Promise.all([
        llmConfigApi.list(),
        llmConfigApi.active(),
      ]);
      setLlmConfigs(listResp.items || []);
      setActiveLlm(activeResp);
    } catch {
      setLlmConfigs([]);
      setActiveLlm(null);
    } finally {
      setModelLoading(false);
    }
  }, []);

  const refreshWorkspaceServers = useCallback(async () => {
    try {
      const result = await assistantWorkspaceApi.servers();
      const items = result.items || [];
      setWorkspaceServers(items);
      setWorkspaceServerId((current) => (
        items.some((item) => item.id === preferredWorkspaceServerId)
          ? preferredWorkspaceServerId
          : items.some((item) => item.id === current)
            ? current
            : items[0]?.id || "local"
      ));
    } catch {
      setWorkspaceServers([{ id: "local", label: "本地", kind: "native", available: true }]);
      setWorkspaceServerId((current) => (
        preferredWorkspaceServerId === "local"
          ? "local"
          : current === preferredWorkspaceServerId
            ? current
            : "local"
      ));
    }
  }, [preferredWorkspaceServerId]);

  const handleWorkspaceServerChange = useCallback((nextServerId: string) => {
    setWorkspaceServerId(nextServerId);
    const nextServer = workspaceServers.find((item) => item.id === nextServerId) || null;
    patchActiveConversation({
      workspaceServerId: nextServerId === "local" ? null : nextServerId,
      workspaceServerLabel: nextServer?.label || null,
      effectiveWorkspacePath: null,
    });
  }, [patchActiveConversation, workspaceServers]);

  const resetWorkspaceServerDraft = useCallback(() => {
    setWorkspaceServerDraft({
      label: "",
      host: "",
      port: 22,
      username: "",
      password: "",
      private_key: "",
      passphrase: "",
      workspace_root: "",
      enabled: true,
    });
    setWorkspaceServerProbeResult(null);
    setWorkspaceServerProbeSuccess(null);
  }, []);

  const handleCreateWorkspaceServer = useCallback(() => {
    setWorkspaceServerEditingId(null);
    resetWorkspaceServerDraft();
    setShowWorkspaceServerModal(true);
  }, [resetWorkspaceServerDraft]);

  const handleEditWorkspaceServer = useCallback((server: AssistantWorkspaceServer) => {
    if (!isRemoteWorkspaceServer(server)) return;
    setWorkspaceServerEditingId(server.id);
    setWorkspaceServerDraft({
      id: server.id,
      label: server.label || "",
      host: server.host || "",
      port: server.port || 22,
      username: server.username || "",
      password: "",
      private_key: "",
      passphrase: "",
      workspace_root: server.workspace_root || "",
      enabled: server.enabled ?? server.phase !== "disabled",
    });
    setWorkspaceServerProbeResult(null);
    setWorkspaceServerProbeSuccess(null);
    setShowWorkspaceServerModal(true);
  }, []);

  const handleSaveWorkspaceServer = useCallback(async () => {
    const label = (workspaceServerDraft.label || "").trim();
    const host = (workspaceServerDraft.host || "").trim();
    const username = (workspaceServerDraft.username || "").trim();
    if (!label) {
      toast("warning", "请先填写服务器名称");
      return;
    }
    if (!host) {
      toast("warning", "请先填写 SSH 主机");
      return;
    }
    if (!username) {
      toast("warning", "请先填写 SSH 用户名");
      return;
    }
    setWorkspaceServerSaving(true);
    try {
      const payload: AssistantWorkspaceServerPayload = {
        id: workspaceServerDraft.id,
        label,
        host,
        port: workspaceServerDraft.port || 22,
        username,
        password: workspaceServerDraft.password || "",
        private_key: workspaceServerDraft.private_key || "",
        passphrase: workspaceServerDraft.passphrase || "",
        workspace_root: workspaceServerDraft.workspace_root || "",
        enabled: workspaceServerDraft.enabled ?? true,
      };
      if (workspaceServerEditingId) {
        await assistantWorkspaceApi.updateServer(workspaceServerEditingId, payload);
      } else {
        await assistantWorkspaceApi.createServer(payload);
      }
      await refreshWorkspaceServers();
      setShowWorkspaceServerModal(false);
      setWorkspaceServerEditingId(null);
      resetWorkspaceServerDraft();
      toast("success", workspaceServerEditingId ? "SSH 服务器已更新" : "SSH 服务器已新增");
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "保存 SSH 服务器失败");
    } finally {
      setWorkspaceServerSaving(false);
    }
  }, [refreshWorkspaceServers, resetWorkspaceServerDraft, toast, workspaceServerDraft, workspaceServerEditingId]);

  const handleProbeWorkspaceServer = useCallback(async () => {
    const host = (workspaceServerDraft.host || "").trim();
    const username = (workspaceServerDraft.username || "").trim();
    if (!host) {
      toast("warning", "请先填写 SSH 主机");
      return;
    }
    if (!username) {
      toast("warning", "请先填写 SSH 用户名");
      return;
    }
    setWorkspaceServerProbeLoading(true);
    setWorkspaceServerProbeResult(null);
    setWorkspaceServerProbeSuccess(null);
    try {
      const result = await assistantWorkspaceApi.probeSsh({
        host,
        port: workspaceServerDraft.port || 22,
        username,
        password: workspaceServerDraft.password || "",
        private_key: workspaceServerDraft.private_key || "",
        passphrase: workspaceServerDraft.passphrase || "",
        workspace_root: workspaceServerDraft.workspace_root || "",
      });
      setWorkspaceServerProbeSuccess(result.success);
      setWorkspaceServerProbeResult(result.message || (result.success ? "SSH 连接成功" : "SSH 连接失败"));
      toast(result.success ? "success" : "warning", result.message || (result.success ? "SSH 连接成功" : "SSH 连接失败"));
    } catch (error) {
      const message = error instanceof Error ? error.message : "SSH 连接测试失败";
      setWorkspaceServerProbeSuccess(false);
      setWorkspaceServerProbeResult(message);
      toast("error", message);
    } finally {
      setWorkspaceServerProbeLoading(false);
    }
  }, [toast, workspaceServerDraft]);

  const handleDeleteWorkspaceServer = useCallback(async (server: AssistantWorkspaceServer) => {
    if (!server?.id || !isRemoteWorkspaceServer(server)) return;
    setWorkspaceServerDeletingId(server.id);
    try {
      await assistantWorkspaceApi.deleteServer(server.id);
      if (workspaceServerId === server.id) {
        setWorkspaceServerId("local");
        if (activeId) {
          patchActiveConversation({
            workspaceServerId: null,
            workspaceServerLabel: null,
            effectiveWorkspacePath: null,
          });
        }
      }
      await refreshWorkspaceServers();
      toast("success", `已删除 SSH 服务器：${server.label}`);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "删除 SSH 服务器失败");
    } finally {
      setWorkspaceServerDeletingId(null);
    }
  }, [activeId, patchActiveConversation, refreshWorkspaceServers, toast, workspaceServerId]);

  const refreshWorkspaceOverview = useCallback(async (): Promise<AssistantWorkspaceOverview | null> => {
    const requestSeq = ++workspaceOverviewRequestSeqRef.current;
    if (!workspaceDirectory) {
      setWorkspaceOverview(null);
      setWorkspaceError(null);
      return null;
    }
    setWorkspaceLoading(true);
    setWorkspaceError(null);
    try {
      const snapshot = await assistantWorkspaceApi.overview(
        workspaceDirectory,
        AGENT_WORKSPACE_OVERVIEW_DEPTH,
        AGENT_WORKSPACE_OVERVIEW_MAX_ENTRIES,
        workspaceServerId,
      );
      if (requestSeq !== workspaceOverviewRequestSeqRef.current) {
        return null;
      }
      setWorkspaceOverview(snapshot);
      if (activeIdRef.current) {
        const nextServerId = workspaceServerId === "local" ? null : workspaceServerId;
        const currentWorkspace = activeWorkspaceRef.current;
        const nextServerLabel = activeWorkspaceServerRef.current?.label || currentWorkspace?.serverLabel || null;
        const nextWorkspacePath = snapshot.workspace_path || null;
        if (
          currentWorkspace?.effectivePath !== nextWorkspacePath
          || currentWorkspace?.serverId !== nextServerId
          || currentWorkspace?.serverLabel !== nextServerLabel
        ) {
          patchActiveConversation({
            workspaceServerId: nextServerId,
            workspaceServerLabel: nextServerLabel,
            effectiveWorkspacePath: nextWorkspacePath,
          });
        }
      }
      return snapshot;
    } catch (error) {
      if (requestSeq !== workspaceOverviewRequestSeqRef.current) {
        return null;
      }
      const message = error instanceof Error ? error.message : "读取工作区信息失败";
      setWorkspaceError(message);
      setWorkspaceOverview(null);
      return null;
    } finally {
      if (requestSeq === workspaceOverviewRequestSeqRef.current) {
        setWorkspaceLoading(false);
      }
    }
  }, [
    patchActiveConversation,
    workspaceDirectory,
    workspaceServerId,
  ]);

  const clearScheduledWorkspaceOverviewRefresh = useCallback(() => {
    if (workspaceOverviewRefreshTimerRef.current === null) return;
    window.clearTimeout(workspaceOverviewRefreshTimerRef.current);
    workspaceOverviewRefreshTimerRef.current = null;
  }, []);

  const scheduleWorkspaceOverviewRefresh = useCallback((delayMs = 220) => {
    if (!workspaceDirectory) return;
    clearScheduledWorkspaceOverviewRefresh();
    workspaceOverviewRefreshTimerRef.current = window.setTimeout(() => {
      workspaceOverviewRefreshTimerRef.current = null;
      void refreshWorkspaceOverview();
    }, delayMs);
  }, [clearScheduledWorkspaceOverviewRefresh, refreshWorkspaceOverview, workspaceDirectory]);

  useEffect(() => {
    return () => {
      clearScheduledWorkspaceOverviewRefresh();
    };
  }, [clearScheduledWorkspaceOverviewRefresh]);

  const loadGitDiff = useCallback(async (filePath?: string) => {
    if (!workspaceDirectory) return;
    const requestSeq = ++gitDiffRequestSeqRef.current;
    setGitDiffLoading(true);
    try {
      const data = await assistantWorkspaceApi.gitDiff(workspaceDirectory, filePath, workspaceServerId);
      if (requestSeq !== gitDiffRequestSeqRef.current) {
        return;
      }
      setGitDiff(data);
      setSelectedDiffFile(filePath || null);
    } catch (error) {
      if (requestSeq !== gitDiffRequestSeqRef.current) {
        return;
      }
      toast("error", error instanceof Error ? error.message : "读取 Git 变更失败");
      setGitDiff(null);
      setSelectedDiffFile(filePath || null);
    } finally {
      if (requestSeq === gitDiffRequestSeqRef.current) {
        setGitDiffLoading(false);
      }
    }
  }, [toast, workspaceDirectory, workspaceServerId]);


  const loadSessionReview = useCallback(async (options?: { silent?: boolean }) => {
    if (!activeSessionId) {
      setSessionReviewError(null);
      setSessionDiffEntries([]);
      setSessionPatchCheckpoints([]);
      setSessionRevertInfo(null);
      return;
    }
    if (!options?.silent) {
      setSessionReviewLoading(true);
    }
    setSessionReviewError(null);
    try {
      const [diffs, state] = await Promise.all([
        sessionApi.diff(activeSessionId),
        sessionApi.state(activeSessionId),
      ]);
      const messages = state.messages;
      setSessionDiffEntries(diffs);
      setSessionPatchCheckpoints(buildSessionPatchCheckpoints(messages));
      setSessionRevertInfo(state.session?.revert || null);
    } catch (error) {
      const message = error instanceof Error ? error.message : "读取会话改动失败";
      setSessionReviewError(message);
    } finally {
      if (!options?.silent) {
        setSessionReviewLoading(false);
      }
    }
  }, [activeSessionId]);

  const handleToggleWorkspaceDir = useCallback((dirPath: string) => {
    setWorkspaceExpandedDirs((current) => ({
      ...current,
      [dirPath]: !current[dirPath],
    }));
  }, []);

  const handleOpenWorkspaceFile = useCallback(async (relativePath: string) => {
    if (!workspaceDirectory) {
      toast("warning", "当前没有可用工作区");
      return;
    }
    setActiveWorkspaceFile(relativePath);
    setWorkspaceFileLoading(true);
    setWorkspaceFileError(null);
    try {
      const result = await assistantWorkspaceApi.readFile(workspaceDirectory, relativePath, 120000, workspaceServerId);
      setWorkspaceFileContent(result.content || "");
      setWorkspaceFileDirty(false);
    } catch (error) {
      setWorkspaceFileError(error instanceof Error ? error.message : "读取文件失败");
      setWorkspaceFileContent("");
      setWorkspaceFileDirty(false);
    } finally {
      setWorkspaceFileLoading(false);
    }
  }, [toast, workspaceDirectory, workspaceServerId]);

  const refreshGitWorkspaceState = useCallback(async (filePath?: string | null) => {
    await refreshWorkspaceOverview();
    await loadGitDiff(filePath || undefined);
    if (activeWorkspaceFile) {
      await handleOpenWorkspaceFile(activeWorkspaceFile);
    }
  }, [activeWorkspaceFile, handleOpenWorkspaceFile, loadGitDiff, refreshWorkspaceOverview]);

  const handleSaveWorkspaceFile = useCallback(async () => {
    if (!workspaceDirectory || !activeWorkspaceFile) return;
    setWorkspaceFileSaving(true);
    setWorkspaceFileError(null);
    try {
      await assistantWorkspaceApi.writeFile({
        path: workspaceDirectory,
        server_id: workspaceServerId,
        relative_path: activeWorkspaceFile,
        content: workspaceFileContent,
        create_dirs: true,
        overwrite: true,
      });
      setWorkspaceFileDirty(false);
      toast("success", "文件已保存");
      await refreshWorkspaceOverview();
      await loadGitDiff(activeWorkspaceFile);
    } catch (error) {
      setWorkspaceFileError(error instanceof Error ? error.message : "保存文件失败");
      toast("error", error instanceof Error ? error.message : "保存文件失败");
    } finally {
      setWorkspaceFileSaving(false);
    }
  }, [activeWorkspaceFile, loadGitDiff, refreshWorkspaceOverview, toast, workspaceDirectory, workspaceFileContent, workspaceServerId]);

  const handleInitGitRepo = useCallback(async () => {
    if (!workspaceDirectory) {
      toast("warning", "当前没有可用工作区");
      return;
    }
    try {
      await assistantWorkspaceApi.initGit(workspaceDirectory, workspaceServerId);
      toast("success", "Git 仓库初始化完成");
      await refreshGitWorkspaceState();
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "Git 初始化失败");
    }
  }, [refreshGitWorkspaceState, toast, workspaceDirectory, workspaceServerId]);

  const handleRevealWorkspace = useCallback(async () => {
    if (!workspaceDirectory) {
      toast("warning", "当前没有可用工作区");
      return;
    }
    const copyPath = async (path: string) => {
      try {
        await navigator.clipboard.writeText(path);
        toast("info", "已复制工作区路径");
      } catch {
        toast("warning", "当前环境不支持直接打开，且复制路径失败");
      }
    };
    try {
      const result = await assistantWorkspaceApi.reveal(workspaceDirectory, workspaceServerId);
      if (result.opened) {
        toast("success", "已尝试打开工作区目录");
      } else {
        if (result.path) {
          await copyPath(result.path);
        }
        if (result.message) {
          toast("warning", result.message);
        }
      }
    } catch (error) {
      await copyPath(workspaceDirectory);
      toast("error", error instanceof Error ? error.message : "打开目录失败");
    }
  }, [toast, workspaceDirectory, workspaceServerId]);

  const handleRevertSessionCheckpoint = useCallback(async (messageId: string) => {
    if (!activeSessionId) {
      toast("warning", "当前会话还没有可回退的运行时");
      return;
    }
    if (activeStatus.type !== "idle") {
      toast("warning", "请等待当前会话空闲后再执行回退");
      return;
    }
    const normalizedMessageId = String(messageId || "").trim();
    if (!normalizedMessageId) return;
    const checkpoint = sessionPatchCheckpoints.find((item) => item.messageId === normalizedMessageId) || null;
    setSessionReviewActionKey(`revert:${normalizedMessageId}`);
    try {
      const payload = await sessionApi.revert(activeSessionId, normalizedMessageId);
      setSessionRevertInfo(payload.revert || null);
      await refreshWorkspaceOverview();
      if (workspaceDirectory) {
        await loadGitDiff(selectedDiffFile || undefined);
      }
      if (activeWorkspaceFile) {
        await handleOpenWorkspaceFile(activeWorkspaceFile);
      }
      await loadSessionReview({ silent: true });
      toast("success", checkpoint ? `已回退：${checkpoint.label}` : "已回退到所选检查点");
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "会话回退失败");
    } finally {
      setSessionReviewActionKey(null);
    }
  }, [
    activeSessionId,
    activeStatus.type,
    activeWorkspaceFile,
    handleOpenWorkspaceFile,
    loadGitDiff,
    loadSessionReview,
    refreshWorkspaceOverview,
    selectedDiffFile,
    sessionPatchCheckpoints,
    toast,
    workspaceDirectory,
  ]);

  const handleUnrevertSession = useCallback(async () => {
    if (!activeSessionId) {
      toast("warning", "当前没有可恢复的会话");
      return;
    }
    if (activeStatus.type !== "idle") {
      toast("warning", "请等待当前会话空闲后再恢复改动");
      return;
    }
    setSessionReviewActionKey("unrevert");
    try {
      const payload = await sessionApi.unrevert(activeSessionId);
      setSessionRevertInfo(payload.revert || null);
      await refreshWorkspaceOverview();
      if (workspaceDirectory) {
        await loadGitDiff(selectedDiffFile || undefined);
      }
      if (activeWorkspaceFile) {
        await handleOpenWorkspaceFile(activeWorkspaceFile);
      }
      await loadSessionReview({ silent: true });
      toast("success", "已恢复当前回退链路中的改动");
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "恢复改动失败");
    } finally {
      setSessionReviewActionKey(null);
    }
  }, [
    activeSessionId,
    activeStatus.type,
    activeWorkspaceFile,
    handleOpenWorkspaceFile,
    loadGitDiff,
    loadSessionReview,
    refreshWorkspaceOverview,
    selectedDiffFile,
    toast,
    workspaceDirectory,
  ]);

  const closeTerminalSessionsSilently = useCallback((sessionIds: string[]) => {
    const targets = sessionIds.filter(Boolean);
    if (targets.length === 0) return;
    void Promise.allSettled(targets.map((sessionId) => assistantWorkspaceApi.closeTerminalSession(sessionId)));
  }, []);

  const spawnTerminalSession = useCallback(async (mode: "append" | "replaceClosed" = "append") => {
    const liveSession = terminalSessionsRef.current.find((item) => item.state === "connecting" || item.state === "ready");
    if (mode === "replaceClosed" && liveSession) {
      setActiveTerminalSessionId(liveSession.id);
      return;
    }
    if (mode === "replaceClosed" && terminalSpawnModeRef.current) {
      return;
    }
    if (mode === "append" && terminalSpawnModeRef.current === "append") {
      return;
    }
    const candidates = terminalWorkspaceCandidates;
    if (candidates.length === 0) {
      toast("warning", "当前没有可用工作目录");
      return;
    }
    const attemptErrors: string[] = [];
    const requestSeq = terminalSessionRequestSeqRef.current;
    terminalSpawnModeRef.current = mode;
    for (const candidate of candidates) {
      try {
        const result = await assistantWorkspaceApi.createTerminalSession(
          candidate,
          120,
          32,
          workspaceServerId,
        );
        const sessionInfo = result.session;
        if (requestSeq !== terminalSessionRequestSeqRef.current) {
          closeTerminalSessionsSilently([sessionInfo.session_id]);
          terminalSpawnModeRef.current = null;
          return;
        }
        const resolvedPath = normalizeAssistantWorkspacePath(sessionInfo.workspace_path || candidate);
        setTerminalSessions((current) => {
          const baseSessions = mode === "replaceClosed"
            ? current.filter((item) => item.state === "connecting" || item.state === "ready")
            : current;
          return [
            ...baseSessions,
            {
              id: sessionInfo.session_id,
              name: buildTerminalSessionName(baseSessions.length + 1),
              info: sessionInfo,
              state: sessionInfo.closed ? "closed" : "connecting",
              lastExitCode: sessionInfo.exit_code ?? null,
            },
          ];
        });
        setActiveTerminalSessionId(sessionInfo.session_id);
        if (resolvedPath && resolvedPath !== workspaceDirectory) {
          patchActiveConversation({
            effectiveWorkspacePath: resolvedPath,
            workspaceServerId: workspaceServerId === "local" ? null : workspaceServerId,
            workspaceServerLabel: activeWorkspaceServer?.label || activeWorkspace?.serverLabel || null,
          });
        }
        terminalSpawnModeRef.current = null;
        return;
      } catch (error) {
        const message = error instanceof Error ? error.message : "终端创建失败";
        attemptErrors.push(`${candidate}: ${message}`);
      }
    }
    toast("error", attemptErrors[0] || "终端创建失败");
    terminalSpawnModeRef.current = null;
  }, [
    activeConversationId,
    activeWorkspace?.serverLabel,
    activeWorkspaceServer?.label,
    closeTerminalSessionsSilently,
    patchActiveConversation,
    terminalWorkspaceCandidates,
    toast,
    workspaceDirectory,
  ]);

  useEffect(() => {
    if (!terminalSpawnModeRef.current) return;
    if (!hasLiveTerminalSession) return;
    terminalSpawnModeRef.current = null;
  }, [hasLiveTerminalSession]);

  const handleCreateTerminalSession = useCallback(async () => {
    setTerminalDrawerOpen(true);
    await spawnTerminalSession("append");
  }, [spawnTerminalSession]);

  const handleCloseTerminalSession = useCallback(async (sessionId: string) => {
    if (terminalSessions.length <= 1) {
      return;
    }
    try {
      await assistantWorkspaceApi.closeTerminalSession(sessionId);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "关闭终端失败");
      return;
    }
    setTerminalSessions((current) => {
      const next = current.filter((item) => item.id !== sessionId);
      if (!next.some((item) => item.id === activeTerminalSessionId)) {
        setActiveTerminalSessionId(next[0]?.id || "");
      }
      return next;
    });
  }, [activeTerminalSessionId, terminalSessions.length, toast]);

  const handleTerminalSessionStateChange = useCallback((sessionId: string, state: WorkspaceTerminalState) => {
    setTerminalSessions((current) => current.map((item) => (
      item.id === sessionId
        ? { ...item, state }
        : item
    )));
  }, []);

  const handleTerminalSessionInfo = useCallback((sessionId: string, info: AssistantWorkspaceTerminalSessionInfo) => {
    setTerminalSessions((current) => current.map((item) => (
      item.id === sessionId
        ? {
          ...item,
          info,
          lastExitCode: info.exit_code ?? item.lastExitCode ?? null,
        }
        : item
    )));
  }, []);

  const handleTerminalSessionExit = useCallback((sessionId: string, exitCode: number | null) => {
    setTerminalSessions((current) => current.map((item) => (
      item.id === sessionId
        ? { ...item, state: "closed", lastExitCode: exitCode }
        : item
    )));
    scheduleWorkspaceOverviewRefresh(180);
    void loadGitDiff(selectedDiffFile || undefined);
  }, [loadGitDiff, scheduleWorkspaceOverviewRefresh, selectedDiffFile]);

  const handleCreateGitBranch = useCallback(async () => {
    const target = gitBranchName.trim();
    if (!workspaceDirectory) {
      toast("warning", "当前没有可用工作区");
      return;
    }
    if (!target) {
      toast("warning", "请输入分支名称");
      return;
    }
    try {
      const result = await assistantWorkspaceApi.createGitBranch(workspaceDirectory, target, true, workspaceServerId);
      if (!result.ok) {
        toast("error", "分支创建失败，请检查分支名或 Git 状态");
        return;
      }
      setGitBranchName("");
      setWorkspaceOverview((current) => current ? { ...current, git: result.git } : current);
      await refreshGitWorkspaceState(selectedDiffFile || undefined);
      toast("success", result.created ? `已创建并切换到分支：${result.branch}` : `已切换分支：${result.branch}`);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "分支操作失败");
    }
  }, [gitBranchName, refreshGitWorkspaceState, selectedDiffFile, toast, workspaceDirectory, workspaceServerId]);

  const handleStageGit = useCallback(async (filePath?: string) => {
    if (!workspaceDirectory) {
      toast("warning", "当前没有可用工作区");
      return;
    }
    const target = String(filePath || selectedDiffFile || "").trim() || undefined;
    const actionKey = target ? `stage:${target}` : "stage:all";
    setGitActionKey(actionKey);
    try {
      await assistantWorkspaceApi.stageGit(workspaceDirectory, target, workspaceServerId);
      await refreshGitWorkspaceState(target || selectedDiffFile || undefined);
      toast("success", target ? `已暂存：${target}` : "已暂存全部改动");
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "Git 暂存失败");
    } finally {
      setGitActionKey(null);
    }
  }, [refreshGitWorkspaceState, selectedDiffFile, toast, workspaceDirectory, workspaceServerId]);

  const handleUnstageGit = useCallback(async (filePath?: string) => {
    if (!workspaceDirectory) {
      toast("warning", "当前没有可用工作区");
      return;
    }
    const target = String(filePath || selectedDiffFile || "").trim() || undefined;
    const actionKey = target ? `unstage:${target}` : "unstage:all";
    setGitActionKey(actionKey);
    try {
      await assistantWorkspaceApi.unstageGit(workspaceDirectory, target, workspaceServerId);
      await refreshGitWorkspaceState(target || selectedDiffFile || undefined);
      toast("success", target ? `已撤销暂存：${target}` : "已撤销全部暂存");
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "Git 撤销暂存失败");
    } finally {
      setGitActionKey(null);
    }
  }, [refreshGitWorkspaceState, selectedDiffFile, toast, workspaceDirectory, workspaceServerId]);

  const handleDiscardGit = useCallback(async (filePath?: string) => {
    if (!workspaceDirectory) {
      toast("warning", "当前没有可用工作区");
      return;
    }
    const target = String(filePath || selectedDiffFile || "").trim();
    if (!target) {
      toast("warning", "请先选择要丢弃的文件");
      return;
    }
    if (!window.confirm(`确定丢弃 ${target} 的本地改动吗？`)) {
      return;
    }
    setGitActionKey(`discard:${target}`);
    try {
      await assistantWorkspaceApi.discardGit(workspaceDirectory, target, workspaceServerId);
      await refreshGitWorkspaceState(target);
      toast("success", `已丢弃：${target}`);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "Git 丢弃改动失败");
    } finally {
      setGitActionKey(null);
    }
  }, [refreshGitWorkspaceState, selectedDiffFile, toast, workspaceDirectory, workspaceServerId]);

  const handleCommitGit = useCallback(async () => {
    if (!workspaceDirectory) {
      toast("warning", "当前没有可用工作区");
      return;
    }
    const message = gitCommitMessage.trim();
    if (!message) {
      toast("warning", "请输入提交说明");
      return;
    }
    setGitActionKey("commit");
    try {
      await assistantWorkspaceApi.commitGit(workspaceDirectory, message, workspaceServerId);
      setGitCommitMessage("");
      await refreshGitWorkspaceState(selectedDiffFile || undefined);
      toast("success", "提交完成");
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "Git 提交失败");
    } finally {
      setGitActionKey(null);
    }
  }, [gitCommitMessage, refreshGitWorkspaceState, selectedDiffFile, toast, workspaceDirectory, workspaceServerId]);

  const handleSyncGit = useCallback(async (action: "fetch" | "pull" | "push") => {
    if (!workspaceDirectory) {
      toast("warning", "当前没有可用工作区");
      return;
    }
    setGitActionKey(action);
    try {
      await assistantWorkspaceApi.syncGit(workspaceDirectory, action, workspaceServerId);
      await refreshGitWorkspaceState(selectedDiffFile || undefined);
      toast("success", action === "fetch" ? "Fetch 完成" : action === "pull" ? "Pull 完成" : "Push 完成");
    } catch (error) {
      toast("error", error instanceof Error ? error.message : `Git ${action} 失败`);
    } finally {
      setGitActionKey(null);
    }
  }, [refreshGitWorkspaceState, selectedDiffFile, toast, workspaceDirectory, workspaceServerId]);

  const applyPermissionPreset = useCallback(async (nextPreset: WritablePermissionPreset) => {
    const previousPreset = permissionPreset;
    setPermissionPreset(nextPreset);
    setPolicySyncState("saving");
    try {
      await assistantExecPolicyApi.update(PERMISSION_POLICY_MAP[nextPreset]);
      setPolicySyncState("idle");
    } catch (error) {
      setPermissionPreset(previousPreset);
      setPolicySyncState("error");
      toast("error", error instanceof Error ? error.message : "权限配置保存失败");
    }
  }, [permissionPreset, setPermissionPreset, toast]);

  const handleTogglePermissionPreset = useCallback(() => {
    const nextPreset: WritablePermissionPreset =
      permissionPreset === "full_access" ? "confirm" : permissionPreset === "custom" ? "confirm" : "full_access";
    void applyPermissionPreset(nextPreset);
  }, [applyPermissionPreset, permissionPreset]);

  useEffect(() => {
    void refreshModelOptions();
    void refreshWorkspaceServers();
    assistantExecPolicyApi.get()
      .then((policy) => {
        setPermissionPreset(inferPermissionPresetFromPolicy(policy));
      })
      .catch(() => {});
  }, [refreshModelOptions, refreshWorkspaceServers, setPermissionPreset]);

  useEffect(() => {
    setWorkspaceServerId((current) => (current === preferredWorkspaceServerId ? current : preferredWorkspaceServerId));
  }, [activeConversationId, preferredWorkspaceServerId]);

  useEffect(() => {
    if (terminalSessions.length === 0) {
      setActiveTerminalSessionId("");
      return;
    }
    const preferredSession = terminalSessions.find((item) => item.state === "connecting" || item.state === "ready") || terminalSessions[0];
    if (
      !activeTerminalSessionId
      || !terminalSessions.some((item) => item.id === activeTerminalSessionId)
      || terminalSessions.find((item) => item.id === activeTerminalSessionId)?.state === "closed"
    ) {
      setActiveTerminalSessionId(preferredSession.id);
    }
  }, [activeTerminalSessionId, terminalSessions]);

  useEffect(() => {
    setSelectedPaperIds(mountedPaperIds);
    setFocusedPaperId(mountedPrimaryPaperId || mountedPaperIds[0] || null);
    setSelectedSlashCommand(null);
    setMountedPaperPanelOpen(false);
  }, [activeConversationId, mountedPaperIds, mountedPrimaryPaperId]);

  useEffect(() => {
    const staleTerminalIds = terminalSessions.map((item) => item.id);
    workspaceOverviewRequestSeqRef.current += 1;
    gitDiffRequestSeqRef.current += 1;
    terminalSessionRequestSeqRef.current += 1;
    terminalSpawnModeRef.current = null;
    setWorkspaceOverview(null);
    setWorkspaceError(null);
    setWorkspaceExpandedDirs({});
    setActiveWorkspaceFile(null);
    setWorkspaceFileContent("");
    setWorkspaceFileDirty(false);
    setWorkspaceFileError(null);
    setGitDiff(null);
    setSelectedDiffFile(null);
    setSessionReviewError(null);
    setSessionDiffEntries([]);
    setSessionPatchCheckpoints([]);
    setSessionRevertInfo(null);
    setSelectedSessionDiffId(null);
    setSessionReviewActionKey(null);
    setTerminalSessions([]);
    setActiveTerminalSessionId("");
    setTerminalDrawerOpen(false);
    setGitBranchName("");
    setGitCommitMessage("");
    setGitActionKey(null);
    closeTerminalSessionsSilently(staleTerminalIds);
    if (!workspaceDirectory) return;
    if (workspaceServerId !== preferredWorkspaceServerId) return;
    let cancelled = false;
    const bootstrapWorkspacePanel = async () => {
      const snapshot = await refreshWorkspaceOverview();
      if (cancelled || !snapshot) return;
      await loadGitDiff();
    };
    void bootstrapWorkspacePanel();
    return () => {
      cancelled = true;
    };
  }, [
    activeConversationId,
    closeTerminalSessionsSilently,
    loadGitDiff,
    preferredWorkspaceServerId,
    refreshWorkspaceOverview,
    workspaceDirectory,
    workspaceServerId,
  ]);

  useEffect(() => {
    if (!terminalDrawerOpen || !workspaceDirectory) return;
    if (hasLiveTerminalSession) return;
    void spawnTerminalSession("replaceClosed");
  }, [hasLiveTerminalSession, spawnTerminalSession, terminalDrawerOpen, workspaceDirectory]);

  useEffect(() => {
    if (!workspaceDirectory) return;
    if (!workspacePanelOpen) {
      clearScheduledWorkspaceOverviewRefresh();
      return;
    }
    scheduleWorkspaceOverviewRefresh(120);
  }, [clearScheduledWorkspaceOverviewRefresh, scheduleWorkspaceOverviewRefresh, workspaceDirectory, workspacePanelOpen]);

  useEffect(() => {
    if (!activeSessionId) {
      setSessionReviewError(null);
      setSessionDiffEntries([]);
      setSessionPatchCheckpoints([]);
      setSessionRevertInfo(null);
      return;
    }
    if (!workspacePanelOpen || workspacePanelTab !== "review") return;
    void loadSessionReview();
  }, [activeSessionId, loadSessionReview, workspacePanelOpen, workspacePanelTab]);

  useEffect(() => {
    const previousStatus = previousReviewStatusRef.current;
    previousReviewStatusRef.current = activeStatus.type;
    if (!activeSessionId) return;
    if (!workspacePanelOpen || workspacePanelTab !== "review") return;
    if (activeStatus.type === "idle" && previousStatus !== "idle") {
      void loadSessionReview({ silent: true });
    }
  }, [activeSessionId, activeStatus.type, loadSessionReview, workspacePanelOpen, workspacePanelTab]);

  useEffect(() => {
    if (!activeWorkspaceFile) return;
    if (!workspaceOverview?.files?.includes(activeWorkspaceFile)) {
      setActiveWorkspaceFile(null);
      setWorkspaceFileContent("");
      setWorkspaceFileDirty(false);
      setWorkspaceFileError(null);
    }
  }, [activeWorkspaceFile, workspaceOverview?.files]);

  useEffect(() => {
    if (selectedSlashCommand) {
      setSlashQuery("");
      setSlashActiveIndex(0);
      return;
    }
    if (parsedSlashQuery === null) {
      setSlashQuery("");
      setSlashActiveIndex(0);
      return;
    }
    setSlashQuery(parsedSlashQuery);
    setSlashActiveIndex(0);
  }, [parsedSlashQuery, selectedSlashCommand]);

  useEffect(() => {
    if (slashFilteredCommands.length === 0) {
      setSlashActiveIndex(0);
      return;
    }
    if (slashActiveIndex >= slashFilteredCommands.length) {
      setSlashActiveIndex(0);
    }
  }, [slashActiveIndex, slashFilteredCommands.length]);

  useEffect(() => {
    if (!slashMenuOpen) return;
    const container = slashMenuRef.current;
    if (!container) return;
    const node = container.querySelector<HTMLElement>(`[data-slash-index="${slashActiveIndex}"]`);
    if (!node) return;
    node.scrollIntoView({ block: "nearest" });
  }, [slashActiveIndex, slashFilteredCommands.length, slashMenuOpen]);

  useEffect(() => {
    if (!showImportModal) return;
    let cancelled = false;
    setTopicLoading(true);
    topicApi.list(false)
      .then((result) => {
        if (cancelled) return;
        const items = [...(result.items || [])].sort((a, b) => {
          if (a.kind !== b.kind) {
            return a.kind === "folder" ? -1 : 1;
          }
          return a.name.localeCompare(b.name, "zh-CN");
        });
        setTopicItems(items);
      })
      .catch(() => {
        if (!cancelled) {
          setTopicItems([]);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setTopicLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [showImportModal]);

  useEffect(() => {
    if (!showImportModal) return;
    let cancelled = false;
    const selectedTopicId = paperScope.startsWith("topic:") ? paperScope.slice(6) : undefined;
    const selectedFolder = ["favorites", "recent", "unclassified"].includes(paperScope)
      ? paperScope
      : undefined;
    const timer = window.setTimeout(async () => {
      setPaperLoading(true);
      try {
        const result = await paperApi.latest({
          page: 1,
          pageSize: 50,
          search: paperQuery.trim() || undefined,
          folder: selectedFolder,
          topicId: selectedTopicId,
          sortBy: "created_at",
          sortOrder: "desc",
        });
        if (!cancelled) {
          setPaperItems(result.items || []);
          setPaperTotal(result.total || 0);
        }
      } catch {
        if (!cancelled) {
          setPaperItems([]);
          setPaperTotal(0);
        }
      } finally {
        if (!cancelled) {
          setPaperLoading(false);
        }
      }
    }, 220);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [paperQuery, paperScope, showImportModal]);

  const handleModelChange = useCallback(async (configId: string) => {
    if (!configId || configId === activeLlm?.config?.id) return;
    setModelSwitching(true);
    try {
      await llmConfigApi.activate(configId);
      await refreshModelOptions();
      toast("success", "研究助手模型已切换");
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "切换模型失败");
    } finally {
      setModelSwitching(false);
    }
  }, [activeLlm?.config?.id, refreshModelOptions, toast]);

  const handleTogglePaper = useCallback((paperId: string) => {
    setSelectedPaperIds((current) => {
      const exists = current.includes(paperId);
      if (exists) {
        const next = current.filter((item) => item !== paperId);
        setFocusedPaperId((focused) => (focused === paperId ? next[0] || null : focused));
        return next;
      }
      setFocusedPaperId(paperId);
      return [...current, paperId];
    });
  }, []);

  const handleImportSelectedPapers = useCallback(async () => {
    const importIds = Array.from(new Set(selectedPaperIds));
    if (importIds.length === 0) {
      toast("warning", "请先选择至少一篇论文");
      return;
    }
    setImportingPapers(true);
    try {
      const papers = await Promise.all(importIds.map((paperId) => paperApi.detail(paperId)));
      const titleMap = new Map(papers.map((paper) => [paper.id, paper.title]));
      const importedTitles = importIds.map((paperId) => titleMap.get(paperId) || paperId);
      const primaryPaperId = focusedPaperId || importIds[0] || null;
      void ensureConversation();
      setMountedPapers({
        paperIds: importIds,
        paperTitles: importedTitles,
        primaryPaperId,
        conversationTitle: activeConv?.title === "新对话"
          ? (importedTitles.length === 1 ? truncateText(importedTitles[0], 28) : `论文讨论 · ${importIds.length} 篇`)
          : null,
      });
      setShowImportModal(false);
      toast("success", importedTitles.length === 1 ? `已导入：${truncateText(importedTitles[0], 28)}` : `已导入 ${importIds.length} 篇论文`);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "导入论文失败");
    } finally {
      setImportingPapers(false);
    }
  }, [activeConv?.title, ensureConversation, focusedPaperId, selectedPaperIds, setMountedPapers, toast]);

  const handleRemoveMountedPaper = useCallback((paperId: string) => {
    removeMountedPaper(paperId, { focusedPaperId });
  }, [focusedPaperId, removeMountedPaper]);

  const handleClearMountedPapers = useCallback(() => {
    clearMountedPapers();
  }, [clearMountedPapers]);

  const handleUploadFile = useCallback(async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    if (files.length === 0) return;
    if (!workspaceDirectory) {
      toast("warning", "当前聊天未绑定工作区，请先导入或选择目录");
      return;
    }
    setUploadingPdf(true);
    try {
      const uploadedWorkspaceFiles: string[] = [];
      void ensureConversation();
      const targetWorkspacePath = workspaceDirectory;

      for (const file of files) {
        const uploaded = await assistantWorkspaceApi.uploadFile(targetWorkspacePath, file, undefined, workspaceServerId);
        uploadedWorkspaceFiles.push(uploaded.relative_path || file.name);
      }

      if (uploadedWorkspaceFiles.length > 0) {
        try {
          const snapshot = await assistantWorkspaceApi.overview(
            targetWorkspacePath,
            AGENT_WORKSPACE_OVERVIEW_DEPTH,
            AGENT_WORKSPACE_OVERVIEW_MAX_ENTRIES,
            workspaceServerId,
          );
          setWorkspaceOverview(snapshot);
          setWorkspaceError(null);
          patchActiveConversation({
            workspaceServerId: workspaceServerId === "local" ? null : workspaceServerId,
            workspaceServerLabel: activeWorkspaceServer?.label || null,
            effectiveWorkspacePath: snapshot.workspace_path || null,
          });
        } catch {
          // Ignore snapshot refresh errors; upload itself has already succeeded.
        }
      }

      const preview = uploadedWorkspaceFiles.slice(0, 3).join("、");
      const suffix = uploadedWorkspaceFiles.length > 3 ? "…" : "";
      toast(
        "success",
        uploadedWorkspaceFiles.length > 0
          ? `上传完成：${uploadedWorkspaceFiles.length} 个文件${preview ? `（${preview}${suffix}）` : ""}`
          : "上传完成",
      );
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "上传文件失败");
    } finally {
      setUploadingPdf(false);
    }
  }, [
    activeWorkspaceServer?.label,
    ensureConversation,
    patchActiveConversation,
    toast,
    workspaceDirectory,
    workspaceServerId,
  ]);

  const refreshMcpState = useCallback(async () => {
    setMcpLoading(true);
    setMcpError(null);
    setMcpConfigLoading(true);
    try {
      const [runtime, nextServers, nextConfig] = await Promise.all([
        mcpApi.runtime(),
        mcpApi.servers(),
        mcpApi.config(),
      ]);
      setMcpRuntime(runtime);
      setMcpServers(nextServers.items || []);
      setMcpConfig(nextConfig);
    } catch (error) {
      setMcpServers([]);
      setMcpConfig(null);
      setMcpError(error instanceof Error ? error.message : "读取 MCP 状态失败");
    } finally {
      setMcpLoading(false);
      setMcpConfigLoading(false);
    }
  }, []);

  const handleSaveCustomMcp = useCallback(async () => {
    const name = customMcpName.trim();
    if (!name) {
      toast("warning", "请先填写 MCP 名称");
      return;
    }

    if (customMcpTransport === "stdio" && !customMcpCommand.trim()) {
      toast("warning", "请先填写启动命令");
      return;
    }
    if (customMcpTransport === "http" && !customMcpUrl.trim()) {
      toast("warning", "请先填写远程 MCP URL");
      return;
    }

    setMcpConfigSaving(true);
    try {
      const latestConfig = await mcpApi.config().catch(() => mcpConfig || { version: 1, servers: {} });
      const currentServers = latestConfig?.servers ? { ...latestConfig.servers } : {};
      currentServers[name] = customMcpTransport === "stdio"
        ? {
            name,
            label: name,
            transport: "stdio",
            command: customMcpCommand.trim(),
            args: parseMultilineArgs(customMcpArgsText),
            env: parseMultilineKeyValue(customMcpEnvText),
            enabled: true,
            builtin: false,
            timeout_sec: 30,
          }
        : {
            name,
            label: name,
            transport: "http",
            url: customMcpUrl.trim(),
            headers: parseMultilineKeyValue(customMcpHeadersText),
            enabled: true,
            builtin: false,
            timeout_sec: 30,
          };
      const nextConfig: McpRegistryConfig = {
        version: latestConfig?.version || 1,
        servers: currentServers,
      };
      await mcpApi.updateConfig(nextConfig);
      setCustomMcpName("");
      setCustomMcpCommand("");
      setCustomMcpArgsText("");
      setCustomMcpEnvText("");
      setCustomMcpUrl("");
      setCustomMcpHeadersText("");
      toast("success", `MCP 配置已保存：${name}`);
      await refreshMcpState();
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "保存 MCP 配置失败");
    } finally {
      setMcpConfigSaving(false);
    }
  }, [
    customMcpArgsText,
    customMcpCommand,
    customMcpEnvText,
    customMcpHeadersText,
    customMcpName,
    customMcpTransport,
    customMcpUrl,
    mcpConfig,
    refreshMcpState,
    toast,
  ]);

  const handleDeleteCustomMcp = useCallback(async (name: string) => {
    setMcpConfigSaving(true);
    try {
      const latestConfig = await mcpApi.config().catch(() => mcpConfig || { version: 1, servers: {} });
      const currentServers = latestConfig?.servers ? { ...latestConfig.servers } : {};
      delete currentServers[name];
      const nextConfig: McpRegistryConfig = {
        version: latestConfig?.version || 1,
        servers: currentServers,
      };
      await mcpApi.updateConfig(nextConfig);
      toast("success", `已移除 MCP：${name}`);
      await refreshMcpState();
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "移除 MCP 配置失败");
    } finally {
      setMcpConfigSaving(false);
    }
  }, [mcpConfig, refreshMcpState, toast]);

  useEffect(() => {
    if (!showMcpModal) return;
    void refreshMcpState();
  }, [refreshMcpState, showMcpModal]);

  useEffect(() => {
    void refreshMcpState();
  }, [refreshMcpState]);

  const scrollRafRef = useRef<number | null>(null);
  const updateViewportBottomState = useCallback(() => {
    const viewport = chatViewportRef.current;
    if (!viewport) return;
    const distanceFromBottom = viewport.scrollHeight - (viewport.scrollTop + viewport.clientHeight);
    const nearBottom = distanceFromBottom <= 48;
    isAtBottomRef.current = nearBottom;
    autoFollowRef.current = nearBottom;
  }, []);

  const scrollToBottom = useCallback((force = false) => {
    const viewport = chatViewportRef.current;
    if (!viewport) return;
    if (!force && !autoFollowRef.current) return;
    if (scrollRafRef.current) return;
    scrollRafRef.current = requestAnimationFrame(() => {
      scrollRafRef.current = null;
      viewport.scrollTo({ top: viewport.scrollHeight, behavior: force ? "smooth" : "auto" });
    });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [items, loading, scrollToBottom]);

  useEffect(() => {
    updateViewportBottomState();
    const viewport = chatViewportRef.current;
    if (!viewport) return () => undefined;
    const handleViewportChange = () => {
      updateViewportBottomState();
      if (!isMobileViewport) {
        setMobileChromeCollapsed(false);
        lastViewportScrollTopRef.current = viewport.scrollTop;
        return;
      }
      const currentTop = viewport.scrollTop;
      const previousTop = lastViewportScrollTopRef.current;
      const delta = currentTop - previousTop;
      lastViewportScrollTopRef.current = currentTop;

      if (currentTop <= 24 || delta <= -18) {
        setMobileChromeCollapsed(false);
        return;
      }
      if (delta >= 18 && currentTop > 96 && document.activeElement !== textareaRef.current) {
        setMobileChromeCollapsed(true);
      }
    };
    viewport.addEventListener("scroll", handleViewportChange, { passive: true });
    window.addEventListener("resize", handleViewportChange);
    return () => {
      viewport.removeEventListener("scroll", handleViewportChange);
      window.removeEventListener("resize", handleViewportChange);
    };
  }, [isMobileViewport, updateViewportBottomState]);

  useEffect(() => {
    if (!isMobileViewport) {
      setMobileChromeCollapsed(false);
    }
  }, [isMobileViewport]);

  useEffect(() => () => {
    if (scrollRafRef.current) {
      cancelAnimationFrame(scrollRafRef.current);
      scrollRafRef.current = null;
    }
  }, []);

  // 有新的 pendingAction 时强制滚动到底部
  useEffect(() => {
    if (pendingActions.size > 0) {
      isAtBottomRef.current = true;
      autoFollowRef.current = true;
      scrollToBottom(true);
    }
  }, [pendingActions, scrollToBottom]);

  const composerLocked = hasPendingConfirm;

  const executeSlashAction = useCallback((command: SlashCommandItem) => {
    switch (command.action) {
      case "new_chat": {
        const conversationId = createConversationWithRuntime();
        navigate(`/assistant/${conversationId}`);
        break;
      }
      case "open_workspace_panel": {
        setWorkspacePanelOpen(true);
        setWorkspacePanelTab("files");
        if (!workspaceDirectory) {
          toast("warning", "当前没有可用工作区");
        }
        break;
      }
      case "toggle_terminal": {
        setTerminalDrawerOpen((current) => !current);
        if (!workspaceDirectory) {
          toast("warning", "当前没有可用工作区");
        }
        break;
      }
      case "focus_model": {
        modelSelectRef.current?.focus();
        break;
      }
      case "open_mcp": {
        setShowMcpModal(true);
        break;
      }
      case "cycle_agent_mode": {
        setAgentMode(agentMode === "build" ? "plan" : "build");
        break;
      }
      case "init_git": {
        void handleInitGitRepo();
        break;
      }
      case "open_workspace": {
        void handleRevealWorkspace();
        break;
      }
      default:
        break;
    }
  }, [
    createConversationWithRuntime,
    handleInitGitRepo,
    handleRevealWorkspace,
    navigate,
    setAgentMode,
    toast,
    workspaceDirectory,
  ]);

  const handleSlashSelect = useCallback((command: SlashCommandItem) => {
    if (command.action) {
      executeSlashAction(command);
      setInput("");
      setSelectedSlashCommand(null);
      return;
    }
    setSelectedSlashCommand(command);
    setInput("");
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, [executeSlashAction]);

  const handleStartConversation = useCallback(() => {
    const conversationId = createConversationWithRuntime();
    navigate(`/assistant/${conversationId}`);
  }, [createConversationWithRuntime, navigate]);

  const handleSend = useCallback(async (text: string) => {
    const cleaned = text.trim();
    const activeCommand = selectedSlashCommand;
    const commandPrefix = activeCommand
      ? (activeCommand.insertText || `/${activeCommand.trigger} `)
      : "";
    const requestPayload = `${commandPrefix}${cleaned}`.trim();
    if (!requestPayload) return;
    const workflowLaunchRequest = resolveWorkflowSlashLaunchRequest(requestPayload, activeCommand)
      || (!activeCommand ? resolveWorkflowIntentLaunchRequest(requestPayload) : null);
    const displayText = activeCommand
      ? `${activeCommand.description}${cleaned ? `：${cleaned}` : ""}`
      : requestPayload;
    const savedInput = cleaned;
    const savedCommand = activeCommand;
    isAtBottomRef.current = true;
    autoFollowRef.current = true;
    setInput("");
    setSelectedSlashCommand(null);
    try {
      if (workflowLaunchRequest) {
        await launchWorkflowFromChatCommand(workflowLaunchRequest);
        return;
      }
      await sendMessage({
        displayText,
        requestText: requestPayload,
      });
    } catch (error) {
      setInput(savedInput);
      setSelectedSlashCommand(savedCommand);
      toast("error", error instanceof Error ? error.message : "发送失败");
    }
  }, [launchWorkflowFromChatCommand, selectedSlashCommand, sendMessage, toast]);

  const handleConfirmAction = useCallback((actionId: string) => {
    isAtBottomRef.current = true;
    autoFollowRef.current = true;
    handleConfirm(actionId);
  }, [handleConfirm]);

  const handleQuestionSubmit = useCallback((actionId: string, answers: string[][]) => {
    isAtBottomRef.current = true;
    autoFollowRef.current = true;
    handleQuestionReply(actionId, answers);
  }, [handleQuestionReply]);

  const handleOpenArtifact = useCallback((title: string, content: string, isHtml?: boolean) => {
    setCanvas({ title, markdown: content, isHtml });
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (slashMenuOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (slashFilteredCommands.length > 0) {
          setSlashActiveIndex((current) => (current + 1) % slashFilteredCommands.length);
        }
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        if (slashFilteredCommands.length > 0) {
          setSlashActiveIndex((current) => (current - 1 + slashFilteredCommands.length) % slashFilteredCommands.length);
        }
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        if (slashFilteredCommands.length > 0) {
          e.preventDefault();
          const picked = slashFilteredCommands[Math.max(0, Math.min(slashActiveIndex, slashFilteredCommands.length - 1))];
          if (picked) {
            handleSlashSelect(picked);
            return;
          }
        }
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setInput("");
        return;
      }
    }
    if (selectedSlashCommand && e.key === "Backspace" && !input.trim()) {
      e.preventDefault();
      setSelectedSlashCommand(null);
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend(input);
    }
  };

  const resizeTextarea = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "0px";
    const minHeight = isEmpty ? 96 : 64;
    const nextHeight = Math.min(Math.max(el.scrollHeight, minHeight), 200);
    el.style.height = `${nextHeight}px`;
    el.style.overflowY = "hidden";
  }, [isEmpty]);

  useEffect(() => {
    resizeTextarea();
  }, [input, resizeTextarea]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      sessionStorage.removeItem(AGENT_DRAFT_PROMPT_KEY);
      sessionStorage.removeItem(LEGACY_AGENT_DRAFT_PROMPT_KEY);
    }
  }, []);

  useEffect(() => {
    if (input) {
      requestAnimationFrame(() => textareaRef.current?.focus());
    }
  }, [input]);

  useEffect(() => {
    if (!canvas) {
      lastAutoOpenedCanvasRef.current = "";
      setWorkspacePanelTab((current) => (current === "artifact" ? "review" : current));
      return;
    }
    const canvasKey = `${canvas.title}:${canvas.markdown.length}:${canvas.isHtml ? "html" : "md"}`;
    if (lastAutoOpenedCanvasRef.current === canvasKey) {
      return;
    }
    lastAutoOpenedCanvasRef.current = canvasKey;
    setWorkspacePanelTab("artifact");
    setWorkspacePanelOpen(true);
  }, [canvas]);

  const focusedPaperLabel = focusedPaperId
    ? (
      paperItems.find((item) => item.id === focusedPaperId)?.title
      || mountedPaperTitleMap.get(focusedPaperId)
      || focusedPaperId
    )
    : "";
  const selectedTopicId = paperScope.startsWith("topic:") ? paperScope.slice(6) : "";
  const selectedTopic = selectedTopicId
    ? topicItems.find((item) => item.id === selectedTopicId) || null
    : null;
  const paperScopeLabel = selectedTopic
    ? `${selectedTopic.kind === "folder" ? "文件夹" : "自动订阅"} · ${selectedTopic.name}`
    : ({
      all: "全部论文",
      favorites: "仅收藏",
      recent: "近 7 天",
      unclassified: "未归类",
    } as const)[paperScope as "all" | "favorites" | "recent" | "unclassified"] || "全部论文";
  const sessionTitle = conversationTitle;
  const sessionStatusLabel = hasPendingConfirm
    ? "待确认"
    : activeStatus.type === "retry"
      ? `重试 ${activeStatus.attempt}`
      : loading
        ? "生成中"
        : isEmpty
          ? "就绪"
          : "在线";
  const sessionStatusClassName = hasPendingConfirm
    ? "border-warning/20 bg-warning-light text-warning"
    : (loading || activeStatus.type === "retry")
      ? "border-primary/20 bg-primary/8 text-primary"
      : "border-border/70 bg-white text-ink-secondary";
  const workspaceName = workspaceDirectory ? deriveProjectName(workspaceDirectory) : "";
  const sidePanelTabs = useMemo<Array<{ id: WorkspacePanelTab; label: string; icon: typeof FolderTree }>>(
    () => (
      canvas
        ? [{ id: "artifact", label: "工件", icon: PanelRightOpen }, ...WORKSPACE_PANEL_TABS]
        : WORKSPACE_PANEL_TABS
    ),
    [canvas],
  );
  const sidePanelVisible = workspacePanelOpen;
  const terminalPanelHeight = terminalDrawerOpen ? terminalPanelSize : 0;
  const terminalViewportPadding = terminalDrawerOpen ? terminalPanelHeight + 28 : 0;
  const terminalComposerOffset = terminalDrawerOpen ? terminalPanelHeight + 10 : 0;
  const preferredProjectId = searchParams.get("project");
  const closeSidePanel = () => {
    setWorkspacePanelOpen(false);
  };
  const openSidePanel = (tab?: WorkspacePanelTab) => {
    if (tab) {
      setWorkspacePanelTab(tab);
    }
    setWorkspacePanelOpen(true);
  };
  const handleWorkspacePanelResizeStart = (event: ReactMouseEvent<HTMLDivElement>) => {
    workspacePanelResizeRef.current = {
      startX: event.clientX,
      startWidth: workspacePanelWidth,
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };
  const handleTerminalResizeStart = (event: ReactMouseEvent<HTMLDivElement>) => {
    terminalPanelResizeRef.current = {
      startY: event.clientY,
      startHeight: terminalPanelSize,
    };
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
  };

  return (
    <>
    <div className="flex h-[100dvh] min-h-[100dvh] min-w-0 flex-col overflow-hidden bg-page">
      <div className="flex min-w-0 min-h-0 flex-1 flex-col overflow-hidden">
        <div className="flex min-h-0 w-full flex-1">
          <section className="relative flex min-h-0 flex-1 flex-col overflow-hidden overscroll-none">
            <div className={cn(
              "sticky top-0 z-20 shrink-0 border-b border-border bg-white transition-[max-height,opacity,padding,transform] duration-200 ease-out sm:px-4 lg:px-6",
              isMobileViewport ? "px-3 py-2" : "px-3 py-3",
              isMobileViewport && mobileChromeCollapsed && "max-h-0 -translate-y-3 overflow-hidden border-b-0 px-3 py-0 opacity-0",
            )}>
              <div className={cn(
                "flex flex-col sm:flex-row sm:items-center sm:justify-between",
                isMobileViewport ? "gap-2" : "gap-3",
              )}>
                <div className="min-w-0">
                  <div className={cn("flex flex-wrap items-center", isMobileViewport ? "gap-1.5" : "gap-2")}>
                    <span className={cn("truncate font-semibold text-ink", isMobileViewport ? "text-[13px]" : "text-sm")}>{sessionTitle}</span>
                    {workspaceName ? (
                      <span className="inline-flex items-center rounded-full border border-border/70 bg-white px-2 py-0.5 text-[10px] text-ink-secondary">
                        {workspaceName}
                      </span>
                    ) : null}
                    <span className="inline-flex items-center rounded-full border border-border/70 bg-[#fcfbf8] px-2 py-0.5 text-[10px] font-medium text-ink-secondary">
                      {agentMode === "plan" ? "Plan" : "Build"}
                    </span>
                    <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium", sessionStatusClassName)}>
                      {sessionStatusLabel}
                    </span>
                  </div>
                </div>

                <div className={cn(
                  "flex shrink-0 flex-wrap items-center sm:justify-end",
                  isMobileViewport ? "gap-1" : "gap-1.5",
                )}>
                  {loading && (
                    <button
                      type="button"
                      onClick={stopGeneration}
                      className={cn(
                        "inline-flex items-center rounded-full border border-error/20 bg-error-light font-medium text-error transition hover:bg-error/10",
                        isMobileViewport ? "h-7 gap-1 px-2.5 text-[10px]" : "h-8 gap-1.5 px-3 text-[11px]",
                      )}
                    >
                      <Square className="h-3.5 w-3.5" />
                      停止
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setWorkflowDrawerOpen(true)}
                    className={cn(
                      isMobileViewport ? "inline-flex h-7 items-center gap-1 rounded-full border px-2.5 text-[10px] font-medium transition" : "inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[11px] font-medium transition",
                      workflowDrawerOpen
                        ? "border-primary/30 bg-primary/10 text-primary"
                        : "border-border/70 bg-white/72 text-ink-secondary hover:border-primary/20 hover:text-primary",
                    )}
                  >
                    <Play className="h-3.5 w-3.5" />
                    流程
                    {assistantWorkflowRun && !isTerminalProjectRunStatus(assistantWorkflowRun.status) ? (
                      <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                        {assistantWorkflowRun.status === "paused" ? "等待" : "运行中"}
                      </span>
                    ) : null}
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowMcpModal(true)}
                    className={cn(
                      "inline-flex rounded-full border border-border/70 bg-white/72 font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary",
                      isMobileViewport ? "h-7 gap-1 px-2.5 text-[10px]" : "h-8 gap-1.5 px-3 text-[11px]",
                    )}
                  >
                    <Server className="h-3.5 w-3.5 text-primary" />
                    集成
                    {builtinMcpAvailable && builtinMcpToolCount > 0 ? (
                      <span className="rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] text-emerald-700">
                        {builtinMcpToolCount}
                      </span>
                    ) : null}
                  </button>
                  <button
                    type="button"
                    data-testid="assistant-ssh-button"
                    onClick={handleCreateWorkspaceServer}
                    className={cn(
                      "inline-flex rounded-full border border-border/70 bg-white/88 font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary",
                      isMobileViewport ? "h-7 gap-1 px-2.5 text-[10px]" : "h-8 gap-1.5 px-3 text-[11px]",
                    )}
                  >
                    <Server className="h-3.5 w-3.5" />
                    SSH
                  </button>
                  <button
                    type="button"
                    onClick={() => setTerminalDrawerOpen((current) => !current)}
                    className={cn(
                      isMobileViewport ? "inline-flex h-7 items-center gap-1 rounded-full border px-2.5 text-[10px] font-medium transition" : "inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[11px] font-medium transition",
                      terminalDrawerOpen
                        ? "border-primary/30 bg-primary/10 text-primary"
                        : "border-border/70 bg-white/88 text-ink-secondary hover:border-primary/20 hover:text-primary",
                    )}
                  >
                    <TerminalSquare className="h-3.5 w-3.5" />
                    终端
                  </button>
                  {canvas ? (
                    <>
                      <button
                        type="button"
                        onClick={() => {
                          setWorkspacePanelTab("artifact");
                          setWorkspacePanelOpen(true);
                        }}
                        className={cn(
                          "hidden h-8 items-center gap-1.5 rounded-full border px-3 text-[11px] font-medium transition xl:inline-flex",
                          sidePanelVisible && workspacePanelTab === "artifact"
                            ? "border-primary/30 bg-primary/10 text-primary"
                            : "border-border/70 bg-white/88 text-ink-secondary hover:border-primary/20 hover:text-primary",
                        )}
                      >
                        <PanelRightOpen className="h-3.5 w-3.5" />
                        工件
                      </button>
                      <button
                        type="button"
                        onClick={() => setCanvas(null)}
                        className="inline-flex h-8 items-center gap-1.5 rounded-full border border-border/70 bg-white/88 px-3 text-[11px] font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary"
                      >
                        <X className="h-3.5 w-3.5" />
                        关闭工件
                      </button>
                    </>
                  ) : null}
                  <button
                    type="button"
                    data-testid="assistant-workspace-toggle"
                    onClick={() => {
                      if (sidePanelVisible) {
                        closeSidePanel();
                        return;
                      }
                      openSidePanel(workspaceDirectory ? "files" : "review");
                    }}
                    className={cn(
                      "hidden h-8 items-center gap-1.5 rounded-full border px-3 text-[11px] font-medium transition xl:inline-flex",
                      sidePanelVisible
                        ? "border-primary/30 bg-primary/10 text-primary"
                        : "border-border/70 bg-white/88 text-ink-secondary hover:border-primary/20 hover:text-primary",
                    )}
                  >
                    <PanelRightOpen className="h-3.5 w-3.5" />
                    {sidePanelVisible ? "收起侧栏" : "工作区"}
                  </button>
                </div>
              </div>
              <AssistantWorkflowStrip
                run={assistantWorkflowRun}
                loading={workflowRunLoading}
                error={workflowRunError}
                onOpenConfig={() => setWorkflowDrawerOpen(true)}
                onOpenDetail={handleOpenWorkflowRunDetail}
                onRefresh={handleRefreshWorkflowRun}
                onDismiss={handleDismissWorkflowRun}
              />
            </div>
        <div
          ref={chatViewportRef}
          className="relative min-h-0 flex-1 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]"
          style={{
            ...(terminalDrawerOpen ? { paddingBottom: `${terminalViewportPadding}px` } : undefined),
            WebkitOverflowScrolling: "touch",
            touchAction: "pan-y",
            overscrollBehaviorY: isMobileViewport ? "contain" : "auto",
          }}
        >
          {isEmpty ? (
            <EmptyState
              mountedPaperSummary={mountedPaperSummary}
              mountedPaperCount={mountedPaperIds.length}
              sessionTitle={sessionTitle}
              workspaceName={workspaceName}
              assistantDirectory={workspaceDirectory}
              onDraftPrompt={(prompt) => {
                setInput(prompt);
                requestAnimationFrame(() => textareaRef.current?.focus());
              }}
              onStartConversation={handleStartConversation}
              onOpenWorkspace={workspaceDirectory ? () => openSidePanel("files") : undefined}
            />
          ) : (
            <div className={cn(
              "mx-auto w-full max-w-[1040px] lg:px-6 lg:pb-16 lg:pt-10",
              isMobileViewport ? "px-3 pb-8 pt-4" : "px-4 pb-14 pt-8",
            )}>
              {items.map((item, idx) => {
                const retryFn = item.type === "error" ? (() => {
                  for (let i = idx - 1; i >= 0; i--) {
                    if (items[i].type === "user") {
                      handleSend(items[i].content);
                      return;
                    }
                  }
                }) : undefined;
                return (
                  <ChatBlock
                    key={item.id}
                    item={item}
                    mountedPrimaryPaperId={mountedPrimaryPaperId || mountedPaperIds[0] || null}
                    isPending={item.actionId ? pendingActions.has(item.actionId) : false}
                    isConfirming={item.actionId ? confirmingActions.has(item.actionId) : false}
                    onConfirm={handleConfirmAction}
                    onReject={handleReject}
                    onQuestionSubmit={handleQuestionSubmit}
                    onOpenArtifact={handleOpenArtifact}
                    onRetry={retryFn}
                  />
                );
              })}
              {loading && items[items.length - 1]?.type !== "action_confirm" && items[items.length - 1]?.type !== "question" && (
                <div className="flex items-center gap-2 py-4 text-sm text-ink-tertiary">
                  <div className="flex gap-1">
                    <span className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-primary [animation-delay:0ms]" />
                    <span className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-primary [animation-delay:150ms]" />
                    <span className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-primary [animation-delay:300ms]" />
                  </div>
                  <span>生成中...</span>
                </div>
              )}
              <div ref={endRef} />
            </div>
          )}
        </div>

        <div
          className={cn(
            "sticky bottom-0 z-20 shrink-0 border-t border-border bg-page transition-[max-height,opacity,padding,transform] duration-200 ease-out lg:px-6 lg:pb-3",
            isMobileViewport ? "px-3 pb-1.5 pt-1.5" : "px-4 pb-2 pt-2",
            isMobileViewport && mobileChromeCollapsed && "max-h-0 translate-y-4 overflow-hidden border-t-0 px-3 py-0 opacity-0",
          )}
          style={{ bottom: `calc(var(--task-dock-offset, 0px) + ${terminalComposerOffset}px)` }}
        >
          <div className={cn("mx-auto max-w-[980px]", isMobileViewport ? "space-y-1.5" : "space-y-2.5")}>
            {hasPendingConfirm && (
              <div className="flex items-center gap-2 rounded-md border border-warning/20 bg-warning-light px-3 py-2 text-[11px] font-medium text-warning">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                <span>先处理确认请求。</span>
              </div>
            )}

            <input
              ref={fileInputRef}
              type="file"
              accept="*/*"
              multiple
              className="hidden"
              onChange={handleUploadFile}
            />

            <div className={cn(
              isMobileViewport ? "rounded-xl border border-border bg-white p-2 transition-colors duration-150 focus-within:border-primary/25" : "rounded-xl border border-border bg-white p-2.5 transition-colors duration-150 focus-within:border-primary/25",
              hasPendingConfirm && "opacity-60",
            )}>
              {selectedSlashCommand && (
                <div className="mb-2 flex flex-wrap items-center gap-2 rounded-md border border-primary/20 bg-primary/8 px-3 py-2">
                  <span className="rounded-md bg-primary/12 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-primary">
                    指令
                  </span>
                  <span className="text-xs font-medium text-ink">{selectedSlashCommand.description}</span>
                  <code className="rounded-md bg-white px-1.5 py-0.5 text-[11px] text-ink-secondary">
                    {(selectedSlashCommand.insertText || `/${selectedSlashCommand.trigger}`).trim()}
                  </code>
                  <button
                    type="button"
                    onClick={() => setSelectedSlashCommand(null)}
                    className="ml-auto rounded-md p-1 text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-ink"
                    title="清除指令"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              )}
              {slashMenuOpen && (
                <div ref={slashMenuRef} className="mb-2 max-h-56 overflow-y-auto rounded-lg border border-border bg-page p-1.5">
                  {slashFilteredCommands.length === 0 ? (
                    <div className="px-2 py-1.5 text-xs text-ink-tertiary">没有匹配的命令</div>
                  ) : (
                    slashFilteredCommands.map((command, idx) => (
                      <button
                        key={command.id}
                        data-slash-id={command.id}
                        data-slash-index={idx}
                        type="button"
                        onClick={() => handleSlashSelect(command)}
                        className={cn(
                          "flex w-full items-center justify-between gap-3 rounded-md border border-transparent px-3 py-2 text-left text-xs transition-colors duration-150",
                          idx === slashActiveIndex
                            ? "bg-active text-ink"
                            : "text-ink-secondary hover:bg-hover hover:text-ink",
                        )}
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <span className="font-semibold text-ink">/{command.trigger}</span>
                          <span className="truncate text-ink-tertiary">{command.description}</span>
                        </div>
                        <span className="rounded-md border border-border px-2 py-0.5 text-[10px] uppercase tracking-[0.08em] text-ink-tertiary">
                          {command.source}
                        </span>
                      </button>
                    ))
                  )}
                </div>
              )}

              <div className="relative">
                <div className={cn(
                  "rounded-lg border border-border bg-page pr-[4.4rem]",
                  isMobileViewport ? "px-3 py-2.5" : "px-4 py-3",
                )}>
                  <textarea
                    ref={textareaRef}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={
                      hasPendingConfirm
                        ? "先处理确认..."
                        : activeWorkspace
                          ? `在 ${activeWorkspace.title} 中继续`
                          : "输入消息"
                    }
                    className={cn(
                      "w-full resize-none overflow-hidden bg-transparent pr-2 text-ink placeholder:text-ink-placeholder focus:outline-none",
                      isMobileViewport ? "text-[14px] leading-6" : "text-[15px] leading-7",
                      isEmpty
                        ? (isMobileViewport ? "min-h-[72px]" : "min-h-[96px]")
                        : (isMobileViewport ? "h-[54px]" : "h-[68px]"),
                    )}
                    rows={1}
                    disabled={composerLocked}
                  />
                </div>
                <div className={cn("absolute flex items-center gap-2", isMobileViewport ? "bottom-2 right-2" : "bottom-2.5 right-2.5")}>
                  {loading && (
                    <button
                      aria-label="停止生成"
                      onClick={stopGeneration}
                      className={cn(
                        "flex items-center justify-center rounded-md border border-error/20 bg-error-light text-error transition-colors duration-150 hover:bg-error/10",
                        isMobileViewport ? "h-9 w-9" : "h-10 w-10",
                      )}
                    >
                      <Square className="h-4 w-4" />
                    </button>
                  )}
                  <button
                    aria-label="发送消息"
                    onClick={() => handleSend(input)}
                    disabled={!input.trim() || composerLocked}
                    className={cn(
                      isMobileViewport ? "flex h-9 w-9 items-center justify-center rounded-md transition-colors duration-150" : "flex h-10 w-10 items-center justify-center rounded-md transition-colors duration-150",
                      input.trim() && !composerLocked
                        ? "bg-primary text-white hover:bg-primary-hover"
                        : "bg-hover text-ink-tertiary",
                    )}
                  >
                    <Send className="h-4.5 w-4.5" />
                  </button>
                </div>
              </div>

              <div className={cn(
                "mt-2 flex flex-wrap items-center overflow-x-auto border-t border-border/50",
                isMobileViewport ? "gap-1 pt-1.5" : "gap-1.5 pt-2",
              )}>
                <button
                  type="button"
                  onClick={() => setShowImportModal(true)}
                  className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border bg-page px-2.5 text-[11px] font-medium text-ink-secondary transition-colors duration-150 hover:bg-hover hover:text-ink"
                >
                  <Link2 className="h-3.5 w-3.5" />
                  导入论文
                </button>

                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploadingPdf}
                  className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border bg-page px-2.5 text-[11px] font-medium text-ink-secondary transition-colors duration-150 hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {uploadingPdf ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
                  上传文件
                </button>

                <label className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border bg-page px-2.5 text-[11px] text-ink-secondary">
                  <Brain className="h-3.5 w-3.5 text-primary" />
                  <span className="text-ink-tertiary">模型</span>
                  <select
                    ref={modelSelectRef}
                    value={activeLlm?.config?.id || ""}
                    onChange={(event) => void handleModelChange(event.target.value)}
                    disabled={modelLoading || modelSwitching || llmConfigs.length === 0}
                    className="agent-inline-select max-w-[8rem] appearance-none bg-transparent pr-4 text-[11px] text-ink outline-none disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {llmConfigs.length === 0 ? (
                      <option value="">未配置模型</option>
                    ) : (
                      llmConfigs.map((config) => (
                        <option key={config.id} value={config.id}>
                          {config.name}
                        </option>
                      ))
                    )}
                  </select>
                  {(modelLoading || modelSwitching) && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                </label>

                <label className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border bg-page px-2.5 text-[11px] text-ink-secondary">
                  <Sparkles className="h-3.5 w-3.5 text-primary" />
                  <span className="text-ink-tertiary">推理</span>
                  <select
                    value={reasoningLevel}
                    onChange={(event) => setReasoningLevel(event.target.value as AgentReasoningLevel)}
                    className="agent-inline-select appearance-none bg-transparent pr-4 text-[11px] text-ink outline-none"
                  >
                    {REASONING_LEVEL_OPTIONS.map((item) => (
                      <option key={item.id} value={item.id}>{item.label}</option>
                    ))}
                  </select>
                </label>

                <button
                  type="button"
                  onClick={() => void handleTogglePermissionPreset()}
                  disabled={policySyncState === "saving"}
                  className={cn(
                    "inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-[11px] font-medium transition-colors duration-150 disabled:cursor-wait disabled:opacity-60",
                    permissionPreset === "full_access"
                      ? "border-primary/30 bg-primary/10 text-primary"
                      : permissionPreset === "custom"
                        ? "border-warning/20 bg-warning-light text-warning hover:bg-warning-light/80"
                        : "border-border bg-page text-ink-secondary hover:bg-hover hover:text-ink",
                  )}
                  title="切换权限策略"
                >
                  {policySyncState === "saving" ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : permissionPreset === "full_access" ? (
                    <BadgeCheck className="h-3.5 w-3.5" />
                  ) : permissionPreset === "custom" ? (
                    <AlertTriangle className="h-3.5 w-3.5" />
                  ) : (
                    <Shield className="h-3.5 w-3.5" />
                  )}
                  权限
                  <span className="text-ink-tertiary">{runtimeControls.permissionLabel}</span>
                </button>

                <label className="inline-flex h-7 items-center gap-1.5 rounded-full border border-border/70 bg-page/72 px-2.5 text-[11px] text-ink-secondary">
                  <Globe className="h-3.5 w-3.5 text-primary" />
                  <span className="text-ink-tertiary">模式</span>
                  <select
                    value={agentMode}
                    onChange={(event) => setAgentMode(event.target.value as AgentMode)}
                    className="agent-inline-select min-w-0 appearance-none bg-transparent pr-4 text-[11px] text-ink outline-none"
                  >
                    {MODE_OPTIONS.map((mode) => (
                      <option key={mode.id} value={mode.id}>{mode.label}</option>
                    ))}
                  </select>
                </label>

                <label className="inline-flex h-7 max-w-full items-center gap-1.5 rounded-full border border-border/70 bg-page/72 px-2.5 text-[11px] text-ink-secondary">
                  <Server className="h-3.5 w-3.5 text-primary" />
                  <span className="text-ink-tertiary">目标</span>
                  <select
                    data-testid="assistant-target-select"
                    value={workspaceServerId}
                    onChange={(event) => handleWorkspaceServerChange(event.target.value)}
                    className="agent-inline-select max-w-[10rem] min-w-0 appearance-none bg-transparent pr-4 text-[11px] text-ink outline-none"
                  >
                    {workspaceServers.map((server) => (
                      <option key={server.id} value={server.id}>
                        {server.label}{server.available ? "" : "（离线）"}
                      </option>
                    ))}
                    {workspaceServers.length === 0 && <option value="local">本地</option>}
                  </select>
                </label>

              </div>

              {workspaceError && (
                <div className="mt-2 rounded-xl border border-warning/25 bg-warning-light px-3 py-2 text-[11px] text-warning">
                  {workspaceError}
                </div>
              )}

              {mountedPaperIds.length > 0 && (
                <div className={cn(
                  "mt-2 rounded-[22px] border border-primary/15 bg-primary/6",
                  isMobileViewport ? "px-2.5 py-2" : "px-3 py-2.5",
                )}>
                  <div className={cn("flex flex-wrap items-center text-[11px]", isMobileViewport ? "gap-1.5" : "gap-2")}>
                    <span className={cn(
                      "inline-flex items-center rounded-full bg-white/88 font-medium text-primary",
                      isMobileViewport ? "gap-1 px-2 py-0.5 text-[10px]" : "gap-1.5 px-2.5 py-1",
                    )}>
                      <Link2 className="h-3.5 w-3.5" />
                      已导入 {mountedPaperIds.length} 篇
                    </span>
                    <span
                      className={cn(
                        "min-w-0 break-words text-ink-secondary",
                        isMobileViewport ? "order-3 basis-full text-[10px] leading-4" : "flex-1",
                      )}
                      title={mountedPaperSummary}
                    >
                      {mountedPaperSummary}
                    </span>
                    {focusedPaperId ? (
                      <span className={cn(
                        "inline-flex items-center rounded-full border border-primary/15 bg-white/84 text-primary",
                        isMobileViewport ? "px-2 py-0.5 text-[10px]" : "px-2.5 py-1",
                      )}>
                        目标：{truncateText(focusedPaperLabel, 20)}
                      </span>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => setShowImportModal(true)}
                      className={cn(
                        "inline-flex items-center gap-1 rounded-full border border-border/70 bg-white/86 font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary",
                        isMobileViewport ? "h-6 px-2 text-[10px]" : "h-7 px-2.5 text-[11px]",
                      )}
                    >
                      管理
                    </button>
                    <button
                      type="button"
                      onClick={() => setMountedPaperPanelOpen((current) => !current)}
                      className={cn(
                        "inline-flex items-center gap-1 rounded-full border border-border/70 bg-white/86 font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary",
                        isMobileViewport ? "h-6 px-2 text-[10px]" : "h-7 px-2.5 text-[11px]",
                      )}
                    >
                      {mountedPaperPanelOpen ? "收起" : "详情"}
                    </button>
                    <button
                      type="button"
                      onClick={handleClearMountedPapers}
                      className={cn(
                        "inline-flex items-center gap-1 rounded-full border border-border/70 bg-white/86 font-medium text-ink-secondary transition hover:border-error/25 hover:text-error",
                        isMobileViewport ? "h-6 px-2 text-[10px]" : "h-7 px-2.5 text-[11px]",
                      )}
                    >
                      清空
                    </button>
                  </div>
                  {mountedPaperPanelOpen && (
                    <div className={cn("mt-2 flex flex-wrap", isMobileViewport ? "gap-1.5" : "gap-2")}>
                      {mountedPaperItems.map((paper) => (
                        <span
                          key={paper.id}
                          className={cn(
                            "inline-flex max-w-full items-center rounded-full border border-primary/15 bg-white/86 text-primary",
                            isMobileViewport ? "gap-1.5 px-2.5 py-1 text-[10px]" : "gap-2 px-3 py-1 text-[11px]",
                          )}
                          title={paper.title}
                        >
                          <span className={cn("break-words text-left", isMobileViewport ? "max-w-[220px]" : "max-w-[320px]")}>
                            {paper.title}
                          </span>
                          <button
                            type="button"
                            onClick={() => handleRemoveMountedPaper(paper.id)}
                            className="rounded-full p-0.5 transition hover:bg-primary/10"
                            aria-label="移除导入论文"
                          >
                            <X className="h-3 w-3" />
                          </button>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        {terminalDrawerOpen && (
          <div
            className="theme-terminal-drawer absolute inset-x-0 bottom-0 z-30 border-t shadow-[0_-14px_28px_rgba(15,23,42,0.16)]"
            style={{ height: `${terminalPanelHeight}px` }}
          >
            <div className="flex h-full flex-col">
              <div
                role="separator"
                aria-orientation="horizontal"
                onMouseDown={handleTerminalResizeStart}
                className="theme-terminal-rail flex h-3 cursor-row-resize items-center justify-center border-b"
                title="拖拽调整终端高度"
              >
                <GripVertical className="theme-console-dim h-3.5 w-3.5 rotate-90" />
              </div>
              <div className="theme-terminal-header flex h-8 items-center justify-between gap-3 border-b px-3 text-[10px] lg:px-4">
                <div className="flex min-w-0 items-center gap-2 overflow-x-auto">
                  <span className="inline-flex h-full items-center border-b-2 border-[#f59e0b] px-2 text-[10px] font-medium uppercase tracking-[0.14em] theme-console-fg">
                    Terminal
                  </span>
                  {terminalSessions.map((session) => (
                    <button
                      key={session.id}
                      type="button"
                      onClick={() => setActiveTerminalSessionId(session.id)}
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[10px] transition",
                        activeTerminalSessionId === session.id
                          ? "theme-terminal-tab-active"
                          : "theme-terminal-tab",
                      )}
                    >
                      <TerminalSquare className="h-3 w-3" />
                      {session.name}
                      {terminalSessions.length > 1 ? (
                        <span
                          className="rounded p-0.5 transition hover:bg-white/10"
                          onClick={(event) => {
                            event.stopPropagation();
                            void handleCloseTerminalSession(session.id);
                          }}
                          role="button"
                          tabIndex={0}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              void handleCloseTerminalSession(session.id);
                            }
                          }}
                        >
                          <X className="h-3 w-3" />
                        </span>
                      ) : null}
                    </button>
                  ))}
                </div>

                {workspaceDirectory ? (
                  <div className="hidden min-w-0 flex-1 items-center xl:flex">
                    <span className="theme-console-dim truncate text-[10px]">
                      {activeTerminalSession?.info.workspace_path || workspaceDirectory}
                    </span>
                  </div>
                ) : <div className="flex-1" />}

                <div className="flex shrink-0 items-center gap-1.5">
                  <button
                    type="button"
                    onClick={handleCreateTerminalSession}
                    className="theme-terminal-action inline-flex h-7 items-center gap-1 rounded-md border bg-transparent px-2.5 text-[10px] font-medium transition"
                  >
                    <TerminalSquare className="h-3 w-3" />
                    新建
                  </button>
                  <button
                    type="button"
                    onClick={() => void spawnTerminalSession("replaceClosed")}
                    className="theme-terminal-action inline-flex h-7 items-center gap-1 rounded-md border bg-transparent px-2.5 text-[10px] font-medium transition"
                  >
                    <RotateCcw className="h-3 w-3" />
                    重开
                  </button>
                  <button
                    type="button"
                    onClick={() => setTerminalDrawerOpen(false)}
                    className="theme-terminal-action inline-flex h-7 items-center gap-1 rounded-md border bg-transparent px-2.5 text-[10px] font-medium transition"
                  >
                    收起
                  </button>
                </div>
              </div>

              <div className="theme-terminal-drawer min-h-0 flex-1 px-3 pb-3 pt-2 lg:px-4">
                {!workspaceDirectory ? (
                  <div className="theme-terminal-empty flex h-full items-center justify-center rounded-md border border-dashed px-4 text-center text-[12px] leading-6">
                    当前会话未绑定工作区
                  </div>
                ) : (
                  <div className="theme-terminal-drawer flex h-full min-h-0 flex-col overflow-hidden rounded-md border">
                    {activeTerminalSession ? (
                      <div className="theme-terminal-drawer min-h-0 flex-1 p-1">
                        <div className="theme-terminal-drawer h-full rounded-sm">
                          <WorkspaceTerminal
                            sessionId={activeTerminalSession.id}
                            className="rounded-sm"
                            onStateChange={(state) => handleTerminalSessionStateChange(activeTerminalSession.id, state)}
                            onSessionInfo={(info) => handleTerminalSessionInfo(activeTerminalSession.id, info)}
                            onError={(message) => toast("error", message)}
                            onExit={(exitCode) => handleTerminalSessionExit(activeTerminalSession.id, exitCode)}
                          />
                        </div>
                      </div>
                    ) : (
                      <div className="theme-console-dim flex h-full items-center justify-center px-4 text-center text-[12px]">
                        终端未就绪
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </section>

      {sidePanelVisible && (
        <>
        <div
          role="separator"
          aria-orientation="vertical"
          onMouseDown={handleWorkspacePanelResizeStart}
          className="hidden w-3 shrink-0 cursor-col-resize items-center justify-center bg-transparent xl:flex"
          title="拖拽调整工作区侧栏宽度"
        >
          <div className="flex h-16 w-2 items-center justify-center rounded-full bg-border/55 text-ink-tertiary transition hover:bg-primary/20 hover:text-primary">
            <GripVertical className="h-4 w-4" />
          </div>
        </div>
        <div
          className="hidden min-h-0 border-l border-border bg-white xl:flex xl:shrink-0"
          style={{ width: `${workspacePanelWidth}px` }}
        >
          <aside data-testid="assistant-workspace-panel" className="flex h-full min-h-0 w-full flex-col overflow-hidden">
          <div className="border-b border-border/60 px-4 py-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-ink">
                  {workspacePanelTab === "artifact" && canvas ? canvas.title : "工作区"}
                </p>
                {workspaceDirectory ? (
                  <p className="mt-0.5 truncate text-[11px] text-ink-tertiary">{workspaceDirectory}</p>
                ) : null}
              </div>
              <button
                type="button"
                onClick={closeSidePanel}
                className="inline-flex h-7 items-center gap-1 rounded-full border border-border/70 bg-white px-2.5 text-[11px] text-ink-secondary transition hover:border-primary/25 hover:text-primary"
              >
                <X className="h-3.5 w-3.5" />
                收起
              </button>
            </div>
            {workspacePanelTab === "artifact" && canvas ? (
              <div className="mt-2">
                <button
                  type="button"
                  onClick={() => setCanvas(null)}
                  className="inline-flex h-7 items-center gap-1 rounded-full border border-border/70 bg-white px-2.5 text-[11px] text-ink-secondary transition hover:border-primary/25 hover:text-primary"
                >
                  <X className="h-3.5 w-3.5" />
                  关闭当前工件
                </button>
              </div>
            ) : null}
            <div className="mt-2 flex items-center gap-1.5 text-[11px] text-ink-secondary">
              <span className="inline-flex items-center gap-1 rounded-full bg-page/70 px-2 py-0.5">
                <Server className="h-3 w-3 text-primary" />
                {workspaceServerDisplayLabel}
              </span>
              {activeWorkspaceServer?.phase ? (
                <span className={cn(
                  "inline-flex rounded-full px-2 py-0.5",
                  activeWorkspaceServer.available ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700",
                )}>
                  {activeWorkspaceServer.phase}
                </span>
              ) : null}
              {workspaceDirectory && workspacePanelTab !== "artifact" ? (
                <button
                  type="button"
                  onClick={() => void refreshWorkspaceOverview()}
                  disabled={workspaceLoading}
                  className="inline-flex h-6 items-center gap-1 rounded-full border border-border/70 bg-white px-2 text-[10px] transition hover:border-primary/25 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {workspaceLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                  刷新
                </button>
              ) : null}
              <button
                type="button"
                onClick={handleCreateWorkspaceServer}
                className="inline-flex h-6 items-center gap-1 rounded-full border border-border/70 bg-white px-2 text-[10px] transition hover:border-primary/25 hover:text-primary"
              >
                管理服务器
              </button>
            </div>
          </div>

          <div className="flex items-center gap-1 overflow-x-auto border-b border-border/60 px-3 py-2">
            {sidePanelTabs.map((tab) => {
              const Icon = tab.icon;
              const disabled = !workspaceDirectory && tab.id !== "review" && tab.id !== "artifact";
              return (
              <button
                key={tab.id}
                type="button"
                onClick={() => {
                  if (disabled) return;
                  openSidePanel(tab.id);
                }}
                disabled={disabled}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[11px] font-medium transition disabled:cursor-not-allowed disabled:opacity-45",
                  workspacePanelTab === tab.id
                    ? "bg-primary/10 text-primary"
                    : "text-ink-secondary hover:bg-page/70 hover:text-ink",
                )}
              >
                <Icon className="h-3.5 w-3.5" />
                {tab.label}
              </button>
              );
            })}
          </div>

          {workspaceError && workspacePanelTab !== "artifact" && (
            <div className="mx-3 mt-3 rounded-xl border border-warning/25 bg-warning-light px-3 py-2 text-[11px] text-warning">
              {workspaceError}
            </div>
          )}

          <div className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
            {workspacePanelTab === "artifact" && (
              canvas ? (
                <div className="min-h-0 flex-1">
                  <CanvasPanel
                    title={canvas.title}
                    content={canvas.markdown}
                    isHtml={canvas.isHtml}
                    onClose={() => setCanvas(null)}
                    onNavigate={(paperId) => navigate(`/papers/${paperId}`)}
                  />
                </div>
              ) : (
                <div className="rounded-2xl border border-border/60 bg-page/55 px-3 py-4 text-[11px] text-ink-tertiary">
                  暂无工件
                </div>
              )
            )}

            {(workspacePanelTab === "files" || workspacePanelTab === "git") && !workspaceDirectory && (
              <div className="rounded-2xl border border-border/60 bg-page/55 px-3 py-4 text-[11px] leading-6 text-ink-tertiary">
                当前聊天未绑定工作区
              </div>
            )}

            {workspacePanelTab === "files" && workspaceDirectory && (
              <>
                <div className="glass-card rounded-2xl p-2.5">
                  <div className="mb-1.5 flex items-center justify-between text-[11px] text-ink-tertiary">
                    <span>文件目录</span>
                    <span>
                      {workspaceOverview?.total_entries || 0} 项
                      {workspaceOverview?.truncated ? " · 已截断" : ""}
                    </span>
                  </div>
                  <div className="max-h-56 overflow-auto rounded-lg bg-page/70 p-1.5 text-[11px] text-ink-secondary">
                    {workspaceFileTree.length === 0 ? (
                      <div className="px-2 py-1.5 text-ink-tertiary">暂无目录</div>
                    ) : (
                      workspaceFileTree.map((node) => (
                        <WorkspaceTreeNodeView
                          key={node.path}
                          node={node}
                          depth={0}
                          expandedDirs={workspaceExpandedDirs}
                          activeFile={activeWorkspaceFile}
                          onToggleDir={handleToggleWorkspaceDir}
                          onOpenFile={handleOpenWorkspaceFile}
                        />
                      ))
                    )}
                  </div>
                </div>
                <div className="glass-card rounded-2xl p-2.5">
                  <div className="mb-1.5 flex items-center justify-between text-[11px] text-ink-tertiary">
                    <span>{activeWorkspaceFile ? `编辑：${activeWorkspaceFile}` : "文件编辑器"}</span>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => void handleRevealWorkspace()}
                        className="inline-flex items-center gap-1 rounded-md border border-border/70 bg-page/70 px-2 py-1 text-[10px] text-ink-secondary transition hover:border-primary/20 hover:text-primary"
                      >
                        <FolderOpen className="h-3 w-3" />
                        目录
                      </button>
                      {activeWorkspaceFile && (
                        <button
                          type="button"
                          onClick={() => void handleSaveWorkspaceFile()}
                          disabled={workspaceFileLoading || workspaceFileSaving || !workspaceFileDirty}
                          className="inline-flex items-center gap-1 rounded-md border border-border/70 bg-white px-2 py-1 text-[10px] font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {workspaceFileSaving ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
                          保存
                        </button>
                      )}
                    </div>
                  </div>
                  {workspaceFileError && (
                    <div className="mb-2 rounded-md border border-warning/25 bg-warning-light px-2 py-1.5 text-[10px] text-warning">
                      {workspaceFileError}
                    </div>
                  )}
                  {!activeWorkspaceFile ? (
                    <div className="rounded-md bg-page/70 px-2 py-1.5 text-[10px] text-ink-tertiary">
                      未选择文件
                    </div>
                  ) : workspaceFileLoading ? (
                    <div className="flex items-center gap-1.5 rounded-md bg-page/70 px-2 py-1.5 text-[10px] text-ink-tertiary">
                      <Loader2 className="h-3 w-3 animate-spin" />
                      读取中...
                    </div>
                  ) : (
                    <textarea
                      value={workspaceFileContent}
                      onChange={(event) => {
                        setWorkspaceFileContent(event.target.value);
                        setWorkspaceFileDirty(true);
                      }}
                      className="h-56 w-full resize-y rounded-md border border-border/70 bg-white px-2 py-1.5 text-[11px] leading-5 text-ink outline-none focus:border-primary/25"
                    />
                  )}
                </div>
              </>
            )}

            {workspacePanelTab === "git" && workspaceDirectory && (
              <div className="glass-card rounded-2xl p-2.5">
                <div className="mb-2 flex items-center justify-between gap-2 text-[11px] text-ink-tertiary">
                  <div className="flex flex-wrap items-center gap-2">
                    <span>Git</span>
                    {workspaceOverview?.git?.is_repo ? (
                      <>
                        <span className="rounded-full bg-page/70 px-2 py-1 text-ink-secondary">
                          {workspaceOverview.git.branch || "main"}
                        </span>
                        <span className="rounded-full bg-page/70 px-2 py-1 text-ink-secondary">
                          暂存 {stagedGitCount}
                        </span>
                        <span className="rounded-full bg-page/70 px-2 py-1 text-ink-secondary">
                          工作区 {unstagedGitCount}
                        </span>
                      </>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    onClick={() => void refreshGitWorkspaceState(selectedDiffFile || undefined)}
                    className="inline-flex h-7 items-center gap-1 rounded-lg border border-border/70 bg-white px-2.5 text-[11px] text-ink-secondary transition hover:border-primary/20 hover:text-primary"
                  >
                    <RefreshCw className="h-3 w-3" />
                    刷新
                  </button>
                </div>
                {!workspaceOverview?.git?.available ? (
                  <div className="rounded-lg bg-page/70 px-2 py-2 text-[11px] text-ink-tertiary">
                    未检测到 Git
                  </div>
                ) : !workspaceOverview.git.is_repo ? (
                  <div className="space-y-2">
                    <div className="rounded-lg bg-page/70 px-2 py-2 text-[11px] text-ink-tertiary">
                      {workspaceOverview.git.message || "当前目录不是 Git 仓库"}
                    </div>
                    <button
                      type="button"
                      onClick={() => void handleInitGitRepo()}
                      className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border/70 bg-white px-3 text-[11px] font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary"
                    >
                      <GitBranch className="h-3.5 w-3.5" />
                      初始化仓库
                    </button>
                  </div>
                ) : (
                  <>
                    <div className="mb-2 flex flex-wrap gap-2 text-[11px] text-ink-secondary">
                      <span className="rounded-full bg-page/70 px-2 py-1">
                        远程：{
                          workspaceOverview.git.remotes && workspaceOverview.git.remotes.length > 0
                            ? workspaceOverview.git.remotes.join(" · ")
                            : "未配置"
                        }
                      </span>
                      <span className="rounded-full bg-page/70 px-2 py-1">
                        改动 {workspaceOverview.git.changed_count || 0}
                      </span>
                      <span className="rounded-full bg-page/70 px-2 py-1">
                        未跟踪 {workspaceOverview.git.untracked_count || 0}
                      </span>
                    </div>
                    <div className="mb-2 flex items-center gap-2">
                      <input
                        value={gitBranchName}
                        onChange={(event) => setGitBranchName(event.target.value)}
                        placeholder="新分支名称，如 feature/exp-1"
                        className="h-8 min-w-0 flex-1 rounded-lg border border-border/70 bg-page/40 px-2 text-[11px] text-ink outline-none focus:border-primary/30"
                      />
                      <button
                        type="button"
                        onClick={() => void handleCreateGitBranch()}
                        disabled={!gitBranchName.trim()}
                        className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border/70 bg-white px-3 text-[11px] font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        <GitBranch className="h-3.5 w-3.5" />
                        创建/切换
                      </button>
                    </div>
                    <div className="mb-2 flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => void handleStageGit()}
                        disabled={gitActionKey === "stage:all"}
                        className="inline-flex h-8 items-center rounded-lg border border-border/70 bg-white px-3 text-[11px] font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        全部暂存
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleUnstageGit()}
                        disabled={gitActionKey === "unstage:all" || stagedGitCount === 0}
                        className="inline-flex h-8 items-center rounded-lg border border-border/70 bg-white px-3 text-[11px] font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        全部撤暂存
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleSyncGit("fetch")}
                        disabled={gitActionKey === "fetch"}
                        className="inline-flex h-8 items-center rounded-lg border border-border/70 bg-white px-3 text-[11px] font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        Fetch
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleSyncGit("pull")}
                        disabled={gitActionKey === "pull" || !(workspaceOverview.git.remotes && workspaceOverview.git.remotes.length > 0)}
                        className="inline-flex h-8 items-center rounded-lg border border-border/70 bg-white px-3 text-[11px] font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        Pull
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleSyncGit("push")}
                        disabled={gitActionKey === "push" || !(workspaceOverview.git.remotes && workspaceOverview.git.remotes.length > 0)}
                        className="inline-flex h-8 items-center rounded-lg border border-border/70 bg-white px-3 text-[11px] font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        Push
                      </button>
                    </div>
                    <div className="mb-2 flex items-center gap-2">
                      <input
                        value={gitCommitMessage}
                        onChange={(event) => setGitCommitMessage(event.target.value)}
                        placeholder="提交说明"
                        className="h-8 min-w-0 flex-1 rounded-lg border border-border/70 bg-page/40 px-2 text-[11px] text-ink outline-none focus:border-primary/30"
                      />
                      <button
                        type="button"
                        onClick={() => void handleCommitGit()}
                        disabled={!gitCommitMessage.trim() || gitActionKey === "commit"}
                        className="inline-flex h-8 items-center rounded-lg border border-border/70 bg-white px-3 text-[11px] font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        提交
                      </button>
                    </div>
                    {selectedGitEntry ? (
                      <div className="mb-2 rounded-lg bg-page/70 px-2 py-2 text-[11px] text-ink-secondary">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-ink">{selectedGitEntry.path}</span>
                          <span className="font-mono text-[10px]">{selectedGitEntry.code}</span>
                        </div>
                        <div className="mt-2 flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() => void handleStageGit(selectedGitEntry.path)}
                            disabled={gitActionKey === `stage:${selectedGitEntry.path}`}
                            className="inline-flex h-7 items-center rounded-lg border border-border/70 bg-white px-2.5 text-[11px] font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            暂存
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleUnstageGit(selectedGitEntry.path)}
                            disabled={gitActionKey === `unstage:${selectedGitEntry.path}`}
                            className="inline-flex h-7 items-center rounded-lg border border-border/70 bg-white px-2.5 text-[11px] font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            撤暂存
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleDiscardGit(selectedGitEntry.path)}
                            disabled={gitActionKey === `discard:${selectedGitEntry.path}`}
                            className="inline-flex h-7 items-center rounded-lg border border-border/70 bg-white px-2.5 text-[11px] font-medium text-ink-secondary transition hover:border-error/20 hover:text-error disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            丢弃
                          </button>
                        </div>
                      </div>
                    ) : null}
                    <div className="max-h-44 space-y-1 overflow-y-auto">
                      {gitEntries.length === 0 ? (
                        <div className="rounded-lg bg-page/70 px-2 py-1.5 text-[11px] text-ink-tertiary">
                          工作区干净
                        </div>
                      ) : (
                        gitEntries.slice(0, 120).map((entry) => (
                          <button
                            key={`${entry.code}_${entry.path}`}
                            type="button"
                            onClick={() => void loadGitDiff(entry.path)}
                            className={cn(
                              "flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-[11px] transition",
                              selectedDiffFile === entry.path
                                ? "bg-primary/10 text-primary"
                                : "bg-page/70 text-ink-secondary hover:bg-page",
                            )}
                          >
                            <span className="truncate">{entry.path}</span>
                            <span className="font-mono text-[10px]">{entry.code}</span>
                          </button>
                        ))
                      )}
                    </div>
                    <div className="mt-2">
                      <div className="mb-1 text-[11px] text-ink-tertiary">
                        {selectedDiffFile ? `Diff: ${selectedDiffFile}` : "Diff 预览"}
                      </div>
                      {gitDiffLoading ? (
                        <div className="rounded-lg bg-page/70 px-2 py-2 text-[11px] text-ink-tertiary">
                          读取中...
                        </div>
                      ) : (
                        <pre className="max-h-[40vh] overflow-auto rounded-lg bg-page/70 p-2 text-[10px] leading-5 text-ink-secondary">
                          {gitDiff?.diff
                            || (selectedGitEntry?.code === "??" ? "未暂存" : "")
                            || gitDiff?.message
                            || "暂无 diff"}
                        </pre>
                      )}
                    </div>
                  </>
                )}
              </div>
            )}

            {workspacePanelTab === "review" && (
              <div className="space-y-3">
                <div className="glass-card rounded-2xl p-2.5">
                  <div className="mb-2 flex items-center justify-between gap-2 text-[11px] text-ink-tertiary">
                    <span>会话改动追踪</span>
                    <button
                      type="button"
                      onClick={() => void loadSessionReview()}
                      disabled={sessionReviewLoading || !activeSessionId}
                      className="inline-flex items-center gap-1 rounded-md border border-border/70 bg-white px-2 py-1 text-[10px] text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {sessionReviewLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                      刷新
                    </button>
                  </div>
                  {!activeSessionId ? (
                    <div className="rounded-lg bg-page/70 px-2 py-2 text-[11px] text-ink-tertiary">
                      暂无会话
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {sessionReviewError ? (
                        <div className="rounded-lg border border-warning/25 bg-warning-light px-3 py-2 text-[11px] text-warning">
                          {sessionReviewError}
                        </div>
                      ) : null}
                      <div className="grid gap-2 md:grid-cols-3">
                        <div className="rounded-xl border border-border/70 bg-page/70 px-3 py-2">
                          <div className="text-[10px] uppercase tracking-[0.12em] text-ink-tertiary">文件</div>
                          <div className="mt-1 text-lg font-semibold text-ink">{sessionDiffStats.files}</div>
                        </div>
                        <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 px-3 py-2">
                          <div className="text-[10px] uppercase tracking-[0.12em] text-emerald-700">新增行</div>
                          <div className="mt-1 text-lg font-semibold text-emerald-700">+{sessionDiffStats.additions}</div>
                        </div>
                        <div className="rounded-xl border border-red-200 bg-red-50/70 px-3 py-2">
                          <div className="text-[10px] uppercase tracking-[0.12em] text-red-700">删除行</div>
                          <div className="mt-1 text-lg font-semibold text-red-700">-{sessionDiffStats.deletions}</div>
                        </div>
                      </div>

                      {sessionRevertInfo ? (
                        <div className="rounded-xl border border-amber-200 bg-amber-50/80 px-3 py-3 text-[11px] text-amber-900">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="font-medium">回退态</div>
                            <button
                              type="button"
                              onClick={() => void handleUnrevertSession()}
                              disabled={sessionReviewActionKey === "unrevert"}
                              className="inline-flex items-center gap-1 rounded-md border border-amber-300 bg-white px-2.5 py-1.5 text-[10px] font-medium text-amber-900 transition hover:border-amber-400 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              {sessionReviewActionKey === "unrevert" ? (
                                <Loader2 className="h-3 w-3 animate-spin" />
                              ) : (
                                <RotateCcw className="h-3 w-3" />
                              )}
                              恢复改动
                            </button>
                          </div>
                        </div>
                      ) : null}

                      {sessionDiffEntries.length === 0 ? (
                        <div className="rounded-lg bg-page/70 px-2 py-2 text-[11px] text-ink-tertiary">
                          暂无改动
                        </div>
                      ) : (
                        <div className="space-y-3">
                          <div className="max-h-40 space-y-1 overflow-y-auto">
                            {sessionDiffEntries.map((entry) => {
                              const entryId = getSessionDiffIdentity(entry);
                              const active = selectedSessionDiffId === entryId;
                              return (
                                <button
                                  key={entryId}
                                  type="button"
                                  onClick={() => setSelectedSessionDiffId(entryId)}
                                  className={cn(
                                    "flex w-full items-center justify-between gap-2 rounded-lg border px-2 py-2 text-left text-[11px] transition",
                                    active
                                      ? "border-primary/30 bg-primary/8"
                                      : "border-border/60 bg-page/70 hover:border-primary/20 hover:bg-page",
                                  )}
                                >
                                  <div className="min-w-0">
                                    <div className="truncate font-medium text-ink">{getSessionDiffTarget(entry)}</div>
                                    <div className="mt-1 flex flex-wrap items-center gap-1 text-[10px] text-ink-tertiary">
                                      <TraceBadge tone={getSessionDiffStatusTone(entry.status)} text={getSessionDiffStatusLabel(entry.status)} />
                                      <span>+{Number(entry.additions || 0)}</span>
                                      <span>-{Number(entry.deletions || 0)}</span>
                                    </div>
                                  </div>
                                  <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-tertiary" />
                                </button>
                              );
                            })}
                          </div>

                          {selectedSessionDiff ? (
                            <div className="rounded-xl border border-border/70 bg-page/60 p-3">
                              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                                <span className="font-medium text-ink">{getSessionDiffTarget(selectedSessionDiff)}</span>
                                <TraceBadge
                                  tone={getSessionDiffStatusTone(selectedSessionDiff.status)}
                                  text={getSessionDiffStatusLabel(selectedSessionDiff.status)}
                                />
                                <span className="text-emerald-700">+{Number(selectedSessionDiff.additions || 0)}</span>
                                <span className="text-red-700">-{Number(selectedSessionDiff.deletions || 0)}</span>
                              </div>
                              <div className="mt-3 grid gap-3 xl:grid-cols-2">
                                <div>
                                  <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.12em] text-ink-tertiary">
                                    修改前
                                  </div>
                                  <pre className="max-h-56 overflow-auto rounded-lg border border-border/60 bg-white p-2 text-[10px] leading-5 text-ink-secondary">
                                    {selectedSessionDiff.exists_before === false
                                      ? "不存在"
                                      : selectedSessionDiff.before || "(空文件)"}
                                  </pre>
                                </div>
                                <div>
                                  <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.12em] text-ink-tertiary">
                                    修改后
                                  </div>
                                  <pre className="max-h-56 overflow-auto rounded-lg border border-border/60 bg-white p-2 text-[10px] leading-5 text-ink-secondary">
                                    {selectedSessionDiff.exists_after === false
                                      ? "已删除"
                                      : selectedSessionDiff.after || "(空文件)"}
                                  </pre>
                                </div>
                              </div>
                            </div>
                          ) : null}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                <div className="glass-card rounded-2xl p-2.5">
                  <div className="mb-2 flex items-center justify-between text-[11px] text-ink-tertiary">
                    <span>回退链路</span>
                    <span>{sessionPatchCheckpoints.length} 个检查点</span>
                  </div>
                  {!activeSessionId ? (
                    <div className="rounded-lg bg-page/70 px-2 py-2 text-[11px] text-ink-tertiary">
                      暂无检查点
                    </div>
                  ) : sessionPatchCheckpoints.length === 0 ? (
                    <div className="rounded-lg bg-page/70 px-2 py-2 text-[11px] text-ink-tertiary">
                      暂无回退点
                    </div>
                  ) : (
                    <div className="max-h-[32vh] space-y-1.5 overflow-y-auto">
                      {sessionPatchCheckpoints.map((checkpoint) => {
                        const isCurrentRevert = sessionRevertInfo?.message_id === checkpoint.messageId;
                        const actionKey = `revert:${checkpoint.messageId}`;
                        return (
                          <div key={checkpoint.messageId} className="rounded-lg border border-border/70 bg-page/55 px-3 py-2">
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                                  <span className="font-medium text-ink">{checkpoint.label}</span>
                                  <TraceBadge tone="neutral" text={formatSessionReviewTimestamp(checkpoint.createdAt)} />
                                  <TraceBadge tone="info" text={`${checkpoint.fileCount} 文件`} />
                                </div>
                                <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-ink-tertiary">
                                  <span className="text-emerald-700">+{checkpoint.additions}</span>
                                  <span className="text-red-700">-{checkpoint.deletions}</span>
                                </div>
                              </div>
                              <button
                                type="button"
                                onClick={() => void handleRevertSessionCheckpoint(checkpoint.messageId)}
                                disabled={isCurrentRevert || sessionReviewActionKey === actionKey}
                                className="inline-flex shrink-0 items-center gap-1 rounded-md border border-border/70 bg-white px-2 py-1 text-[10px] font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                              >
                                {sessionReviewActionKey === actionKey ? (
                                  <Loader2 className="h-3 w-3 animate-spin" />
                                ) : (
                                  <RotateCcw className="h-3 w-3" />
                                )}
                                {isCurrentRevert ? "当前回退点" : "回退到这里"}
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>

                <div className="glass-card rounded-2xl p-2.5">
                  <div className="mb-2 flex items-center justify-between text-[11px] text-ink-tertiary">
                    <span>最近执行步骤</span>
                    <span>{recentStepItems.length} 条</span>
                  </div>
                  {recentStepItems.length === 0 ? (
                    <div className="rounded-lg bg-page/70 px-2 py-2 text-[11px] text-ink-tertiary">
                      暂无记录
                    </div>
                  ) : (
                    <div className="max-h-[56vh] space-y-1.5 overflow-y-auto">
                      {recentStepItems.map((step) => (
                        <div key={step.id} className="rounded-lg border border-border/70 bg-page/55 px-2 py-1.5">
                          <div className="flex items-center justify-between gap-2 text-[11px]">
                            <span className="truncate font-medium text-ink">{getToolMeta(step.toolName).label}</span>
                            <span className={cn(
                              "rounded px-1.5 py-0.5 text-[10px]",
                              step.status === "done"
                                ? "bg-emerald-100 text-emerald-700"
                                : step.status === "error"
                                  ? "bg-red-100 text-red-700"
                                  : "bg-amber-100 text-amber-700",
                            )}>
                              {step.status}
                            </span>
                          </div>
                          {step.summary ? (
                            <div className="mt-1 text-[10px] leading-5 text-ink-secondary">{step.summary}</div>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
          </aside>
        </div>
        </>
      )}

        </div>
      </div>

      {canvas && (
        <div className="fixed inset-0 z-50 flex flex-col bg-surface xl:hidden">
          <CanvasPanel
            title={canvas.title}
            content={canvas.markdown}
            isHtml={canvas.isHtml}
            onClose={() => setCanvas(null)}
            onNavigate={(paperId) => navigate(`/papers/${paperId}`)}
            mobile
          />
        </div>
      )}
    </div>

      <AssistantWorkflowDrawer
        open={workflowDrawerOpen}
        onClose={() => setWorkflowDrawerOpen(false)}
        initialProjectId={preferredProjectId}
        workspacePath={workspaceDirectory}
        workspaceTitle={workspaceName || sessionTitle}
        workspaceServerId={workspaceServerId}
        initialPaperIds={mountedPaperIds}
        onLaunch={handleWorkflowLaunched}
      />

      <Modal
        open={showImportModal}
        onClose={() => setShowImportModal(false)}
        title="导入论文到当前聊天"
        maxWidth="xl"
      >
        <div className="space-y-4">
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_260px]">
            <label className="flex items-center gap-3 rounded-2xl border border-border/70 bg-white/82 px-4 py-3">
              <Search className="h-4 w-4 text-primary" />
              <input
                value={paperQuery}
                onChange={(event) => setPaperQuery(event.target.value)}
                placeholder="搜索论文标题、arXiv ID 或摘要关键词"
                className="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-tertiary"
              />
            </label>

            <label className="flex items-center gap-3 rounded-2xl border border-border/70 bg-white/82 px-4 py-3">
              <FolderTree className="h-4 w-4 text-primary" />
              <select
                value={paperScope}
                onChange={(event) => setPaperScope(event.target.value)}
                className="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none"
              >
                <option value="all">全部论文</option>
                <option value="favorites">仅收藏</option>
                <option value="recent">近 7 天</option>
                <option value="unclassified">未归类</option>
                {topicItems.some((item) => item.kind === "folder") && (
                  <optgroup label="文件夹">
                    {topicItems
                      .filter((item) => item.kind === "folder")
                      .map((item) => (
                        <option key={item.id} value={`topic:${item.id}`}>
                          {item.name}
                        </option>
                      ))}
                  </optgroup>
                )}
                {topicItems.some((item) => item.kind === "subscription") && (
                  <optgroup label="自动订阅">
                    {topicItems
                      .filter((item) => item.kind === "subscription")
                      .map((item) => (
                        <option key={item.id} value={`topic:${item.id}`}>
                          {item.name}
                        </option>
                      ))}
                  </optgroup>
                )}
              </select>
            </label>
          </div>

          <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-border/70 bg-page/45 px-4 py-3 text-[12px] text-ink-secondary">
            <span>当前范围：<span className="font-medium text-ink">{paperScopeLabel}</span></span>
            <span>共 {paperTotal} 篇</span>
            {topicLoading ? <span>正在加载文件夹与订阅...</span> : null}
          </div>

          <div className="max-h-[50vh] space-y-2 overflow-y-auto pr-1">
            {paperLoading ? (
              <div className="flex items-center gap-2 rounded-2xl border border-border/70 bg-page/55 px-4 py-4 text-sm text-ink-secondary">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在搜索论文...
              </div>
            ) : paperItems.length === 0 ? (
              <div className="rounded-2xl border border-border/70 bg-page/55 px-4 py-4 text-sm text-ink-secondary">
                没有找到论文。
              </div>
            ) : (
              paperItems.map((paper) => {
                const selected = selectedPaperIds.includes(paper.id);
                const imported = mountedPaperIds.includes(paper.id);
                const focused = focusedPaperId === paper.id;
                return (
                  <button
                    type="button"
                    key={paper.id}
                    onClick={() => handleTogglePaper(paper.id)}
                    className={cn(
                      "w-full rounded-2xl border px-4 py-3 text-left transition",
                      selected
                        ? "border-primary/20 bg-primary/8"
                        : imported
                          ? "border-emerald-200 bg-emerald-50/70"
                          : "border-border/70 bg-white/82 hover:border-primary/20 hover:bg-page/55",
                    )}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="text-sm font-medium text-ink">{paper.title}</div>
                          {paper.title_zh ? <div className="mt-1 text-xs text-ink-secondary">{paper.title_zh}</div> : null}
                          <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-ink-tertiary">
                            <span>{paper.arxiv_id || "本地上传论文"}</span>
                            <span>{paper.read_status}</span>
                            {paper.favorited ? (
                              <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-700">
                                <Star className="h-3 w-3" />
                                已收藏
                              </span>
                            ) : null}
                          </div>
                          {paper.topics && paper.topics.length > 0 ? (
                            <div className="mt-2 flex flex-wrap gap-1.5">
                              {paper.topics.slice(0, 4).map((topic) => (
                                <span
                                  key={`${paper.id}_${topic}`}
                                  className="rounded-full border border-border/70 bg-page/70 px-2 py-0.5 text-[10px] text-ink-secondary"
                                >
                                  {topic}
                                </span>
                              ))}
                            </div>
                          ) : null}
                        </div>
                        <div className="flex shrink-0 flex-col items-end gap-2">
                          {focused ? (
                            <span className="rounded-full border border-primary/15 bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                              当前目标
                          </span>
                        ) : null}
                        {imported ? (
                          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
                            已导入
                          </span>
                        ) : null}
                        <span className={cn(
                          "inline-flex h-5 w-5 items-center justify-center rounded-full border",
                          selected
                            ? "border-primary/20 bg-primary text-white"
                            : "border-border/70 bg-white text-transparent",
                        )}>
                          <BadgeCheck className="h-3.5 w-3.5" />
                        </span>
                      </div>
                    </div>
                  </button>
                );
              })
            )}
          </div>

          <div className="flex flex-col gap-3 rounded-2xl border border-border/70 bg-page/45 px-4 py-4 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
            <div className="min-w-0 text-sm text-ink-secondary">
              {selectedPaperIds.length > 0 ? (
                <>
                  已选 {selectedPaperIds.length} 篇论文
                  {focusedPaperId ? <span className="font-medium text-ink">，当前目标：{truncateText(focusedPaperLabel, 32)}</span> : null}
                </>
              ) : mountedPaperIds.length > 0 ? (
                <>当前聊天已导入：<span className="font-medium break-words text-ink" title={mountedPaperSummary}>{mountedPaperSummary}</span></>
              ) : (
                "未选择论文"
              )}
            </div>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <button
                type="button"
                onClick={() => {
                  setSelectedPaperIds([]);
                  setFocusedPaperId(null);
                }}
                disabled={selectedPaperIds.length === 0 || importingPapers}
                className="inline-flex items-center gap-2 rounded-2xl border border-border/75 bg-white/86 px-4 py-2 text-sm font-medium text-ink transition hover:bg-page/80 disabled:cursor-not-allowed disabled:opacity-60"
              >
                清空选择
              </button>
              <button
                type="button"
                onClick={() => void handleImportSelectedPapers()}
                disabled={selectedPaperIds.length === 0 || importingPapers}
                className="inline-flex items-center gap-2 rounded-2xl bg-primary px-4 py-2 text-sm font-medium text-white transition hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-60"
              >
                {importingPapers ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
                导入选中论文
              </button>
            </div>
          </div>
        </div>
      </Modal>

      <Modal
        open={showWorkspaceServerModal}
        onClose={() => {
          setShowWorkspaceServerModal(false);
          setWorkspaceServerEditingId(null);
          resetWorkspaceServerDraft();
        }}
        title="SSH 服务器管理"
        maxWidth="lg"
      >
        <div className="max-h-[72vh] space-y-4 overflow-y-auto pr-1">
          <div className="rounded-[20px] border border-border/70 bg-page/55 px-4 py-4">
            <div className="mb-3 flex items-center justify-between gap-2">
              <p className="text-sm font-semibold text-ink">
                {workspaceServerEditingId ? "编辑 SSH 服务器" : "新增 SSH 服务器"}
              </p>
              {workspaceServerEditingId ? (
                <button
                  type="button"
                  onClick={handleCreateWorkspaceServer}
                  className="rounded-lg border border-border/70 bg-white px-2.5 py-1 text-xs text-ink-secondary transition hover:border-primary/20 hover:text-primary"
                >
                  新建
                </button>
              ) : null}
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="space-y-1.5 text-xs text-ink-secondary">
                <span>名称</span>
                <input
                  value={workspaceServerDraft.label || ""}
                  onChange={(event) => setWorkspaceServerDraft((current) => ({ ...current, label: event.target.value }))}
                  placeholder="输入名称"
                  className="h-9 w-full rounded-lg border border-border/70 bg-white px-3 text-sm text-ink outline-none focus:border-primary/25"
                />
              </label>
              <label className="space-y-1.5 text-xs text-ink-secondary">
                <span>SSH 主机</span>
                <input
                  value={workspaceServerDraft.host || ""}
                  onChange={(event) => setWorkspaceServerDraft((current) => ({ ...current, host: event.target.value }))}
                  placeholder="server.example.com"
                  className="h-9 w-full rounded-lg border border-border/70 bg-white px-3 text-sm text-ink outline-none focus:border-primary/25"
                />
              </label>
              <label className="space-y-1.5 text-xs text-ink-secondary">
                <span>端口</span>
                <input
                  type="number"
                  min={1}
                  max={65535}
                  value={workspaceServerDraft.port || 22}
                  onChange={(event) => setWorkspaceServerDraft((current) => ({ ...current, port: Number(event.target.value || 22) }))}
                  placeholder="22"
                  className="h-9 w-full rounded-lg border border-border/70 bg-white px-3 text-sm text-ink outline-none focus:border-primary/25"
                />
              </label>
              <label className="space-y-1.5 text-xs text-ink-secondary">
                <span>用户名</span>
                <input
                  value={workspaceServerDraft.username || ""}
                  onChange={(event) => setWorkspaceServerDraft((current) => ({ ...current, username: event.target.value }))}
                  placeholder="输入用户名"
                  className="h-9 w-full rounded-lg border border-border/70 bg-white px-3 text-sm text-ink outline-none focus:border-primary/25"
                />
              </label>
              <label className="space-y-1.5 text-xs text-ink-secondary">
                <span>密码（可选）</span>
                <input
                  type="password"
                  value={workspaceServerDraft.password || ""}
                  onChange={(event) => setWorkspaceServerDraft((current) => ({ ...current, password: event.target.value }))}
                  placeholder={workspaceServerEditingId ? "留空保持原值" : "输入密码"}
                  className="h-9 w-full rounded-lg border border-border/70 bg-white px-3 text-sm text-ink outline-none focus:border-primary/25"
                />
              </label>
              <label className="space-y-1.5 text-xs text-ink-secondary md:col-span-2">
                <span>私钥（可选）</span>
                <textarea
                  value={workspaceServerDraft.private_key || ""}
                  onChange={(event) => setWorkspaceServerDraft((current) => ({ ...current, private_key: event.target.value }))}
                  placeholder={workspaceServerEditingId ? "留空保持原值" : "输入私钥或路径"}
                  className="min-h-[96px] w-full rounded-lg border border-border/70 bg-white px-3 py-2 text-xs text-ink outline-none focus:border-primary/25"
                />
              </label>
              <label className="space-y-1.5 text-xs text-ink-secondary">
                <span>私钥口令（可选）</span>
                <input
                  type="password"
                  value={workspaceServerDraft.passphrase || ""}
                  onChange={(event) => setWorkspaceServerDraft((current) => ({ ...current, passphrase: event.target.value }))}
                  placeholder={workspaceServerEditingId ? "留空保持原值" : "输入口令"}
                  className="h-9 w-full rounded-lg border border-border/70 bg-white px-3 text-sm text-ink outline-none focus:border-primary/25"
                />
              </label>
              <label className="space-y-1.5 text-xs text-ink-secondary">
                <span>远程工作区目录（可选）</span>
                <input
                  value={workspaceServerDraft.workspace_root || ""}
                  onChange={(event) => setWorkspaceServerDraft((current) => ({ ...current, workspace_root: event.target.value }))}
                  placeholder="/home/user/project"
                  className="h-9 w-full rounded-lg border border-border/70 bg-white px-3 text-sm text-ink outline-none focus:border-primary/25"
                />
              </label>
              <label className="inline-flex items-center gap-2 text-xs text-ink-secondary">
                <input
                  type="checkbox"
                  checked={workspaceServerDraft.enabled ?? true}
                  onChange={(event) => setWorkspaceServerDraft((current) => ({ ...current, enabled: event.target.checked }))}
                  className="h-4 w-4 rounded border-border/70"
                />
                启用该服务器
              </label>
            </div>
            {workspaceServerProbeResult ? (
              <div className={`mt-3 rounded-xl border px-3 py-2 text-xs ${workspaceServerProbeSuccess ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-amber-200 bg-amber-50 text-amber-700"}`}>
                {workspaceServerProbeResult}
              </div>
            ) : null}
            <div className="mt-3 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => void handleProbeWorkspaceServer()}
                disabled={workspaceServerProbeLoading}
                className="inline-flex items-center gap-2 rounded-xl border border-border/70 bg-white px-3 py-2 text-xs font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
              >
                {workspaceServerProbeLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                测试连接
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowWorkspaceServerModal(false);
                  setWorkspaceServerEditingId(null);
                  resetWorkspaceServerDraft();
                }}
                className="rounded-xl border border-border/70 bg-white px-3 py-2 text-xs font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void handleSaveWorkspaceServer()}
                disabled={workspaceServerSaving}
                className="inline-flex items-center gap-2 rounded-xl bg-primary px-3 py-2 text-xs font-medium text-white transition hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-60"
              >
                {workspaceServerSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                {workspaceServerEditingId ? "保存修改" : "新增 SSH 服务器"}
              </button>
            </div>
          </div>

          <div className="rounded-[20px] border border-border/70 bg-page/55 px-4 py-4">
            <p className="mb-2 text-sm font-semibold text-ink">已配置 SSH 服务器</p>
            <div className="max-h-[280px] space-y-2 overflow-y-auto pr-1">
              {workspaceServers.filter((item) => isRemoteWorkspaceServer(item)).length === 0 ? (
                <div className="rounded-xl border border-border/70 bg-white/80 px-3 py-3 text-xs text-ink-tertiary">
                  还没有 SSH 服务器配置。
                </div>
              ) : (
                workspaceServers
                  .filter((item) => isRemoteWorkspaceServer(item))
                  .map((server) => (
                    <div key={server.id} className="rounded-xl border border-border/70 bg-white px-3 py-2.5">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium text-ink">{server.label}</p>
                          <p className="mt-0.5 truncate text-[11px] text-ink-tertiary">{server.host || server.base_url || "-"}</p>
                          <p className="mt-1 text-[10px] text-ink-tertiary">
                            状态：{server.phase || "unknown"}
                            {server.port ? ` · port: ${server.port}` : ""}
                            {server.username ? ` · user: ${server.username}` : ""}
                            {server.password_masked ? ` · pwd: ${server.password_masked}` : ""}
                            {server.private_key_masked ? ` · key: ${server.private_key_masked}` : ""}
                            {server.auth_mode ? ` · auth: ${server.auth_mode}` : ""}
                            {server.workspace_root ? ` · root: ${server.workspace_root}` : ""}
                          </p>
                        </div>
                        <div className="flex items-center gap-1">
                          <button
                            type="button"
                            onClick={() => handleEditWorkspaceServer(server)}
                            className="rounded-md border border-border/70 bg-page/70 px-2 py-1 text-[10px] text-ink-secondary transition hover:border-primary/20 hover:text-primary"
                          >
                            编辑
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleDeleteWorkspaceServer(server)}
                            disabled={workspaceServerDeletingId === server.id}
                            className="rounded-md border border-red-200 bg-red-50 px-2 py-1 text-[10px] text-red-600 transition hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {workspaceServerDeletingId === server.id ? "删除中..." : "删除"}
                          </button>
                        </div>
                      </div>
                    </div>
                  ))
              )}
            </div>
          </div>
        </div>
      </Modal>

      <Modal
        open={showMcpModal}
        onClose={() => setShowMcpModal(false)}
        title="MCP 服务"
        maxWidth="lg"
      >
        <div className="max-h-[72vh] space-y-4 overflow-y-auto pr-1">
          <div className="rounded-[22px] border border-border/70 bg-page/55 px-4 py-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm font-semibold text-ink">
                ResearchOS MCP
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void refreshMcpState()}
                  disabled={mcpLoading}
                  className="rounded-2xl border border-border/70 bg-white px-4 py-2 text-sm font-medium text-ink transition hover:bg-page/70 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {mcpLoading ? "刷新中..." : "刷新"}
                </button>
              </div>
            </div>

            <div className="mt-3 flex flex-wrap gap-2 text-[12px]">
              <span className={cn(
                "inline-flex items-center rounded-full border px-3 py-1",
                builtinMcpAvailable
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                  : "border-warning/20 bg-warning-light text-warning",
              )}>
                {builtinMcpAvailable
                  ? `内置工具 ${builtinMcpToolCount}`
                  : "内置 MCP 不可用"}
              </span>
              <span className="inline-flex items-center rounded-full border border-border/70 bg-white px-3 py-1 text-ink-secondary">
                自定义配置 {configuredCustomMcpServers.length}
              </span>
              {policySyncState === "error" && (
                <span className="inline-flex items-center rounded-full border border-error/20 bg-error-light px-3 py-1 text-error">
                  权限同步失败
                </span>
              )}
            </div>
          </div>

          {mcpLoading && mcpServers.length === 0 ? (
            <div className="flex items-center justify-center gap-2 rounded-[22px] border border-border/70 bg-page/55 px-4 py-8 text-sm text-ink-secondary">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在读取 MCP 状态...
            </div>
          ) : (
            <div className="space-y-4">
              {mcpError && (
                <div className="rounded-[18px] border border-warning/20 bg-warning-light px-4 py-3 text-sm text-warning">
                  {mcpError}
                </div>
              )}

              {mcpServers.length === 0 ? (
                <div className="rounded-[22px] border border-dashed border-border/80 bg-page/45 px-4 py-6 text-sm text-ink-secondary">
                  暂无 MCP 配置
                </div>
              ) : (
                mcpServers.map((server) => {
                  const serverToolCount = Number(server.tool_count ?? server.tools?.length ?? 0) || 0;
                  const builtinServerAvailable = server.builtin
                    ? Boolean(server.connected || serverToolCount > 0 || mcpRuntime?.builtin_ready || builtinMcpToolCount > 0)
                    : Boolean(server.connected || serverToolCount > 0);
                  const detail = server.builtin
                    ? (builtinServerAvailable
                      ? `会话启动时自动注入，当前共 ${Math.max(serverToolCount, builtinMcpToolCount)} 个工具`
                      : (server.last_error || "内置 MCP 当前不可用"))
                    : (server.transport === "stdio"
                      ? `${server.command || "未配置命令"} ${(server.args || []).join(" ")}`
                      : server.url || "未配置 URL");
                  return (
                    <div key={server.name} className="flex items-center justify-between gap-3 rounded-[20px] border border-border/70 bg-white/82 px-4 py-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <div className="text-sm font-semibold text-ink">{server.name}</div>
                          <span className="rounded-full border border-border/70 bg-page/55 px-2 py-0.5 text-[10px] text-ink-tertiary">
                            {server.transport.toUpperCase()}
                          </span>
                          <span className={cn(
                            "rounded-full border px-2 py-0.5 text-[10px]",
                            server.builtin
                              ? builtinServerAvailable
                                ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                                : "border-warning/20 bg-warning-light text-warning"
                              : server.enabled
                                ? "border-primary/20 bg-primary/10 text-primary"
                                : "border-border/70 bg-page/55 text-ink-tertiary",
                          )}>
                            {server.builtin ? (builtinServerAvailable ? "内置可用" : "内置异常") : server.enabled ? "已配置" : "已禁用"}
                          </span>
                        </div>
                        <div className="mt-1 text-xs text-ink-secondary">{detail}</div>
                      </div>
                      {!server.builtin ? (
                        <div className="text-[11px] text-ink-tertiary">{server.enabled ? "已配置" : "未启用"}</div>
                      ) : null}
                    </div>
                  );
                })
              )}

              <div className="rounded-[22px] border border-border/70 bg-white/82 px-4 py-4">
                <div className="mb-2 flex items-center justify-between">
                  <p className="text-sm font-semibold text-ink">自定义 MCP 配置</p>
                  {mcpConfigLoading ? <Loader2 className="h-4 w-4 animate-spin text-ink-tertiary" /> : null}
                </div>

                <div className="space-y-2">
                  <label className="block">
                    <span className="mb-1 block text-xs text-ink-tertiary">名称</span>
                    <input
                      value={customMcpName}
                      onChange={(event) => setCustomMcpName(event.target.value)}
                      placeholder="mcp server name"
                      className="w-full rounded-xl border border-border/70 bg-white px-3 py-2 text-sm text-ink outline-none focus:border-primary/25"
                    />
                  </label>

                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <button
                      type="button"
                      onClick={() => setCustomMcpTransport("stdio")}
                      className={cn(
                        "rounded-xl border px-3 py-2 text-sm font-medium transition",
                        customMcpTransport === "stdio"
                          ? "border-primary/30 bg-primary/10 text-primary"
                          : "border-border/70 bg-white text-ink-secondary hover:border-primary/20 hover:text-primary",
                      )}
                    >
                      STDIO
                    </button>
                    <button
                      type="button"
                      onClick={() => setCustomMcpTransport("http")}
                      className={cn(
                        "rounded-xl border px-3 py-2 text-sm font-medium transition",
                        customMcpTransport === "http"
                          ? "border-primary/30 bg-primary/10 text-primary"
                          : "border-border/70 bg-white text-ink-secondary hover:border-primary/20 hover:text-primary",
                      )}
                    >
                      流式 HTTP
                    </button>
                  </div>

                  {customMcpTransport === "stdio" ? (
                    <div className="space-y-2">
                      <label className="block">
                        <span className="mb-1 block text-xs text-ink-tertiary">启动命令</span>
                        <input
                          value={customMcpCommand}
                          onChange={(event) => setCustomMcpCommand(event.target.value)}
                          placeholder="openai-dev-mcp serve-sqlite"
                          className="w-full rounded-xl border border-border/70 bg-white px-3 py-2 text-sm text-ink outline-none focus:border-primary/25"
                        />
                      </label>
                      <label className="block">
                        <span className="mb-1 block text-xs text-ink-tertiary">参数（每行一个）</span>
                        <textarea
                          value={customMcpArgsText}
                          onChange={(event) => setCustomMcpArgsText(event.target.value)}
                          placeholder="--port=4096"
                          className="h-20 w-full rounded-xl border border-border/70 bg-white px-3 py-2 text-sm text-ink outline-none focus:border-primary/25"
                        />
                      </label>
                      <label className="block">
                        <span className="mb-1 block text-xs text-ink-tertiary">环境变量（每行 `KEY=VALUE`）</span>
                        <textarea
                          value={customMcpEnvText}
                          onChange={(event) => setCustomMcpEnvText(event.target.value)}
                          placeholder="API_KEY=xxxx"
                          className="h-20 w-full rounded-xl border border-border/70 bg-white px-3 py-2 text-sm text-ink outline-none focus:border-primary/25"
                        />
                      </label>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <label className="block">
                        <span className="mb-1 block text-xs text-ink-tertiary">URL</span>
                        <input
                          value={customMcpUrl}
                          onChange={(event) => setCustomMcpUrl(event.target.value)}
                          placeholder="https://mcp.example.com/mcp"
                          className="w-full rounded-xl border border-border/70 bg-white px-3 py-2 text-sm text-ink outline-none focus:border-primary/25"
                        />
                      </label>
                      <label className="block">
                        <span className="mb-1 block text-xs text-ink-tertiary">标头（每行 `KEY=VALUE`）</span>
                        <textarea
                          value={customMcpHeadersText}
                          onChange={(event) => setCustomMcpHeadersText(event.target.value)}
                          placeholder="Authorization=Bearer xxx"
                          className="h-20 w-full rounded-xl border border-border/70 bg-white px-3 py-2 text-sm text-ink outline-none focus:border-primary/25"
                        />
                      </label>
                    </div>
                  )}

                  <button
                    type="button"
                    onClick={() => void handleSaveCustomMcp()}
                    disabled={mcpConfigSaving}
                    className="inline-flex h-9 items-center justify-center gap-1.5 rounded-xl bg-primary px-4 text-sm font-medium text-white transition hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {mcpConfigSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                    保存自定义 MCP
                  </button>
                </div>
              </div>

              {configuredMcpNames.length > 0 && (
                <div className="rounded-[22px] border border-border/70 bg-white/82 px-4 py-4">
                  <p className="mb-2 text-sm font-semibold text-ink">已配置 MCP</p>
                  <div className="space-y-2">
                    {configuredMcpNames.map((name) => (
                      <div key={name} className="flex items-center justify-between gap-3 rounded-xl border border-border/70 bg-page/40 px-3 py-2">
                        <span className="truncate text-sm text-ink">{name}</span>
                        <button
                          type="button"
                          onClick={() => void handleDeleteCustomMcp(name)}
                          disabled={mcpConfigSaving || name === "researchos"}
                          className="rounded-lg border border-border/70 bg-white px-3 py-1 text-xs font-medium text-ink-secondary transition hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-60"
                          title={name === "researchos" ? "内置 MCP 不支持删除" : "删除该 MCP 配置"}
                        >
                          删除
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </Modal>
    </>
  );
}
