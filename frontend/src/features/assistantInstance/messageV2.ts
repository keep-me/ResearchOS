import type { CanvasData, ChatItem, QuestionPromptItem, StepItem } from "./types";
import { appendReasoningChunk } from "./reasoningText";
import { assistantMessageIdOf, assistantPartIdOf } from "./sessionProtocol";

const messageItemsCache = new WeakMap<Record<string, unknown>, ChatItem[]>();
const permissionItemCache = new WeakMap<Record<string, unknown>, ChatItem | null>();

export function normalizeBackendToolName(value: unknown): string {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const mcpMatch = raw.match(/^mcp__.+?__(.+)$/i);
  const normalized = mcpMatch ? String(mcpMatch[1] || "").trim() : raw;
  return normalized || raw;
}

const ARTIFACT_TOOL_NAMES = new Set([
  "generate_wiki",
  "generate_daily_brief",
]);

function backendTimestamp(raw: unknown): Date {
  const value = Number(raw);
  if (Number.isFinite(value) && value > 0) return new Date(value);
  return new Date();
}

function backendText(parts: Array<Record<string, unknown>>): string {
  return parts
    .filter((part) => String(part.type || "") === "text")
    .map((part) => String(part.text || part.content || ""))
    .join("");
}

function userDisplayText(info: Record<string, unknown>, fallback: string): string {
  const explicit = String(info.displayText || info.display_text || "").trim();
  if (explicit) return explicit;

  const text = String(fallback || "");
  const contextPrefix = "以下是用户已选择导入当前对话上下文";
  const questionMarker = "用户本轮问题：";
  if (!text.includes(contextPrefix) || !text.includes(questionMarker)) {
    return text;
  }
  const markerIndex = text.lastIndexOf(questionMarker);
  const visible = text.slice(markerIndex + questionMarker.length).trim();
  return visible || text;
}

function backendMessageMode(info: Record<string, unknown>): string | undefined {
  const mode = String(info.mode || info.agent || "").trim().toLowerCase();
  return mode || undefined;
}

function pendingQuestionItems(metadata: Record<string, unknown>): QuestionPromptItem[] {
  const raw = Array.isArray(metadata.questions) ? metadata.questions : [];
  const items: QuestionPromptItem[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const record = item as Record<string, unknown>;
    const header = String(record.header || "").trim();
    const question = String(record.question || "").trim();
    const rawOptions = Array.isArray(record.options) ? record.options : [];
    const options = rawOptions
      .filter((option): option is Record<string, unknown> => Boolean(option && typeof option === "object"))
      .map((option) => ({
        label: String(option.label || "").trim(),
        description: String(option.description || "").trim(),
      }))
      .filter((option) => option.label && option.description);
    if (!header || !question || options.length === 0) continue;
    items.push({
      header,
      question,
      options,
      multiple: record.multiple === true,
      custom: record.custom !== false,
    });
  }
  return items;
}

export function sortBackendMessages(messages: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  return [...messages].sort((left, right) => {
    const leftInfo = (left.info && typeof left.info === "object" ? left.info : {}) as Record<string, unknown>;
    const rightInfo = (right.info && typeof right.info === "object" ? right.info : {}) as Record<string, unknown>;
    const leftTime = Number(((leftInfo.time as Record<string, unknown> | undefined)?.created) || 0);
    const rightTime = Number(((rightInfo.time as Record<string, unknown> | undefined)?.created) || 0);
    if (leftTime !== rightTime) return leftTime - rightTime;
    return backendMessageId(left).localeCompare(backendMessageId(right));
  });
}

export function backendMessageId(message: Record<string, unknown>): string {
  const info = (message.info && typeof message.info === "object" ? message.info : {}) as Record<string, unknown>;
  return String(info.id || "");
}

function buildArtifactFromData(
  data: Record<string, unknown> | undefined,
  toolName?: string,
): { item: ChatItem; canvas: CanvasData } | null {
  if (!data) return null;
  const normalizedToolName = normalizeBackendToolName(toolName);
  if (!ARTIFACT_TOOL_NAMES.has(normalizedToolName)) {
    return null;
  }
  if (data.html) {
    const title = String(data.title || "研究日报");
    const content = String(data.html);
    return {
      item: {
        id: `art_${Date.now()}`,
        type: "artifact",
        content: "",
        artifactTitle: title,
        artifactContent: content,
        artifactIsHtml: true,
        timestamp: new Date(),
      },
      canvas: { title, markdown: content, isHtml: true },
    };
  }
  if (data.markdown) {
    const title = String(data.title || "报告");
    const content = String(data.markdown);
    return {
      item: {
        id: `art_${Date.now()}`,
        type: "artifact",
        content: "",
        artifactTitle: title,
        artifactContent: content,
        artifactIsHtml: false,
        timestamp: new Date(),
      },
      canvas: { title, markdown: content, isHtml: false },
    };
  }
  return null;
}

function backendToolStepItem(part: Record<string, unknown>): StepItem {
  const state = (part.state && typeof part.state === "object" ? part.state : {}) as Record<string, unknown>;
  const status = String(state.status || "").trim().toLowerCase();
  const stepStatus: StepItem["status"] = status === "error"
    ? "error"
    : status === "completed"
      ? "done"
      : "running";
  const metadata = (state.metadata && typeof state.metadata === "object" ? state.metadata : null) as Record<string, unknown> | null;
  const partData = (part.data && typeof part.data === "object" ? part.data : null) as Record<string, unknown> | null;
  const output = String(state.output || "").trim();
  const progressCurrent = Number(partData?.current ?? metadata?.current ?? metadata?.progress_current ?? NaN);
  const progressTotal = Number(partData?.total ?? metadata?.total ?? metadata?.progress_total ?? NaN);
  const progressMessage = String(
    partData?.message
    ?? partData?.progress_message
    ?? metadata?.message
    ?? metadata?.progress_message
    ?? state.title
    ?? part.summary
    ?? "",
  ).trim();

  let data: Record<string, unknown> | undefined;
  if (partData && Object.keys(partData).length > 0) {
    data = { ...partData };
    if (output && data.output === undefined) data.output = output;
  } else if (metadata && Object.keys(metadata).length > 0) {
    data = { ...metadata };
    if (output && data.output === undefined) data.output = output;
  } else if (output) {
    data = { output };
  }

  return {
    id: String(part.callID || part.id || `tool_${Math.random().toString(36).slice(2, 8)}`),
    status: stepStatus,
    toolName: normalizeBackendToolName(part.tool || "tool"),
    toolArgs: (state.input && typeof state.input === "object" ? state.input : undefined) as Record<string, unknown> | undefined,
    success: status === "completed" ? true : status === "error" ? false : undefined,
    summary: String(part.summary || state.title || ""),
    data,
    progressMessage: progressMessage || undefined,
    progressCurrent: Number.isFinite(progressCurrent) ? progressCurrent : undefined,
    progressTotal: Number.isFinite(progressTotal) ? progressTotal : undefined,
  };
}

function assistantMessageItems(
  messageId: string,
  timestamp: Date,
  parts: Array<Record<string, unknown>>,
  messageMode?: string,
): ChatItem[] {
  const items: ChatItem[] = [];
  let buffer = "";
  let bufferType: "assistant" | "reasoning" | null = null;
  let sequence = 0;

  const flushBuffer = () => {
    const text = buffer;
    const type = bufferType;
    buffer = "";
    bufferType = null;
    if (!type || !text.trim()) return;
    items.push({
      id: `${type}_${messageId}_${sequence++}`,
      type,
      content: text,
      timestamp,
      messageMode,
    });
  };

  for (const part of parts) {
    const partType = String(part.type || "").trim().toLowerCase();
    if (partType === "text" || partType === "reasoning") {
      const targetType: "assistant" | "reasoning" = partType === "reasoning" ? "reasoning" : "assistant";
      const text = String(part.text || part.content || "");
      if (!text) continue;
      if (bufferType !== targetType) {
        flushBuffer();
        bufferType = targetType;
      }
      buffer = targetType === "reasoning" ? appendReasoningChunk(buffer, text) : `${buffer}${text}`;
      continue;
    }

    flushBuffer();

    if (partType === "tool") {
      items.push({
        id: `tool_${messageId}_${String(part.id || sequence++)}`,
        type: "step_group",
        content: "",
        steps: [backendToolStepItem(part)],
        timestamp,
        messageMode,
      });
      const artifact = buildArtifactFromData(
        (part.data && typeof part.data === "object" ? part.data : undefined) as Record<string, unknown> | undefined,
        String(part.tool || ""),
      );
      if (artifact) {
        items.push({
          ...artifact.item,
          id: `artifact_${messageId}_${String(part.id || sequence++)}`,
          timestamp,
          messageMode,
        });
      }
      continue;
    }

    if (partType === "retry") {
      const attempt = Number(part.attempt || 0);
      const retryMessage = String(part.message || "");
      const reason = String(((part.error && typeof part.error === "object" ? part.error : {}) as Record<string, unknown>).message || "");
      const content = [
        `模型第 ${attempt || 1} 次重试`,
        retryMessage || reason,
      ].filter(Boolean).join("：");
      if (content) {
        items.push({
          id: `retry_${messageId}_${String(part.id || sequence++)}`,
          type: "reasoning",
          content,
          timestamp,
          messageMode,
        });
      }
    }
  }

  flushBuffer();
  return items;
}

export function sessionMessagesToChatItems(
  messages: Array<Record<string, unknown>>,
  permissions: Array<Record<string, unknown>>,
): ChatItem[] {
  const items: ChatItem[] = [];

  for (const message of messages) {
    const cachedItems = messageItemsCache.get(message);
    if (cachedItems) {
      items.push(...cachedItems);
      continue;
    }

    const messageItems: ChatItem[] = [];
    const info = (message.info && typeof message.info === "object" ? message.info : {}) as Record<string, unknown>;
    const role = String(info.role || "");
    const parts = Array.isArray(message.parts)
      ? message.parts.filter((part): part is Record<string, unknown> => Boolean(part && typeof part === "object"))
      : [];
    const timestamp = backendTimestamp((info.time && typeof info.time === "object" ? (info.time as Record<string, unknown>).created : undefined));
    if (role === "user") {
      const text = userDisplayText(info, backendText(parts) || String(message.content || ""));
      if (text.trim()) {
        messageItems.push({
          id: String(info.id || `user_${timestamp.getTime()}`),
          type: "user",
          content: text,
          timestamp,
        });
      }
      messageItemsCache.set(message, messageItems);
      items.push(...messageItems);
      continue;
    }
    if (role !== "assistant") {
      messageItemsCache.set(message, messageItems);
      continue;
    }
    const messageId = String(info.id || `assistant_${timestamp.getTime()}`);
    const messageMode = backendMessageMode(info);
    messageItems.push(...assistantMessageItems(messageId, timestamp, parts, messageMode));

    const errorInfo = (info.error && typeof info.error === "object" ? info.error : {}) as Record<string, unknown>;
    const errorMessage = String(errorInfo.message || "").trim();
    if (errorMessage) {
      messageItems.push({
        id: `error_${messageId}`,
        type: "error",
        content: errorMessage,
        timestamp,
        messageMode,
      });
    }
    messageItemsCache.set(message, messageItems);
    items.push(...messageItems);
  }

  for (const permission of permissions) {
    const cachedItem = permissionItemCache.get(permission);
    if (cachedItem !== undefined) {
      if (cachedItem) items.push(cachedItem);
      continue;
    }

    const permissionId = String(permission.id || "").trim();
    if (!permissionId) {
      permissionItemCache.set(permission, null);
      continue;
    }
    const metadata = (permission.metadata && typeof permission.metadata === "object"
      ? permission.metadata
      : {}) as Record<string, unknown>;
    const questionItems = pendingQuestionItems(metadata);
    const nextItem: ChatItem = (String(permission.permission || "").trim() === "question" || questionItems.length > 0)
      ? {
          id: `pending_${permissionId}`,
          type: "question",
          content: "",
          actionId: permissionId,
          actionDescription: String(metadata.title || metadata.description || "智能体需要更多信息"),
          actionTool: "question",
          questionItems,
          timestamp: new Date(),
        }
      : {
          id: `pending_${permissionId}`,
          type: "action_confirm",
          content: "",
          actionId: permissionId,
          actionDescription: String(
            permission.title
            || permission.description
            || metadata.title
            || metadata.description
            || permission.permission
            || "",
          ),
          actionTool: String(((permission.tool as Record<string, unknown> | undefined)?.name) || permission.permission || ""),
          toolArgs: (((permission.tool as Record<string, unknown> | undefined)?.arguments) || {}) as Record<string, unknown>,
          timestamp: new Date(),
        };
    permissionItemCache.set(permission, nextItem);
    items.push(nextItem);
  }

  return items;
}

export function deriveCanvas(messages: Array<Record<string, unknown>>): CanvasData | null {
  let latest: CanvasData | null = null;
  for (const message of messages) {
    const parts = Array.isArray(message.parts)
      ? message.parts.filter((part): part is Record<string, unknown> => Boolean(part && typeof part === "object"))
      : [];
    for (const part of parts) {
      if (String(part.type || "") !== "tool") continue;
      const artifact = buildArtifactFromData(
        (part.data && typeof part.data === "object" ? part.data : undefined) as Record<string, unknown> | undefined,
        String(part.tool || ""),
      );
      if (artifact) {
        latest = artifact.canvas;
      }
    }
  }
  return latest;
}

export function upsertBackendMessage(
  messages: Array<Record<string, unknown>>,
  message: Record<string, unknown>,
): Array<Record<string, unknown>> {
  const targetId = backendMessageId(message);
  if (!targetId) return messages;
  const existingIndex = messages.findIndex((item) => backendMessageId(item) === targetId);
  if (existingIndex < 0) return sortBackendMessages([...messages, message]);
  return sortBackendMessages([
    ...messages.slice(0, existingIndex),
    message,
    ...messages.slice(existingIndex + 1),
  ]);
}

export function removeBackendMessage(messages: Array<Record<string, unknown>>, messageId: string): Array<Record<string, unknown>> {
  return messages.filter((message) => backendMessageId(message) !== messageId);
}

function placeholderAssistantMessageForPart(
  messageId: string,
  part: Record<string, unknown>,
): Record<string, unknown> {
  const partTime = (part.time && typeof part.time === "object") ? part.time as Record<string, unknown> : {};
  const created = Number(partTime.start ?? partTime.end ?? Date.now());
  const createdAt = Number.isFinite(created) && created > 0 ? created : Date.now();
  return {
    info: {
      id: messageId,
      role: "assistant",
      time: {
        created: createdAt,
        updated: createdAt,
      },
    },
    content: "",
    parts: [part],
  };
}

export function upsertBackendPart(
  messages: Array<Record<string, unknown>>,
  part: Record<string, unknown>,
): Array<Record<string, unknown>> {
  const messageId = assistantMessageIdOf(part);
  const partId = assistantPartIdOf(part);
  if (!messageId || !partId) return messages;
  let matched = false;
  const nextMessages = messages.map((message) => {
    if (backendMessageId(message) !== messageId) return message;
    matched = true;
    const parts = Array.isArray(message.parts)
      ? message.parts.filter((candidate): candidate is Record<string, unknown> => Boolean(candidate && typeof candidate === "object"))
      : [];
    const index = parts.findIndex((candidate) => String(candidate.id || "") === partId);
    const nextParts = index < 0
      ? [...parts, part]
      : [...parts.slice(0, index), part, ...parts.slice(index + 1)];
    return { ...message, parts: nextParts };
  });
  if (matched) return nextMessages;
  return sortBackendMessages([...nextMessages, placeholderAssistantMessageForPart(messageId, part)]);
}

export function applyBackendPartDelta(
  messages: Array<Record<string, unknown>>,
  payload: Record<string, unknown>,
): Array<Record<string, unknown>> {
  const messageId = assistantMessageIdOf(payload);
  const partId = assistantPartIdOf(payload);
  const field = String(payload.field || "");
  const delta = String(payload.delta || "");
  if (!messageId || !partId || !field || !delta) return messages;
  return messages.map((message) => {
    if (backendMessageId(message) !== messageId) return message;
    const parts = Array.isArray(message.parts)
      ? message.parts.filter((candidate): candidate is Record<string, unknown> => Boolean(candidate && typeof candidate === "object"))
      : [];
    const nextParts = parts.map((part) => {
      if (String(part.id || "") !== partId) return part;
      if (field === "text") {
        const current = String(part.text || "");
        const nextText = String(part.type || "").trim().toLowerCase() === "reasoning"
          ? appendReasoningChunk(current, delta)
          : `${current}${delta}`;
        return { ...part, text: nextText };
      }
      if (field === "state.raw") {
        const state = (part.state && typeof part.state === "object" ? part.state : {}) as Record<string, unknown>;
        return { ...part, state: { ...state, raw: `${String(state.raw || "")}${delta}` } };
      }
      return part;
    });
    return { ...message, parts: nextParts };
  });
}

export function removeBackendPart(messages: Array<Record<string, unknown>>, partId: string): Array<Record<string, unknown>> {
  if (!partId) return messages;
  return messages.map((message) => {
    const parts = Array.isArray(message.parts)
      ? message.parts.filter((candidate): candidate is Record<string, unknown> => Boolean(candidate && typeof candidate === "object"))
      : [];
    return {
      ...message,
      parts: parts.filter((part) => String(part.id || "") !== partId),
    };
  });
}
