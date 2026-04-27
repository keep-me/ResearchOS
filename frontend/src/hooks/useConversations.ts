/**
 * 对话历史管理 - localStorage 持久化（元信息 + session 绑定）
 */
import { useSyncExternalStore } from "react";
import { uid } from "@/lib/utils";
import type { AgentMode, AgentReasoningLevel } from "@/types";

const STORAGE_KEY = "researchos_conversations";
const LEGACY_STORAGE_KEY = "researchos_conversations";
const MAX_CONVERSATIONS = 100;
const DEFAULT_ASSISTANT_BACKEND_ID = "native";
const DEFAULT_ASSISTANT_BACKEND_LABEL = "Native（ResearchOS 内嵌内核）";
const LEGACY_DEFAULT_ASSISTANT_BACKEND_LABEL = "Claw（Rust CLI 内核）";

export interface ConversationMeta {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  workspacePath?: string | null;
  workspaceTitle?: string | null;
  workspaceServerId?: string | null;
  workspaceServerLabel?: string | null;
  effectiveWorkspacePath?: string | null;
  assistantSessionId?: string | null;
  assistantSessionDirectory?: string | null;
  assistantContextKey?: string | null;
  assistantBackendId?: string | null;
  assistantBackendLabel?: string | null;
  assistantMode?: AgentMode | null;
  assistantReasoningLevel?: AgentReasoningLevel | null;
  assistantSkillIds?: string[] | null;
  mountedPaperId?: string | null;
  mountedPaperTitle?: string | null;
  mountedPaperIds?: string[] | null;
  mountedPaperTitles?: string[] | null;
}

export interface ConversationWorkspace {
  path: string;
  title: string;
  effectivePath?: string | null;
  serverId?: string | null;
  serverLabel?: string | null;
}

export interface CreateConversationOptions {
  persist?: boolean;
}

export interface Conversation extends ConversationMeta {}

type ConversationWorkspaceLike =
  | ConversationWorkspace
  | Pick<ConversationWorkspace, "path" | "effectivePath" | "serverId" | "serverLabel">
  | Pick<ConversationMeta, "workspacePath" | "workspaceServerId" | "effectiveWorkspacePath">
  | null
  | undefined;

interface ConversationStoreSnapshot {
  metas: ConversationMeta[];
}

interface ConversationStoreState extends ConversationStoreSnapshot {
  initialized: boolean;
}

function isLegacyUpstreamWorkspacePath(path: string | null | undefined): boolean {
  const normalizedPath = String(path || "").trim().replace(/\\/g, "/").toLowerCase();
  if (!normalizedPath) return false;
  return /(^|\/)(aris)(\/|$)/.test(normalizedPath) || /(^|\/)auto-claude-code-research-in-sleep(\/|$)/.test(normalizedPath);
}

function normalizeLegacyWorkspaceTitle(
  value: string | null | undefined,
  workspacePath?: string | null | undefined,
): string | null {
  const title = String(value || "").trim();
  if (!title) return null;
  if (isLegacyUpstreamWorkspacePath(workspacePath)) return title;
  const normalized = title.toLowerCase();
  if (
    normalized === "aris"
    || normalized === "aris workspace"
    || normalized === "aris workbench"
    || title === "ARIS 工作区"
    || title === "ARIS 项目工作区"
  ) {
    return "项目工作区";
  }
  return title;
}

function getStoredItem(key: string, legacyKey?: string): string | null {
  const current = localStorage.getItem(key);
  if (current !== null) return current;
  if (!legacyKey) return null;
  return localStorage.getItem(legacyKey);
}

/**
 * 从 localStorage 加载对话列表（仅元信息）
 */
function loadMetas(): ConversationMeta[] {
  try {
    const raw = getStoredItem(STORAGE_KEY + "_index", LEGACY_STORAGE_KEY + "_index");
    if (!raw) return [];
    return JSON.parse(raw);
  } catch {
    return [];
  }
}

function saveMetas(metas: ConversationMeta[]) {
  localStorage.setItem(STORAGE_KEY + "_index", JSON.stringify(metas));
}

export function loadConversation(id: string): Conversation | null {
  try {
    const currentKey = `${STORAGE_KEY}_${id}`;
    const legacyKey = `${LEGACY_STORAGE_KEY}_${id}`;
    const raw = getStoredItem(currentKey, legacyKey);
    if (!raw) return null;
    const normalized = normalizeConversation(JSON.parse(raw));
    if (!normalized) return null;
    localStorage.setItem(currentKey, JSON.stringify(normalized));
    return normalized;
  } catch {
    return null;
  }
}

function saveConversation(conv: Conversation) {
  localStorage.setItem(`${STORAGE_KEY}_${conv.id}`, JSON.stringify(conv));
}

function removeConversation(id: string) {
  localStorage.removeItem(`${STORAGE_KEY}_${id}`);
  localStorage.removeItem(`${LEGACY_STORAGE_KEY}_${id}`);
}

function extractWorkspaceState(value: ConversationWorkspaceLike) {
  const item = (value || {}) as Partial<ConversationWorkspace> & Partial<ConversationMeta>;
  const sourcePath = (item.workspacePath ?? item.path ?? "").trim();
  const effectivePath = (item.effectiveWorkspacePath ?? item.effectivePath ?? sourcePath).trim();
  const serverId = (item.workspaceServerId ?? item.serverId ?? "").trim();
  return {
    sourcePath,
    effectivePath,
    serverId: serverId || "local",
  };
}

function normalizeWorkspaceServerId(serverId: string | null | undefined): string {
  return (serverId || "").trim().toLowerCase() || "local";
}

export function normalizeWorkspacePath(path: string | null | undefined): string {
  const trimmed = (path || "").trim();
  if (!trimmed) return "";
  const normalized = trimmed.replace(/[\\/]+/g, "/").replace(/\/+$/, "");
  if (!normalized) return "";
  return /^[A-Za-z]:\//.test(normalized) || normalized.startsWith("//")
    ? normalized.toLowerCase()
    : normalized;
}

export function getConversationWorkspaceKey(value: ConversationWorkspaceLike): string {
  const { sourcePath, effectivePath, serverId } = extractWorkspaceState(value);
  const normalizedPath = normalizeWorkspacePath(effectivePath || sourcePath);
  if (!normalizedPath) return "";
  return `${normalizeWorkspaceServerId(serverId)}::${normalizedPath}`;
}

function matchesConversationWorkspace(
  meta: Pick<ConversationMeta, "workspacePath" | "workspaceServerId" | "effectiveWorkspacePath"> | null | undefined,
  workspace: ConversationWorkspaceLike,
): boolean {
  const workspaceKey = getConversationWorkspaceKey(workspace);
  if (!workspaceKey) return false;
  return getConversationWorkspaceKey(meta) === workspaceKey;
}

/**
 * 自动生成对话标题（取首条用户输入前 30 字）
 */
export function deriveConversationTitle(text: string): string {
  const normalized = text.trim();
  if (!normalized) return "新对话";
  return normalized.length > 30 ? normalized.slice(0, 30) + "..." : normalized;
}

export function isUntouchedConversation(
  value: Pick<
    ConversationMeta,
    "title" | "mountedPaperId" | "mountedPaperIds" | "mountedPaperTitle" | "mountedPaperTitles"
  > | null | undefined,
): boolean {
  if (!value) return false;
  const title = String(value.title || "").trim() || "新对话";
  if (title !== "新对话") return false;
  if (String(value.mountedPaperId || "").trim()) return false;
  if (String(value.mountedPaperTitle || "").trim()) return false;
  if ((value.mountedPaperIds || []).filter(Boolean).length > 0) return false;
  if ((value.mountedPaperTitles || []).filter(Boolean).length > 0) return false;
  return true;
}

function normalizeStoredAssistantBackendId(value: string, label: string): string {
  const normalizedValue = value.trim().toLowerCase();
  const normalizedLabel = label.trim();
  if (!normalizedValue || normalizedValue === "researchos_native") {
    return DEFAULT_ASSISTANT_BACKEND_ID;
  }
  if (
    normalizedValue === "claw"
    && (!normalizedLabel || normalizedLabel === LEGACY_DEFAULT_ASSISTANT_BACKEND_LABEL)
  ) {
    return DEFAULT_ASSISTANT_BACKEND_ID;
  }
  return normalizedValue;
}

function defaultAssistantBackendLabel(backendId: string): string {
  if (backendId === "custom_acp") return "Custom ACP";
  if (backendId === DEFAULT_ASSISTANT_BACKEND_ID) return DEFAULT_ASSISTANT_BACKEND_LABEL;
  if (backendId === "claw") return LEGACY_DEFAULT_ASSISTANT_BACKEND_LABEL;
  return backendId;
}

function normalizeConversation(raw: unknown): Conversation | null {
  if (!raw || typeof raw !== "object") return null;
  const item = raw as Record<string, unknown>;
  const id = String(item.id || "").trim();
  if (!id) return null;
  const createdAt = String(item.createdAt || "").trim() || new Date().toISOString();
  const updatedAt = String(item.updatedAt || "").trim() || createdAt;
  const storedAssistantBackendId = typeof item.assistantBackendId === "string" ? item.assistantBackendId.trim() : "";
  const storedAssistantBackendLabel = typeof item.assistantBackendLabel === "string" ? item.assistantBackendLabel.trim() : "";
  const normalizedAssistantBackendId = normalizeStoredAssistantBackendId(
    storedAssistantBackendId,
    storedAssistantBackendLabel,
  );
  const normalizedAssistantBackendLabel = normalizedAssistantBackendId
    ? (
      !storedAssistantBackendLabel
      || (
        normalizedAssistantBackendId === DEFAULT_ASSISTANT_BACKEND_ID
        && storedAssistantBackendLabel === LEGACY_DEFAULT_ASSISTANT_BACKEND_LABEL
      )
        ? defaultAssistantBackendLabel(normalizedAssistantBackendId)
        : storedAssistantBackendLabel
    )
    : "";
  return {
    id,
    title: String(item.title || "").trim() || "新对话",
    createdAt,
    updatedAt,
    workspacePath: typeof item.workspacePath === "string" ? item.workspacePath : null,
    workspaceTitle: normalizeLegacyWorkspaceTitle(
      typeof item.workspaceTitle === "string" ? item.workspaceTitle : null,
      typeof item.effectiveWorkspacePath === "string"
        ? item.effectiveWorkspacePath
        : typeof item.workspacePath === "string"
          ? item.workspacePath
          : null,
    ),
    workspaceServerId: typeof item.workspaceServerId === "string" ? item.workspaceServerId : null,
    workspaceServerLabel: typeof item.workspaceServerLabel === "string" ? item.workspaceServerLabel : null,
    effectiveWorkspacePath: typeof item.effectiveWorkspacePath === "string" ? item.effectiveWorkspacePath : null,
    assistantSessionId: typeof item.assistantSessionId === "string" ? item.assistantSessionId : null,
    assistantSessionDirectory: typeof item.assistantSessionDirectory === "string" ? item.assistantSessionDirectory : null,
    assistantContextKey: typeof item.assistantContextKey === "string" ? item.assistantContextKey : null,
    assistantBackendId: normalizedAssistantBackendId || null,
    assistantBackendLabel: normalizedAssistantBackendLabel || null,
    assistantMode: item.assistantMode === "plan" || item.assistantMode === "build" ? item.assistantMode : null,
    assistantReasoningLevel:
      item.assistantReasoningLevel === "low"
      || item.assistantReasoningLevel === "medium"
      || item.assistantReasoningLevel === "high"
      || item.assistantReasoningLevel === "xhigh"
      || item.assistantReasoningLevel === "default"
        ? item.assistantReasoningLevel
        : null,
    assistantSkillIds: Array.isArray(item.assistantSkillIds)
      ? item.assistantSkillIds.map((value) => String(value || "").trim()).filter(Boolean)
      : null,
    mountedPaperId: typeof item.mountedPaperId === "string" ? item.mountedPaperId : null,
    mountedPaperTitle: typeof item.mountedPaperTitle === "string" ? item.mountedPaperTitle : null,
    mountedPaperIds: Array.isArray(item.mountedPaperIds)
      ? item.mountedPaperIds.map((value) => String(value || "").trim()).filter(Boolean)
      : null,
    mountedPaperTitles: Array.isArray(item.mountedPaperTitles)
      ? item.mountedPaperTitles.map((value) => String(value || "").trim()).filter(Boolean)
      : null,
  };
}

function conversationMeta(conv: Conversation): ConversationMeta {
  return {
    id: conv.id,
    title: conv.title,
    createdAt: conv.createdAt,
    updatedAt: conv.updatedAt,
    workspacePath: conv.workspacePath,
    workspaceTitle: conv.workspaceTitle,
    workspaceServerId: conv.workspaceServerId,
    workspaceServerLabel: conv.workspaceServerLabel,
    effectiveWorkspacePath: conv.effectiveWorkspacePath,
    assistantSessionId: conv.assistantSessionId,
    assistantSessionDirectory: conv.assistantSessionDirectory,
    assistantContextKey: conv.assistantContextKey,
    assistantBackendId: conv.assistantBackendId,
    assistantBackendLabel: conv.assistantBackendLabel,
    assistantMode: conv.assistantMode,
    assistantReasoningLevel: conv.assistantReasoningLevel,
    assistantSkillIds: conv.assistantSkillIds,
    mountedPaperId: conv.mountedPaperId,
    mountedPaperTitle: conv.mountedPaperTitle,
    mountedPaperIds: conv.mountedPaperIds,
    mountedPaperTitles: conv.mountedPaperTitles,
  };
}

const conversationListeners = new Set<() => void>();
let conversationState: ConversationStoreState = {
  metas: [],
  initialized: false,
};

function emitConversationStore() {
  for (const listener of conversationListeners) listener();
}

function readConversationStoreSnapshot(): ConversationStoreSnapshot {
  return {
    metas: loadMetas(),
  };
}

function ensureConversationStoreInitialized() {
  if (conversationState.initialized) return;
  conversationState = {
    ...readConversationStoreSnapshot(),
    initialized: true,
  };
}

function getConversationStoreSnapshot(): ConversationStoreSnapshot {
  ensureConversationStoreInitialized();
  return {
    metas: conversationState.metas,
  };
}

function commitConversationStore(next: ConversationStoreSnapshot) {
  conversationState = {
    ...next,
    initialized: true,
  };
  emitConversationStore();
}

function createConversationInternal(
  workspace?: ConversationWorkspace | null,
  options?: CreateConversationOptions,
): string {
  ensureConversationStoreInitialized();
  const now = new Date().toISOString();
  const id = uid();
  const conv: Conversation = {
    id,
    title: "新对话",
    createdAt: now,
    updatedAt: now,
    workspacePath: workspace?.path || null,
    workspaceTitle: normalizeLegacyWorkspaceTitle(workspace?.title || null, workspace?.effectivePath || workspace?.path || null),
    workspaceServerId: workspace?.serverId || null,
    workspaceServerLabel: workspace?.serverLabel || null,
    effectiveWorkspacePath: workspace?.effectivePath || workspace?.path || null,
    assistantSessionId: null,
    assistantSessionDirectory: null,
    assistantContextKey: null,
    assistantBackendId: null,
    assistantBackendLabel: null,
    assistantMode: null,
    assistantReasoningLevel: null,
    assistantSkillIds: null,
    mountedPaperId: null,
    mountedPaperTitle: null,
    mountedPaperIds: null,
    mountedPaperTitles: null,
  };

  const shouldPersist = options?.persist !== false;
  if (!shouldPersist) return id;

  saveConversation(conv);
  const nextMetas = [conversationMeta(conv), ...conversationState.metas].slice(0, MAX_CONVERSATIONS);
  saveMetas(nextMetas);
  commitConversationStore({ metas: nextMetas });
  return id;
}

function upsertConversationInternal(conv: Conversation) {
  ensureConversationStoreInitialized();
  saveConversation(conv);
  const meta = conversationMeta(conv);
  const hasExistingMeta = conversationState.metas.some((item) => item.id === conv.id);
  const metas = hasExistingMeta
    ? conversationState.metas.map((item) => (item.id === conv.id ? meta : item))
    : [meta, ...conversationState.metas].slice(0, MAX_CONVERSATIONS);
  saveMetas(metas);
  commitConversationStore({ metas });
}

function renameWorkspaceConversationsInternal(workspace: ConversationWorkspace, workspaceTitle: string) {
  ensureConversationStoreInitialized();
  if (!getConversationWorkspaceKey(workspace)) return;
  const normalizedWorkspaceTitle = normalizeLegacyWorkspaceTitle(
    workspaceTitle,
    workspace.effectivePath || workspace.path || null,
  ) || "项目工作区";

  const metas = conversationState.metas.map((meta) => {
    if (!matchesConversationWorkspace(meta, workspace)) {
      return meta;
    }
    const conv = loadConversation(meta.id);
    if (conv) {
      saveConversation({
        ...conv,
        workspaceTitle: normalizedWorkspaceTitle,
      });
    }
    return {
      ...meta,
      workspaceTitle: normalizedWorkspaceTitle,
    };
  });
  saveMetas(metas);
  commitConversationStore({ metas });
}

function clearWorkspaceConversationsInternal(workspace: ConversationWorkspace) {
  ensureConversationStoreInitialized();
  if (!getConversationWorkspaceKey(workspace)) return;

  const metas = conversationState.metas.map((meta) => {
    if (!matchesConversationWorkspace(meta, workspace)) {
      return meta;
    }
    const conv = loadConversation(meta.id);
    if (conv) {
      saveConversation({
        ...conv,
        workspacePath: null,
        workspaceTitle: null,
        workspaceServerId: null,
        workspaceServerLabel: null,
        effectiveWorkspacePath: null,
      });
    }
    return {
      ...meta,
      workspacePath: null,
      workspaceTitle: null,
      workspaceServerId: null,
      workspaceServerLabel: null,
      effectiveWorkspacePath: null,
    };
  });
  saveMetas(metas);
  commitConversationStore({ metas });
}

function deleteConversationInternal(id: string) {
  ensureConversationStoreInitialized();
  removeConversation(id);
  const metas = conversationState.metas.filter((meta) => meta.id !== id);
  saveMetas(metas);
  commitConversationStore({ metas });
}

function patchConversationInternal(id: string, patch: Partial<ConversationMeta>) {
  ensureConversationStoreInitialized();
  const targetId = id.trim();
  if (!targetId) return;
  const existing = loadConversation(targetId);
  if (!existing) return;

  const next: Conversation = {
    id: targetId,
    title: existing.title || "新对话",
    createdAt: existing.createdAt || new Date().toISOString(),
    updatedAt: existing.updatedAt || new Date().toISOString(),
    workspacePath: existing.workspacePath ?? null,
    workspaceTitle: existing.workspaceTitle ?? null,
    workspaceServerId: existing.workspaceServerId ?? null,
    workspaceServerLabel: existing.workspaceServerLabel ?? null,
    effectiveWorkspacePath: existing.effectiveWorkspacePath ?? null,
    assistantSessionId: existing.assistantSessionId ?? null,
    assistantSessionDirectory: existing.assistantSessionDirectory ?? null,
    assistantContextKey: existing.assistantContextKey ?? null,
    assistantBackendId: existing.assistantBackendId ?? null,
    assistantBackendLabel: existing.assistantBackendLabel ?? null,
    assistantMode: existing.assistantMode ?? null,
    assistantReasoningLevel: existing.assistantReasoningLevel ?? null,
    assistantSkillIds: existing.assistantSkillIds ?? null,
    mountedPaperId: existing.mountedPaperId ?? null,
    mountedPaperTitle: existing.mountedPaperTitle ?? null,
    mountedPaperIds: existing.mountedPaperIds ?? null,
    mountedPaperTitles: existing.mountedPaperTitles ?? null,
    ...patch,
  };
  upsertConversationInternal(next);
}

export const conversationStore = {
  subscribe(listener: () => void) {
    ensureConversationStoreInitialized();
    conversationListeners.add(listener);
    return () => conversationListeners.delete(listener);
  },
  getSnapshot: getConversationStoreSnapshot,
  createConversation: createConversationInternal,
  upsertConversation: upsertConversationInternal,
  deleteConversation: deleteConversationInternal,
  renameWorkspaceConversations: renameWorkspaceConversationsInternal,
  clearWorkspaceConversations: clearWorkspaceConversationsInternal,
  patchConversation: patchConversationInternal,
};

/**
 * 对话管理 Hook
 */
export function useConversations() {
  const snapshot = useSyncExternalStore(
    conversationStore.subscribe,
    conversationStore.getSnapshot,
    conversationStore.getSnapshot,
  );

  return {
    metas: snapshot.metas,
    createConversation: conversationStore.createConversation,
    upsertConversation: conversationStore.upsertConversation,
    deleteConversation: conversationStore.deleteConversation,
    renameWorkspaceConversations: conversationStore.renameWorkspaceConversations,
    clearWorkspaceConversations: conversationStore.clearWorkspaceConversations,
    patchConversation: conversationStore.patchConversation,
  };
}

/**
 * 按日期分组
 */
export function groupByDate(metas: ConversationMeta[]): { label: string; items: ConversationMeta[] }[] {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const weekAgo = new Date(today.getTime() - 7 * 86400000);

  const groups: Record<string, ConversationMeta[]> = {
    "今天": [],
    "昨天": [],
    "最近 7 天": [],
    "更早": [],
  };

  for (const m of metas) {
    const d = new Date(m.updatedAt);
    if (d >= today) groups["今天"].push(m);
    else if (d >= yesterday) groups["昨天"].push(m);
    else if (d >= weekAgo) groups["最近 7 天"].push(m);
    else groups["更早"].push(m);
  }

  return Object.entries(groups)
    .filter(([, items]) => items.length > 0)
    .map(([label, items]) => ({ label, items }));
}
