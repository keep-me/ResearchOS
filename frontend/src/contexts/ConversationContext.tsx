/**
 * 对话上下文 - Sidebar 和 Agent 共享对话状态
 * @author Bamzc
 */
import { createContext, useContext, useMemo } from "react";
import {
  conversationStore,
  type ConversationMeta,
  type Conversation,
  type ConversationWorkspace,
  type CreateConversationOptions,
} from "@/hooks/useConversations";
import { useAssistantInstance } from "@/contexts/AssistantInstanceContext";

interface ConversationCtx {
  metas: ConversationMeta[];
  activeId: string | null;
  activeConv: Conversation | null;
  activeWorkspace: ConversationWorkspace | null;
  createConversation: (workspace?: ConversationWorkspace | null, options?: CreateConversationOptions) => string;
  switchConversation: (id: string) => void;
  deleteConversation: (id: string) => void;
  renameWorkspaceConversations: (workspace: ConversationWorkspace, workspaceTitle: string) => void;
  clearWorkspaceConversations: (workspace: ConversationWorkspace) => void;
  patchConversation: (id: string, patch: Partial<ConversationMeta>) => void;
}

const Ctx = createContext<ConversationCtx | null>(null);

export function ConversationProvider({ children }: { children: React.ReactNode }) {
  const assistant = useAssistantInstance();

  const value = useMemo<ConversationCtx>(() => ({
    metas: assistant.conversationMetas,
    activeId: assistant.activeConversationId,
    activeConv: assistant.activeConversation,
    activeWorkspace: assistant.activeWorkspace,
    createConversation: assistant.createConversation,
    switchConversation: assistant.switchConversation,
    deleteConversation: assistant.deleteConversation,
    renameWorkspaceConversations: conversationStore.renameWorkspaceConversations,
    clearWorkspaceConversations: conversationStore.clearWorkspaceConversations,
    patchConversation: assistant.patchConversation,
  }), [
    assistant.activeConversation,
    assistant.activeConversationId,
    assistant.activeWorkspace,
    assistant.conversationMetas,
    assistant.createConversation,
    assistant.deleteConversation,
    assistant.patchConversation,
    assistant.switchConversation,
  ]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useConversationCtx(): ConversationCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useConversationCtx must be inside ConversationProvider");
  return ctx;
}
