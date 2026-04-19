import type {
  AssistantSessionDiffEntry,
  AssistantSessionRevertInfo,
  OpenCodeSessionInfo,
  OpenCodeSessionStatus,
  OpenCodeSessionSummary,
} from "@/types";

function toRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? value as Record<string, unknown> : null;
}

function toPositiveNumber(value: unknown, fallback: number): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : fallback;
}

export function normalizeRecordArray(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"));
}

export function assistantMessageIdOf(value: unknown): string {
  const source = toRecord(value);
  return String(source?.message_id || source?.messageID || "").trim();
}

export function assistantPartIdOf(value: unknown): string {
  const source = toRecord(value);
  return String(source?.part_id || source?.partID || source?.id || "").trim();
}

export function normalizeSessionDiffEntry(raw: unknown): AssistantSessionDiffEntry | null {
  const source = toRecord(raw);
  if (!source) return null;
  const file = String(source.file || "").trim();
  const path = String(source.path || "").trim();
  if (!file && !path) return null;
  const status = String(source.status || "").trim();
  return {
    file: file || undefined,
    path: path || undefined,
    status: status || undefined,
    before: source.before == null ? undefined : String(source.before),
    after: source.after == null ? undefined : String(source.after),
    exists_before: typeof source.exists_before === "boolean" ? source.exists_before : undefined,
    exists_after: typeof source.exists_after === "boolean" ? source.exists_after : undefined,
    additions: Number.isFinite(Number(source.additions)) ? Number(source.additions) : undefined,
    deletions: Number.isFinite(Number(source.deletions)) ? Number(source.deletions) : undefined,
    workspace_path: source.workspace_path == null ? null : String(source.workspace_path || ""),
    workspace_server_id: source.workspace_server_id == null ? null : String(source.workspace_server_id || ""),
  };
}

export function normalizeSessionDiffEntries(raw: unknown): AssistantSessionDiffEntry[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => normalizeSessionDiffEntry(item))
    .filter((item): item is AssistantSessionDiffEntry => Boolean(item));
}

export function normalizeSessionRevertInfo(raw: unknown): AssistantSessionRevertInfo | null {
  const source = toRecord(raw);
  if (!source) return null;
  const message_id = assistantMessageIdOf(source) || null;
  const snapshot = String(source.snapshot || "").trim() || null;
  const diffs = normalizeSessionDiffEntries(source.diffs);
  if (!message_id && !snapshot && diffs.length === 0) return null;
  return {
    message_id,
    snapshot,
    diffs: diffs.length > 0 ? diffs : undefined,
  };
}

export function normalizeSessionSummary(raw: unknown): OpenCodeSessionSummary | undefined {
  const source = toRecord(raw);
  if (!source) return undefined;
  const diffs = normalizeSessionDiffEntries(source.diffs);
  const additions = Number(source.additions || 0);
  const deletions = Number(source.deletions || 0);
  const files = Number(source.files || 0);
  if (!Number.isFinite(additions) || !Number.isFinite(deletions) || !Number.isFinite(files)) {
    return undefined;
  }
  return {
    additions,
    deletions,
    files,
    diffs: diffs.length > 0 ? diffs : undefined,
  };
}

export function normalizeSessionInfo(
  raw: Record<string, unknown> | null | undefined,
  fallback?: {
    id?: string | null;
    directory?: string | null;
    title?: string | null;
  },
): OpenCodeSessionInfo | null {
  const source = raw && typeof raw === "object" ? raw : null;
  const id = String(source?.id || fallback?.id || "").trim();
  if (!id) return null;
  const now = Date.now();
  const time = (source?.time && typeof source.time === "object" ? source.time : {}) as Record<string, unknown>;
  const created = toPositiveNumber(time.created, now);
  const updated = toPositiveNumber(time.updated, created);
  const compacting = time.compacting == null ? undefined : toPositiveNumber(time.compacting, created);
  const archived = time.archived == null ? undefined : toPositiveNumber(time.archived, updated);
  const summary = normalizeSessionSummary(source?.summary);
  const revert = normalizeSessionRevertInfo(source?.revert);
  return {
    id,
    slug: source?.slug ? String(source.slug) : undefined,
    projectID: source?.projectID ? String(source.projectID) : undefined,
    workspaceID: source?.workspaceID ? String(source.workspaceID) : undefined,
    workspace_path: source?.workspace_path == null ? null : String(source.workspace_path || ""),
    workspace_server_id: source?.workspace_server_id == null ? null : String(source.workspace_server_id || ""),
    directory: String(source?.directory || source?.workspace_path || fallback?.directory || "").trim(),
    parentID: source?.parentID ? String(source.parentID) : null,
    title: String(source?.title || fallback?.title || "新对话"),
    version: source?.version ? String(source.version) : undefined,
    permission: Array.isArray(source?.permission) ? source.permission : undefined,
    summary,
    revert,
    time: {
      created,
      updated,
      compacting,
      archived,
    },
  };
}

export function normalizeSessionStatus(raw: unknown): OpenCodeSessionStatus {
  const source = toRecord(raw);
  const type = String(source?.type || "").trim();
  if (type === "busy") return { type: "busy" };
  if (type === "retry") {
    return {
      type: "retry",
      attempt: Number(source?.attempt || 0),
      message: String(source?.message || ""),
      next: Number(source?.next || 0),
    };
  }
  return { type: "idle" };
}
