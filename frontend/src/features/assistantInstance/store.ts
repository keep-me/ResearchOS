import {
  conversationStore,
  deriveConversationTitle,
  loadConversation,
  type Conversation,
  type ConversationMeta,
  type ConversationWorkspace,
  type CreateConversationOptions,
} from "@/hooks/useConversations";
import { uid } from "@/lib/utils";
import type { AgentMode, AgentReasoningLevel, GlobalBusEnvelope } from "@/types";
import type { OpenCodeSessionInfo, OpenCodeSessionStatus } from "@/types";
import {
  abortAssistantSession,
  ensureAssistantSession,
  fetchAssistantSessionPermissions,
  fetchAssistantSessionState,
  promptAssistantSession,
  promptAssistantSessionDetached,
  replyAssistantPermission,
  replyAssistantPermissionDetached,
  shouldUseDetachedAssistantTransport,
  subscribeAssistantBus,
} from "./backend";
import {
  applyBackendPartDelta,
  backendMessageId,
  deriveCanvas,
  normalizeBackendToolName,
  removeBackendMessage,
  removeBackendPart,
  sessionMessagesToChatItems,
  sortBackendMessages,
  upsertBackendMessage,
  upsertBackendPart,
} from "./messageV2";
import { appendReasoningChunk } from "./reasoningText";
import { assistantMessageIdOf, assistantPartIdOf } from "./sessionProtocol";
import type {
  AssistantInstanceRequestContext,
  AssistantInstanceSnapshot,
  AssistantInstanceStore,
  AssistantInstanceStoreContext,
  AssistantSessionBusState,
  AssistantSessionRuntime,
  CanvasData,
  ChatItem,
  MountedPapersInput,
  RemoveMountedPaperOptions,
  SendMessageInput,
} from "./types";

const IDLE_STATUS: OpenCodeSessionStatus = { type: "idle" };
const PENDING_PERMISSION_SYNC_MS = 1500;
const DETACHED_SESSION_SYNC_MS = 900;
const DETACHED_SESSION_MAX_ATTEMPTS = 90;
const ACTIVE_CONVERSATION_KEY = "researchos_assistant_active_conversation";
const LEGACY_ACTIVE_CONVERSATION_KEYS = [
  "researchos_active_conversation",
  "researchos_active_conversation",
];
const DEFAULT_ASSISTANT_BACKEND_ID = "native";
const DEFAULT_ASSISTANT_BACKEND_LABEL = "Native（ResearchOS 内嵌内核）";
const LEGACY_DEFAULT_ASSISTANT_BACKEND_LABEL = "Claw（Rust CLI 内核）";
const STREAM_ITEM_PREFIX = "stream_";
const ASSISTANT_WORKSPACE_REQUIRED_MESSAGE = "当前未绑定工作区，请先导入或选择目录";

interface ParsedSseEvent {
  event: string;
  data: Record<string, unknown> | null;
}

function createEmptyBusState(): AssistantSessionBusState {
  return {
    sessions: {},
    sessionStatus: {},
    messages: {},
    permissions: {},
  };
}

function createRuntime(): AssistantSessionRuntime {
  return {
    abortController: null,
    hydrating: false,
    requesting: false,
    confirmingActionIds: [],
    pendingSyncTimer: null,
    detachedSyncTimer: null,
    detachedSyncGeneration: 0,
    canvas: null,
    localItems: [],
    persistedItemsCache: {
      messagesRef: null,
      permissionsRef: null,
      items: [],
    },
    streamState: {
      token: null,
      assistantMessageId: null,
      currentTextItemId: null,
      currentReasoningItemId: null,
      busCaptured: false,
    },
  };
}

function isTransientStreamItem(item: ChatItem): boolean {
  return String(item.id || "").startsWith(STREAM_ITEM_PREFIX);
}

function isLocalRuntimeErrorItem(item: ChatItem): boolean {
  return item.type === "error" && String(item.id || "").startsWith("e_");
}

function addUnique(values: string[], target: string): string[] {
  if (!target || values.includes(target)) return values;
  return [...values, target];
}

function removeValue(values: string[], target: string): string[] {
  if (!target) return values;
  return values.filter((value) => value !== target);
}

function isAbortLikeError(error: unknown, signal?: AbortSignal): boolean {
  if (signal?.aborted) return true;
  if (error instanceof DOMException && error.name === "AbortError") return true;
  return error instanceof Error && /aborted|aborterror/i.test(error.message);
}

function parseSseBlocks(chunk: string): ParsedSseEvent[] {
  const events: ParsedSseEvent[] = [];
  for (const block of chunk.split("\n\n")) {
    const trimmed = block.trim();
    if (!trimmed) continue;
    let eventName = "";
    const dataLines: string[] = [];
    for (const line of trimmed.split("\n")) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    if (!eventName || dataLines.length === 0) continue;
    try {
      events.push({
        event: eventName,
        data: JSON.parse(dataLines.join("\n")) as Record<string, unknown>,
      });
    } catch {
      events.push({ event: eventName, data: null });
    }
  }
  return events;
}

function isSessionLoading(status: OpenCodeSessionStatus | undefined): boolean {
  if (!status) return false;
  return status.type !== "idle";
}

function normalizeComparableChatText(value: string): string {
  return String(value || "")
    .replace(/\s+/g, " ")
    .trim();
}

function normalizeComparableReasoningText(value: string): string {
  return String(value || "")
    .replace(/\r\n?/g, "\n")
    .replace(/[*_`#>-]/g, " ")
    .replace(/\s+/g, "")
    .trim()
    .toLowerCase();
}

function toolStepIdsFromChatItem(item: ChatItem): string[] {
  if (item.type !== "step_group" || !Array.isArray(item.steps)) return [];
  return item.steps
    .map((step) => String(step.id || "").trim())
    .filter(Boolean);
}

function persistedItemsContainToolStep(persistedItems: ChatItem[], callId: string): boolean {
  const normalizedCallId = String(callId || "").trim();
  if (!normalizedCallId) return false;
  return persistedItems.some((candidate) => (
    candidate.type === "step_group"
    && toolStepIdsFromChatItem(candidate).includes(normalizedCallId)
  ));
}

function createConversationFromMeta(meta: ConversationMeta): Conversation {
  return {
    id: meta.id,
    title: meta.title || "新对话",
    createdAt: meta.createdAt || new Date().toISOString(),
    updatedAt: meta.updatedAt || meta.createdAt || new Date().toISOString(),
    workspacePath: meta.workspacePath ?? null,
    workspaceTitle: meta.workspaceTitle ?? null,
    workspaceServerId: meta.workspaceServerId ?? null,
    workspaceServerLabel: meta.workspaceServerLabel ?? null,
    effectiveWorkspacePath: meta.effectiveWorkspacePath ?? null,
    assistantSessionId: meta.assistantSessionId ?? null,
    assistantSessionDirectory: meta.assistantSessionDirectory ?? null,
    assistantContextKey: meta.assistantContextKey ?? null,
    assistantBackendId: meta.assistantBackendId ?? null,
    assistantBackendLabel: meta.assistantBackendLabel ?? null,
    assistantMode: meta.assistantMode ?? null,
    assistantReasoningLevel: meta.assistantReasoningLevel ?? null,
    assistantSkillIds: meta.assistantSkillIds ?? null,
    mountedPaperId: meta.mountedPaperId ?? null,
    mountedPaperTitle: meta.mountedPaperTitle ?? null,
    mountedPaperIds: meta.mountedPaperIds ?? null,
    mountedPaperTitles: meta.mountedPaperTitles ?? null,
  };
}

function readStoredActiveConversationId(): string | null {
  const stored = localStorage.getItem(ACTIVE_CONVERSATION_KEY);
  if (stored) return stored;
  for (const key of LEGACY_ACTIVE_CONVERSATION_KEYS) {
    const legacy = localStorage.getItem(key);
    if (legacy) return legacy;
  }
  return null;
}

function writeStoredActiveConversationId(conversationId: string | null): void {
  if (conversationId) {
    localStorage.setItem(ACTIVE_CONVERSATION_KEY, conversationId);
    localStorage.setItem("researchos_active_conversation", conversationId);
    return;
  }
  localStorage.removeItem(ACTIVE_CONVERSATION_KEY);
  localStorage.removeItem("researchos_active_conversation");
}

function createDraftConversation(
  settings: AssistantInstanceStoreContext,
  conversationId: string,
  workspace?: ConversationWorkspace | null,
): Conversation {
  const now = new Date().toISOString();
  return {
    id: conversationId,
    title: "新对话",
    createdAt: now,
    updatedAt: now,
    workspacePath: workspace?.path || null,
    workspaceTitle: workspace?.title || null,
    workspaceServerId: workspace?.serverId || null,
    workspaceServerLabel: workspace?.serverLabel || null,
    effectiveWorkspacePath: workspace?.effectivePath || workspace?.path || null,
    assistantSessionId: null,
    assistantSessionDirectory: null,
    assistantContextKey: null,
    assistantBackendId: DEFAULT_ASSISTANT_BACKEND_ID,
    assistantBackendLabel: DEFAULT_ASSISTANT_BACKEND_LABEL,
    assistantMode: settings.defaultAgentMode || "build",
    assistantReasoningLevel: settings.defaultReasoningLevel || "default",
    assistantSkillIds: settings.defaultActiveSkillIds.length > 0 ? [...settings.defaultActiveSkillIds] : null,
    mountedPaperId: null,
    mountedPaperTitle: null,
    mountedPaperIds: null,
    mountedPaperTitles: null,
  };
}

function normalizeMountedPaperIds(conversation: Conversation | null): string[] {
  const ids = (conversation?.mountedPaperIds || []).filter((item): item is string => Boolean(item));
  if (ids.length > 0) return ids;
  return conversation?.mountedPaperId ? [conversation.mountedPaperId] : [];
}

function normalizeMountedPaperTitles(conversation: Conversation | null): string[] {
  const titles = (conversation?.mountedPaperTitles || []).filter((item): item is string => Boolean(item));
  if (titles.length > 0) return titles;
  return conversation?.mountedPaperTitle ? [conversation.mountedPaperTitle] : [];
}

function buildMountedPaperSummary(titles: string[]): string {
  const cleaned = titles.map((item) => item.trim()).filter(Boolean);
  if (cleaned.length === 0) return "";
  if (cleaned.length === 1) return cleaned[0];
  return `${cleaned[0]} 等 ${cleaned.length} 篇`;
}

function normalizeAgentMode(value: string | null | undefined, fallback: AgentMode): AgentMode {
  if (value === "plan" || value === "build") {
    return value;
  }
  return fallback === "plan" ? "plan" : "build";
}

function normalizeReasoningLevel(value: string | null | undefined, fallback: AgentReasoningLevel): AgentReasoningLevel {
  if (value === "low" || value === "medium" || value === "high" || value === "xhigh" || value === "default") {
    return value;
  }
  return fallback;
}

function normalizeAssistantBackendId(value: string | null | undefined): string {
  const raw = String(value || "").trim();
  if (!raw || raw === "native" || raw === "researchos_native" || raw === "claw") {
    return DEFAULT_ASSISTANT_BACKEND_ID;
  }
  return raw;
}

function defaultAssistantBackendLabel(backendId: string): string {
  if (backendId === "custom_acp") return "Custom ACP";
  if (backendId === "claw") return LEGACY_DEFAULT_ASSISTANT_BACKEND_LABEL;
  return DEFAULT_ASSISTANT_BACKEND_LABEL;
}

function normalizeSkillIds(values: string[] | null | undefined): string[] {
  if (!Array.isArray(values)) return [];
  return values.map((value) => String(value || "").trim()).filter(Boolean);
}

function shouldSeedConversationTitle(conversation: Conversation | null): boolean {
  const title = String(conversation?.title || "").trim();
  return !title || title === "新对话";
}

function deriveWorkspaceTitle(workspacePath: string): string {
  const normalized = workspacePath.replace(/[\\/]+/g, "/").replace(/\/+$/, "");
  if (!normalized) return "未命名工作区";
  const parts = normalized.split("/").filter(Boolean);
  return parts[parts.length - 1] || normalized;
}

function resolveWorkspace(
  conversation: Conversation | null,
  session: OpenCodeSessionInfo | null,
): ConversationWorkspace | null {
  const sourcePath = String(conversation?.workspacePath || session?.directory || "").trim();
  const effectivePath = String(
    conversation?.effectiveWorkspacePath
    || session?.directory
    || conversation?.workspacePath
    || "",
  ).trim();
  const workspacePath = sourcePath || effectivePath;
  if (!workspacePath) return null;
  return {
    path: workspacePath,
    title: String(conversation?.workspaceTitle || deriveWorkspaceTitle(effectivePath || workspacePath)).trim() || "未命名工作区",
    effectivePath: effectivePath || workspacePath,
    serverId: String(conversation?.workspaceServerId || "").trim() || null,
    serverLabel: String(conversation?.workspaceServerLabel || "").trim() || null,
  };
}

export function createAssistantInstanceStore(initialContext: AssistantInstanceStoreContext): AssistantInstanceStore {
  let context = initialContext;
  let conversationSnapshot = conversationStore.getSnapshot();
  let activeConversationId: string | null = null;
  let activeConversation: Conversation | null = null;
  let busState = createEmptyBusState();
  let suppressConversationStoreChange = false;
  const draftConversations = new Map<string, Conversation>();
  const runtimes = new Map<string, AssistantSessionRuntime>();
  const bootstrapping = new Map<string, Promise<void>>();
  const listeners = new Set<() => void>();
  let snapshot!: AssistantInstanceSnapshot;
  let scheduledNotifyHandle: number | null = null;
  let scheduledNotifyMode: "raf" | "timeout" | null = null;
  const unsubscribeConversation = conversationStore.subscribe(handleConversationStoreChange);
  const unsubscribeBus = subscribeAssistantBus(handleGlobalEnvelope);

  function ensureRuntime(sessionId: string): AssistantSessionRuntime {
    const normalizedId = sessionId.trim();
    let runtime = runtimes.get(normalizedId);
    if (!runtime) {
      runtime = createRuntime();
      const messages = busState.messages[normalizedId] || [];
      runtime.canvas = deriveCanvas(messages);
      runtimes.set(normalizedId, runtime);
    }
    return runtime;
  }

  function resetTransientStreamState(
    sessionId: string,
    options?: { notifyView?: boolean; assistantMessageId?: string | null; busCaptured?: boolean },
  ): void {
    const runtime = ensureRuntime(sessionId);
    runtime.localItems = runtime.localItems.filter((item) => !isTransientStreamItem(item));
    runtime.streamState = {
      token: null,
      assistantMessageId: options?.assistantMessageId || null,
      currentTextItemId: null,
      currentReasoningItemId: null,
      busCaptured: Boolean(options?.busCaptured),
    };
    if (options?.notifyView) notify();
  }

  function ensureTransientStreamToken(sessionId: string): string {
    const runtime = ensureRuntime(sessionId);
    if (!runtime.streamState.token) {
      runtime.streamState.token = uid();
    }
    return runtime.streamState.token;
  }

  function backendMessageHasVisibleParts(message: Record<string, unknown> | null | undefined): boolean {
    const parts = Array.isArray(message?.parts)
      ? message.parts.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
      : [];
    return parts.some((part) => {
      const partType = String(part.type || "").trim().toLowerCase();
      if (partType === "tool") return true;
      if (partType === "text" || partType === "reasoning") {
        return Boolean(String(part.text || part.content || "").trim());
      }
      return false;
    });
  }

  function latestVisibleAssistantMessageId(sessionId: string): string | null {
    const messages = busState.messages[sessionId] || [];
    let latestUserIndex = -1;
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const info = (messages[index].info && typeof messages[index].info === "object" ? messages[index].info : {}) as Record<string, unknown>;
      if (String(info.role || "").trim() === "user") {
        latestUserIndex = index;
        break;
      }
    }
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (latestUserIndex >= 0 && index <= latestUserIndex) break;
      const message = messages[index];
      const info = (message.info && typeof message.info === "object" ? message.info : {}) as Record<string, unknown>;
      if (String(info.role || "").trim() !== "assistant") continue;
      if (!backendMessageHasVisibleParts(message)) continue;
      return backendMessageId(message) || null;
    }
    return null;
  }

  function shouldSuppressTransientStreamItem(
    sessionId: string,
    item: ChatItem,
    persistedItems: ChatItem[],
  ): boolean {
    if (!isTransientStreamItem(item)) return false;
    if (item.type === "step_group") {
      const toolStepIds = toolStepIdsFromChatItem(item);
      if (toolStepIds.length === 0) return false;
      return toolStepIds.some((callId) => persistedItemsContainToolStep(persistedItems, callId));
    }
    if (item.type !== "assistant" && item.type !== "reasoning") return false;
    const runtime = ensureRuntime(sessionId);
    const assistantMessageId = String(runtime.streamState.assistantMessageId || "").trim();
    if (assistantMessageId) {
      const currentMessage = (busState.messages[sessionId] || []).find(
        (message) => backendMessageId(message) === assistantMessageId,
      );
      if (backendMessageHasVisibleParts(currentMessage)) {
        return true;
      }
    }

    const normalizeComparable = item.type === "reasoning"
      ? normalizeComparableReasoningText
      : normalizeComparableChatText;
    const localText = normalizeComparable(item.content);
    if (!localText) return false;
    return persistedItems.some((candidate) => {
      if (candidate.type !== item.type) return false;
      const persistedText = normalizeComparable(candidate.content);
      if (!persistedText) return false;
      if (localText.length < 12 && persistedText.length < 12) return false;
      return (
        persistedText === localText
        || persistedText.startsWith(localText)
        || localText.startsWith(persistedText)
      );
    });
  }

  function visibleSessionItems(
    sessionId: string | null,
    messages: Array<Record<string, unknown>>,
    permissions: Array<Record<string, unknown>>,
    runtime: AssistantSessionRuntime | null,
  ): ChatItem[] {
    const persistedItems = (() => {
      if (!runtime) return sessionMessagesToChatItems(messages, permissions);
      const cache = runtime.persistedItemsCache;
      if (cache.messagesRef === messages && cache.permissionsRef === permissions) {
        return cache.items;
      }
      const items = sessionMessagesToChatItems(messages, permissions);
      runtime.persistedItemsCache = {
        messagesRef: messages,
        permissionsRef: permissions,
        items,
      };
      return items;
    })();
    if (!sessionId || !runtime) return persistedItems;
    const localItems = runtime.localItems.filter((item) => !shouldSuppressTransientStreamItem(sessionId, item, persistedItems));
    return [
      ...persistedItems,
      ...localItems,
    ];
  }

  function reconcileTransientStreamWithBus(sessionId: string): void {
    const runtime = ensureRuntime(sessionId);
    let assistantMessageId = String(runtime.streamState.assistantMessageId || "").trim();
    if (!assistantMessageId) {
      assistantMessageId = latestVisibleAssistantMessageId(sessionId) || "";
      if (assistantMessageId) {
        runtime.streamState.assistantMessageId = assistantMessageId;
      }
    }
    if (!assistantMessageId) return;
    const currentMessage = (busState.messages[sessionId] || []).find(
      (message) => backendMessageId(message) === assistantMessageId,
    );
    if (!backendMessageHasVisibleParts(currentMessage)) return;
    resetTransientStreamState(sessionId, { assistantMessageId, busCaptured: true });
  }

  function upsertLocalChatItem(sessionId: string, item: ChatItem): void {
    const runtime = ensureRuntime(sessionId);
    const index = runtime.localItems.findIndex((candidate) => candidate.id === item.id);
    if (index < 0) {
      runtime.localItems = [...runtime.localItems, item];
      return;
    }
    runtime.localItems = [
      ...runtime.localItems.slice(0, index),
      item,
      ...runtime.localItems.slice(index + 1),
    ];
  }

  function backendSessionHasErrorMessage(sessionId: string, message: string): boolean {
    const normalized = message.trim();
    if (!normalized) return false;
    return (busState.messages[sessionId] || []).some((entry) => {
      const info = (entry.info && typeof entry.info === "object" ? entry.info : {}) as Record<string, unknown>;
      const errorInfo = (info.error && typeof info.error === "object" ? info.error : {}) as Record<string, unknown>;
      return String(errorInfo.message || "").trim() === normalized;
    });
  }

  function clearLocalErrors(sessionId: string, options?: { notifyView?: boolean }): void {
    const runtime = ensureRuntime(sessionId);
    const nextItems = runtime.localItems.filter((item) => !isLocalRuntimeErrorItem(item));
    if (nextItems.length === runtime.localItems.length) return;
    runtime.localItems = nextItems;
    if (options?.notifyView) {
      notify();
    }
  }

  function pruneDuplicateLocalErrors(sessionId: string): void {
    const runtime = ensureRuntime(sessionId);
    const nextItems = runtime.localItems.filter((item) => {
      if (!isLocalRuntimeErrorItem(item)) return true;
      return !backendSessionHasErrorMessage(sessionId, item.content);
    });
    if (nextItems.length === runtime.localItems.length) return;
    runtime.localItems = nextItems;
  }

  function patchLocalChatItem(
    sessionId: string,
    itemId: string | null | undefined,
    updater: (item: ChatItem) => ChatItem,
  ): void {
    const normalizedId = String(itemId || "").trim();
    if (!normalizedId) return;
    const runtime = ensureRuntime(sessionId);
    const index = runtime.localItems.findIndex((candidate) => candidate.id === normalizedId);
    if (index < 0) return;
    runtime.localItems = [
      ...runtime.localItems.slice(0, index),
      updater(runtime.localItems[index]),
      ...runtime.localItems.slice(index + 1),
    ];
  }

  function toStepDataPayload(value: unknown): Record<string, unknown> | undefined {
    if (value && typeof value === "object") {
      return value as Record<string, unknown>;
    }
    if (value === undefined || value === null) return undefined;
    return { output: value };
  }

  function ensureLiveAssistantItem(
    sessionId: string,
    partType: "assistant" | "reasoning",
  ): string {
    const runtime = ensureRuntime(sessionId);
    const token = ensureTransientStreamToken(sessionId);
    const stateKey = partType === "reasoning" ? "currentReasoningItemId" : "currentTextItemId";
    const existingId = String(runtime.streamState[stateKey] || "").trim();
    if (existingId) return existingId;
    const nextId = `${STREAM_ITEM_PREFIX}${token}_${partType}_${uid()}`;
    runtime.streamState[stateKey] = nextId;
    upsertLocalChatItem(sessionId, {
      id: nextId,
      type: partType,
      content: "",
      streaming: partType === "assistant",
      timestamp: new Date(),
    });
    return nextId;
  }

  function closeLiveAssistantItem(sessionId: string, partType: "assistant" | "reasoning"): void {
    const runtime = ensureRuntime(sessionId);
    const stateKey = partType === "reasoning" ? "currentReasoningItemId" : "currentTextItemId";
    const itemId = String(runtime.streamState[stateKey] || "").trim();
    if (!itemId) return;
    patchLocalChatItem(sessionId, itemId, (item) => ({
      ...item,
      streaming: false,
    }));
    runtime.streamState[stateKey] = null;
  }

  function updateLiveAssistantDelta(
    sessionId: string,
    partType: "assistant" | "reasoning",
    content: string,
  ): void {
    const normalized = String(content || "");
    if (!normalized) return;
    const itemId = ensureLiveAssistantItem(sessionId, partType);
    patchLocalChatItem(sessionId, itemId, (item) => ({
      ...item,
      content: partType === "reasoning"
        ? appendReasoningChunk(item.content, normalized)
        : `${item.content}${normalized}`,
      streaming: partType === "assistant",
      timestamp: new Date(),
    }));
  }

  function updateLiveToolStep(
    sessionId: string,
    callId: string,
    updater: (current: ChatItem | null) => ChatItem,
  ): void {
    const normalizedCallId = String(callId || "").trim() || uid();
    const token = ensureTransientStreamToken(sessionId);
    const itemId = `${STREAM_ITEM_PREFIX}${token}_tool_${normalizedCallId}`;
    const runtime = ensureRuntime(sessionId);
    const existing = runtime.localItems.find((item) => item.id === itemId) || null;
    const persistedItems = sessionMessagesToChatItems(
      busState.messages[sessionId] || [],
      busState.permissions[sessionId] || [],
    );
    if (persistedItemsContainToolStep(persistedItems, normalizedCallId)) {
      if (existing) {
        runtime.localItems = runtime.localItems.filter((item) => item.id !== itemId);
      }
      return;
    }
    upsertLocalChatItem(sessionId, updater(existing));
  }

  function markTransientAssistantItemsFinished(sessionId: string): void {
    const runtime = ensureRuntime(sessionId);
    runtime.localItems = runtime.localItems.map((item) => {
      if (!isTransientStreamItem(item)) return item;
      if (item.type !== "assistant") return item;
      return { ...item, streaming: false };
    });
    runtime.streamState.currentTextItemId = null;
    runtime.streamState.currentReasoningItemId = null;
  }

  function handleDirectStreamEvent(
    sessionId: string,
    eventName: string,
    data: Record<string, unknown> | null,
  ): void {
    const payload = data || {};
    const runtime = ensureRuntime(sessionId);
    switch (eventName) {
      case "assistant_message_id": {
        const messageId = assistantMessageIdOf(payload);
        if (messageId) {
          runtime.streamState.assistantMessageId = messageId;
        }
        return;
      }
      case "done":
        markTransientAssistantItemsFinished(sessionId);
        return;
      default:
        break;
    }
    if (runtime.streamState.busCaptured) {
      return;
    }
    switch (eventName) {
      case "text-start":
        closeLiveAssistantItem(sessionId, "assistant");
        ensureLiveAssistantItem(sessionId, "assistant");
        return;
      case "text_delta":
        updateLiveAssistantDelta(sessionId, "assistant", String(payload.content || ""));
        return;
      case "text-end":
        closeLiveAssistantItem(sessionId, "assistant");
        return;
      case "reasoning-start":
        closeLiveAssistantItem(sessionId, "reasoning");
        ensureLiveAssistantItem(sessionId, "reasoning");
        return;
      case "reasoning_delta":
        updateLiveAssistantDelta(sessionId, "reasoning", String(payload.content || ""));
        return;
      case "reasoning-end":
        closeLiveAssistantItem(sessionId, "reasoning");
        return;
      case "tool_start": {
        const callId = String(payload.id || "").trim() || uid();
        const toolName = normalizeBackendToolName(payload.name);
        const toolArgs = payload.args && typeof payload.args === "object"
          ? payload.args as Record<string, unknown>
          : undefined;
        updateLiveToolStep(sessionId, callId, (current) => ({
          id: current?.id || `${STREAM_ITEM_PREFIX}${runtime.streamState.token || ensureTransientStreamToken(sessionId)}_tool_${callId}`,
          type: "step_group",
          content: "",
          steps: [
            {
              id: callId,
              status: "running",
              toolName,
              toolArgs,
            },
          ],
          timestamp: new Date(),
        }));
        return;
      }
      case "tool_result": {
        const callId = String(payload.id || "").trim() || uid();
        const success = payload.success === undefined ? true : Boolean(payload.success);
        const toolName = normalizeBackendToolName(payload.name);
        const summary = String(payload.summary || "").trim();
        const dataPayload = toStepDataPayload(payload.data);
        updateLiveToolStep(sessionId, callId, (current) => {
          const previousStep = current?.steps?.[0];
          return {
            id: current?.id || `${STREAM_ITEM_PREFIX}${runtime.streamState.token || ensureTransientStreamToken(sessionId)}_tool_${callId}`,
            type: "step_group",
            content: "",
            steps: [
              {
                id: callId,
                status: success ? "done" : "error",
                toolName: toolName || previousStep?.toolName || "tool",
                toolArgs: previousStep?.toolArgs,
                success,
                summary: summary || previousStep?.summary,
                data: dataPayload || previousStep?.data,
              },
            ],
            timestamp: new Date(),
          };
        });
        return;
      }
      case "tool-input-delta": {
        const callId = String(payload.id || "").trim();
        const delta = String(payload.delta || "");
        if (!callId || !delta) return;
        updateLiveToolStep(sessionId, callId, (current) => {
          const previousStep = current?.steps?.[0];
          const previousData = previousStep?.data && typeof previousStep.data === "object"
            ? previousStep.data
            : {};
          const previousRaw = String(previousData.raw || "");
          return {
            id: current?.id || `${STREAM_ITEM_PREFIX}${runtime.streamState.token || ensureTransientStreamToken(sessionId)}_tool_${callId}`,
            type: "step_group",
            content: "",
            steps: [
              {
                id: callId,
                status: previousStep?.status || "running",
                toolName: previousStep?.toolName || normalizeBackendToolName(payload.toolName),
                toolArgs: previousStep?.toolArgs,
                success: previousStep?.success,
                summary: previousStep?.summary,
                data: {
                  ...previousData,
                  raw: `${previousRaw}${delta}`,
                },
              },
            ],
            timestamp: new Date(),
          };
        });
        return;
      }
      default:
        return;
    }
  }

  function clearScheduledNotify(): void {
    if (scheduledNotifyHandle == null) return;
    if (scheduledNotifyMode === "raf" && typeof window !== "undefined" && typeof window.cancelAnimationFrame === "function") {
      window.cancelAnimationFrame(scheduledNotifyHandle);
    } else {
      globalThis.clearTimeout(scheduledNotifyHandle);
    }
    scheduledNotifyHandle = null;
    scheduledNotifyMode = null;
  }

  function emitSnapshot(): void {
    snapshot = createSnapshot();
    for (const listener of listeners) listener();
  }

  function notify(): void {
    clearScheduledNotify();
    emitSnapshot();
  }

  function scheduleNotify(): void {
    if (scheduledNotifyHandle != null) return;
    if (typeof window !== "undefined" && typeof window.requestAnimationFrame === "function") {
      scheduledNotifyMode = "raf";
      scheduledNotifyHandle = window.requestAnimationFrame(() => {
        scheduledNotifyHandle = null;
        scheduledNotifyMode = null;
        emitSnapshot();
      });
      return;
    }
    scheduledNotifyMode = "timeout";
    scheduledNotifyHandle = globalThis.setTimeout(() => {
      scheduledNotifyHandle = null;
      scheduledNotifyMode = null;
      emitSnapshot();
    }, 16);
  }

  function currentActiveConversationId(): string | null {
    return activeConversationId;
  }

  function currentActiveConversation(): Conversation | null {
    return activeConversation || resolveConversation(activeConversationId);
  }

  function rememberActiveConversation(conversationId: string | null, conversation: Conversation | null): void {
    activeConversationId = conversationId;
    activeConversation = conversation;
    writeStoredActiveConversationId(conversationId);
  }

  function loadPreferredConversation(
    conversationId: string | null,
    preferred: Conversation | null = null,
  ): Conversation | null {
    const normalizedId = String(conversationId || "").trim() || null;
    if (!normalizedId) return null;
    if (preferred?.id === normalizedId) {
      return preferred;
    }
    const draft = draftConversations.get(normalizedId);
    if (draft) return draft;
    const stored = loadConversation(normalizedId);
    if (stored) return stored;
    const meta = conversationSnapshot.metas.find((item) => item.id === normalizedId);
    if (meta) {
      return createConversationFromMeta(meta);
    }
    if (activeConversation?.id === normalizedId) {
      return activeConversation;
    }
    return null;
  }

  function resolveFallbackConversationId(excludedConversationId: string | null = null): string | null {
    const excludedId = String(excludedConversationId || "").trim() || null;
    const draftCandidate = Array.from(draftConversations.keys()).find((id) => id !== excludedId) || null;
    if (draftCandidate) return draftCandidate;
    const metaCandidate = conversationSnapshot.metas.find((item) => item.id !== excludedId);
    return metaCandidate?.id || null;
  }

  function refreshConversationSelection(
    preferredConversationId: string | null = activeConversationId,
    preferredConversation: Conversation | null = null,
  ): void {
    conversationSnapshot = conversationStore.getSnapshot();
    const normalizedPreferredId = String(preferredConversationId || "").trim() || null;
    const resolvedPreferred = loadPreferredConversation(normalizedPreferredId, preferredConversation);
    if (normalizedPreferredId && resolvedPreferred) {
      rememberActiveConversation(normalizedPreferredId, resolvedPreferred);
      return;
    }

    const fallbackId = resolveFallbackConversationId(normalizedPreferredId);
    rememberActiveConversation(fallbackId, loadPreferredConversation(fallbackId));
  }

  function applyConversationSelection(
    preferredConversationId: string | null,
    previousActiveId: string | null,
    previousSessionId: string | null,
    previousBootstrapKey: string,
    preferredConversation: Conversation | null = null,
  ): void {
    refreshConversationSelection(preferredConversationId, preferredConversation);
    pruneUnusedSessions();
    const nextActiveId = currentActiveConversationId();
    const nextSessionId = currentActiveSessionId();
    const nextBootstrapKey = conversationBootstrapKey(nextActiveId);
    notify();
    if (nextActiveId && (
      previousActiveId !== nextActiveId
      || previousSessionId !== nextSessionId
      || previousBootstrapKey !== nextBootstrapKey
    )) {
      void queueBootstrapConversation(nextActiveId);
    }
  }

  function resolveConversation(conversationId: string | null): Conversation | null {
    const normalizedId = String(conversationId || "").trim() || null;
    if (!normalizedId) return null;
    if (activeConversation?.id === normalizedId) {
      return activeConversation;
    }
    const draft = draftConversations.get(normalizedId);
    if (draft) return draft;
    const stored = loadConversation(normalizedId);
    if (stored) return stored;
    const meta = conversationSnapshot.metas.find((item) => item.id === normalizedId);
    if (meta) {
      return createConversationFromMeta(meta);
    }
    return null;
  }

  refreshConversationSelection(readStoredActiveConversationId());
  snapshot = createSnapshot();

  function resolveSessionIdForConversationId(conversationId: string | null): string | null {
    const conversation = resolveConversation(conversationId);
    if (!conversation) return null;
    const sessionId = String(conversation.assistantSessionId || conversation.id || "").trim();
    return sessionId || null;
  }

  function currentActiveSessionId(): string | null {
    return resolveSessionIdForConversationId(currentActiveConversationId());
  }

  function resolveConversationIdForSession(sessionId: string): string | null {
    const preferred = activeConversationId;
    if (preferred && resolveSessionIdForConversationId(preferred) === sessionId) {
      return preferred;
    }
    const activeConv = activeConversation;
    if (activeConv && String(activeConv.assistantSessionId || activeConv.id || "").trim() === sessionId) {
      return activeConv.id;
    }
    const match = conversationSnapshot.metas.find((meta) => String(meta.assistantSessionId || meta.id || "").trim() === sessionId);
    return match?.id || null;
  }

  function resolveAgentMode(conversation: Conversation | null): AgentMode {
    return normalizeAgentMode(conversation?.assistantMode || null, context.defaultAgentMode || "build");
  }

  function resolveReasoningLevel(conversation: Conversation | null): AgentReasoningLevel {
    return normalizeReasoningLevel(conversation?.assistantReasoningLevel || null, context.defaultReasoningLevel || "default");
  }

  function resolveSkillIds(conversation: Conversation | null): string[] {
    const local = normalizeSkillIds(conversation?.assistantSkillIds);
    if (local.length > 0) return local;
    return normalizeSkillIds(context.defaultActiveSkillIds);
  }

  function currentBackendIdentity(conversation: Conversation | null): { id: string; label: string } {
    const id = normalizeAssistantBackendId(
      conversation?.assistantBackendId || context.defaultAssistantBackendId || DEFAULT_ASSISTANT_BACKEND_ID,
    );
    const rawLabel = String(conversation?.assistantBackendLabel || "").trim();
    const label = (
      !rawLabel
      || (id === DEFAULT_ASSISTANT_BACKEND_ID && rawLabel === LEGACY_DEFAULT_ASSISTANT_BACKEND_LABEL)
    )
      ? defaultAssistantBackendLabel(id)
      : rawLabel;
    return {
      id,
      label,
    };
  }

  function activeSessionMessages(sessionId: string | null): Array<Record<string, unknown>> {
    if (!sessionId) return [];
    return busState.messages[sessionId] || [];
  }

  function activeSessionPermissions(sessionId: string | null): Array<Record<string, unknown>> {
    if (!sessionId) return [];
    return busState.permissions[sessionId] || [];
  }

  function createSnapshot(): AssistantInstanceSnapshot {
    const activeConversation = currentActiveConversation();
    const activeSessionId = currentActiveSessionId();
    const activeSession = activeSessionId ? busState.sessions[activeSessionId] || null : null;
    const activeWorkspace = resolveWorkspace(activeConversation, activeSession);
    const runtime = activeSessionId ? ensureRuntime(activeSessionId) : null;
    const messages = activeSessionMessages(activeSessionId);
    const permissions = activeSessionPermissions(activeSessionId);
    const pendingActionIds = permissions
      .map((item) => String(item.id || "").trim())
      .filter(Boolean);
    const { id: activeBackendId, label: activeBackendLabel } = currentBackendIdentity(activeConversation);
    const agentMode = resolveAgentMode(activeConversation);
    const reasoningLevel = resolveReasoningLevel(activeConversation);
    const activeSkillIds = resolveSkillIds(activeConversation);
    return {
      conversationMetas: conversationSnapshot.metas,
      activeConversationId,
      activeConversation,
      activeWorkspace,
      activeSessionId,
      activeSession,
      activeStatus: activeSessionId ? busState.sessionStatus[activeSessionId] || IDLE_STATUS : IDLE_STATUS,
      conversationTitle: String(activeSession?.title || activeConversation?.title || activeWorkspace?.title || "新对话").trim() || "新对话",
      activeBackendId,
      activeBackendLabel,
      agentMode,
      reasoningLevel,
      activeSkillIds,
      items: visibleSessionItems(activeSessionId, messages, permissions, runtime),
      loading: Boolean(
        activeSessionId
        && (
          isSessionLoading(busState.sessionStatus[activeSessionId])
          || runtime?.requesting
          || runtime?.abortController
        )
      ),
      pendingActionIds,
      confirmingActionIds: runtime?.confirmingActionIds || [],
      canvas: runtime?.canvas || null,
      hasPendingConfirm: pendingActionIds.length > 0,
    };
  }

  function conversationBootstrapKey(conversationId: string | null): string {
    const conversation = resolveConversation(conversationId);
    if (!conversation) return "";
    const skillIds = normalizeSkillIds(conversation.assistantSkillIds).join(",");
    return [
      conversation.id,
      conversation.assistantSessionId || conversation.id || "",
      conversation.workspacePath || "",
      conversation.effectiveWorkspacePath || "",
      conversation.workspaceServerId || "",
      conversation.assistantMode || "",
      conversation.assistantReasoningLevel || "",
      conversation.assistantBackendId || "",
      skillIds,
    ].join("|");
  }

  function queueBootstrapConversation(conversationId: string | null): Promise<void> {
    const normalizedId = String(conversationId || "").trim();
    if (!normalizedId) return Promise.resolve();
    const pending = bootstrapping.get(normalizedId);
    if (pending) return pending;
    const task = bootstrapConversation(normalizedId).finally(() => {
      bootstrapping.delete(normalizedId);
    });
    bootstrapping.set(normalizedId, task);
    return task;
  }

  function handleConversationStoreChange(): void {
    conversationSnapshot = conversationStore.getSnapshot();
    if (suppressConversationStoreChange) {
      return;
    }
    const previousActiveId = activeConversationId;
    const previousSessionId = currentActiveSessionId();
    const previousBootstrapKey = conversationBootstrapKey(previousActiveId);
    applyConversationSelection(activeConversationId, previousActiveId, previousSessionId, previousBootstrapKey, activeConversation);
  }

  function requestContextFor(conversationId: string): AssistantInstanceRequestContext {
    const conversation = resolveConversation(conversationId);
    const workspace = resolveWorkspace(
      conversation,
      conversationId === activeConversationId ? busState.sessions[currentActiveSessionId() || ""] || null : null,
    );
    const backend = currentBackendIdentity(conversation);
    return {
      agentBackendId: backend.id,
      workspacePath:
        workspace?.effectivePath
        || workspace?.path
        || conversation?.effectiveWorkspacePath
        || conversation?.workspacePath
        || null,
      workspaceServerId: workspace?.serverId || conversation?.workspaceServerId || null,
      agentMode: resolveAgentMode(conversation),
      reasoningLevel: resolveReasoningLevel(conversation),
      activeSkillIds: resolveSkillIds(conversation),
      mountedPaperIds: normalizeMountedPaperIds(conversation),
      mountedPrimaryPaperId: conversation?.mountedPaperId || null,
    };
  }

  function upsertSession(session: OpenCodeSessionInfo | null): void {
    if (!session?.id) return;
    const conversationId = resolveConversationIdForSession(session.id);
    const conversation = conversationId ? resolveConversation(conversationId) : null;
    const sessionTitle = String(session.title || "").trim();
    busState = {
      ...busState,
      sessions: {
        ...busState.sessions,
        [session.id]: session,
      },
    };
    if (conversationId && conversation && shouldSeedConversationTitle(conversation) && sessionTitle) {
      patchResolvedConversation(conversationId, { title: sessionTitle });
    }
  }

  function setSessionStatus(sessionId: string, status: OpenCodeSessionStatus): void {
    busState = {
      ...busState,
      sessionStatus: {
        ...busState.sessionStatus,
        [sessionId]: status,
      },
    };
  }

  function setSessionMessages(sessionId: string, messages: Array<Record<string, unknown>>): void {
    busState = {
      ...busState,
      messages: {
        ...busState.messages,
        [sessionId]: sortBackendMessages(messages),
      },
    };
    ensureRuntime(sessionId).canvas = deriveCanvas(busState.messages[sessionId] || []);
  }

  function setSessionPermissions(sessionId: string, permissions: Array<Record<string, unknown>>): void {
    busState = {
      ...busState,
      permissions: {
        ...busState.permissions,
        [sessionId]: permissions.map((item) => ({ ...item })),
      },
    };
    const runtime = ensureRuntime(sessionId);
    runtime.confirmingActionIds = runtime.confirmingActionIds.filter((actionId) =>
      permissions.some((permission) => String(permission.id || "").trim() === actionId),
    );
    syncPendingPermissionWatch(sessionId);
  }

  function clearPendingPermissionWatch(sessionId: string): void {
    const runtime = runtimes.get(sessionId);
    if (!runtime || runtime.pendingSyncTimer == null) return;
    window.clearTimeout(runtime.pendingSyncTimer);
    runtime.pendingSyncTimer = null;
  }

  function clearDetachedSessionWatch(sessionId: string): void {
    const runtime = runtimes.get(sessionId);
    if (!runtime) return;
    runtime.detachedSyncGeneration += 1;
    if (runtime.detachedSyncTimer != null) {
      window.clearTimeout(runtime.detachedSyncTimer);
      runtime.detachedSyncTimer = null;
    }
  }

  function watchDetachedSession(
    sessionId: string,
    attempt = 0,
    generation?: number,
  ): void {
    const runtime = ensureRuntime(sessionId);
    const currentGeneration = generation ?? (runtime.detachedSyncGeneration + 1);
    runtime.detachedSyncGeneration = currentGeneration;
    if (runtime.detachedSyncTimer != null) {
      window.clearTimeout(runtime.detachedSyncTimer);
      runtime.detachedSyncTimer = null;
    }

    const tick = async () => {
      const currentRuntime = ensureRuntime(sessionId);
      if (currentRuntime.detachedSyncGeneration !== currentGeneration) return;
      await hydrateSession(sessionId);
      if (currentRuntime.detachedSyncGeneration !== currentGeneration) return;
      const status = busState.sessionStatus[sessionId] || IDLE_STATUS;
      const permissions = busState.permissions[sessionId] || [];
      if (permissions.length > 0 || status.type === "idle") {
        currentRuntime.requesting = false;
        currentRuntime.detachedSyncTimer = null;
        notify();
        return;
      }
      if (attempt + 1 >= DETACHED_SESSION_MAX_ATTEMPTS) {
        currentRuntime.requesting = false;
        currentRuntime.detachedSyncTimer = null;
        notify();
        return;
      }
      currentRuntime.detachedSyncTimer = window.setTimeout(() => {
        watchDetachedSession(sessionId, attempt + 1, currentGeneration);
      }, DETACHED_SESSION_SYNC_MS);
    };

    void tick();
  }

  function syncPendingPermissionWatch(sessionId: string): void {
    const runtime = ensureRuntime(sessionId);
    const permissions = busState.permissions[sessionId] || [];
    if (permissions.length === 0 || !isTrackedSession(sessionId)) {
      clearPendingPermissionWatch(sessionId);
      return;
    }
    if (runtime.pendingSyncTimer != null) {
      return;
    }
    runtime.pendingSyncTimer = window.setTimeout(() => {
      runtime.pendingSyncTimer = null;
      if (!isTrackedSession(sessionId)) {
        clearPendingPermissionWatch(sessionId);
        return;
      }
      if ((busState.permissions[sessionId] || []).length === 0) {
        clearPendingPermissionWatch(sessionId);
        return;
      }
      void hydrateSession(sessionId).finally(() => {
        syncPendingPermissionWatch(sessionId);
      });
    }, PENDING_PERMISSION_SYNC_MS);
  }

  function removeSession(sessionId: string): void {
    const runtime = runtimes.get(sessionId);
    runtime?.abortController?.abort();
    clearPendingPermissionWatch(sessionId);
    clearDetachedSessionWatch(sessionId);
    runtimes.delete(sessionId);
    const { [sessionId]: removedSession, ...nextSessions } = busState.sessions;
    const { [sessionId]: removedStatus, ...nextStatus } = busState.sessionStatus;
    const { [sessionId]: removedMessages, ...nextMessages } = busState.messages;
    const { [sessionId]: removedPermissions, ...nextPermissions } = busState.permissions;
    void removedSession;
    void removedStatus;
    void removedMessages;
    void removedPermissions;
    busState = {
      sessions: nextSessions,
      sessionStatus: nextStatus,
      messages: nextMessages,
      permissions: nextPermissions,
    };
  }

  function clearAllSessions(): void {
    for (const [sessionId, runtime] of runtimes.entries()) {
      runtime.abortController?.abort();
      clearPendingPermissionWatch(sessionId);
      clearDetachedSessionWatch(sessionId);
    }
    runtimes.clear();
    busState = createEmptyBusState();
  }

  function appendError(sessionId: string, message: string): void {
    const content = message.trim();
    if (!content) return;
    const runtime = ensureRuntime(sessionId);
    const duplicateLocal = runtime.localItems.some(
      (item) => isLocalRuntimeErrorItem(item) && item.content === content,
    );
    if (duplicateLocal || backendSessionHasErrorMessage(sessionId, content)) {
      runtime.requesting = false;
      runtime.abortController = null;
      notify();
      return;
    }
    runtime.localItems = [
      ...runtime.localItems,
      {
        id: `e_${Date.now()}`,
        type: "error",
        content,
        timestamp: new Date(),
      },
    ];
    runtime.requesting = false;
    runtime.abortController = null;
    notify();
  }

  function appendAssistantNotice(sessionId: string, message: string): void {
    const content = message.trim();
    if (!content) return;
    const runtime = ensureRuntime(sessionId);
    const duplicate = runtime.localItems.some((item) => item.type === "assistant" && item.content === content);
    if (duplicate) return;
    runtime.localItems = [
      ...runtime.localItems,
      {
        id: `n_${Date.now()}`,
        type: "assistant",
        content,
        timestamp: new Date(),
      },
    ];
    notify();
  }

  async function hydrateSession(sessionId: string): Promise<void> {
    const runtime = ensureRuntime(sessionId);
    if (runtime.hydrating) return;
    runtime.hydrating = true;
    try {
      const payload = await fetchAssistantSessionState(sessionId);
      upsertSession(payload.session);
      setSessionStatus(sessionId, payload.status || IDLE_STATUS);
      setSessionMessages(sessionId, payload.messages);
      setSessionPermissions(sessionId, payload.permissions);
      reconcileTransientStreamWithBus(sessionId);
      pruneDuplicateLocalErrors(sessionId);
    } finally {
      runtime.hydrating = false;
      notify();
    }
  }

  async function refreshPermissions(sessionId: string): Promise<void> {
    const permissions = await fetchAssistantSessionPermissions(sessionId);
    setSessionPermissions(sessionId, permissions);
    notify();
  }

  function persistDraftConversation(conversationId: string): Conversation | null {
    const normalizedId = String(conversationId || "").trim();
    if (!normalizedId) return null;
    const draft = draftConversations.get(normalizedId);
    if (!draft) {
      return resolveConversation(normalizedId);
    }
    draftConversations.delete(normalizedId);
    conversationStore.upsertConversation(draft);
    return draft;
  }

  function patchResolvedConversation(conversationId: string, patch: Partial<Conversation>): void {
    const conversation = resolveConversation(conversationId);
    if (!conversation) return;
    const nextConversation: Conversation = {
      ...conversation,
      ...patch,
    };
    if (draftConversations.has(conversationId)) {
      draftConversations.set(conversationId, nextConversation);
      if (activeConversationId === conversationId) {
        activeConversation = nextConversation;
      }
      notify();
      return;
    }
    if (activeConversationId === conversationId) {
      activeConversation = nextConversation;
    }
    conversationStore.patchConversation(conversationId, patch);
    if (activeConversationId === conversationId) {
      notify();
    }
  }

  function setActiveConversationRuntimePatch(patch: Partial<Conversation>): void {
    const activeConversationId = currentActiveConversationId();
    if (!activeConversationId) return;
    patchResolvedConversation(activeConversationId, patch);
  }

  function seedConversationTitle(conversationId: string, text: string): void {
    const conversation = resolveConversation(conversationId);
    if (!conversation || !shouldSeedConversationTitle(conversation)) return;
    const title = deriveConversationTitle(text);
    if (!title || title === conversation.title) return;
    patchResolvedConversation(conversationId, { title });
  }

  function patchConversationSessionBinding(
    conversationId: string,
    conversation: Conversation,
    request: AssistantInstanceRequestContext,
    session: OpenCodeSessionInfo | null,
  ): string {
    const sessionId = String(session?.id || conversation.assistantSessionId || conversation.id || "").trim();
    const directory = String(session?.directory || request.workspacePath || "").trim() || null;
    const backend = currentBackendIdentity(conversation);
    const sessionTitle = String(session?.title || "").trim();
    const nextPatch = {
      title: shouldSeedConversationTitle(conversation) && sessionTitle ? sessionTitle : conversation.title,
      assistantSessionId: sessionId,
      assistantSessionDirectory: directory,
      assistantContextKey: directory || request.workspacePath || null,
      assistantBackendId: backend.id,
      assistantBackendLabel: backend.label,
      assistantMode: request.agentMode,
      assistantReasoningLevel: request.reasoningLevel,
      assistantSkillIds: request.activeSkillIds.length > 0 ? [...request.activeSkillIds] : null,
    };
    if (
      conversation.title !== nextPatch.title
      || conversation.assistantSessionId !== nextPatch.assistantSessionId
      || conversation.assistantSessionDirectory !== nextPatch.assistantSessionDirectory
      || conversation.assistantContextKey !== nextPatch.assistantContextKey
      || conversation.assistantBackendId !== nextPatch.assistantBackendId
      || conversation.assistantBackendLabel !== nextPatch.assistantBackendLabel
      || conversation.assistantMode !== nextPatch.assistantMode
      || conversation.assistantReasoningLevel !== nextPatch.assistantReasoningLevel
      || JSON.stringify(normalizeSkillIds(conversation.assistantSkillIds)) !== JSON.stringify(nextPatch.assistantSkillIds || [])
    ) {
      patchResolvedConversation(conversationId, nextPatch);
    }
    return sessionId;
  }

  async function ensureSessionForConversation(conversationId: string): Promise<string | null> {
    const initialConversation = resolveConversation(conversationId);
    if (!initialConversation) return null;
    const conversation = initialConversation;
    if (!conversation) return null;
    const request = requestContextFor(conversationId);
    if (!String(request.workspacePath || "").trim()) {
      throw new Error(ASSISTANT_WORKSPACE_REQUIRED_MESSAGE);
    }
    const preferredSessionId = String(conversation.assistantSessionId || conversation.id || "").trim();
    if (!preferredSessionId) return null;
    const session = await ensureAssistantSession(preferredSessionId, {
      directory: request.workspacePath,
      workspace_path: request.workspacePath,
      workspace_server_id: request.workspaceServerId,
      agent_backend_id: request.agentBackendId,
      title: conversation.title || "新对话",
      mode: request.agentMode,
    });
    const persistedConversation = persistDraftConversation(conversationId) || resolveConversation(conversationId) || conversation;
    upsertSession(session);
    const sessionId = patchConversationSessionBinding(conversationId, persistedConversation, request, session);
    if (!(sessionId in busState.sessionStatus)) {
      setSessionStatus(sessionId, IDLE_STATUS);
    }
    ensureRuntime(sessionId);
    notify();
    return sessionId;
  }

  async function bootstrapConversation(conversationId: string): Promise<void> {
    const request = requestContextFor(conversationId);
    if (!String(request.workspacePath || "").trim()) {
      notify();
      return;
    }
    const sessionId = await ensureSessionForConversation(conversationId);
    if (!sessionId) {
      notify();
      return;
    }
    await hydrateSession(sessionId);
  }

  function trackedSessionIds(): Set<string> {
    const known = new Set<string>();
    for (const meta of conversationSnapshot.metas) {
      const sessionId = String(meta.assistantSessionId || meta.id || "").trim();
      if (sessionId) known.add(sessionId);
    }
    const currentConversation = activeConversation;
    const activeConversationSessionId = String(
      currentConversation?.assistantSessionId || currentConversation?.id || "",
    ).trim();
    if (activeConversationSessionId) {
      known.add(activeConversationSessionId);
    }
    const activeSessionId = currentActiveSessionId();
    if (activeSessionId) known.add(activeSessionId);
    return known;
  }

  function pruneUnusedSessions(): void {
    const known = trackedSessionIds();
    for (const sessionId of Object.keys(busState.sessions)) {
      if (!known.has(sessionId)) {
        removeSession(sessionId);
      }
    }
    for (const sessionId of Object.keys(busState.messages)) {
      if (!known.has(sessionId)) {
        removeSession(sessionId);
      }
    }
  }

  function resolveActionSessionId(actionId: string): string | null {
    const preferred = currentActiveSessionId();
    if (preferred) {
      const permissions = busState.permissions[preferred] || [];
      const runtime = ensureRuntime(preferred);
      if (
        permissions.some((permission) => String(permission.id || "").trim() === actionId)
        || runtime.confirmingActionIds.includes(actionId)
      ) {
        return preferred;
      }
    }
    for (const sessionId of trackedSessionIds()) {
      const permissions = busState.permissions[sessionId] || [];
      const runtime = ensureRuntime(sessionId);
      if (
        permissions.some((permission) => String(permission.id || "").trim() === actionId)
        || runtime.confirmingActionIds.includes(actionId)
      ) {
        return sessionId;
      }
    }
    return preferred || null;
  }

  function cancelSessionStream(sessionId: string): void {
    const runtime = ensureRuntime(sessionId);
    clearDetachedSessionWatch(sessionId);
    if (!runtime.abortController) return;
    runtime.abortController.abort();
    runtime.abortController = null;
  }

  async function interruptSessionRequest(sessionId: string): Promise<void> {
    const runtime = ensureRuntime(sessionId);
    cancelSessionStream(sessionId);
    runtime.requesting = false;
    runtime.confirmingActionIds = [];
    notify();
    await abortAssistantSession(sessionId).catch(() => undefined);
    await refreshPermissions(sessionId).catch(() => undefined);
    runtime.requesting = false;
    runtime.abortController = null;
    notify();
  }

  function finalizeSessionStream(sessionId: string): void {
    const runtime = ensureRuntime(sessionId);
    clearDetachedSessionWatch(sessionId);
    runtime.abortController = null;
    runtime.requesting = false;
    notify();
    void Promise.allSettled([
      hydrateSession(sessionId),
      refreshPermissions(sessionId),
    ]).finally(() => {
      resetTransientStreamState(sessionId, { notifyView: true });
    });
  }

  function startSessionStream(
    sessionId: string,
    reader: ReadableStreamDefaultReader<Uint8Array>,
    signal?: AbortSignal,
  ): void {
    const decoder = new TextDecoder();
    let buffer = "";
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      finalizeSessionStream(sessionId);
    };

    const drain = async () => {
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          if (value) {
            buffer += decoder.decode(value, { stream: true });
            const boundary = buffer.lastIndexOf("\n\n");
            if (boundary >= 0) {
              const ready = buffer.slice(0, boundary);
              buffer = buffer.slice(boundary + 2);
              for (const event of parseSseBlocks(ready)) {
                handleDirectStreamEvent(sessionId, event.event, event.data);
                scheduleNotify();
                if (event.event !== "done" || !event.data) continue;
                const fallbackReason = String(event.data.fallback_reason || "").trim();
                const executionMode = String(event.data.execution_mode || "").trim().toLowerCase();
                if (fallbackReason) {
                  appendAssistantNotice(
                    sessionId,
                    `注意：本轮未能接管远端工作区，已回退为本地执行。原因：${fallbackReason}`,
                  );
                } else if (executionMode === "ssh") {
                  const serverId = String(event.data.workspace_server_id || "").trim();
                  if (serverId) {
                    appendAssistantNotice(
                      sessionId,
                      `本轮已通过远端工作区服务器执行：${serverId}。`,
                    );
                  }
                }
              }
            }
          }
        }
        if (buffer.trim()) {
          for (const event of parseSseBlocks(buffer)) {
            handleDirectStreamEvent(sessionId, event.event, event.data);
            scheduleNotify();
            if (event.event !== "done" || !event.data) continue;
            const fallbackReason = String(event.data.fallback_reason || "").trim();
            if (fallbackReason) {
              appendAssistantNotice(
                sessionId,
                `注意：本轮未能接管远端工作区，已回退为本地执行。原因：${fallbackReason}`,
              );
            }
          }
        }
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          console.warn("[assistant-instance] request stream drain failed", error);
        }
      } finally {
        finish();
      }
    };

    void drain();

    if (signal) {
      signal.addEventListener("abort", () => {
        reader.cancel().catch(() => undefined);
        finish();
      }, { once: true });
    }
  }

  function isTrackedSession(sessionId: string): boolean {
    return trackedSessionIds().has(sessionId);
  }

  function handleDisposedDirectory(directory: string): void {
    const normalizedDirectory = directory.trim();
    if (!normalizedDirectory) return;
    let changed = false;
    for (const [sessionId, session] of Object.entries(busState.sessions)) {
      if ((session.directory || "").trim() !== normalizedDirectory) continue;
      removeSession(sessionId);
      changed = true;
    }
    if (changed) {
      notify();
      if (activeConversationId) {
        void queueBootstrapConversation(activeConversationId);
      }
    }
  }

  function applyEnvelope(sessionId: string, type: string, properties: Record<string, unknown>): void {
    const runtime = ensureRuntime(sessionId);
    switch (type) {
      case "session.message.updated": {
        const message = (properties.message && typeof properties.message === "object"
          ? properties.message
          : null) as Record<string, unknown> | null;
        if (!message) return;
        setSessionMessages(sessionId, upsertBackendMessage(busState.messages[sessionId] || [], message));
        pruneDuplicateLocalErrors(sessionId);
        reconcileTransientStreamWithBus(sessionId);
        notify();
        return;
      }
      case "session.message.part.updated": {
        const part = (properties.part && typeof properties.part === "object"
          ? properties.part
          : null) as Record<string, unknown> | null;
        if (!part) return;
        setSessionMessages(sessionId, upsertBackendPart(busState.messages[sessionId] || [], part));
        reconcileTransientStreamWithBus(sessionId);
        notify();
        return;
      }
      case "session.message.part.delta":
        setSessionMessages(sessionId, applyBackendPartDelta(busState.messages[sessionId] || [], properties));
        reconcileTransientStreamWithBus(sessionId);
        notify();
        return;
      case "session.message.part.deleted":
        setSessionMessages(sessionId, removeBackendPart(busState.messages[sessionId] || [], assistantPartIdOf(properties)));
        notify();
        return;
      case "session.message.deleted":
        setSessionMessages(sessionId, removeBackendMessage(busState.messages[sessionId] || [], assistantMessageIdOf(properties)));
        notify();
        return;
      case "session.prompt.started":
      case "session.prompt.queued":
        runtime.requesting = true;
        notify();
        return;
      case "session.status":
        setSessionStatus(
          sessionId,
          (properties.status && typeof properties.status === "object"
            ? properties.status
            : { type: "busy" }) as OpenCodeSessionStatus,
        );
        notify();
        return;
      case "session.prompt.finished":
      case "session.prompt.paused":
        runtime.requesting = false;
        clearDetachedSessionWatch(sessionId);
        notify();
        void refreshPermissions(sessionId);
        return;
      case "session.idle":
        runtime.requesting = false;
        clearDetachedSessionWatch(sessionId);
        setSessionStatus(sessionId, IDLE_STATUS);
        notify();
        void refreshPermissions(sessionId);
        return;
      case "session.error": {
        runtime.requesting = false;
        clearDetachedSessionWatch(sessionId);
        const message = String(properties.message || properties.error || "会话执行失败").trim();
        if (message) {
          appendError(sessionId, message);
        } else {
          notify();
        }
        return;
      }
      default:
        return;
    }
  }

  function handleGlobalEnvelope(envelope: GlobalBusEnvelope): void {
    const payload = envelope.payload;
    if (!payload || typeof payload !== "object") return;
    const type = String(payload.type || "").trim();
    if (!type || type === "server.connected" || type === "server.heartbeat") return;
    const properties = (payload.properties && typeof payload.properties === "object"
      ? payload.properties
      : {}) as Record<string, unknown>;

    if (type === "global.disposed") {
      clearAllSessions();
      notify();
      if (activeConversationId) {
        void queueBootstrapConversation(activeConversationId);
      }
      return;
    }

    if (type === "server.instance.disposed") {
      handleDisposedDirectory(String(properties.directory || ""));
      return;
    }

    const sessionId = String(properties.sessionID || "").trim();
    if (!sessionId || !isTrackedSession(sessionId)) return;
    applyEnvelope(sessionId, type, properties);
  }

  function createConversation(workspace?: ConversationWorkspace | null, options?: CreateConversationOptions): string {
    const previousActiveId = activeConversationId;
    const previousSessionId = currentActiveSessionId();
    const previousBootstrapKey = conversationBootstrapKey(previousActiveId);
    const conversation = createDraftConversation(context, uid(), workspace);
    suppressConversationStoreChange = true;
    try {
      if (options?.persist === false) {
        draftConversations.set(conversation.id, conversation);
      } else {
        conversationStore.upsertConversation(conversation);
      }
      applyConversationSelection(
        conversation.id,
        previousActiveId,
        previousSessionId,
        previousBootstrapKey,
        conversation,
      );
      return conversation.id;
    } finally {
      suppressConversationStoreChange = false;
    }
  }

  function switchConversation(conversationId: string): void {
    const previousActiveId = activeConversationId;
    const previousSessionId = currentActiveSessionId();
    const previousBootstrapKey = conversationBootstrapKey(previousActiveId);
    applyConversationSelection(conversationId, previousActiveId, previousSessionId, previousBootstrapKey);
  }

  function deleteConversation(conversationId: string): void {
    const previousActiveId = activeConversationId;
    const previousSessionId = currentActiveSessionId();
    const previousBootstrapKey = conversationBootstrapKey(previousActiveId);
    suppressConversationStoreChange = true;
    try {
      draftConversations.delete(conversationId);
      conversationStore.deleteConversation(conversationId);
      const nextPreferredConversationId = previousActiveId === conversationId ? null : previousActiveId;
      applyConversationSelection(nextPreferredConversationId, previousActiveId, previousSessionId, previousBootstrapKey);
    } finally {
      suppressConversationStoreChange = false;
    }
  }

  function createConversationWithRuntime(options?: CreateConversationOptions): string {
    const source = createSnapshot();
    const conversationId = createConversation(source.activeWorkspace || undefined, options);
    patchResolvedConversation(conversationId, {
      assistantBackendId: source.activeBackendId,
      assistantBackendLabel: source.activeBackendLabel,
      assistantMode: source.agentMode,
      assistantReasoningLevel: source.reasoningLevel,
      assistantSkillIds: source.activeSkillIds.length > 0 ? [...source.activeSkillIds] : null,
    });
    return conversationId;
  }

  function ensureConversation(): string {
    const current = String(currentActiveConversationId() || "").trim();
    if (current) return current;
    return createConversationWithRuntime();
  }

  function patchConversation(conversationId: string, patch: Partial<ConversationMeta>): void {
    patchResolvedConversation(conversationId, patch);
  }

  function patchActiveConversation(patch: Partial<ConversationMeta>): string {
    const conversationId = ensureConversation();
    patchConversation(conversationId, patch);
    return conversationId;
  }

  function setMountedPapers(input: MountedPapersInput): string {
    const nextIds = input.paperIds.map((item) => String(item || "").trim()).filter(Boolean);
    const nextTitles = input.paperTitles.map((item) => String(item || "").trim()).filter(Boolean);
    const primaryPaperId = String(input.primaryPaperId || nextIds[0] || "").trim() || null;
    return patchActiveConversation({
      mountedPaperIds: nextIds.length > 0 ? nextIds : null,
      mountedPaperTitles: nextTitles.length > 0 ? nextTitles : null,
      mountedPaperId: primaryPaperId,
      mountedPaperTitle: nextTitles.length > 0 ? buildMountedPaperSummary(nextTitles) : null,
      ...(input.conversationTitle ? { title: input.conversationTitle } : {}),
    });
  }

  function removeMountedPaper(paperId: string, options?: RemoveMountedPaperOptions): void {
    const normalized = String(paperId || "").trim();
    const conversation = currentActiveConversation();
    const mountedPaperIds = normalizeMountedPaperIds(conversation);
    if (!normalized || mountedPaperIds.length === 0) return;
    const mountedPaperTitleMap = new Map<string, string>();
    const mountedPaperTitles = normalizeMountedPaperTitles(conversation);
    mountedPaperIds.forEach((id, index) => {
      mountedPaperTitleMap.set(id, mountedPaperTitles[index] || "");
    });
    const nextIds = mountedPaperIds.filter((item) => item !== normalized);
    const nextTitles = nextIds.map((id) => mountedPaperTitleMap.get(id) || id);
    const focusedPaperId = String(options?.focusedPaperId || "").trim() || null;
    void patchActiveConversation({
      mountedPaperIds: nextIds.length > 0 ? nextIds : null,
      mountedPaperTitles: nextTitles.length > 0 ? nextTitles : null,
      mountedPaperId: nextIds.length > 0 ? (focusedPaperId === normalized ? nextIds[0] : (focusedPaperId || nextIds[0])) : null,
      mountedPaperTitle: nextTitles.length > 0 ? buildMountedPaperSummary(nextTitles) : null,
    });
  }

  function clearMountedPapers(): void {
    void patchActiveConversation({
      mountedPaperIds: null,
      mountedPaperTitles: null,
      mountedPaperId: null,
      mountedPaperTitle: null,
    });
  }

  if (activeConversationId) {
    void queueBootstrapConversation(activeConversationId);
  }

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getSnapshot() {
      return snapshot;
    },
    syncContext(nextContext) {
      context = nextContext;
      notify();
    },
    createConversation,
    createConversationWithRuntime,
    switchConversation,
    deleteConversation,
    ensureConversation,
    patchConversation,
    patchActiveConversation,
    setMountedPapers,
    removeMountedPaper,
    clearMountedPapers,
    setAgentMode(mode) {
      const normalized = normalizeAgentMode(mode, "build");
      if (currentActiveConversationId()) {
        setActiveConversationRuntimePatch({ assistantMode: normalized });
        return;
      }
      context = {
        ...context,
        defaultAgentMode: normalized,
      };
      context.setDefaultAgentMode(normalized);
      notify();
    },
    setReasoningLevel(level) {
      const normalized = normalizeReasoningLevel(level, "default");
      if (currentActiveConversationId()) {
        setActiveConversationRuntimePatch({ assistantReasoningLevel: normalized });
        return;
      }
      context = {
        ...context,
        defaultReasoningLevel: normalized,
      };
      context.setDefaultReasoningLevel(normalized);
      notify();
    },
    replaceSkills(skillIds) {
      const normalized = normalizeSkillIds(skillIds);
      if (currentActiveConversationId()) {
        setActiveConversationRuntimePatch({
          assistantSkillIds: normalized.length > 0 ? normalized : null,
        });
        return;
      }
      context = {
        ...context,
        defaultActiveSkillIds: normalized,
      };
      context.setDefaultActiveSkillIds(normalized);
      notify();
    },
    clearSkills() {
      if (currentActiveConversationId()) {
        setActiveConversationRuntimePatch({ assistantSkillIds: null });
        return;
      }
      context = {
        ...context,
        defaultActiveSkillIds: [],
      };
      context.setDefaultActiveSkillIds([]);
      notify();
    },
    setAssistantBackendId(backendId) {
      const normalized = normalizeAssistantBackendId(backendId);
      if (currentActiveConversationId()) {
        setActiveConversationRuntimePatch({
          assistantBackendId: normalized,
          assistantBackendLabel: defaultAssistantBackendLabel(normalized),
        });
        return;
      }
      context = {
        ...context,
        defaultAssistantBackendId: normalized,
      };
      context.setDefaultAssistantBackendId(normalized);
      notify();
    },
    setCanvas(value) {
      const sessionId = currentActiveSessionId();
      if (!sessionId) return;
      ensureRuntime(sessionId).canvas = value;
      notify();
    },
    async sendMessage(input) {
      const payload = typeof input === "string" ? { displayText: input, requestText: input } : input;
      const displayText = payload.displayText.trim();
      const requestText = (payload.requestText || payload.displayText).trim();
      if (!displayText || !requestText) {
        throw new Error("blocked");
      }

      let conversationId = currentActiveConversationId();
      if (!conversationId) {
        const source = createSnapshot();
        if (!source.activeWorkspace) {
          throw new Error(ASSISTANT_WORKSPACE_REQUIRED_MESSAGE);
        }
        conversationId = createConversationWithRuntime();
      }

      seedConversationTitle(conversationId, displayText);
      const sessionId = await ensureSessionForConversation(conversationId);
      if (!sessionId) {
        throw new Error("blocked");
      }

      const runtime = ensureRuntime(sessionId);
      if ((busState.permissions[sessionId] || []).length > 0) {
        throw new Error("blocked");
      }

      if (runtime.requesting) {
        await interruptSessionRequest(sessionId);
      }
      if ((busState.permissions[sessionId] || []).length > 0) {
        throw new Error("blocked");
      }

      const abortController = new AbortController();
      runtime.abortController = abortController;
      runtime.requesting = true;
      clearLocalErrors(sessionId);
      resetTransientStreamState(sessionId);
      notify();
      try {
        const request = requestContextFor(conversationId);
        const payload = {
          parts: [{ type: "text", text: requestText }],
          display_text: displayText,
          agent_backend_id: request.agentBackendId,
          mode: request.agentMode,
          workspace_path: request.workspacePath,
          workspace_server_id: request.workspaceServerId,
          reasoning_level: request.reasoningLevel,
          active_skill_ids: request.activeSkillIds,
          mounted_paper_ids: request.mountedPaperIds,
          mounted_primary_paper_id: request.mountedPrimaryPaperId,
        };
        if (shouldUseDetachedAssistantTransport()) {
          const response = await promptAssistantSessionDetached(sessionId, payload, abortController.signal);
          if (runtime.abortController === abortController) {
            runtime.abortController = null;
          }
          if (!response.accepted) {
            runtime.requesting = false;
            notify();
            return;
          }
          watchDetachedSession(sessionId);
          notify();
          return;
        }
        const response = await promptAssistantSession(sessionId, payload, abortController.signal);
        void hydrateSession(sessionId);
        if (!response.body) {
          appendError(sessionId, "无响应流");
          return;
        }
        startSessionStream(sessionId, response.body.getReader(), abortController.signal);
      } catch (error) {
        if (runtime.abortController === abortController) {
          runtime.abortController = null;
        }
        runtime.requesting = false;
        notify();
        if (isAbortLikeError(error, abortController.signal)) {
          return;
        }
        appendError(sessionId, error instanceof Error ? error.message : "请求失败");
      }
    },
    async handleConfirm(actionId) {
      const sessionId = resolveActionSessionId(actionId);
      if (!sessionId) return;
      const runtime = ensureRuntime(sessionId);
      runtime.requesting = true;
      runtime.confirmingActionIds = addUnique(runtime.confirmingActionIds, actionId);
      clearLocalErrors(sessionId);
      resetTransientStreamState(sessionId);
      notify();
      cancelSessionStream(sessionId);
      const abortController = new AbortController();
      try {
        runtime.abortController = abortController;
        if (shouldUseDetachedAssistantTransport()) {
          const response = await replyAssistantPermissionDetached(
            sessionId,
            actionId,
            { response: "once" },
            abortController.signal,
          );
          if (runtime.abortController === abortController) {
            runtime.abortController = null;
          }
          if (!response.accepted) {
            runtime.requesting = false;
            runtime.confirmingActionIds = removeValue(runtime.confirmingActionIds, actionId);
            notify();
            return;
          }
          watchDetachedSession(sessionId);
          notify();
          return;
        }
        const response = await replyAssistantPermission(sessionId, actionId, { response: "once" }, abortController.signal);
        if (!response.body) {
          runtime.requesting = false;
          runtime.confirmingActionIds = removeValue(runtime.confirmingActionIds, actionId);
          notify();
          return;
        }
        startSessionStream(sessionId, response.body.getReader(), abortController.signal);
      } catch (error) {
        if (runtime.abortController === abortController) {
          runtime.abortController = null;
        }
        runtime.requesting = false;
        runtime.confirmingActionIds = removeValue(runtime.confirmingActionIds, actionId);
        notify();
        if (isAbortLikeError(error, abortController.signal)) {
          return;
        }
        appendError(sessionId, error instanceof Error ? error.message : "确认失败");
      }
    },
    async handleReject(actionId) {
      const sessionId = resolveActionSessionId(actionId);
      if (!sessionId) return;
      const runtime = ensureRuntime(sessionId);
      runtime.requesting = true;
      clearLocalErrors(sessionId);
      resetTransientStreamState(sessionId);
      notify();
      cancelSessionStream(sessionId);
      const abortController = new AbortController();
      try {
        runtime.abortController = abortController;
        if (shouldUseDetachedAssistantTransport()) {
          const response = await replyAssistantPermissionDetached(
            sessionId,
            actionId,
            { response: "reject" },
            abortController.signal,
          );
          if (runtime.abortController === abortController) {
            runtime.abortController = null;
          }
          if (!response.accepted) {
            runtime.requesting = false;
            notify();
            return;
          }
          watchDetachedSession(sessionId);
          notify();
          return;
        }
        const response = await replyAssistantPermission(sessionId, actionId, { response: "reject" }, abortController.signal);
        if (!response.body) {
          runtime.requesting = false;
          notify();
          return;
        }
        startSessionStream(sessionId, response.body.getReader(), abortController.signal);
      } catch (error) {
        if (runtime.abortController === abortController) {
          runtime.abortController = null;
        }
        runtime.requesting = false;
        notify();
        if (isAbortLikeError(error, abortController.signal)) {
          return;
        }
        appendError(sessionId, error instanceof Error ? error.message : "拒绝操作失败");
      }
    },
    async handleQuestionReply(actionId, answers) {
      const sessionId = resolveActionSessionId(actionId);
      if (!sessionId) return;
      const runtime = ensureRuntime(sessionId);
      runtime.requesting = true;
      runtime.confirmingActionIds = addUnique(runtime.confirmingActionIds, actionId);
      clearLocalErrors(sessionId);
      resetTransientStreamState(sessionId);
      notify();
      cancelSessionStream(sessionId);
      const abortController = new AbortController();
      try {
        runtime.abortController = abortController;
        if (shouldUseDetachedAssistantTransport()) {
          const response = await replyAssistantPermissionDetached(
            sessionId,
            actionId,
            {
              response: "answer",
              answers,
            },
            abortController.signal,
          );
          if (runtime.abortController === abortController) {
            runtime.abortController = null;
          }
          if (!response.accepted) {
            runtime.requesting = false;
            runtime.confirmingActionIds = removeValue(runtime.confirmingActionIds, actionId);
            notify();
            return;
          }
          watchDetachedSession(sessionId);
          notify();
          return;
        }
        const response = await replyAssistantPermission(sessionId, actionId, {
          response: "answer",
          answers,
        }, abortController.signal);
        if (!response.body) {
          runtime.requesting = false;
          runtime.confirmingActionIds = removeValue(runtime.confirmingActionIds, actionId);
          notify();
          return;
        }
        startSessionStream(sessionId, response.body.getReader(), abortController.signal);
      } catch (error) {
        if (runtime.abortController === abortController) {
          runtime.abortController = null;
        }
        runtime.requesting = false;
        runtime.confirmingActionIds = removeValue(runtime.confirmingActionIds, actionId);
        notify();
        if (isAbortLikeError(error, abortController.signal)) {
          return;
        }
        appendError(sessionId, error instanceof Error ? error.message : "提交问题回答失败");
      }
    },
    stopGeneration() {
      const sessionId = currentActiveSessionId();
      if (!sessionId) return;
      void interruptSessionRequest(sessionId);
    },
    destroy() {
      unsubscribeConversation();
      unsubscribeBus();
      clearAllSessions();
      listeners.clear();
    },
  };
}
