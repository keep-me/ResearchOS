import { useEffect, useState, type CSSProperties, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import type { AgentPermissionPreset } from "@/contexts/AgentWorkbenchContext";
import type { WorkspaceTerminalState } from "@/components/WorkspaceTerminal";
import type {
  AssistantExecPolicy,
  AssistantSessionDiffEntry,
  AssistantSessionRevertInfo,
  AssistantSkillItem,
  AssistantWorkspaceOverview,
  AssistantWorkspaceServer,
  AssistantWorkspaceTerminalSessionInfo,
  AgentMode,
  AgentReasoningLevel,
  Project,
  ProjectRun,
  ProjectWorkflowType,
} from "@/types";
import {
  normalizeSessionDiffEntries,
  normalizeSessionDiffEntry as normalizeAssistantSessionDiffEntry,
  normalizeSessionRevertInfo as normalizeAssistantSessionRevertInfo,
} from "@/features/assistantInstance/sessionProtocol";
import {
  BadgeCheck,
  BookOpen,
  Brain,
  CheckCircle2,
  Circle,
  Download,
  FileText,
  FolderTree,
  GitBranch,
  Globe,
  Newspaper,
  Play,
  RotateCcw,
  Search,
  Sparkles,
  TrendingUp,
  Upload,
} from "@/lib/lucide";

export const AGENT_DRAFT_PROMPT_KEY = "researchos.agent.draftPrompt";
export const LEGACY_AGENT_DRAFT_PROMPT_KEY = "researchos.agent.draftPrompt";
export const WORKFLOW_RUN_STORAGE_KEY = "researchos.agent.workflowRun";
export const WORKFLOW_LAUNCHER_PROJECT_KEY = "researchos.assistant.workflowLauncher.projectId";
export const WORKFLOW_LAUNCHER_WORKFLOW_KEY = "researchos.assistant.workflowLauncher.workflow";

export type WritablePermissionPreset = Exclude<AgentPermissionPreset, "custom">;

export const PERMISSION_POLICY_MAP: Record<WritablePermissionPreset, Partial<AssistantExecPolicy>> = {
  full_access: {
    workspace_access: "read_write",
    command_execution: "full",
    approval_mode: "off",
  },
  confirm: {
    workspace_access: "read_write",
    command_execution: "full",
    approval_mode: "on_request",
  },
};

export function matchesPermissionPreset(
  policy: AssistantExecPolicy,
  preset: WritablePermissionPreset,
) {
  const expected = PERMISSION_POLICY_MAP[preset];
  return policy.workspace_access === expected.workspace_access
    && policy.command_execution === expected.command_execution
    && policy.approval_mode === expected.approval_mode;
}

export const REASONING_LEVEL_OPTIONS: Array<{
  id: AgentReasoningLevel;
  label: string;
}> = [
  { id: "default", label: "默认" },
  { id: "low", label: "低" },
  { id: "medium", label: "中" },
  { id: "high", label: "高" },
  { id: "xhigh", label: "超高" },
];

export function truncateText(value: string, limit = 34) {
  return value.length > limit ? `${value.slice(0, limit)}...` : value;
}

export function deriveProjectName(path: string) {
  if (!path) return "未命名项目";
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "");
  const parts = normalized.split("/");
  return parts[parts.length - 1] || path;
}

export function normalizeAssistantWorkspacePath(value: string | null | undefined): string {
  const trimmed = (value || "").trim();
  if (!trimmed) return "";
  const normalizedSlashes = trimmed.replace(/\\/g, "/");
  if (/^\/app\/projects(?:\/|$)/i.test(normalizedSlashes)) {
    const suffix = normalizedSlashes.replace(/^\/app\/projects\/?/i, "");
    return suffix ? `projects/${suffix}` : "projects";
  }
  if (/^\/app(?:\/|$)/i.test(normalizedSlashes)) {
    const suffix = normalizedSlashes.replace(/^\/app\/?/i, "");
    return suffix || ".";
  }
  return trimmed;
}

export function pushWorkspaceCandidate(
  candidates: string[],
  seen: Set<string>,
  value: string | null | undefined,
) {
  const normalized = normalizeAssistantWorkspacePath(value);
  if (!normalized) return;
  const key = normalized.replace(/\\/g, "/").toLowerCase();
  if (seen.has(key)) return;
  seen.add(key);
  candidates.push(normalized);
}

export function buildAssistantWorkspaceCandidates(params: {
  primaryPath?: string | null;
  secondaryPath?: string | null;
  sessionPath?: string | null;
  conversationId?: string | null;
  includeRepoRootFallback?: boolean;
}): string[] {
  const seen = new Set<string>();
  const candidates: string[] = [];
  pushWorkspaceCandidate(candidates, seen, params.primaryPath);
  pushWorkspaceCandidate(candidates, seen, params.secondaryPath);
  pushWorkspaceCandidate(candidates, seen, params.sessionPath);
  if (params.conversationId) {
    pushWorkspaceCandidate(candidates, seen, `projects/default/${params.conversationId}`);
  }
  if (params.includeRepoRootFallback) {
    pushWorkspaceCandidate(candidates, seen, ".");
  }
  return candidates;
}

export function inferPermissionPresetFromPolicy(policy: AssistantExecPolicy | null | undefined): AgentPermissionPreset {
  if (!policy) return "confirm";
  if (matchesPermissionPreset(policy, "full_access")) return "full_access";
  if (matchesPermissionPreset(policy, "confirm")) return "confirm";
  return "custom";
}

export function getPermissionPresetLabel(preset: AgentPermissionPreset): string {
  if (preset === "full_access") return "自动确认";
  if (preset === "custom") return "自定义策略";
  return "需确认";
}

export function isTerminalProjectRunStatus(status: ProjectRun["status"] | null | undefined) {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}

/* ========== 工具元数据 ========== */

export const TOOL_META: Record<string, { icon: typeof Search; label: string }> = {
  search_papers: { icon: Search, label: "搜索论文" },
  paper_search: { icon: Search, label: "搜索论文" },
  search_literature: { icon: Search, label: "检索文献" },
  preview_external_paper_head: { icon: FileText, label: "目录预览" },
  preview_external_paper_section: { icon: BookOpen, label: "章节预读" },
  get_paper_detail: { icon: FileText, label: "论文详情" },
  paper_detail: { icon: FileText, label: "论文详情" },
  get_paper_analysis: { icon: FileText, label: "三轮分析" },
  paper_figures: { icon: FileText, label: "论文图表" },
  paper_import_arxiv: { icon: Download, label: "导入 arXiv" },
  paper_import_pdf: { icon: Upload, label: "导入 PDF" },
  paper_extract_figures: { icon: FileText, label: "提取图表任务" },
  paper_skim: { icon: BookOpen, label: "粗读任务" },
  paper_deep_read: { icon: BookOpen, label: "精读任务" },
  paper_reasoning: { icon: Brain, label: "推理链任务" },
  paper_embed: { icon: Brain, label: "向量嵌入任务" },
  task_status: { icon: TrendingUp, label: "任务状态" },
  task_list: { icon: TrendingUp, label: "任务列表" },
  get_similar_papers: { icon: Search, label: "相似论文" },
  get_citation_tree: { icon: Search, label: "引用树" },
  get_timeline: { icon: Search, label: "时间线" },
  research_kg_status: { icon: Brain, label: "KG 状态" },
  build_research_kg: { icon: Brain, label: "构建 KG" },
  graph_rag_query: { icon: Brain, label: "GraphRAG" },
  list_topics: { icon: Search, label: "主题列表" },
  get_system_status: { icon: Search, label: "系统状态" },
  search_web: { icon: Globe, label: "网页搜索" },
  websearch: { icon: Globe, label: "网页搜索" },
  webfetch: { icon: Globe, label: "网页抓取" },
  codesearch: { icon: Search, label: "代码搜索" },
  search_arxiv: { icon: Search, label: "搜索 arXiv" },
  ingest_arxiv: { icon: Download, label: "入库论文" },
  ingest_external_literature: { icon: Download, label: "导入外部论文" },
  skim_paper: { icon: BookOpen, label: "粗读论文" },
  deep_read_paper: { icon: BookOpen, label: "精读论文" },
  analyze_paper_rounds: { icon: BookOpen, label: "三轮分析" },
  embed_paper: { icon: Brain, label: "向量嵌入" },
  generate_wiki: { icon: FileText, label: "生成综述" },
  generate_daily_brief: { icon: Newspaper, label: "生成研究日报" },
  manage_subscription: { icon: BookOpen, label: "订阅管理" },
  idea_discovery: { icon: Sparkles, label: "Idea Discovery" },
  auto_review_loop: { icon: RotateCcw, label: "Auto Review Loop" },
  paper_writing: { icon: FileText, label: "Paper Writing" },
  research_pipeline: { icon: BadgeCheck, label: "Research Pipeline" },
  list_local_skills: { icon: Sparkles, label: "列出 Skills" },
  read_local_skill: { icon: Sparkles, label: "读取 Skill" },
  inspect_workspace: { icon: Search, label: "检查工作区" },
  read_workspace_file: { icon: FileText, label: "读取文件" },
  write_workspace_file: { icon: FileText, label: "写入文件" },
  replace_workspace_text: { icon: FileText, label: "修改文件" },
  run_workspace_command: { icon: Play, label: "执行命令" },
  get_workspace_task_status: { icon: TrendingUp, label: "任务状态" },
  ls: { icon: FolderTree, label: "列目录" },
  glob: { icon: Search, label: "Glob 查找" },
  grep: { icon: Search, label: "Grep 搜索" },
  read: { icon: FileText, label: "读取路径" },
  write: { icon: FileText, label: "写入路径" },
  edit: { icon: FileText, label: "编辑路径" },
  multiedit: { icon: FileText, label: "批量编辑" },
  bash: { icon: Play, label: "Bash 命令" },
  todoread: { icon: CheckCircle2, label: "读取待办" },
  todowrite: { icon: CheckCircle2, label: "更新待办" },
  task: { icon: Sparkles, label: "子任务" },
  question: { icon: Sparkles, label: "用户问题" },
  plan_exit: { icon: Sparkles, label: "结束规划" },
};

export const MODE_OPTIONS: Array<{
  id: AgentMode;
  label: string;
}> = [
  { id: "build", label: "Build" },
  { id: "plan", label: "Plan" },
];

export function getToolMeta(name: string) {
  return TOOL_META[name] || { icon: Circle, label: name };
}

export type SlashCommandAction =
  | "new_chat"
  | "open_workspace_panel"
  | "toggle_terminal"
  | "focus_model"
  | "open_mcp"
  | "cycle_agent_mode"
  | "init_git"
  | "open_workspace";

export interface SlashCommandItem {
  id: string;
  trigger: string;
  description: string;
  action?: SlashCommandAction;
  insertText?: string;
  source: "builtin" | "workflow" | "skill";
  workflowType?: ProjectWorkflowType;
}

export interface WorkspaceTerminalSession {
  id: string;
  name: string;
  info: AssistantWorkspaceTerminalSessionInfo;
  state: WorkspaceTerminalState;
  lastExitCode?: number | null;
}

export interface WorkspaceFileTreeNode {
  name: string;
  path: string;
  type: "dir" | "file";
  children: WorkspaceFileTreeNode[];
}

export type WorkspacePanelTab = "files" | "git" | "review" | "artifact";

export const AGENT_WORKSPACE_PANEL_WIDTH_KEY = "researchos.agent.workspacePanelWidth";
export const AGENT_TERMINAL_PANEL_HEIGHT_KEY = "researchos.agent.terminalPanelHeight";
export const AGENT_WORKSPACE_PANEL_MIN_WIDTH = 300;
export const AGENT_WORKSPACE_PANEL_MAX_WIDTH = 560;
export const AGENT_WORKSPACE_PANEL_DEFAULT_WIDTH = 360;
export const AGENT_TERMINAL_PANEL_MIN_HEIGHT = 168;
export const AGENT_TERMINAL_PANEL_MAX_HEIGHT = 520;
export const AGENT_TERMINAL_PANEL_DEFAULT_HEIGHT = 188;
export const AGENT_WORKSPACE_OVERVIEW_DEPTH = 4;
// `0` means "no entry cap" on the backend overview endpoint.
export const AGENT_WORKSPACE_OVERVIEW_MAX_ENTRIES = 0;

export function readAgentWorkspacePanelWidth(): number {
  if (typeof window === "undefined") return AGENT_WORKSPACE_PANEL_DEFAULT_WIDTH;
  const raw = Number.parseInt(window.localStorage.getItem(AGENT_WORKSPACE_PANEL_WIDTH_KEY) || "", 10);
  if (Number.isFinite(raw)) {
    return Math.min(AGENT_WORKSPACE_PANEL_MAX_WIDTH, Math.max(AGENT_WORKSPACE_PANEL_MIN_WIDTH, raw));
  }
  return AGENT_WORKSPACE_PANEL_DEFAULT_WIDTH;
}

export function readAgentTerminalPanelHeight(): number {
  if (typeof window === "undefined") return AGENT_TERMINAL_PANEL_DEFAULT_HEIGHT;
  const raw = Number.parseInt(window.localStorage.getItem(AGENT_TERMINAL_PANEL_HEIGHT_KEY) || "", 10);
  if (Number.isFinite(raw)) {
    return Math.min(AGENT_TERMINAL_PANEL_MAX_HEIGHT, Math.max(AGENT_TERMINAL_PANEL_MIN_HEIGHT, raw));
  }
  return AGENT_TERMINAL_PANEL_DEFAULT_HEIGHT;
}

export function buildWorkspaceFileTree(paths: string[]): WorkspaceFileTreeNode[] {
  const root: WorkspaceFileTreeNode[] = [];
  const dirMap = new Map<string, WorkspaceFileTreeNode>();

  const ensureDir = (dirPath: string) => {
    const normalized = dirPath.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
    if (!normalized) return null;
    const existing = dirMap.get(normalized);
    if (existing) return existing;
    const parts = normalized.split("/").filter(Boolean);
    const name = parts[parts.length - 1] || normalized;
    const parentPath = parts.slice(0, -1).join("/");
    const parent = parentPath ? ensureDir(parentPath) : null;
    const node: WorkspaceFileTreeNode = { name, path: normalized, type: "dir", children: [] };
    if (parent) {
      parent.children.push(node);
    } else {
      root.push(node);
    }
    dirMap.set(normalized, node);
    return node;
  };

  for (const rawPath of paths) {
    const normalized = (rawPath || "").replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
    if (!normalized) continue;
    const parts = normalized.split("/").filter(Boolean);
    if (parts.length === 0) continue;
    const fileName = parts[parts.length - 1];
    const dirPath = parts.slice(0, -1).join("/");
    const parent = dirPath ? ensureDir(dirPath) : null;
    const fileNode: WorkspaceFileTreeNode = {
      name: fileName,
      path: normalized,
      type: "file",
      children: [],
    };
    if (parent) {
      parent.children.push(fileNode);
    } else {
      root.push(fileNode);
    }
  }

  const sortNodes = (nodes: WorkspaceFileTreeNode[]) => {
    nodes.sort((a, b) => {
      if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
      return a.name.localeCompare(b.name, "zh-CN");
    });
    nodes.forEach((node) => {
      if (node.children.length > 0) sortNodes(node.children);
    });
  };
  sortNodes(root);
  return root;
}

export function buildWorkspaceTreeFromOverview(
  overview: AssistantWorkspaceOverview | null | undefined,
): WorkspaceFileTreeNode[] {
  const tree = String(overview?.tree || "").trim();
  if (!tree) return buildWorkspaceFileTree(overview?.files || []);

  const lines = tree
    .split(/\r?\n/)
    .map((line) => line.replace(/\t/g, "  ").trimEnd())
    .filter(Boolean);
  if (lines.length <= 1) return buildWorkspaceFileTree(overview?.files || []);

  const root: WorkspaceFileTreeNode[] = [];
  const dirStack: WorkspaceFileTreeNode[] = [];

  for (const line of lines.slice(1)) {
    const match = line.match(/^(\s*)-\s(.+)$/);
    if (!match) continue;
    const depth = Math.floor(match[1].length / 2);
    const rawName = match[2].trim();
    const isDir = rawName.endsWith("/");
    const name = isDir ? rawName.slice(0, -1) : rawName;
    if (!name) continue;

    while (dirStack.length > depth) dirStack.pop();
    const parent = dirStack[dirStack.length - 1] || null;
    const path = parent ? `${parent.path}/${name}` : name;
    const node: WorkspaceFileTreeNode = {
      name,
      path,
      type: isDir ? "dir" : "file",
      children: [],
    };

    if (parent) {
      parent.children.push(node);
    } else {
      root.push(node);
    }

    if (isDir) {
      dirStack[depth] = node;
      dirStack.length = depth + 1;
    }
  }

  return root.length > 0 ? root : buildWorkspaceFileTree(overview?.files || []);
}

export function parseMultilineArgs(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

export function parseMultilineKeyValue(value: string): Record<string, string> {
  const result: Record<string, string> = {};
  value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .forEach((line) => {
      const splitIndex = line.includes("=") ? line.indexOf("=") : line.indexOf(":");
      if (splitIndex <= 0) return;
      const key = line.slice(0, splitIndex).trim();
      const val = line.slice(splitIndex + 1).trim();
      if (!key || !val) return;
      result[key] = val;
    });
  return result;
}

export function buildTerminalSessionName(index: number) {
  return `终端 ${index}`;
}

export function isRemoteWorkspaceServer(server?: AssistantWorkspaceServer | null) {
  return server?.kind === "ssh" || server?.kind === "remote";
}

export function extractSlashQuery(value: string): string | null {
  const normalized = String(value || "").replace(/／/g, "/").trim();
  const matched = normalized.match(/^\/([^\s/]*)$/);
  return matched ? (matched[1] || "") : null;
}

export function sortSkillSlashItems(a: AssistantSkillItem, b: AssistantSkillItem): number {
  const sourceRank = { project: 0, codex: 1, agents: 2 } as const;
  const aRank = sourceRank[a.source] ?? 99;
  const bRank = sourceRank[b.source] ?? 99;
  if (aRank !== bRank) return aRank - bRank;

  const aProject = `${a.relative_path} ${a.name}`.toLowerCase().includes("researchos");
  const bProject = `${b.relative_path} ${b.name}`.toLowerCase().includes("researchos");
  if (aProject !== bProject) return aProject ? -1 : 1;

  const pathOrder = a.relative_path.localeCompare(b.relative_path, "zh-CN");
  if (pathOrder !== 0) return pathOrder;
  return a.name.localeCompare(b.name, "zh-CN");
}

export function buildSkillSlashTrigger(skill: AssistantSkillItem): string {
  const raw = String(skill.relative_path || skill.name || "").trim().toLowerCase();
  return raw
    .replace(/^researchos[-/]/, "")
    .replace(/[\\/]+/g, "-")
    .replace(/\s+/g, "-");
}

export const BUILTIN_SLASH_COMMANDS: SlashCommandItem[] = [
  { id: "session.new", trigger: "new", description: "新建聊天", action: "new_chat", source: "builtin" },
  { id: "file.open", trigger: "open", description: "打开文件/目录面板", action: "open_workspace_panel", source: "builtin" },
  { id: "terminal.toggle", trigger: "terminal", description: "切换终端面板", action: "toggle_terminal", source: "builtin" },
  { id: "model.choose", trigger: "model", description: "选择模型", action: "focus_model", source: "builtin" },
  { id: "mcp.toggle", trigger: "mcp", description: "打开 MCP 面板", action: "open_mcp", source: "builtin" },
  { id: "agent.cycle", trigger: "agent", description: "切换 Agent 模式", action: "cycle_agent_mode", source: "builtin" },
  { id: "project.git.init", trigger: "git", description: "初始化 Git 仓库", action: "init_git", source: "builtin" },
  { id: "workspace.open", trigger: "workspace", description: "打开当前工作区目录", action: "open_workspace", source: "builtin" },
];

export const WORKFLOW_SLASH_COMMANDS: SlashCommandItem[] = [
  {
    id: "workflow.idea_discovery",
    trigger: "idea-discovery",
    description: "Workflow 1 · 想法发现",
    insertText: "/idea-discovery ",
    source: "workflow",
    workflowType: "idea_discovery",
  },
  {
    id: "workflow.experiment_bridge",
    trigger: "experiment-bridge",
    description: "Workflow 1.5 · 实验桥接",
    insertText: "/experiment-bridge ",
    source: "workflow",
    workflowType: "run_experiment",
  },
  {
    id: "workflow.auto_review_loop",
    trigger: "auto-review-loop",
    description: "Workflow 2 · 自动评审循环",
    insertText: "/auto-review-loop ",
    source: "workflow",
    workflowType: "auto_review_loop",
  },
  {
    id: "workflow.paper_writing",
    trigger: "paper-writing",
    description: "Workflow 3 · 论文写作",
    insertText: "/paper-writing ",
    source: "workflow",
    workflowType: "paper_writing",
  },
  {
    id: "workflow.rebuttal",
    trigger: "rebuttal",
    description: "Workflow 4 · Rebuttal",
    insertText: "/rebuttal ",
    source: "workflow",
    workflowType: "rebuttal",
  },
  {
    id: "workflow.research_pipeline",
    trigger: "research-pipeline",
    description: "One-Click · Research Pipeline",
    insertText: "/research-pipeline ",
    source: "workflow",
    workflowType: "full_pipeline",
  },
];

export function normalizeComparableWorkspacePath(value: string | null | undefined): string {
  return normalizeAssistantWorkspacePath(value).replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

export function normalizeComparableServerId(value: string | null | undefined): string {
  const trimmed = String(value || "").trim();
  return trimmed || "local";
}

export function inferWorkflowExecutionCommand(value: string): string {
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

export function projectMatchesWorkspace(project: Project, workspacePath: string, workspaceServerId: string): boolean {
  const projectPath = normalizeComparableWorkspacePath(project.workspace_path || project.remote_workdir || project.workdir || "");
  const projectServerId = normalizeComparableServerId(project.workspace_server_id);
  if (!projectPath || !workspacePath) return false;
  return projectPath === workspacePath && projectServerId === workspaceServerId;
}

export function findWorkflowSlashCommand(trigger: string): SlashCommandItem | null {
  const normalizedTrigger = trigger.trim().toLowerCase();
  return WORKFLOW_SLASH_COMMANDS.find((item) => item.trigger.toLowerCase() === normalizedTrigger) || null;
}

export function resolveWorkflowSlashLaunchRequest(
  inputText: string,
  activeCommand: SlashCommandItem | null,
): {
  command: SlashCommandItem;
  workflowType: ProjectWorkflowType;
  prompt: string;
  trigger: string;
} | null {
  if (activeCommand?.workflowType) {
    return {
      command: activeCommand,
      workflowType: activeCommand.workflowType,
      prompt: inputText.trim(),
      trigger: activeCommand.trigger,
    };
  }

  const matched = inputText.trim().match(/^\/([a-z0-9-]+)(?:\s+([\s\S]*))?$/i);
  if (!matched) return null;
  const trigger = String(matched[1] || "").trim().toLowerCase();
  const command = findWorkflowSlashCommand(trigger);
  if (!command?.workflowType) return null;
  return {
    command,
    workflowType: command.workflowType,
    prompt: String(matched[2] || "").trim(),
    trigger: command.trigger,
  };
}

export const IDEA_DISCOVERY_INTENT_PATTERNS: RegExp[] = [
  /研究想法/,
  /研究点子/,
  /研究创意/,
  /研究选题/,
  /idea\s*discovery/i,
  /\bresearch ideas?\b/i,
  /\bresearch idea generation\b/i,
  /\bgenerate (?:promising )?research ideas?\b/i,
  /发掘.*研究想法/,
  /挖掘.*研究想法/,
  /围绕.*方向.*研究想法/,
  /潜力.*研究想法/,
  /帮我.*想.*研究想法/,
  /找.*研究想法/,
];

export function resolveWorkflowIntentLaunchRequest(
  inputText: string,
): {
  command: SlashCommandItem;
  workflowType: ProjectWorkflowType;
  prompt: string;
  trigger: string;
} | null {
  const prompt = inputText.trim();
  if (!prompt || prompt.startsWith("/")) return null;
  if (!IDEA_DISCOVERY_INTENT_PATTERNS.some((pattern) => pattern.test(prompt))) {
    return null;
  }
  const command = findWorkflowSlashCommand("idea-discovery");
  if (!command?.workflowType) return null;
  return {
    command,
    workflowType: command.workflowType,
    prompt,
    trigger: command.trigger,
  };
}

export const WORKSPACE_PANEL_TABS: Array<{
  id: WorkspacePanelTab;
  label: string;
  icon: typeof FolderTree;
}> = [
  { id: "files", label: "文件", icon: FolderTree },
  { id: "git", label: "Git", icon: GitBranch },
  { id: "review", label: "审查", icon: CheckCircle2 },
];

export interface SessionPatchCheckpoint {
  messageId: string;
  label: string;
  createdAt: number;
  additions: number;
  deletions: number;
  fileCount: number;
  diffs: AssistantSessionDiffEntry[];
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? value as Record<string, unknown> : null;
}

export function toSessionDiffEntry(value: unknown): AssistantSessionDiffEntry | null {
  return normalizeAssistantSessionDiffEntry(value);
}

export function collectSessionDiffEntries(value: unknown): AssistantSessionDiffEntry[] {
  return normalizeSessionDiffEntries(value);
}

export function toSessionRevertInfo(value: unknown): AssistantSessionRevertInfo | null {
  return normalizeAssistantSessionRevertInfo(value);
}

export function getSessionDiffIdentity(entry: AssistantSessionDiffEntry): string {
  return [
    String(entry.workspace_server_id || "local").trim() || "local",
    String(entry.path || entry.file || "").trim(),
  ].join("::");
}

export function getSessionDiffTarget(entry: AssistantSessionDiffEntry): string {
  return String(entry.file || entry.path || "未命名文件").trim() || "未命名文件";
}

export function getSessionDiffStatusLabel(status: string | null | undefined): string {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "added") return "新增";
  if (normalized === "deleted") return "删除";
  return "修改";
}

export function getSessionDiffStatusTone(status: string | null | undefined): "success" | "error" | "info" | "neutral" {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "added") return "success";
  if (normalized === "deleted") return "error";
  if (normalized === "modified") return "info";
  return "neutral";
}

export function getSessionMessagePreview(message: Record<string, unknown>): string {
  const parts = Array.isArray(message.parts) ? message.parts : [];
  const text = parts
    .map((part) => asRecord(part))
    .filter((part): part is Record<string, unknown> => Boolean(part))
    .filter((part) => String(part.type || "").trim().toLowerCase() === "text")
    .map((part) => String(part.text || part.content || "").trim())
    .filter(Boolean)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
  if (text) return truncateText(text, 72);
  const tool = parts
    .map((part) => asRecord(part))
    .filter((part): part is Record<string, unknown> => Boolean(part))
    .find((part) => String(part.type || "").trim().toLowerCase() === "tool");
  if (tool) {
    const summary = String(tool.summary || tool.tool || "").trim();
    if (summary) return truncateText(summary, 72);
  }
  return "代码改动检查点";
}

export function buildSessionPatchCheckpoints(messages: Array<Record<string, unknown>>): SessionPatchCheckpoint[] {
  const checkpoints: SessionPatchCheckpoint[] = [];
  for (const message of messages) {
    const info = asRecord(message.info) || {};
    if (String(info.role || "").trim() !== "assistant") continue;
    const parts = Array.isArray(message.parts) ? message.parts : [];
    const diffs = parts.flatMap((part) => {
      const payload = asRecord(part);
      if (!payload || String(payload.type || "").trim().toLowerCase() !== "patch") return [];
      if (Array.isArray(payload.diffs)) {
        return collectSessionDiffEntries(payload.diffs);
      }
      if (Array.isArray(payload.patches)) {
        return collectSessionDiffEntries(payload.patches);
      }
      const single = toSessionDiffEntry(payload);
      return single ? [single] : [];
    });
    if (diffs.length === 0) continue;
    const uniqueFiles = new Set(diffs.map((entry) => getSessionDiffTarget(entry)));
    const time = asRecord(info.time) || {};
    checkpoints.push({
      messageId: String(info.id || "").trim(),
      label: getSessionMessagePreview(message),
      createdAt: Number(time.updated || time.created || Date.now()),
      additions: diffs.reduce((sum, entry) => sum + Number(entry.additions || 0), 0),
      deletions: diffs.reduce((sum, entry) => sum + Number(entry.deletions || 0), 0),
      fileCount: uniqueFiles.size,
      diffs,
    });
  }
  return checkpoints
    .filter((item) => item.messageId)
    .sort((left, right) => right.createdAt - left.createdAt);
}

export function formatSessionReviewTimestamp(value: number | null | undefined): string {
  if (!value || !Number.isFinite(value)) return "未记录";
  return new Date(value).toLocaleString("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export interface FloatingPopoverProps {
  open: boolean;
  anchorRef: RefObject<HTMLElement | null>;
  panelRef?: RefObject<HTMLDivElement | null>;
  align?: "left" | "right";
  className: string;
  children: ReactNode;
}

export function FloatingPopover({
  open,
  anchorRef,
  panelRef,
  align = "left",
  className,
  children,
}: FloatingPopoverProps) {
  const [floatingStyle, setFloatingStyle] = useState<CSSProperties | null>(null);

  useEffect(() => {
    if (!open) {
      setFloatingStyle(null);
      return;
    }

    const updatePosition = () => {
      const anchor = anchorRef.current;
      if (!anchor) return;

      const rect = anchor.getBoundingClientRect();
      const viewportWidth = window.innerWidth;
      const estimatedWidth = panelRef?.current?.offsetWidth || Math.min(384, Math.max(240, viewportWidth - 32));
      const bottom = Math.max(16, window.innerHeight - rect.top + 8);

      if (align === "right") {
        const right = Math.min(
          Math.max(16, viewportWidth - rect.right),
          Math.max(16, viewportWidth - estimatedWidth - 16),
        );
        setFloatingStyle({ position: "fixed", right, bottom, zIndex: 80 });
        return;
      }

      const left = Math.min(
        Math.max(16, rect.left),
        Math.max(16, viewportWidth - estimatedWidth - 16),
      );
      setFloatingStyle({ position: "fixed", left, bottom, zIndex: 80 });
    };

    updatePosition();
    const rafId = window.requestAnimationFrame(updatePosition);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);

    return () => {
      window.cancelAnimationFrame(rafId);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [align, anchorRef, open, panelRef]);

  if (!open || !floatingStyle || typeof document === "undefined") return null;

  return createPortal(
    <div ref={panelRef} className={className} style={floatingStyle}>
      {children}
    </div>,
    document.body,
  );
}
