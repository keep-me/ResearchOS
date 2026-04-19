import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { useLocation, useMatch, useNavigate } from "react-router-dom";
import type {
  Conversation,
  ConversationMeta,
  ConversationWorkspace,
  CreateConversationOptions,
} from "@/hooks/useConversations";
import { useAgentWorkbench } from "@/contexts/AgentWorkbenchContext";
import type { OpenCodeSessionInfo, OpenCodeSessionStatus } from "@/types";
import {
  createAssistantInstanceStore,
  type AssistantInstanceStore,
  type AssistantInstanceStoreContext,
  type CanvasData,
  type ChatItem,
  type MountedPapersInput,
  type RemoveMountedPaperOptions,
  type SendMessageInput,
  type StepItem,
} from "@/features/assistantInstance";

export type { CanvasData, ChatItem, SendMessageInput, StepItem } from "@/features/assistantInstance";

type ConversationPatch = Partial<ConversationMeta>;

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

interface AssistantInstanceContextValue {
  conversationMetas: ConversationMeta[];
  activeConversationId: string | null;
  activeConversation: Conversation | null;
  activeWorkspace: ConversationWorkspace | null;
  conversationTitle: string;
  activeSessionId: string | null;
  activeSession: OpenCodeSessionInfo | null;
  activeStatus: OpenCodeSessionStatus;
  activeBackendId: string;
  activeBackendLabel: string;
  settingsScope: "session" | "default";
  settingsScopeLabel: string;
  permissionPreset: ReturnType<typeof useAgentWorkbench>["permissionPreset"];
  agentMode: ReturnType<typeof useAgentWorkbench>["agentMode"];
  reasoningLevel: ReturnType<typeof useAgentWorkbench>["reasoningLevel"];
  activeSkillIds: string[];
  activeSkills: ReturnType<typeof useAgentWorkbench>["activeSkills"];
  availableSkills: ReturnType<typeof useAgentWorkbench>["availableSkills"];
  skillRoots: ReturnType<typeof useAgentWorkbench>["skillRoots"];
  skillsLoading: boolean;
  skillsError: string | null;
  mountedPaperIds: string[];
  mountedPaperTitles: string[];
  mountedPaperTitleMap: Map<string, string>;
  mountedPaperSummary: string;
  mountedPrimaryPaperId: string | null;
  items: ChatItem[];
  loading: boolean;
  pendingActions: Set<string>;
  confirmingActions: Set<string>;
  canvas: CanvasData | null;
  hasPendingConfirm: boolean;
  setPermissionPreset: ReturnType<typeof useAgentWorkbench>["setPermissionPreset"];
  createConversation: (workspace?: ConversationWorkspace | null, options?: CreateConversationOptions) => string;
  createConversationWithRuntime: (options?: CreateConversationOptions) => string;
  switchConversation: (id: string) => void;
  deleteConversation: (id: string) => void;
  ensureConversation: () => string;
  patchConversation: (id: string, patch: ConversationPatch) => void;
  patchActiveConversation: (patch: ConversationPatch) => string;
  setMountedPapers: (input: MountedPapersInput) => string;
  removeMountedPaper: (paperId: string, options?: RemoveMountedPaperOptions) => void;
  clearMountedPapers: () => void;
  setAgentMode: (mode: ReturnType<typeof useAgentWorkbench>["agentMode"]) => void;
  setReasoningLevel: (level: ReturnType<typeof useAgentWorkbench>["reasoningLevel"]) => void;
  toggleSkill: (skillId: string) => void;
  replaceSkills: (skillIds: string[]) => void;
  clearSkills: () => void;
  refreshSkills: () => Promise<void>;
  setAssistantBackendId: (backendId: string) => void;
  setCanvas: (value: CanvasData | null) => void;
  sendMessage: (input: string | SendMessageInput) => Promise<void>;
  handleConfirm: (actionId: string) => Promise<void>;
  handleReject: (actionId: string) => Promise<void>;
  handleQuestionReply: (actionId: string, answers: string[][]) => Promise<void>;
  stopGeneration: () => void;
}

const Context = createContext<AssistantInstanceContextValue | null>(null);

function buildStoreContext(workbench: ReturnType<typeof useAgentWorkbench>): AssistantInstanceStoreContext {
  return {
    defaultAgentMode: workbench.agentMode,
    setDefaultAgentMode: workbench.setAgentMode,
    defaultReasoningLevel: workbench.reasoningLevel,
    setDefaultReasoningLevel: workbench.setReasoningLevel,
    defaultActiveSkillIds: workbench.activeSkillIds,
    setDefaultActiveSkillIds: workbench.replaceSkills,
    defaultAssistantBackendId: workbench.assistantBackendId,
    setDefaultAssistantBackendId: workbench.setAssistantBackendId,
  };
}

export function AssistantInstanceProvider({ children }: { children: ReactNode }) {
  const workbench = useAgentWorkbench();
  const location = useLocation();
  const navigate = useNavigate();
  const assistantConversationMatch = useMatch("/assistant/:conversationId");
  const storeRef = useRef<AssistantInstanceStore | null>(null);
  const previousRouteConversationIdRef = useRef<string | null>(null);
  const pendingRouteSyncConversationIdRef = useRef<string | null>(null);

  if (storeRef.current === null) {
    storeRef.current = createAssistantInstanceStore(buildStoreContext(workbench));
  }

  const store = storeRef.current;

  useEffect(() => {
    store.syncContext(buildStoreContext(workbench));
  }, [
    store,
    workbench.activeSkillIds,
    workbench.agentMode,
    workbench.assistantBackendId,
    workbench.reasoningLevel,
    workbench.replaceSkills,
    workbench.setAgentMode,
    workbench.setAssistantBackendId,
    workbench.setReasoningLevel,
  ]);

  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const routeConversationId = String(assistantConversationMatch?.params.conversationId || "").trim() || null;
  const isAssistantRoute = location.pathname === "/assistant" || location.pathname.startsWith("/assistant/");

  useEffect(() => {
    const previousRouteConversationId = previousRouteConversationIdRef.current;
    previousRouteConversationIdRef.current = routeConversationId;
    if (!isAssistantRoute || !routeConversationId) {
      pendingRouteSyncConversationIdRef.current = null;
      return;
    }
    const routeConversationChanged = routeConversationId !== previousRouteConversationId;
    if (!routeConversationChanged) return;
    if (routeConversationId === snapshot.activeConversationId) {
      pendingRouteSyncConversationIdRef.current = null;
      return;
    }
    pendingRouteSyncConversationIdRef.current = routeConversationId;
    store.switchConversation(routeConversationId);
  }, [isAssistantRoute, routeConversationId, snapshot.activeConversationId, store]);

  useEffect(() => {
    if (!isAssistantRoute) return;
    const pendingRouteConversationId = pendingRouteSyncConversationIdRef.current;
    if (pendingRouteConversationId) {
      if (snapshot.activeConversationId !== pendingRouteConversationId) return;
      pendingRouteSyncConversationIdRef.current = null;
    }
    const targetPath = snapshot.activeConversationId ? `/assistant/${snapshot.activeConversationId}` : "/assistant";
    if (location.pathname === targetPath) return;
    navigate(targetPath, { replace: true });
  }, [isAssistantRoute, location.pathname, navigate, snapshot.activeConversationId]);

  useEffect(() => () => {
    store.destroy();
  }, [store]);

  const pendingActions = useMemo(() => new Set(snapshot.pendingActionIds), [snapshot.pendingActionIds]);
  const confirmingActions = useMemo(() => new Set(snapshot.confirmingActionIds), [snapshot.confirmingActionIds]);
  const activeSkills = useMemo(
    () => workbench.availableSkills.filter((skill) => snapshot.activeSkillIds.includes(skill.id)),
    [snapshot.activeSkillIds, workbench.availableSkills],
  );
  const mountedPaperIds = useMemo(
    () => normalizeMountedPaperIds(snapshot.activeConversation),
    [snapshot.activeConversation],
  );
  const mountedPaperTitles = useMemo(
    () => normalizeMountedPaperTitles(snapshot.activeConversation),
    [snapshot.activeConversation],
  );
  const mountedPaperTitleMap = useMemo(() => {
    const next = new Map<string, string>();
    mountedPaperIds.forEach((id, index) => {
      next.set(id, mountedPaperTitles[index] || "");
    });
    return next;
  }, [mountedPaperIds, mountedPaperTitles]);
  const mountedPaperSummary = useMemo(
    () => buildMountedPaperSummary(mountedPaperTitles),
    [mountedPaperTitles],
  );
  const settingsScope = snapshot.activeConversationId ? "session" as const : "default" as const;
  const settingsScopeLabel = settingsScope === "session" ? "当前会话" : "默认配置";

  const toggleSkill = useCallback((skillId: string) => {
    const normalized = String(skillId || "").trim();
    if (!normalized) return;
    const nextSkillIds = snapshot.activeSkillIds.includes(normalized)
      ? snapshot.activeSkillIds.filter((id) => id !== normalized)
      : [...snapshot.activeSkillIds, normalized];
    if (nextSkillIds.length === 0) {
      store.clearSkills();
      return;
    }
    store.replaceSkills(nextSkillIds);
  }, [snapshot.activeSkillIds, store]);

  const value = useMemo<AssistantInstanceContextValue>(() => ({
    conversationMetas: snapshot.conversationMetas,
    activeConversationId: snapshot.activeConversationId,
    activeConversation: snapshot.activeConversation,
    activeWorkspace: snapshot.activeWorkspace,
    conversationTitle: snapshot.conversationTitle,
    activeSessionId: snapshot.activeSessionId,
    activeSession: snapshot.activeSession,
    activeStatus: snapshot.activeStatus,
    activeBackendId: snapshot.activeBackendId,
    activeBackendLabel: snapshot.activeBackendLabel,
    settingsScope,
    settingsScopeLabel,
    permissionPreset: workbench.permissionPreset,
    agentMode: snapshot.agentMode,
    reasoningLevel: snapshot.reasoningLevel,
    activeSkillIds: snapshot.activeSkillIds,
    activeSkills,
    availableSkills: workbench.availableSkills,
    skillRoots: workbench.skillRoots,
    skillsLoading: workbench.skillsLoading,
    skillsError: workbench.skillsError,
    mountedPaperIds,
    mountedPaperTitles,
    mountedPaperTitleMap,
    mountedPaperSummary,
    mountedPrimaryPaperId: snapshot.activeConversation?.mountedPaperId || mountedPaperIds[0] || null,
    items: snapshot.items,
    loading: snapshot.loading,
    pendingActions,
    confirmingActions,
    canvas: snapshot.canvas,
    hasPendingConfirm: snapshot.hasPendingConfirm,
    setPermissionPreset: workbench.setPermissionPreset,
    createConversation: store.createConversation,
    createConversationWithRuntime: store.createConversationWithRuntime,
    switchConversation: store.switchConversation,
    deleteConversation: store.deleteConversation,
    ensureConversation: store.ensureConversation,
    patchConversation: store.patchConversation,
    patchActiveConversation: store.patchActiveConversation,
    setMountedPapers: store.setMountedPapers,
    removeMountedPaper: store.removeMountedPaper,
    clearMountedPapers: store.clearMountedPapers,
    setAgentMode: store.setAgentMode,
    setReasoningLevel: store.setReasoningLevel,
    toggleSkill,
    replaceSkills: store.replaceSkills,
    clearSkills: store.clearSkills,
    refreshSkills: workbench.refreshSkills,
    setAssistantBackendId: store.setAssistantBackendId,
    setCanvas: store.setCanvas,
    sendMessage: store.sendMessage,
    handleConfirm: store.handleConfirm,
    handleReject: store.handleReject,
    handleQuestionReply: store.handleQuestionReply,
    stopGeneration: store.stopGeneration,
  }), [
    activeSkills,
    confirmingActions,
    mountedPaperIds,
    mountedPaperSummary,
    mountedPaperTitleMap,
    mountedPaperTitles,
    pendingActions,
    settingsScope,
    settingsScopeLabel,
    snapshot.conversationMetas,
    snapshot.activeBackendId,
    snapshot.activeBackendLabel,
    snapshot.activeConversation,
    snapshot.activeConversationId,
    snapshot.activeConversation?.mountedPaperId,
    snapshot.activeSession,
    snapshot.activeSessionId,
    snapshot.activeSkillIds,
    snapshot.activeStatus,
    snapshot.activeWorkspace,
    snapshot.agentMode,
    snapshot.canvas,
    snapshot.conversationTitle,
    snapshot.hasPendingConfirm,
    snapshot.items,
    snapshot.loading,
    snapshot.reasoningLevel,
    store,
    toggleSkill,
    workbench.availableSkills,
    workbench.permissionPreset,
    workbench.refreshSkills,
    workbench.setPermissionPreset,
    workbench.skillRoots,
    workbench.skillsError,
    workbench.skillsLoading,
  ]);

  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useAssistantInstance(): AssistantInstanceContextValue {
  const value = useContext(Context);
  if (!value) {
    throw new Error("useAssistantInstance must be used inside AssistantInstanceProvider");
  }
  return value;
}
