import type {
  AgentMode,
  AgentReasoningLevel,
  OpenCodeSessionInfo,
  OpenCodeSessionStatus,
} from "@/types";
import { isTauri } from "@/lib/tauri";
import { sessionApi } from "@/services/api";
import { subscribeGlobalBus } from "@/services/globalBus";
import {
  normalizeRecordArray,
  normalizeSessionInfo,
  normalizeSessionStatus,
} from "./sessionProtocol";
import type { AssistantBusSubscriber, AssistantSessionStatePayload } from "./types";

export function subscribeAssistantBus(onEnvelope: AssistantBusSubscriber): () => void {
  return subscribeGlobalBus(onEnvelope);
}

export function shouldUseDetachedAssistantTransport(): boolean {
  return isTauri();
}

export async function fetchAssistantSessionState(sessionId: string): Promise<AssistantSessionStatePayload> {
  return sessionApi.state(sessionId).then((payload) => ({
    session: payload.session,
    messages: normalizeRecordArray(payload.messages),
    permissions: normalizeRecordArray(payload.permissions),
    status: normalizeSessionStatus(payload.status),
  })).catch(() => ({
    session: null,
    messages: [] as Array<Record<string, unknown>>,
    permissions: [] as Array<Record<string, unknown>>,
    status: { type: "idle" } as OpenCodeSessionStatus,
  }));
}

export async function fetchAssistantSessionPermissions(sessionId: string): Promise<Array<Record<string, unknown>>> {
  return sessionApi.permissions(sessionId).catch(() => []);
}

export async function ensureAssistantSession(
  sessionId: string,
  payload?: {
    directory?: string | null;
    workspace_path?: string | null;
    workspace_server_id?: string | null;
    agent_backend_id?: string | null;
    title?: string | null;
    mode?: AgentMode;
  },
): Promise<OpenCodeSessionInfo | null> {
  return sessionApi.create({
    id: sessionId,
    directory: payload?.directory || payload?.workspace_path || null,
    workspace_path: payload?.workspace_path || payload?.directory || null,
    workspace_server_id: payload?.workspace_server_id || null,
    agent_backend_id: payload?.agent_backend_id || null,
    title: payload?.title || null,
    mode: (payload?.mode as "build" | "plan" | undefined) || "build",
  }).catch(() =>
    normalizeSessionInfo(undefined, {
      id: sessionId,
      directory: payload?.directory || payload?.workspace_path || null,
      title: payload?.title || null,
    }),
  );
}

export function promptAssistantSession(
  sessionId: string,
  payload: {
    parts: Array<Record<string, unknown>>;
    agent_backend_id: string;
    mode: AgentMode;
    workspace_path: string | null;
    workspace_server_id: string | null;
    reasoning_level: AgentReasoningLevel;
    active_skill_ids: string[];
    mounted_paper_ids: string[];
    mounted_primary_paper_id: string | null;
  },
  signal?: AbortSignal,
) {
  return sessionApi.prompt(sessionId, payload, { signal });
}

export function promptAssistantSessionDetached(
  sessionId: string,
  payload: {
    parts: Array<Record<string, unknown>>;
    agent_backend_id: string;
    mode: AgentMode;
    workspace_path: string | null;
    workspace_server_id: string | null;
    reasoning_level: AgentReasoningLevel;
    active_skill_ids: string[];
    mounted_paper_ids: string[];
    mounted_primary_paper_id: string | null;
  },
  signal?: AbortSignal,
) {
  return sessionApi.promptDetached(sessionId, payload, { signal });
}

export function replyAssistantPermission(
  sessionId: string,
  actionId: string,
  payload: { response: string; answers?: string[][] },
  signal?: AbortSignal,
) {
  return sessionApi.replyPermission(sessionId, actionId, payload, { signal });
}

export function replyAssistantPermissionDetached(
  sessionId: string,
  actionId: string,
  payload: { response: string; answers?: string[][] },
  signal?: AbortSignal,
) {
  return sessionApi.replyPermissionDetached(sessionId, actionId, payload, { signal });
}

export function abortAssistantSession(sessionId: string) {
  return sessionApi.abort(sessionId);
}
