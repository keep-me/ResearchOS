import type { OpenCodeSessionInfo, OpenCodeSessionStatus } from "@/types";
import type {
  Conversation,
  ConversationMeta,
  ConversationWorkspace,
  CreateConversationOptions,
} from "@/hooks/useConversations";
import type { AgentMode, AgentReasoningLevel, GlobalBusEnvelope } from "@/types";

export interface QuestionOptionItem {
  label: string;
  description: string;
}

export interface QuestionPromptItem {
  header: string;
  question: string;
  options: QuestionOptionItem[];
  multiple?: boolean;
  custom?: boolean;
}

export interface ChatItem {
  id: string;
  type: "user" | "assistant" | "reasoning" | "step_group" | "action_confirm" | "question" | "error" | "artifact";
  content: string;
  streaming?: boolean;
  messageMode?: string;
  steps?: StepItem[];
  actionId?: string;
  actionDescription?: string;
  actionTool?: string;
  toolArgs?: Record<string, unknown>;
  questionItems?: QuestionPromptItem[];
  artifactTitle?: string;
  artifactContent?: string;
  artifactIsHtml?: boolean;
  timestamp: Date;
}

export interface StepItem {
  id: string;
  status: "running" | "done" | "error";
  toolName: string;
  toolArgs?: Record<string, unknown>;
  success?: boolean;
  summary?: string;
  data?: Record<string, unknown>;
  progressMessage?: string;
  progressCurrent?: number;
  progressTotal?: number;
}

export interface CanvasData {
  title: string;
  markdown: string;
  isHtml?: boolean;
}

export interface SendMessageInput {
  displayText: string;
  requestText?: string;
}

export interface AssistantInstanceSnapshot {
  conversationMetas: ConversationMeta[];
  activeConversationId: string | null;
  activeConversation: Conversation | null;
  activeWorkspace: ConversationWorkspace | null;
  activeSessionId: string | null;
  activeSession: OpenCodeSessionInfo | null;
  activeStatus: OpenCodeSessionStatus;
  conversationTitle: string;
  activeBackendId: string;
  activeBackendLabel: string;
  agentMode: AgentMode;
  reasoningLevel: AgentReasoningLevel;
  activeSkillIds: string[];
  items: ChatItem[];
  loading: boolean;
  pendingActionIds: string[];
  confirmingActionIds: string[];
  canvas: CanvasData | null;
  hasPendingConfirm: boolean;
}

export interface AssistantSessionRuntime {
  abortController: AbortController | null;
  hydrating: boolean;
  requesting: boolean;
  confirmingActionIds: string[];
  pendingSyncTimer: number | null;
  detachedSyncTimer: number | null;
  detachedSyncGeneration: number;
  canvas: CanvasData | null;
  localItems: ChatItem[];
  persistedItemsCache: {
    messagesRef: Array<Record<string, unknown>> | null;
    permissionsRef: Array<Record<string, unknown>> | null;
    items: ChatItem[];
  };
  streamState: {
    token: string | null;
    assistantMessageId: string | null;
    currentTextItemId: string | null;
    currentReasoningItemId: string | null;
  };
}

export interface AssistantSessionBusState {
  sessions: Record<string, OpenCodeSessionInfo>;
  sessionStatus: Record<string, OpenCodeSessionStatus>;
  messages: Record<string, Array<Record<string, unknown>>>;
  permissions: Record<string, Array<Record<string, unknown>>>;
}

export interface AssistantInstanceStoreContext {
  defaultAgentMode: AgentMode;
  setDefaultAgentMode: (mode: AgentMode) => void;
  defaultReasoningLevel: AgentReasoningLevel;
  setDefaultReasoningLevel: (level: AgentReasoningLevel) => void;
  defaultActiveSkillIds: string[];
  setDefaultActiveSkillIds: (skillIds: string[]) => void;
  defaultAssistantBackendId: string;
  setDefaultAssistantBackendId: (backendId: string) => void;
}

export interface MountedPapersInput {
  paperIds: string[];
  paperTitles: string[];
  primaryPaperId?: string | null;
  conversationTitle?: string | null;
}

export interface RemoveMountedPaperOptions {
  focusedPaperId?: string | null;
}

export interface AssistantInstanceStore {
  subscribe: (listener: () => void) => () => void;
  getSnapshot: () => AssistantInstanceSnapshot;
  syncContext: (context: AssistantInstanceStoreContext) => void;
  createConversation: (workspace?: ConversationWorkspace | null, options?: CreateConversationOptions) => string;
  createConversationWithRuntime: (options?: CreateConversationOptions) => string;
  switchConversation: (id: string) => void;
  deleteConversation: (id: string) => void;
  ensureConversation: () => string;
  patchConversation: (id: string, patch: Partial<ConversationMeta>) => void;
  patchActiveConversation: (patch: Partial<ConversationMeta>) => string;
  setMountedPapers: (input: MountedPapersInput) => string;
  removeMountedPaper: (paperId: string, options?: RemoveMountedPaperOptions) => void;
  clearMountedPapers: () => void;
  setAgentMode: (mode: AgentMode) => void;
  setReasoningLevel: (level: AgentReasoningLevel) => void;
  replaceSkills: (skillIds: string[]) => void;
  clearSkills: () => void;
  setAssistantBackendId: (backendId: string) => void;
  setCanvas: (value: CanvasData | null) => void;
  sendMessage: (input: string | SendMessageInput) => Promise<void>;
  handleConfirm: (actionId: string) => Promise<void>;
  handleReject: (actionId: string) => Promise<void>;
  handleQuestionReply: (actionId: string, answers: string[][]) => Promise<void>;
  stopGeneration: () => void;
  destroy: () => void;
}

export interface AssistantInstanceRequestContext {
  agentBackendId: string;
  workspacePath: string | null;
  workspaceServerId: string | null;
  agentMode: AgentMode;
  reasoningLevel: AgentReasoningLevel;
  activeSkillIds: string[];
  mountedPaperIds: string[];
  mountedPrimaryPaperId: string | null;
}

export interface AssistantSessionStatePayload {
  session: OpenCodeSessionInfo | null;
  messages: Array<Record<string, unknown>>;
  permissions: Array<Record<string, unknown>>;
  status: OpenCodeSessionStatus;
}

export type AssistantBusSubscriber = (envelope: GlobalBusEnvelope) => void;
