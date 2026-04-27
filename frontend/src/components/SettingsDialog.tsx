/**
 * 设置弹窗 - 参考 ResearchClaw 的 settings 信息架构，承载模型 / 助手策略 / 工作区 / MCP 配置
 */
import { useState, useEffect, useCallback, useMemo, type ReactNode } from "react";
import { useToast } from "@/contexts/ToastContext";
import { useAssistantInstance } from "@/contexts/AssistantInstanceContext";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { Empty } from "@/components/ui/Empty";
import ConfirmDialog from "@/components/ConfirmDialog";
import {
  llmConfigApi,
  assistantWorkspaceApi,
  workspaceRootApi,
  mcpApi,
  acpApi,
} from "@/services/api";
import { getErrorMessage } from "@/lib/errorHandler";
import type {
  LLMProviderConfig,
  LLMProviderCreate,
  LLMProviderUpdate,
  ActiveLLMConfig,
  LLMProviderTestResult,
  LLMProviderPreset,
  AssistantSkillItem,
  AssistantWorkspaceServer,
  AssistantWorkspaceServerPayload,
  AcpRegistryConfig,
  AcpRuntimeStatus,
  AcpServerInfo,
  McpRegistryConfig,
  McpRuntimeStatus,
  McpServerInfo,
} from "@/types";
import { cn } from "@/lib/utils";
import { formatDuration, timeAgo } from "@/lib/utils";
import {
  Bot,
  Cpu,
  HardDrive,
  Plus,
  Eye,
  EyeOff,
  Pencil,
  Power,
  PowerOff,
  Search,
  Server,
  Shield,
  RefreshCw,
  CheckCircle2,
  XCircle,
  Play,
  Zap,
  Trash2,
  ImagePlus,
} from "@/lib/lucide";

type SectionId =
  | "models"
  | "assistant"
  | "workspace"
  | "acp"
  | "mcp";

const SETTINGS_NAV_GROUPS: {
  id: string;
  label: string;
  items: { id: SectionId; label: string; keywords: string[]; icon: typeof Cpu }[];
}[] = [
  {
    id: "models",
    label: "模型与嵌入",
    items: [
      {
        id: "models",
        label: "模型与嵌入",
        keywords: ["model", "api", "llm", "provider", "embedding", "模型", "配置", "向量"],
        icon: Cpu,
      },
    ],
  },
  {
    id: "assistant",
    label: "Skills",
    items: [
      {
        id: "assistant",
        label: "Skills",
        keywords: ["assistant", "skill", "skills", "mode", "reasoning", "助手", "技能", "推理"],
        icon: Bot,
      },
    ],
  },
  {
    id: "workspace",
    label: "工作区与 SSH 服务器",
    items: [
      {
        id: "workspace",
        label: "工作区与 SSH 服务器",
        keywords: ["storage", "workspace", "root", "directory", "ssh", "目录", "工作区", "服务器"],
        icon: HardDrive,
      },
    ],
  },
  {
    id: "mcp",
    label: "ACP / MCP 服务",
    items: [
      {
        id: "acp",
        label: "ACP 智能体",
        keywords: ["acp", "agent", "custom", "assistant", "智能体", "自定义", "未绑定"],
        icon: Bot,
      },
      {
        id: "mcp",
        label: "MCP 服务",
        keywords: ["mcp", "server", "tool", "服务", "连接", "stdio", "http"],
        icon: Server,
      },
    ],
  },
];

const SECTION_IDS = new Set<SectionId>(["models", "assistant", "workspace", "acp", "mcp"]);

function initialSettingsSection(): SectionId {
  if (typeof window === "undefined") return "models";
  const section = new URLSearchParams(window.location.search).get("section") || "";
  return SECTION_IDS.has(section as SectionId) ? (section as SectionId) : "models";
}

const SECTION_META: Record<SectionId, { title: string }> = {
  models: { title: "模型与嵌入" },
  assistant: { title: "Skills" },
  workspace: { title: "工作区与 SSH 服务器" },
  acp: { title: "ACP 智能体" },
  mcp: { title: "MCP 服务" },
};

export function SettingsDialog({ onClose, embedded = false }: { onClose?: () => void; embedded?: boolean }) {
  const [activeSection, setActiveSection] = useState<SectionId>(() => initialSettingsSection());
  const [query, setQuery] = useState("");

  const filteredGroups = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return SETTINGS_NAV_GROUPS;
    return SETTINGS_NAV_GROUPS.map((group) => ({
      ...group,
      items: group.items.filter(
        (item) =>
          item.label.toLowerCase().includes(q) ||
          item.keywords.some((keyword) => keyword.includes(q)),
      ),
    })).filter((group) => group.items.length > 0);
  }, [query]);

  useEffect(() => {
    const visibleIds = filteredGroups.flatMap((group) => group.items.map((item) => item.id));
    if (visibleIds.length === 0) return;
    if (!visibleIds.includes(activeSection)) {
      setActiveSection(visibleIds[0]);
    }
  }, [activeSection, filteredGroups]);

  const content = (
    <div
      className={cn(
        "flex flex-col gap-4 lg:flex-row",
        embedded ? "min-h-0 lg:min-h-[calc(100dvh-11.5rem)]" : "",
      )}
      style={embedded ? undefined : { height: "min(720px, 82vh)" }}
    >
        <aside className="flex w-full shrink-0 flex-col rounded-xl border border-border bg-sidebar p-3 sm:p-4 lg:w-[248px]">
          <div className="mb-4">
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索设置项"
              className="h-10 rounded-md border-border bg-surface pl-10"
            />
            <Search className="pointer-events-none relative -mt-8 ml-3 h-4 w-4 text-ink-tertiary" />
          </div>
          <div className="min-h-0 flex-1 space-y-3 overflow-visible lg:space-y-4 lg:overflow-y-auto lg:pr-1">
            {filteredGroups.map((group) => (
              <div key={group.id}>
                <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-tertiary lg:px-0">
                  {group.label}
                </div>
                <div className="overflow-x-auto pb-1 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden lg:overflow-visible lg:pb-0">
                  <div className="flex min-w-max gap-2 lg:min-w-0 lg:flex-col lg:gap-1">
                    {group.items.map((item) => (
                      <button
                        key={item.id}
                        onClick={() => setActiveSection(item.id)}
                        className={cn(
                          "flex min-h-11 items-center gap-3 rounded-xl border px-3 py-2.5 text-left text-sm transition-colors duration-150 lg:w-full",
                          activeSection === item.id
                            ? "border-border bg-active text-ink"
                            : "border-transparent text-ink-secondary hover:bg-hover hover:text-ink",
                        )}
                      >
                        <item.icon className="h-4 w-4 shrink-0" />
                        <span className="whitespace-nowrap font-medium">{item.label}</span>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </aside>

        <div className="min-w-0 flex-1 overflow-hidden rounded-xl border border-border bg-surface p-4 sm:p-5">
          <div className="mb-4 border-b border-border pb-4">
            <h3 className="text-lg font-semibold text-ink sm:text-xl">
              {SECTION_META[activeSection].title}
            </h3>
          </div>

          <div className="overflow-visible lg:h-[calc(100%-64px)] lg:overflow-y-auto lg:pr-1">
            {activeSection === "models" && <LLMTab />}
            {activeSection === "assistant" && <AssistantSettingsSection />}
            {activeSection === "workspace" && <WorkspaceSettingsSection />}
            {activeSection === "acp" && <AcpSettingsSection />}
            {activeSection === "mcp" && <McpSettingsSection />}
          </div>
        </div>
    </div>
  );

  if (embedded) {
    return <div className="rounded-[24px] border border-border bg-surface p-3 sm:p-4 lg:p-5">{content}</div>;
  }

  return (
    <Modal title="系统配置" onClose={onClose || (() => {})} maxWidth="xl">
      {content}
    </Modal>
  );
}

function parseMultilineList(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseMultilineMap(value: string): Record<string, string> {
  const result: Record<string, string> = {};
  value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .forEach((line) => {
      const separator = line.includes("=") ? line.indexOf("=") : line.indexOf(":");
      if (separator <= 0) return;
      const key = line.slice(0, separator).trim();
      const nextValue = line.slice(separator + 1).trim();
      if (key && nextValue) result[key] = nextValue;
    });
  return result;
}

function formatUnixDate(value?: number | null): string {
  if (!value) return "未记录";
  return new Date(value * 1000).toLocaleString("zh-CN");
}

function isRemoteWorkspaceServer(server?: AssistantWorkspaceServer | null) {
  return server?.kind === "ssh" || server?.kind === "remote";
}

function skillSourceLabel(source: "codex" | "agents" | "project") {
  if (source === "project") return "项目";
  if (source === "codex") return "Codex";
  return "Agents";
}

function acpStatusBadgeVariant(server: AcpServerInfo): "success" | "warning" | "default" {
  if (!server.enabled || server.status === "disabled") return "default";
  return server.connected ? "success" : "warning";
}

function acpStatusLabel(server: AcpServerInfo) {
  if (!server.enabled || server.status === "disabled") return "已禁用";
  return server.connected ? "已连接" : "待连接";
}

function sortSkillsForDisplay(items: AssistantSkillItem[]) {
  const sourceRank: Record<AssistantSkillItem["source"], number> = {
    project: 0,
    codex: 1,
    agents: 2,
  };
  return [...items].sort((a, b) => {
    const sourceDelta = sourceRank[a.source] - sourceRank[b.source];
    if (sourceDelta !== 0) return sourceDelta;
    if (a.system !== b.system) return a.system ? 1 : -1;
    const pathDelta = a.relative_path.localeCompare(b.relative_path, "zh-CN");
    if (pathDelta !== 0) return pathDelta;
    return a.name.localeCompare(b.name, "zh-CN");
  });
}

function createWorkspaceServerDraft(server?: AssistantWorkspaceServer | null): AssistantWorkspaceServerPayload {
  return {
    id: server?.id,
    label: server?.label || "",
    host: server?.host || "",
    port: server?.port || 22,
    username: server?.username || "",
    password: "",
    private_key: "",
    passphrase: "",
    workspace_root: server?.workspace_root || "",
    enabled: server?.enabled ?? server?.phase !== "disabled",
  };
}

function WorkspaceSettingsSection() {
  const { toast } = useToast();
  const [servers, setServers] = useState<AssistantWorkspaceServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [editingServerId, setEditingServerId] = useState<string | null>(null);
  const [showServerModal, setShowServerModal] = useState(false);
  const [deletingServerId, setDeletingServerId] = useState<string | null>(null);
  const [deleteServerTarget, setDeleteServerTarget] = useState<AssistantWorkspaceServer | null>(null);
  const [draft, setDraft] = useState<AssistantWorkspaceServerPayload>(() => createWorkspaceServerDraft());
  const [probeResult, setProbeResult] = useState<string | null>(null);
  const [probeSuccess, setProbeSuccess] = useState<boolean | null>(null);

  const refreshServers = useCallback(async () => {
    setLoading(true);
    try {
      const result = await assistantWorkspaceApi.servers();
      setServers(result.items || []);
    } catch (error) {
      toast("error", getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void refreshServers();
  }, [refreshServers]);

  const remoteServers = useMemo(
    () => servers.filter((server) => isRemoteWorkspaceServer(server)),
    [servers],
  );

  const resetDraft = useCallback(() => {
    setEditingServerId(null);
    setDraft(createWorkspaceServerDraft());
    setProbeResult(null);
    setProbeSuccess(null);
  }, []);

  const closeServerModal = useCallback(() => {
    if (saving || testing) return;
    setShowServerModal(false);
    resetDraft();
  }, [resetDraft, saving, testing]);

  const handleEditServer = useCallback((server: AssistantWorkspaceServer) => {
    if (!isRemoteWorkspaceServer(server)) return;
    setEditingServerId(server.id);
    setDraft(createWorkspaceServerDraft(server));
    setProbeResult(null);
    setProbeSuccess(null);
    setShowServerModal(true);
  }, []);

  const handleSaveServer = useCallback(async () => {
    const label = (draft.label || "").trim();
    const host = (draft.host || "").trim();
    const username = (draft.username || "").trim();
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
    if (!(draft.password || "").trim() && !(draft.private_key || "").trim() && !editingServerId) {
      toast("warning", "请至少填写 SSH 密码或私钥");
      return;
    }

    setSaving(true);
    try {
      const payload: AssistantWorkspaceServerPayload = {
        id: draft.id,
        label,
        host,
        port: draft.port || 22,
        username,
        password: draft.password || "",
        private_key: draft.private_key || "",
        passphrase: draft.passphrase || "",
        workspace_root: draft.workspace_root || "",
        enabled: draft.enabled ?? true,
      };
      if (editingServerId) {
        await assistantWorkspaceApi.updateServer(editingServerId, payload);
      } else {
        await assistantWorkspaceApi.createServer(payload);
      }
      await refreshServers();
      resetDraft();
      setShowServerModal(false);
      toast("success", editingServerId ? "SSH 服务器已更新" : "SSH 服务器已新增");
    } catch (error) {
      toast("error", getErrorMessage(error));
    } finally {
      setSaving(false);
    }
  }, [draft, editingServerId, refreshServers, resetDraft, toast]);

  const handleProbeServer = useCallback(async () => {
    const host = (draft.host || "").trim();
    const username = (draft.username || "").trim();
    if (!host) {
      toast("warning", "请先填写 SSH 主机");
      return;
    }
    if (!username) {
      toast("warning", "请先填写 SSH 用户名");
      return;
    }
    if (!(draft.password || "").trim() && !(draft.private_key || "").trim() && !editingServerId) {
      toast("warning", "测试连接前请先填写 SSH 密码或私钥");
      return;
    }

    setTesting(true);
    setProbeResult(null);
    setProbeSuccess(null);
    try {
      const result = await assistantWorkspaceApi.probeSsh({
        host,
        port: draft.port || 22,
        username,
        password: draft.password || "",
        private_key: draft.private_key || "",
        passphrase: draft.passphrase || "",
        workspace_root: draft.workspace_root || "",
      });
      setProbeSuccess(result.success);
      setProbeResult(result.message || (result.success ? "SSH 连接成功" : "SSH 连接失败"));
      toast(result.success ? "success" : "warning", result.message || (result.success ? "SSH 连接成功" : "SSH 连接失败"));
    } catch (error) {
      const message = getErrorMessage(error);
      setProbeSuccess(false);
      setProbeResult(message);
      toast("error", message);
    } finally {
      setTesting(false);
    }
  }, [draft, editingServerId, toast]);

  const handleDeleteServer = useCallback(async () => {
    const server = deleteServerTarget;
    if (!server || !server.id || !isRemoteWorkspaceServer(server)) return;
    setDeletingServerId(server.id);
    try {
      await assistantWorkspaceApi.deleteServer(server.id);
      await refreshServers();
      if (editingServerId === server.id) {
        resetDraft();
      }
      setDeleteServerTarget(null);
      toast("success", `已删除 SSH 服务器：${server.label}`);
    } catch (error) {
      toast("error", getErrorMessage(error));
    } finally {
      setDeletingServerId(null);
    }
  }, [deleteServerTarget, editingServerId, refreshServers, resetDraft, toast]);

  return (
    <>
    <div className="space-y-5">
      <StorageSettingsSection />

      <SettingsFormCard
        icon={Server}
        title="SSH 服务器"
        description="研究助手和项目工作区复用同一套服务器 profile，不再单独维护第二套远程凭据。"
        action={
          <div className="flex flex-col gap-2 sm:flex-row">
            <Button size="sm" variant="secondary" onClick={() => { resetDraft(); setShowServerModal(true); }}>
              <Plus className="mr-1 h-3.5 w-3.5" />
              新增服务器
            </Button>
            <Button size="sm" variant="secondary" onClick={() => void refreshServers()} loading={loading}>
              刷新
            </Button>
          </div>
        }
      >
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-ink-tertiary">
            <Spinner text="" />
            <span>读取中...</span>
          </div>
        ) : remoteServers.length === 0 ? (
          <Empty
            icon={<Server className="h-10 w-10" />}
            title="还没有 SSH 服务器"
          />
        ) : (
          <div className="space-y-3">
            {remoteServers.map((server) => (
              <div key={server.id} className="rounded-[22px] border border-border/70 bg-surface px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-ink">{server.label}</span>
                      <Badge variant={server.phase === "disabled" ? "warning" : "default"}>
                        {server.phase === "disabled" ? "已禁用" : "SSH"}
                      </Badge>
                    </div>
                    <div className="mt-1 text-xs text-ink-secondary">
                      {server.base_url || server.host || "本地"}
                    </div>
                    {server.message ? (
                      <div className="mt-2 text-[11px] text-ink-tertiary">{server.message}</div>
                    ) : null}
                  </div>
                  <div className="w-full text-left text-xs text-ink-tertiary sm:w-auto sm:text-right">
                    <div>{server.username ? `用户：${server.username}` : "无需认证"}</div>
                    <div>{server.workspace_root || "未设置默认工作区目录"}</div>
                    <div className="mt-2 flex flex-wrap gap-2 sm:justify-end">
                      <Button size="sm" variant="secondary" onClick={() => handleEditServer(server)}>
                        编辑
                      </Button>
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => setDeleteServerTarget(server)}
                        loading={deletingServerId === server.id}
                      >
                        删除
                      </Button>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </SettingsFormCard>

    </div>
    <ConfirmDialog
      open={!!deleteServerTarget}
      title="删除 SSH 服务器"
      description={deleteServerTarget ? `确定删除 SSH 服务器“${deleteServerTarget.label}”吗？` : undefined}
      variant="danger"
      confirmLabel="删除"
      onConfirm={() => void handleDeleteServer()}
      onCancel={() => {
        if (deletingServerId) return;
        setDeleteServerTarget(null);
      }}
    />
    <Modal open={showServerModal} onClose={closeServerModal} title={editingServerId ? "编辑 SSH 服务器" : "新增 SSH 服务器"} maxWidth="lg">
      <div className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          <Input
            label="服务器名称"
            value={draft.label || ""}
            onChange={(event) => setDraft((current) => ({ ...current, label: event.target.value }))}
            placeholder="输入名称"
          />
          <Input
            label="SSH 主机"
            value={draft.host || ""}
            onChange={(event) => setDraft((current) => ({ ...current, host: event.target.value }))}
            placeholder="输入主机"
          />
          <Input
            label="端口"
            type="number"
            min={1}
            max={65535}
            value={String(draft.port || 22)}
            onChange={(event) => setDraft((current) => ({
              ...current,
              port: Math.min(65535, Math.max(1, Number(event.target.value) || 22)),
            }))}
          />
          <Input
            label="SSH 用户名"
            value={draft.username || ""}
            onChange={(event) => setDraft((current) => ({ ...current, username: event.target.value }))}
            placeholder="输入用户名"
          />
          <div className="md:col-span-2">
            <Input
              label={editingServerId ? "SSH 密码（留空则保留原值）" : "SSH 密码"}
              type="password"
              value={draft.password || ""}
              onChange={(event) => setDraft((current) => ({ ...current, password: event.target.value }))}
              placeholder={editingServerId ? "留空保持原值" : "输入密码"}
            />
          </div>
          <div className="md:col-span-2">
            <label className="mb-1.5 block text-sm font-medium text-ink">
              {editingServerId ? "SSH 私钥（留空则保留原值）" : "SSH 私钥"}
            </label>
            <textarea
              value={draft.private_key || ""}
              onChange={(event) => setDraft((current) => ({ ...current, private_key: event.target.value }))}
              placeholder={editingServerId ? "留空保持原值" : "输入私钥"}
              className="theme-input min-h-[120px] w-full rounded-lg border border-border bg-surface px-3.5 py-2.5 text-sm text-ink outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/20"
            />
          </div>
          <Input
            label="私钥口令"
            type="password"
            value={draft.passphrase || ""}
            onChange={(event) => setDraft((current) => ({ ...current, passphrase: event.target.value }))}
            placeholder="输入口令"
          />
          <Input
            label="默认工作区目录"
            value={draft.workspace_root || ""}
            onChange={(event) => setDraft((current) => ({ ...current, workspace_root: event.target.value }))}
            placeholder="输入目录"
          />
          <label className="inline-flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={draft.enabled ?? true}
              onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))}
            />
            启用此 SSH 服务器
          </label>
        </div>

        {probeResult ? (
          <div className={cn(
            "rounded-xl border px-3 py-2 text-xs leading-5",
            probeSuccess
              ? "border-success/20 bg-success/10 text-success"
              : "border-error/20 bg-error/10 text-error",
          )}>
            {probeResult}
          </div>
        ) : null}

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
          <Button size="sm" variant="secondary" onClick={closeServerModal} disabled={saving || testing}>
            取消
          </Button>
          <Button size="sm" variant="secondary" onClick={() => void handleProbeServer()} loading={testing}>
            测试连接
          </Button>
          <Button size="sm" onClick={() => void handleSaveServer()} loading={saving}>
            {editingServerId ? "保存服务器" : "新增服务器"}
          </Button>
        </div>
      </div>
    </Modal>
    </>
  );
}

function AcpSettingsSection() {
  const { toast } = useToast();
  const [runtime, setRuntime] = useState<AcpRuntimeStatus | null>(null);
  const [servers, setServers] = useState<AcpServerInfo[]>([]);
  const [config, setConfig] = useState<AcpRegistryConfig | null>(null);
  const [workspaceServers, setWorkspaceServers] = useState<AssistantWorkspaceServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testingName, setTestingName] = useState<string | null>(null);
  const [deleteServerName, setDeleteServerName] = useState<string | null>(null);
  const [editingName, setEditingName] = useState<string | null>(null);
  const [showEditorModal, setShowEditorModal] = useState(false);
  const [name, setName] = useState("");
  const [label, setLabel] = useState("");
  const [transport, setTransport] = useState<"stdio" | "http">("stdio");
  const [command, setCommand] = useState("");
  const [argsText, setArgsText] = useState("");
  const [cwd, setCwd] = useState("");
  const [envText, setEnvText] = useState("");
  const [url, setUrl] = useState("");
  const [headersText, setHeadersText] = useState("");
  const [workspaceServerId, setWorkspaceServerId] = useState("");
  const [timeoutSec, setTimeoutSec] = useState("60");
  const [enabled, setEnabled] = useState(true);

  const defaultServerName = config?.default_server || runtime?.default_server || "";
  const defaultServer = servers.find((server) => server.name === defaultServerName) || null;

  const resetForm = useCallback(() => {
    setEditingName(null);
    setName("");
    setLabel("");
    setTransport("stdio");
    setCommand("");
    setArgsText("");
    setCwd("");
    setEnvText("");
    setUrl("");
    setHeadersText("");
    setWorkspaceServerId("");
    setTimeoutSec("60");
    setEnabled(true);
  }, []);

  const refreshState = useCallback(async () => {
    setLoading(true);
    try {
      const [runtimeRes, serverRes, configRes, workspaceRes] = await Promise.all([
        acpApi.runtime(),
        acpApi.servers(),
        acpApi.config(),
        assistantWorkspaceApi.servers().catch(() => ({ items: [] as AssistantWorkspaceServer[] })),
      ]);
      setRuntime(runtimeRes);
      setServers(serverRes.items || []);
      setConfig(configRes);
      setWorkspaceServers(workspaceRes.items || []);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "读取 ACP 状态失败");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void refreshState();
  }, [refreshState]);

  const closeEditorModal = useCallback(() => {
    if (saving) return;
    setShowEditorModal(false);
    resetForm();
  }, [resetForm, saving]);

  const handleEdit = useCallback((server: AcpServerInfo) => {
    setEditingName(server.name);
    setName(server.name);
    setLabel(server.label || server.name);
    setTransport(server.transport);
    setCommand(server.command || "");
    setArgsText((server.args || []).join("\n"));
    setCwd(server.cwd || "");
    setEnvText(Object.entries(server.env || {}).map(([k, v]) => `${k}=${v}`).join("\n"));
    setUrl(server.url || "");
    setHeadersText(Object.entries(server.headers || {}).map(([k, v]) => `${k}=${v}`).join("\n"));
    setWorkspaceServerId(server.workspace_server_id || "");
    setTimeoutSec(String(server.timeout_sec || 60));
    setEnabled(server.enabled);
    setShowEditorModal(true);
  }, []);

  const handleSave = useCallback(async () => {
    const nextName = name.trim();
    const parsedTimeout = Number.parseInt(timeoutSec.trim(), 10);
    if (!nextName) {
      toast("warning", "请先填写 ACP 名称");
      return;
    }
    if (transport === "stdio" && !command.trim()) {
      toast("warning", "STDIO ACP 需要填写启动命令");
      return;
    }
    if (transport === "http" && !url.trim()) {
      toast("warning", "HTTP ACP 需要填写服务 URL");
      return;
    }

    const nextServers = { ...(config?.servers || {}) };
    nextServers[nextName] = {
      name: nextName,
      label: label.trim() || nextName,
      transport,
      command: transport === "stdio" ? command.trim() : undefined,
      args: transport === "stdio" ? parseMultilineList(argsText) : [],
      cwd: transport === "stdio" ? cwd.trim() || undefined : undefined,
      env: transport === "stdio" ? parseMultilineMap(envText) : {},
      url: transport === "http" ? url.trim() : undefined,
      headers: transport === "http" ? parseMultilineMap(headersText) : {},
      enabled,
      workspace_server_id: workspaceServerId || undefined,
      timeout_sec: Number.isFinite(parsedTimeout) && parsedTimeout > 0 ? parsedTimeout : 60,
    };
    if (editingName && editingName !== nextName) {
      delete nextServers[editingName];
    }

    const nextDefaultServer =
      config?.default_server === editingName || (!config?.default_server && Object.keys(nextServers).length === 1)
        ? nextName
        : config?.default_server || nextName;

    setSaving(true);
    try {
      const nextConfig = await acpApi.updateConfig({
        version: config?.version || 1,
        default_server: nextDefaultServer,
        servers: nextServers,
      });
      setConfig(nextConfig);
      setShowEditorModal(false);
      resetForm();
      toast("success", `ACP 配置已保存：${nextName}`);
      await refreshState();
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "保存 ACP 配置失败");
    } finally {
      setSaving(false);
    }
  }, [
    argsText,
    command,
    config,
    cwd,
    editingName,
    enabled,
    envText,
    headersText,
    label,
    name,
    refreshState,
    resetForm,
    timeoutSec,
    toast,
    transport,
    url,
    workspaceServerId,
  ]);

  const handleSetDefault = useCallback(async (serverName: string) => {
    setSaving(true);
    try {
      const nextConfig = await acpApi.updateConfig({
        version: config?.version || 1,
        default_server: serverName,
        servers: config?.servers || {},
      });
      setConfig(nextConfig);
      toast("success", `默认 ACP 已设为：${serverName}`);
      await refreshState();
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "设置默认 ACP 失败");
    } finally {
      setSaving(false);
    }
  }, [config, refreshState, toast]);

  const handleDelete = useCallback(async () => {
    const serverName = deleteServerName;
    if (!serverName) return;
    setSaving(true);
    try {
      const nextServers = { ...(config?.servers || {}) };
      delete nextServers[serverName];
      const nextConfig = await acpApi.updateConfig({
        version: config?.version || 1,
        default_server: config?.default_server === serverName ? null : config?.default_server || null,
        servers: nextServers,
      });
      setConfig(nextConfig);
      setDeleteServerName(null);
      toast("success", `已移除 ACP：${serverName}`);
      await refreshState();
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "移除 ACP 配置失败");
    } finally {
      setSaving(false);
    }
  }, [config, deleteServerName, refreshState, toast]);

  const handleConnect = useCallback(async (serverName: string) => {
    setSaving(true);
    try {
      await acpApi.connect(serverName);
      toast("success", `ACP 已连接：${serverName}`);
      await refreshState();
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "连接 ACP 失败");
    } finally {
      setSaving(false);
    }
  }, [refreshState, toast]);

  const handleDisconnect = useCallback(async (serverName: string) => {
    setSaving(true);
    try {
      await acpApi.disconnect(serverName);
      toast("success", `ACP 已断开：${serverName}`);
      await refreshState();
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "断开 ACP 失败");
    } finally {
      setSaving(false);
    }
  }, [refreshState, toast]);

  const handleTest = useCallback(async (server: AcpServerInfo) => {
    setTestingName(server.name);
    try {
      const result = await acpApi.test(server.name, {
        prompt: "请回复 ACP_OK，并用一句话说明该 ACP 服务已可用。",
        workspace_server_id: server.workspace_server_id || undefined,
        timeout_sec: server.timeout_sec || 60,
      });
      const content = String(result.item?.content || result.item?.message || "测试完成").trim();
      toast("success", content ? `ACP 测试通过：${content.slice(0, 80)}` : "ACP 测试通过");
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "测试 ACP 失败");
    } finally {
      setTestingName(null);
    }
  }, [toast]);

  return (
    <>
      <div className="space-y-5">
        <SettingsFormCard
          icon={Bot}
          title="ACP 智能体桥接"
          description="ACP 用来把外部智能体接入 ResearchOS 的 Custom ACP 后端，让它复用项目上下文、工作区和权限确认机制。"
          action={(
            <div className="flex flex-col gap-2 sm:flex-row">
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  resetForm();
                  setShowEditorModal(true);
                }}
              >
                <Plus className="mr-1 h-3.5 w-3.5" />
                新增 ACP
              </Button>
              <Button size="sm" variant="secondary" onClick={() => void refreshState()} loading={loading}>
                刷新
              </Button>
            </div>
          )}
        >
          <div className="grid gap-3 md:grid-cols-3">
            <SummaryMetric
              label="默认服务"
              value={defaultServer?.label || defaultServerName || "未绑定"}
              hint={defaultServer?.connected ? "Custom ACP 已可直接使用" : "未连接时首页会显示未绑定或待连接"}
            />
            <SummaryMetric
              label="已连接"
              value={String(runtime?.connected_count ?? servers.filter((server) => server.connected).length)}
              hint="连接后可在助手后端选择 Custom ACP"
            />
            <SummaryMetric
              label="服务数"
              value={String(runtime?.server_count ?? servers.length)}
              hint={runtime?.message || "ACP 配置视图"}
            />
          </div>
          <div className="rounded-2xl border border-border/70 bg-page px-4 py-3 text-xs leading-6 text-ink-secondary">
            <div className="font-semibold text-ink">配置步骤</div>
            <div className="mt-1">
              新增 ACP 服务，选择 STDIO 命令或 HTTP URL；如服务只允许在某台 SSH 机器执行，绑定对应工作区服务器；保存后设为默认并点击连接/测试。首页“未绑定 ACP”只表示 Custom ACP 没有默认可用服务，不影响普通模型和项目工作流。
            </div>
          </div>
        </SettingsFormCard>

        <SettingsFormCard icon={Server} title="ACP 服务列表" description="这些服务会作为 Custom ACP 后端候选；默认服务决定首页 ACP 状态和新会话默认绑定。">
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-ink-tertiary">
              <Spinner text="" />
              <span>读取中...</span>
            </div>
          ) : servers.length === 0 ? (
            <Empty title="暂无 ACP 服务" description="新增一个 STDIO 或 HTTP ACP 服务后，即可绑定 Custom ACP。" icon={<Bot className="h-10 w-10" />} />
          ) : (
            <div className="space-y-3">
              {servers.map((server) => (
                <div key={server.name} className="rounded-[22px] border border-border/70 bg-surface px-4 py-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold text-ink">{server.label || server.name}</span>
                        <Badge variant={acpStatusBadgeVariant(server)}>{acpStatusLabel(server)}</Badge>
                        <Badge>{server.transport.toUpperCase()}</Badge>
                        {server.name === defaultServerName ? <Badge variant="info">默认</Badge> : null}
                      </div>
                      <div className="mt-1 break-all text-xs leading-5 text-ink-secondary">
                        {server.transport === "stdio"
                          ? `${server.command || "未配置命令"} ${(server.args || []).join(" ")}`
                          : server.url || "未配置 URL"}
                      </div>
                      <div className="mt-2 grid gap-2 text-[11px] text-ink-tertiary md:grid-cols-2">
                        <span>工作区绑定：{server.workspace_server_id || "跟随会话"}</span>
                        <span>最近连接：{formatUnixDate(server.last_connected_at)}</span>
                      </div>
                      {server.last_error ? (
                        <div className="mt-2 text-xs text-error">{server.last_error}</div>
                      ) : null}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {server.name !== defaultServerName ? (
                        <Button size="sm" variant="secondary" onClick={() => void handleSetDefault(server.name)} disabled={saving}>
                          设为默认
                        </Button>
                      ) : null}
                      <Button size="sm" variant="secondary" onClick={() => handleEdit(server)} disabled={saving}>
                        编辑
                      </Button>
                      {server.connected ? (
                        <Button size="sm" variant="secondary" onClick={() => void handleDisconnect(server.name)} disabled={saving}>
                          断开
                        </Button>
                      ) : (
                        <Button size="sm" variant="secondary" onClick={() => void handleConnect(server.name)} disabled={saving || !server.enabled}>
                          连接
                        </Button>
                      )}
                      <Button size="sm" variant="secondary" onClick={() => void handleTest(server)} loading={testingName === server.name} disabled={saving || !server.enabled}>
                        测试
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => setDeleteServerName(server.name)} disabled={saving}>
                        删除
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </SettingsFormCard>
      </div>

      <ConfirmDialog
        open={!!deleteServerName}
        title="删除 ACP 配置"
        description={deleteServerName ? `确定删除 ACP 配置“${deleteServerName}”吗？` : undefined}
        variant="danger"
        confirmLabel="删除"
        onConfirm={() => void handleDelete()}
        onCancel={() => {
          if (saving) return;
          setDeleteServerName(null);
        }}
      />

      <Modal
        open={showEditorModal}
        onClose={closeEditorModal}
        title={editingName ? `编辑 ACP：${editingName}` : "新增 ACP"}
        maxWidth="lg"
      >
        <div className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-ink">名称</span>
              <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="如：my-acp-agent" disabled={!!editingName} />
            </label>
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-ink">显示名称</span>
              <Input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="如：我的 ACP 智能体" />
            </label>
          </div>

          <div className="flex flex-wrap gap-2">
            {(["stdio", "http"] as const).map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setTransport(item)}
                className={cn(
                  "rounded-2xl border px-4 py-2 text-sm font-medium transition",
                  transport === item
                    ? "border-primary/30 bg-primary/10 text-primary"
                    : "border-border/70 bg-surface text-ink-secondary hover:border-primary/20 hover:text-primary",
                )}
              >
                {item.toUpperCase()}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setEnabled((current) => !current)}
              className={cn(
                "rounded-2xl border px-4 py-2 text-sm font-medium transition",
                enabled
                  ? "border-success/30 bg-success/10 text-success"
                  : "border-border/70 bg-surface text-ink-secondary",
              )}
            >
              {enabled ? "已启用" : "已禁用"}
            </button>
          </div>

          {transport === "stdio" ? (
            <div className="grid gap-4 md:grid-cols-2">
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-ink">命令</span>
                <Input value={command} onChange={(event) => setCommand(event.target.value)} placeholder="python" />
              </label>
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-ink">工作目录</span>
                <Input value={cwd} onChange={(event) => setCwd(event.target.value)} placeholder="可选，留空使用后端当前目录" />
              </label>
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-ink">参数</span>
                <textarea value={argsText} onChange={(event) => setArgsText(event.target.value)} placeholder="每行一个参数，例如 scripts/my_acp_server.py" className="theme-input h-24 w-full rounded-2xl border border-border/70 bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-primary/25" />
              </label>
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-ink">环境变量</span>
                <textarea value={envText} onChange={(event) => setEnvText(event.target.value)} placeholder="KEY=value" className="theme-input h-24 w-full rounded-2xl border border-border/70 bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-primary/25" />
              </label>
            </div>
          ) : (
            <div className="grid gap-4 md:grid-cols-2">
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-ink">服务 URL</span>
                <Input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com/acp" />
              </label>
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-ink">请求头</span>
                <textarea value={headersText} onChange={(event) => setHeadersText(event.target.value)} placeholder="Authorization=Bearer xxx" className="theme-input h-24 w-full rounded-2xl border border-border/70 bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-primary/25" />
              </label>
            </div>
          )}

          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-ink">绑定工作区服务器</span>
              <select
                value={workspaceServerId}
                onChange={(event) => setWorkspaceServerId(event.target.value)}
                className="h-10 w-full rounded-lg border border-border bg-surface px-3 text-sm text-ink outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
              >
                <option value="">跟随会话 / 不限制</option>
                {workspaceServers.map((server) => (
                  <option key={server.id} value={server.id}>
                    {server.label || server.id}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-ink">超时秒数</span>
              <Input value={timeoutSec} onChange={(event) => setTimeoutSec(event.target.value)} placeholder="60" />
            </label>
          </div>

          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={closeEditorModal} disabled={saving}>
              取消
            </Button>
            <Button onClick={() => void handleSave()} loading={saving}>
              保存配置
            </Button>
          </div>
        </div>
      </Modal>
    </>
  );
}

function McpSettingsSection() {
  const { toast } = useToast();
  const [runtime, setRuntime] = useState<McpRuntimeStatus | null>(null);
  const [servers, setServers] = useState<McpServerInfo[]>([]);
  const [config, setConfig] = useState<McpRegistryConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editingName, setEditingName] = useState<string | null>(null);
  const [showEditorModal, setShowEditorModal] = useState(false);
  const [name, setName] = useState("");
  const [label, setLabel] = useState("");
  const [transport, setTransport] = useState<"stdio" | "http">("stdio");
  const [command, setCommand] = useState("");
  const [argsText, setArgsText] = useState("");
  const [envText, setEnvText] = useState("");
  const [url, setUrl] = useState("");
  const [headersText, setHeadersText] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [deleteServerName, setDeleteServerName] = useState<string | null>(null);

  const resetForm = useCallback(() => {
    setEditingName(null);
    setName("");
    setLabel("");
    setTransport("stdio");
    setCommand("");
    setArgsText("");
    setEnvText("");
    setUrl("");
    setHeadersText("");
    setEnabled(true);
  }, []);

  const refreshState = useCallback(async () => {
    setLoading(true);
    try {
      const [runtimeRes, serverRes, configRes] = await Promise.all([
        mcpApi.runtime(),
        mcpApi.servers(),
        mcpApi.config(),
      ]);
      setRuntime(runtimeRes);
      setServers(serverRes.items || []);
      setConfig(configRes);
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "读取 MCP 状态失败");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void refreshState();
  }, [refreshState]);

  const builtinServer = useMemo(
    () => servers.find((server) => server.builtin || server.name === "researchos") || null,
    [servers],
  );
  const customServers = useMemo(
    () => servers.filter((server) => !server.builtin),
    [servers],
  );
  const builtinToolCount = Number(
    builtinServer?.tool_count
    ?? builtinServer?.tools?.length
    ?? runtime?.builtin_tool_count
    ?? 0,
  ) || 0;
  const builtinAvailable = Boolean(builtinServer?.connected || builtinToolCount > 0 || runtime?.builtin_ready);

  const closeEditorModal = useCallback(() => {
    if (saving) return;
    setShowEditorModal(false);
    resetForm();
  }, [resetForm, saving]);

  const handleEdit = useCallback((server: McpServerInfo) => {
    if (server.builtin) return;
    setEditingName(server.name);
    setName(server.name);
    setLabel(server.label || server.name);
    setTransport(server.transport);
    setCommand(server.command || "");
    setArgsText((server.args || []).join("\n"));
    setEnvText(Object.entries(server.env || {}).map(([k, v]) => `${k}=${v}`).join("\n"));
    setUrl(server.url || "");
    setHeadersText(Object.entries(server.headers || {}).map(([k, v]) => `${k}=${v}`).join("\n"));
    setEnabled(server.enabled);
    setShowEditorModal(true);
  }, []);

  const handleSave = useCallback(async () => {
    const nextName = name.trim();
    if (!nextName) {
      toast("warning", "请先填写 MCP 名称");
      return;
    }

    const nextServers = { ...(config?.servers || {}) };
    nextServers[nextName] = {
      name: nextName,
      label: label.trim() || nextName,
      transport,
      command: transport === "stdio" ? command.trim() : undefined,
      args: transport === "stdio" ? parseMultilineList(argsText) : [],
      cwd: undefined,
      env: transport === "stdio" ? parseMultilineMap(envText) : {},
      url: transport === "http" ? url.trim() : undefined,
      headers: transport === "http" ? parseMultilineMap(headersText) : {},
      enabled,
      builtin: false,
      timeout_sec: 30,
    };
    if (editingName && editingName !== nextName) {
      delete nextServers[editingName];
    }

    setSaving(true);
    try {
      const nextConfig = await mcpApi.updateConfig({
        version: config?.version || 1,
        servers: nextServers,
      });
      setConfig(nextConfig);
      setShowEditorModal(false);
      resetForm();
      toast("success", `MCP 配置已保存：${nextName}`);
      await refreshState();
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "保存 MCP 配置失败");
    } finally {
      setSaving(false);
    }
  }, [argsText, command, config, editingName, enabled, envText, headersText, label, name, refreshState, resetForm, toast, transport, url]);

  const handleDelete = useCallback(async () => {
    const serverName = deleteServerName;
    if (serverName === "researchos") return;
    if (!serverName) return;
    setSaving(true);
    try {
      const nextServers = { ...(config?.servers || {}) };
      delete nextServers[serverName];
      const nextConfig = await mcpApi.updateConfig({
        version: config?.version || 1,
        servers: nextServers,
      });
      setConfig(nextConfig);
      if (editingName === serverName) resetForm();
      setDeleteServerName(null);
      toast("success", `已移除 MCP：${serverName}`);
      await refreshState();
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "移除 MCP 配置失败");
    } finally {
      setSaving(false);
    }
  }, [config, deleteServerName, editingName, refreshState, resetForm, toast]);

  return (
    <>
    <div className="space-y-5">
      <SettingsFormCard
        icon={Server}
        title="MCP 工具集成"
        description="ResearchOS 内置工具会在对话时自动提供给当前助手；这里额外管理扩展 MCP 配置。"
        action={
          <div className="flex flex-col gap-2 sm:flex-row">
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                resetForm();
                setShowEditorModal(true);
              }}
            >
              <Plus className="mr-1 h-3.5 w-3.5" />
              新增 MCP
            </Button>
            <Button size="sm" variant="secondary" onClick={() => void refreshState()} loading={loading}>
              刷新
            </Button>
          </div>
        }
      >
        <div className="grid gap-3 md:grid-cols-3">
          <SummaryMetric label="内置工具" value={String(builtinToolCount)} hint={builtinAvailable ? "会在对话时自动提供给当前助手" : "当前内置 MCP 不可用"} />
          <SummaryMetric label="自定义配置" value={String(customServers.length)} hint="这些配置会在后续会话中作为扩展 MCP 注入" />
          <SummaryMetric label="总服务数" value={String(runtime?.server_count || servers.length)} hint={runtime?.message || "MCP 配置视图"} />
        </div>
      </SettingsFormCard>

      <SettingsFormCard icon={Server} title="服务列表" description="内置 ResearchOS 用于研究助手主能力；自定义 MCP 只保存配置，不再由后端单独建立连接。">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-ink-tertiary">
            <Spinner text="" />
            <span>读取中...</span>
          </div>
        ) : (
          <div className="space-y-3">
            {servers.map((server) => {
              const serverToolCount = Number(server.tool_count ?? server.tools?.length ?? 0) || 0;
              const serverBuiltinAvailable = server.builtin
                ? Boolean(server.connected || serverToolCount > 0 || runtime?.builtin_ready || builtinToolCount > 0)
                : Boolean(server.connected || serverToolCount > 0);
              return (
              <div key={server.name} className="rounded-[22px] border border-border/70 bg-surface px-4 py-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-ink">{server.label}</span>
                      <Badge variant={server.builtin ? (serverBuiltinAvailable ? "success" : "warning") : server.enabled ? "info" : "warning"}>
                        {server.builtin ? (serverBuiltinAvailable ? "内置可用" : "内置异常") : server.enabled ? "已配置" : "已禁用"}
                      </Badge>
                      <Badge>{server.transport.toUpperCase()}</Badge>
                      {server.builtin ? <Badge variant="warning">内置</Badge> : null}
                    </div>
                    <div className="mt-1 text-xs leading-5 text-ink-secondary">
                      {server.builtin
                        ? (serverBuiltinAvailable
                          ? `ResearchOS 工具由当前助手直接使用，当前共 ${Math.max(serverToolCount, builtinToolCount)} 个工具`
                          : "内置 MCP 当前不可用")
                        : server.transport === "stdio"
                        ? `${server.command || "未配置命令"} ${(server.args || []).join(" ")}`
                        : server.url || "未配置 URL"}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {(server.tools || []).slice(0, 6).map((tool) => (
                        <span key={tool} className="rounded-full border border-border/70 bg-page/55 px-2 py-0.5 text-[10px] text-ink-secondary">
                          {tool}
                        </span>
                      ))}
                    </div>
                    {server.last_error && !serverBuiltinAvailable ? (
                      <div className="mt-2 text-xs text-error">{server.last_error}</div>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {!server.builtin ? (
                      <Button size="sm" variant="secondary" onClick={() => handleEdit(server)}>
                        编辑
                      </Button>
                    ) : null}
                    {!server.builtin ? (
                      <Button size="sm" variant="secondary" onClick={() => setDeleteServerName(server.name)} disabled={saving}>
                        删除
                      </Button>
                    ) : null}
                  </div>
                </div>
                <div className="mt-3 grid gap-2 text-[11px] text-ink-tertiary md:grid-cols-2">
                  <span>{server.builtin ? "作用方式：会话自动提供" : "作用方式：作为扩展 MCP 注入会话"}</span>
                  <span>{server.last_error && !serverBuiltinAvailable ? `状态说明：${server.last_error}` : server.builtin ? "无需手动连接" : "保存后在下一次会话启动时生效"}</span>
                </div>
              </div>
              );
            })}
          </div>
        )}
      </SettingsFormCard>

    </div>
    <ConfirmDialog
      open={!!deleteServerName}
      title="删除 MCP 配置"
      description={deleteServerName ? `确定删除 MCP 配置“${deleteServerName}”吗？` : undefined}
      variant="danger"
      confirmLabel="删除"
      onConfirm={() => void handleDelete()}
      onCancel={() => {
        if (saving) return;
        setDeleteServerName(null);
      }}
    />
    <Modal
      open={showEditorModal}
      onClose={closeEditorModal}
      title={editingName ? `编辑 MCP：${editingName}` : "新增 MCP"}
      maxWidth="lg"
    >
      <div className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-ink">名称</span>
            <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="输入名称" disabled={!!editingName} />
          </label>
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-ink">显示名称</span>
            <Input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="输入标签" />
          </label>
        </div>

        <div className="flex flex-wrap gap-2">
          {(["stdio", "http"] as const).map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setTransport(item)}
              className={cn(
                "rounded-2xl border px-4 py-2 text-sm font-medium transition",
                transport === item
                  ? "border-primary/30 bg-primary/10 text-primary"
                  : "border-border/70 bg-surface text-ink-secondary hover:border-primary/20 hover:text-primary",
              )}
            >
              {item.toUpperCase()}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setEnabled((current) => !current)}
            className={cn(
              "rounded-2xl border px-4 py-2 text-sm font-medium transition",
              enabled
                ? "border-success/30 bg-success/10 text-success"
                : "border-border/70 bg-surface text-ink-secondary",
            )}
          >
            {enabled ? "已启用" : "已禁用"}
          </button>
        </div>

        {transport === "stdio" ? (
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-ink">命令</span>
              <Input value={command} onChange={(event) => setCommand(event.target.value)} placeholder="python" />
            </label>
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-ink">参数</span>
              <textarea value={argsText} onChange={(event) => setArgsText(event.target.value)} placeholder="每行一个参数" className="theme-input h-24 w-full rounded-2xl border border-border/70 bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-primary/25" />
            </label>
            <label className="block md:col-span-2">
              <span className="mb-2 block text-sm font-medium text-ink">环境变量</span>
              <textarea value={envText} onChange={(event) => setEnvText(event.target.value)} placeholder="KEY=value" className="theme-input h-24 w-full rounded-2xl border border-border/70 bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-primary/25" />
            </label>
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-ink">服务 URL</span>
              <Input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com/mcp" />
            </label>
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-ink">请求头</span>
              <textarea value={headersText} onChange={(event) => setHeadersText(event.target.value)} placeholder="Authorization=Bearer xxx" className="theme-input h-24 w-full rounded-2xl border border-border/70 bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-primary/25" />
            </label>
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={closeEditorModal} disabled={saving}>
            取消
          </Button>
          <Button onClick={() => void handleSave()} loading={saving}>
            保存配置
          </Button>
        </div>
      </div>
    </Modal>
    </>
  );
}

/* ======== LLM 配置 Tab ======== */

type ProviderPresetTemplate = {
  label: string;
  base_url: string;
  description?: string;
  models: Partial<LLMProviderCreate>;
};

type ProviderFormPresetState = {
  provider?: string;
  api_base_url?: string;
  model_skim?: string;
  model_deep?: string;
  model_vision?: string;
  model_embedding?: string;
  model_fallback?: string;
};

type ProviderPresetOption = ProviderPresetTemplate & {
  id: string;
  suggestions: string[];
};

type LLMTabSnapshot = {
  configs: LLMProviderConfig[];
  activeInfo: ActiveLLMConfig | null;
  providerPresets: LLMProviderPreset[];
};

let llmTabSnapshot: LLMTabSnapshot | null = null;

const PROVIDER_PRESETS: Record<string, ProviderPresetTemplate> = {
  openai: {
    label: "OpenAI-compatible",
    base_url: "https://api.openai.com/v1",
    description: "统一用于 OpenAI 官方以及常见 OpenAI-compatible 网关服务。",
    models: {
      model_skim: "gpt-4o-mini",
      model_deep: "gpt-5.4",
      model_vision: "gpt-4o",
      model_embedding: "text-embedding-3-small",
      model_fallback: "gpt-4o-mini",
    },
  },
  gemini: {
    label: "Gemini",
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai/",
    description: "用于 Gemini 官方接口。聊天走 OpenAI-compatible，绘图可单独配置 Nano Banana 通道。",
    models: {
      model_skim: "gemini-2.5-flash",
      model_deep: "gemini-2.5-pro",
      model_vision: "gemini-2.5-flash",
      model_embedding: "text-embedding-3-small",
      model_fallback: "gemini-2.5-flash",
    },
  },
  zhipu: {
    label: "智谱 GLM",
    base_url: "https://open.bigmodel.cn/api/paas/v4/",
    description: "用于智谱 BigModel / GLM 系列接口，底层走 OpenAI-compatible 协议。",
    models: {
      model_skim: "glm-4.7",
      model_deep: "glm-4.7",
      model_vision: "glm-4.6v",
      model_embedding: "embedding-3",
      model_fallback: "glm-4.7",
    },
  },
  anthropic: {
    label: "Anthropic-compatible",
    base_url: "",
    description: "统一用于 Anthropic / Claude 风格消息接口。",
    models: {
      model_skim: "claude-haiku-4-5-20251001",
      model_deep: "claude-sonnet-4-6",
      model_embedding: "text-embedding-3-small",
      model_fallback: "claude-haiku-4-5-20251001",
    },
  },
};

const EMBEDDING_PROVIDER_OPTIONS: { value: "" | "openai" | "zhipu" | "anthropic"; label: string }[] = [
  { value: "", label: "跟随主提供方" },
  { value: "openai", label: "OpenAI-compatible" },
  { value: "zhipu", label: "智谱 GLM" },
  { value: "anthropic", label: "Anthropic-compatible" },
];

const IMAGE_PROVIDER_OPTIONS: { value: "" | "gemini"; label: string }[] = [
  { value: "", label: "未启用" },
  { value: "gemini", label: "Gemini Nano Banana" },
];

function buildProviderPresetOptions(
  providerPresets?: LLMProviderPreset[],
): ProviderPresetOption[] {
  const options = new Map<string, ProviderPresetOption>();

  Object.entries(PROVIDER_PRESETS).forEach(([id, preset]) => {
    options.set(id, {
      id,
      label: preset.label,
      base_url: preset.base_url,
      description: preset.description || "",
      models: { ...preset.models },
      suggestions: [],
    });
  });

  (providerPresets || []).forEach((preset) => {
    const id = preset.id || preset.provider;
    const fallback = options.get(id) || {
      id,
      label: preset.label,
      base_url: preset.base_url || "",
      description: preset.description || "",
      models: {},
      suggestions: [],
    };

    options.set(id, {
      ...fallback,
      label: preset.label || fallback.label,
      base_url: preset.base_url ?? fallback.base_url,
      description: preset.description || fallback.description,
      suggestions: preset.models || fallback.suggestions,
    });
  });

  return Array.from(options.values());
}

function applyProviderPreset<T extends ProviderFormPresetState>(
  prev: T,
  provider: string,
  presetOptions: ProviderPresetOption[],
): T {
  const preset =
    presetOptions.find((item) => item.id === provider) ||
    buildProviderPresetOptions().find((item) => item.id === provider);

  if (!preset) {
    return {
      ...prev,
      provider,
    };
  }

  const next = {
    ...prev,
    provider,
    api_base_url: preset.base_url,
    model_embedding: preset.models.model_embedding || prev.model_embedding || "",
    model_skim: "",
    model_deep: "",
    model_vision: "",
    model_fallback: "",
  };

  const primaryModel = getPrimaryModelValue({
    model_deep: preset.models.model_deep,
    model_skim: preset.models.model_skim,
    model_fallback: preset.models.model_fallback,
    model_vision: preset.models.model_vision,
  });

  return syncPrimaryModelFields(next, primaryModel);
}

function getPrimaryModelValue(source: {
  model_deep?: string | null;
  model_skim?: string | null;
  model_fallback?: string | null;
  model_vision?: string | null;
  model_embedding?: string | null;
}): string {
  return String(
    source.model_deep
    || source.model_skim
    || source.model_fallback
    || source.model_vision
    || source.model_embedding
    || "",
  ).trim();
}

function syncPrimaryModelFields<T extends {
  model_skim?: string;
  model_deep?: string;
  model_vision?: string;
  model_embedding?: string;
  model_fallback?: string;
}>(prev: T, value: string): T {
  const nextValue = value.trim();
  const previousPrimary = getPrimaryModelValue(prev);
  return {
    ...prev,
    model_skim: nextValue,
    model_deep: nextValue,
    model_fallback: nextValue,
    model_vision:
      !String(prev.model_vision || "").trim() || String(prev.model_vision || "").trim() === previousPrimary
        ? nextValue
        : prev.model_vision,
    model_embedding:
      !String(prev.model_embedding || "").trim() || String(prev.model_embedding || "").trim() === previousPrimary
        ? nextValue
        : prev.model_embedding,
  };
}

function resolveEffectiveModel(
  source: {
    model_skim?: string | null;
    model_deep?: string | null;
    model_fallback?: string | null;
    model_vision?: string | null;
  },
  key: "model_skim" | "model_deep" | "model_fallback" | "model_vision",
): string {
  return String(source[key] || "").trim() || getPrimaryModelValue(source);
}

function inheritedModelDetail(
  rawValue: string | null | undefined,
  inheritedDetail: string,
  customDetail: string,
): string {
  return String(rawValue || "").trim() ? customDetail : inheritedDetail;
}

function SettingsFormCard({
  icon: Icon,
  title,
  description,
  children,
  tone = "default",
  action,
}: {
  icon?: typeof Server;
  title: string;
  description?: string;
  children: ReactNode;
  tone?: "default" | "accent";
  action?: ReactNode;
}) {
  return (
    <section
      className={cn(
        "rounded-[20px] border bg-surface p-3.5",
        tone === "accent"
          ? "border-primary/20 bg-primary/8"
          : "border-border",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          {Icon ? (
            <div className="rounded-2xl bg-page p-2 text-primary">
              <Icon className="h-4 w-4" />
            </div>
          ) : null}
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-ink">{title}</div>
            {description ? (
              <div className="mt-1 text-xs leading-5 text-ink-tertiary">{description}</div>
            ) : null}
          </div>
        </div>
        {action ? <div className="w-full sm:w-auto sm:shrink-0">{action}</div> : null}
      </div>
      <div className="mt-3 space-y-3">{children}</div>
    </section>
  );
}

function SummaryMetric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-2xl border border-border/70 bg-page px-4 py-3">
      <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-ink-tertiary">
        {label}
      </div>
      <div className="mt-2 text-2xl font-semibold tracking-[-0.04em] text-ink">
        {value}
      </div>
      {hint ? <div className="mt-1 text-[11px] leading-5 text-ink-tertiary">{hint}</div> : null}
    </div>
  );
}

function ProviderSelectionGrid({
  value,
  providerPresets,
  onSelect,
}: {
  value: string;
  providerPresets?: LLMProviderPreset[];
  onSelect: (provider: string) => void;
}) {
  const presetOptions = useMemo(
    () => buildProviderPresetOptions(providerPresets),
    [providerPresets],
  );

  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-2">
        {presetOptions.map((preset) => {
          const selected = preset.id === value;
          return (
            <button
              key={preset.id}
              type="button"
              onClick={() => onSelect(preset.id)}
              data-testid={`provider-card-${preset.id}`}
              className={cn(
                "group rounded-2xl border px-3 py-2.5 text-left transition-all",
                selected
                  ? "border-primary/40 bg-primary/8"
                  : "border-border bg-surface hover:border-primary/25 hover:bg-page",
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <ProviderBadge provider={preset.id} />
                    <span className="text-xs font-semibold text-ink">{preset.label}</span>
                  </div>
                </div>
                <span
                  className={cn(
                    "shrink-0 rounded-full px-2.5 py-1 text-[10px] font-semibold",
                    selected
                      ? "bg-primary text-white"
                      : "bg-page text-ink-tertiary group-hover:text-primary",
                  )}
                >
                  {selected ? "已选中" : "切换"}
                </span>
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                <span className="text-[11px] text-ink-tertiary">{preset.label}</span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function LLMTab() {
  const { toast } = useToast();
  const [configs, setConfigs] = useState<LLMProviderConfig[]>(() => llmTabSnapshot?.configs || []);
  const [activeInfo, setActiveInfo] = useState<ActiveLLMConfig | null>(() => llmTabSnapshot?.activeInfo || null);
  const [providerPresets, setProviderPresets] = useState<LLMProviderPreset[]>(() => llmTabSnapshot?.providerPresets || []);
  const [loading, setLoading] = useState(() => !llmTabSnapshot);
  const [showAdd, setShowAdd] = useState(false);
  const [addPreset, setAddPreset] = useState<string>("openai");
  const [editCfg, setEditCfg] = useState<LLMProviderConfig | null>(null);
  const [deleteCfg, setDeleteCfg] = useState<LLMProviderConfig | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, LLMProviderTestResult>>({});
  const compactPresets = useMemo(
    () => buildProviderPresetOptions(providerPresets),
    [providerPresets],
  );

  const load = useCallback(async (options?: { silent?: boolean }) => {
    if (!options?.silent) {
      setLoading(true);
    }
    try {
      const [listRes, activeRes, presetRes] = await Promise.all([
        llmConfigApi.list(),
        llmConfigApi.active().catch(() => null),
        llmConfigApi.presets().catch(() => ({ items: [] as LLMProviderPreset[] })),
      ]);
      setConfigs(listRes.items);
      if (activeRes) {
        setActiveInfo(activeRes);
      } else {
        setActiveInfo(null);
      }
      setProviderPresets(presetRes.items || []);
      llmTabSnapshot = {
        configs: listRes.items,
        activeInfo: activeRes || null,
        providerPresets: presetRes.items || [],
      };
    } catch (err) {
      toast("error", "加载 LLM 配置失败");
    } finally {
      if (!options?.silent) {
        setLoading(false);
      }
    }
  }, [toast]);

  useEffect(() => {
    void load({ silent: !!llmTabSnapshot });
  }, [load]);

  const handleActivate = async (id: string) => {
    setSubmitting(true);
    try {
      await llmConfigApi.activate(id);
      await load();
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteCfg) return;
    await llmConfigApi.delete(deleteCfg.id);
    setDeleteCfg(null);
    await load();
  };

  const closeAddModal = useCallback(() => {
    setAddPreset("openai");
    setShowAdd(false);
  }, []);

  const closeEditModal = useCallback(() => {
    setEditCfg(null);
  }, []);

  const openAddModal = useCallback((preset = "openai") => {
    setAddPreset(preset);
    setShowAdd(true);
  }, []);

  const openEditModal = useCallback((cfg: LLMProviderConfig) => {
    setEditCfg(cfg);
  }, []);

  const handleTest = async (cfg: LLMProviderConfig) => {
    setTestingId(cfg.id);
    try {
      const result = await llmConfigApi.test(cfg.id);
      setTestResults((prev) => ({ ...prev, [cfg.id]: result }));
      const toastType =
        result.chat.ok && result.embedding.ok
          ? "success"
          : result.chat.ok || result.embedding.ok
            ? "warning"
            : "error";
      toast(
        toastType,
        `${cfg.name} 测试结果：聊天${result.chat.ok ? "正常" : "失败"}；嵌入${result.embedding.ok ? "正常" : "失败"}`,
      );
    } catch (err) {
      toast("error", getErrorMessage(err));
    } finally {
      setTestingId(null);
    }
  };
  return (
    <>
    <div className="space-y-4">
      {activeInfo?.source === "database" && activeInfo.config ? (
        <div className="rounded-2xl border border-border/70 bg-page px-4 py-3.5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-xs">
                <Zap className="h-3.5 w-3.5 text-primary" />
                <span className="font-medium text-ink">当前生效</span>
                <ProviderBadge provider={activeInfo.config.provider || ""} />
                <Badge variant="success">用户配置</Badge>
              </div>
              <div className="text-sm font-semibold text-ink">
                {activeInfo.config.name}
              </div>
              <div className="flex flex-wrap gap-2 text-[11px] text-ink-tertiary">
                <span className="rounded-full bg-surface px-2 py-1 font-mono">{activeInfo.config.api_key_masked}</span>
                <span className="rounded-full bg-surface px-2 py-1">{activeInfo.config.api_base_url || "默认地址"}</span>
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={async () => {
                await llmConfigApi.deactivate();
                await load();
              }}
              disabled={submitting}
            >
              <PowerOff className="mr-1 h-3 w-3" />
              取消激活
            </Button>
          </div>
          {activeInfo.config.compatibility_warnings?.length ? (
            <WarningsPanel
              title="兼容性提醒"
              items={activeInfo.config.compatibility_warnings}
              className="mt-4"
            />
          ) : null}
        </div>
      ) : loading ? (
        <div className="rounded-2xl border border-border/70 bg-page px-4 py-3.5">
          <div className="flex items-center gap-2 text-xs text-ink-tertiary">
            <Spinner className="h-3.5 w-3.5" />
            正在加载当前生效配置...
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-dashed border-border bg-page px-4 py-3">
          <div className="flex items-center gap-2 text-xs font-medium text-ink">
            <Zap className="h-3.5 w-3.5 text-amber-600" />
            当前未激活任何 LLM 配置
          </div>
        </div>
      )}

      <div className="rounded-2xl border border-border bg-page p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="text-sm font-semibold text-ink">新建 LLM 配置</div>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => openAddModal("openai")}
          >
            <Plus className="mr-1.5 h-3.5 w-3.5" />
            新建配置
          </Button>
        </div>
        {compactPresets.length > 0 ? (
          <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {compactPresets.map((preset) => (
              <ProviderPresetLaunchCard
                key={preset.id}
                preset={preset}
                onClick={() => openAddModal(preset.id)}
              />
            ))}
          </div>
        ) : null}
      </div>

      {/* 配置列表 */}
      {loading && configs.length === 0 ? (
        <div className="rounded-2xl border border-border bg-page px-4 py-6">
          <div className="flex items-center gap-2 text-sm text-ink-secondary">
            <Spinner className="h-4 w-4" />
            正在加载模型配置...
          </div>
        </div>
      ) : configs.length === 0 ? (
        <div className="py-6 text-center text-sm text-ink-tertiary">
          暂无模型配置
        </div>
      ) : (
        <div className="space-y-2">
          {configs.map((cfg) => (
            <div
              key={cfg.id}
              data-testid={`llm-config-card-${cfg.id}`}
              className={cn(
                "rounded-2xl border px-4 py-3",
                cfg.is_active
                  ? "border-primary/30 bg-primary/8"
                  : "border-border bg-surface",
              )}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold text-ink">
                      {cfg.name}
                    </span>
                    <ProviderBadge provider={cfg.provider} />
                    {cfg.is_active && <Badge variant="default">已激活</Badge>}
                  </div>
                  <div className="flex flex-wrap gap-2 text-[11px] text-ink-tertiary">
                    <span className="rounded-full bg-page px-2 py-1 font-mono">{cfg.api_key_masked}</span>
                    <span className="rounded-full bg-page px-2 py-1">{cfg.api_base_url || "默认地址"}</span>
                  </div>
                  {cfg.compatibility_warnings?.length ? (
                    <WarningsPanel title="兼容性提醒" items={cfg.compatibility_warnings} />
                  ) : null}
                  {testingId === cfg.id && (
                    <div className="flex items-center gap-2 text-[11px] text-ink-tertiary">
                      <Spinner className="h-3 w-3" />
                      正在测试聊天与嵌入链路...
                    </div>
                  )}
                  {testResults[cfg.id] && (
                    <TestResultSummary result={testResults[cfg.id]} />
                  )}
                </div>
                <div className="flex shrink-0 gap-1">
                  <button
                    onClick={() => handleTest(cfg)}
                    disabled={testingId === cfg.id}
                    className="rounded-lg p-1.5 text-ink-tertiary hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
                    title="测试配置"
                  >
                    {testingId === cfg.id ? (
                      <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Play className="h-3.5 w-3.5" />
                    )}
                  </button>
                  <button
                    onClick={() => openEditModal(cfg)}
                    data-testid={`llm-config-edit-${cfg.id}`}
                    className="rounded-lg p-1.5 text-ink-tertiary hover:bg-hover hover:text-ink"
                    title="编辑"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  {!cfg.is_active && (
                    <button
                      onClick={() => handleActivate(cfg.id)}
                      disabled={submitting}
                      className="rounded-lg p-1.5 text-ink-tertiary hover:bg-hover hover:text-primary"
                      title="激活"
                    >
                      <Power className="h-3.5 w-3.5" />
                    </button>
                  )}
                  <button
                    onClick={() => setDeleteCfg(cfg)}
                    className="rounded-lg p-1.5 text-ink-tertiary hover:bg-error-light hover:text-error"
                    title="删除"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
    <ConfirmDialog
      open={!!deleteCfg}
      title="删除 LLM 配置"
      description={deleteCfg ? `确定删除“${deleteCfg.name}”吗？` : undefined}
      variant="danger"
      confirmLabel="删除"
      onConfirm={() => void handleDelete()}
      onCancel={() => setDeleteCfg(null)}
    />
    <Modal
      open={showAdd}
      onClose={closeAddModal}
      title="新建 LLM 配置"
      maxWidth="lg"
      overlayClassName="bg-black/40"
      className="flex h-[min(76dvh,680px)] min-h-0 w-[min(720px,calc(100vw-0.75rem))] max-w-[min(720px,calc(100vw-0.75rem))] flex-col overflow-hidden !overflow-y-hidden !rounded-xl p-0 sm:h-[min(76dvh,680px)] sm:w-[min(720px,calc(100vw-2rem))] sm:max-w-[min(720px,calc(100vw-2rem))]"
    >
      <LLMEditorModalBody>
        <AddConfigInline
          initialProvider={addPreset}
          providerPresets={providerPresets}
          onCreated={() => {
            closeAddModal();
            void load();
          }}
          onCancel={closeAddModal}
        />
      </LLMEditorModalBody>
    </Modal>
    <Modal
      open={!!editCfg}
      onClose={closeEditModal}
      title={editCfg ? `编辑 LLM 配置：${editCfg.name}` : "编辑 LLM 配置"}
      maxWidth="lg"
      overlayClassName="bg-black/40"
      className="flex h-[min(76dvh,680px)] min-h-0 w-[min(720px,calc(100vw-0.75rem))] max-w-[min(720px,calc(100vw-0.75rem))] flex-col overflow-hidden !overflow-y-hidden !rounded-xl p-0 sm:h-[min(76dvh,680px)] sm:w-[min(720px,calc(100vw-2rem))] sm:max-w-[min(720px,calc(100vw-2rem))]"
    >
      <LLMEditorModalBody>
        {editCfg ? (
          <EditConfigInline
            config={editCfg}
            providerPresets={providerPresets}
            onSaved={() => {
              closeEditModal();
              void load();
            }}
            onCancel={closeEditModal}
          />
        ) : null}
      </LLMEditorModalBody>
    </Modal>
    </>
  );
}

function LLMEditorModalBody({
  children,
}: {
  children: ReactNode;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-3 pb-3 pt-1 sm:px-4 sm:pb-4">
      {children}
    </div>
  );
}

function TestResultSummary({ result }: { result: LLMProviderTestResult }) {
  return (
    <div className="space-y-2 pt-1">
      <div className="grid gap-2 md:grid-cols-2">
        <TestStatusLine label="聊天" status={result.chat} />
        <TestStatusLine label="嵌入" status={result.embedding} />
      </div>
      {result.warnings?.length ? (
        <WarningsPanel title="排查建议" items={result.warnings} />
      ) : null}
    </div>
  );
}

function WarningsPanel({
  title,
  items,
  className,
}: {
  title: string;
  items: string[];
  className?: string;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <div className="flex items-center gap-1.5 text-[11px] font-medium text-amber-700">
        <Shield className="h-3.5 w-3.5" />
        {title}
      </div>
      <div className="space-y-1 rounded-xl border border-amber-200 bg-amber-50/90 px-3 py-2 text-[11px] leading-5 text-amber-900">
        {items.map((item, index) => (
          <div key={`${index}-${item}`}>- {item}</div>
        ))}
      </div>
    </div>
  );
}

function TestStatusLine({
  label,
  status,
}: {
  label: string;
  status: LLMProviderTestResult["chat"];
}) {
  const ok = status.ok;
  return (
    <div className={cn("rounded-xl border px-3 py-2", ok ? "border-emerald-200 bg-emerald-50/70" : "border-red-200 bg-red-50/70")}>
      <div className={cn("flex items-start gap-1.5", ok ? "text-emerald-700" : "text-error")}>
        {ok ? (
          <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        ) : (
          <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        )}
        <div className="min-w-0 space-y-1">
          <div className="font-medium">
            {label} · {ok ? "可用" : "失败"}
          </div>
          <div className="text-[11px] leading-5 text-ink-secondary">
            {status.message}
          </div>
        </div>
      </div>
    </div>
  );
}

function ProviderBadge({ provider }: { provider: string }) {
  const tones: Record<string, { backgroundColor: string; borderColor: string; color: string }> = {
    zhipu: {
      backgroundColor: "rgba(99, 102, 241, 0.12)",
      borderColor: "rgba(99, 102, 241, 0.24)",
      color: "#6f70ff",
    },
    openai: {
      backgroundColor: "rgba(16, 185, 129, 0.12)",
      borderColor: "rgba(16, 185, 129, 0.24)",
      color: "#0f9f72",
    },
    gemini: {
      backgroundColor: "rgba(56, 189, 248, 0.12)",
      borderColor: "rgba(56, 189, 248, 0.22)",
      color: "#3da8d8",
    },
    qwen: {
      backgroundColor: "rgba(34, 211, 238, 0.12)",
      borderColor: "rgba(34, 211, 238, 0.24)",
      color: "#0891b2",
    },
    kimi: {
      backgroundColor: "rgba(244, 114, 182, 0.12)",
      borderColor: "rgba(244, 114, 182, 0.22)",
      color: "#db2777",
    },
    minimax: {
      backgroundColor: "rgba(132, 204, 22, 0.12)",
      borderColor: "rgba(132, 204, 22, 0.22)",
      color: "#65a30d",
    },
    anthropic: {
      backgroundColor: "rgba(249, 115, 22, 0.12)",
      borderColor: "rgba(249, 115, 22, 0.22)",
      color: "#ea7a24",
    },
    custom: {
      backgroundColor: "rgba(100, 116, 139, 0.12)",
      borderColor: "rgba(100, 116, 139, 0.22)",
      color: "#64748b",
    },
  };
  const labels: Record<string, string> = {
    zhipu: "智谱",
    openai: "OpenAI 兼容",
    gemini: "Gemini",
    qwen: "Qwen",
    kimi: "Kimi",
    minimax: "MiniMax",
    anthropic: "Anthropic 兼容",
    custom: "自定义",
  };
  const tone = tones[provider];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium",
        tone ? "" : "border-border bg-hover text-ink-tertiary",
      )}
      style={tone}
    >
      <Server className="h-2.5 w-2.5" />
      {labels[provider] || provider}
    </span>
  );
}

interface LlmChannelDescriptor {
  id: string;
  label: string;
  provider: string;
  model: string;
  detail: string;
}

function buildConfigChannelDescriptors(config: LLMProviderConfig): LlmChannelDescriptor[] {
  const items: LlmChannelDescriptor[] = [
    {
      id: "skim",
      label: "粗读",
      provider: config.provider,
      model: resolveEffectiveModel(config, "model_skim"),
      detail: inheritedModelDetail(
        config.model_skim,
        "继承主模型 / 主通道默认地址",
        config.api_base_url || "主通道默认地址",
      ),
    },
    {
      id: "deep",
      label: "精读",
      provider: config.provider,
      model: resolveEffectiveModel(config, "model_deep"),
      detail: inheritedModelDetail(
        config.model_deep,
        "继承主模型 / 深度分析 / 长链路研究",
        "深度分析 / 长链路研究",
      ),
    },
    {
      id: "fallback",
      label: "降级",
      provider: config.provider,
      model: resolveEffectiveModel(config, "model_fallback"),
      detail: inheritedModelDetail(
        config.model_fallback,
        "继承主模型 / 失败回退 / 低成本兜底",
        "失败回退 / 低成本兜底",
      ),
    },
    {
      id: "embedding",
      label: "嵌入",
      provider: config.embedding_provider || config.provider,
      model: config.model_embedding,
      detail: config.embedding_api_base_url || config.api_base_url || "跟随主通道",
    },
  ];

  if (getPrimaryModelValue(config)) {
    items.push({
      id: "vision",
      label: "视觉",
      provider: config.provider,
      model: resolveEffectiveModel(config, "model_vision"),
      detail: inheritedModelDetail(
        config.model_vision,
        "继承主模型 / 图像 / 多模态理解",
        "图像 / 多模态理解",
      ),
    });
  }

  if (config.image_provider || config.model_image) {
    items.push({
      id: "image",
      label: "绘图",
      provider: config.image_provider || "gemini",
      model: config.model_image || "gemini-2.5-flash-image",
      detail: config.image_api_base_url || "独立图像生成通道",
    });
  }

  return items;
}

function LlmOverviewCard({
  icon: Icon,
  label,
  value,
  detail,
}: {
  icon: typeof Cpu;
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="rounded-2xl border border-border/70 bg-page px-4 py-3">
      <div className="flex items-center gap-2 text-[11px] text-ink-tertiary">
        <Icon className="h-3.5 w-3.5 text-primary" />
        {label}
      </div>
      <div className="mt-2 text-xl font-semibold text-ink">{value}</div>
      <div className="mt-1 text-[11px] leading-5 text-ink-tertiary">{detail}</div>
    </div>
  );
}

function LlmChannelCard({ item }: { item: LlmChannelDescriptor }) {
  return (
    <div className="rounded-2xl border border-border/70 bg-page/75 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-tertiary">
          {item.label}
        </span>
        <ProviderBadge provider={item.provider} />
      </div>
      <div className="mt-2 rounded-xl bg-surface px-2.5 py-2">
        <div className="font-mono text-[11px] text-ink">{item.model || "未配置"}</div>
        <div className="mt-1 text-[10px] leading-5 text-ink-tertiary">{item.detail}</div>
      </div>
    </div>
  );
}

function ProviderPresetLaunchCard({
  preset,
  onClick,
}: {
  preset: ProviderPresetOption;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-2xl border border-border bg-surface px-3.5 py-3 text-left transition hover:border-primary/25 hover:bg-page"
    >
      <div className="flex flex-wrap items-center gap-2">
        <ProviderBadge provider={preset.id} />
        <span className="text-sm font-semibold text-ink">{preset.label}</span>
      </div>
    </button>
  );
}

function AddConfigInline({
  initialProvider = "openai",
  providerPresets,
  onCreated,
  onCancel,
}: {
  initialProvider?: string;
  providerPresets?: LLMProviderPreset[];
  onCreated: () => void;
  onCancel: () => void;
}) {
  const presetOptions = useMemo(
    () => buildProviderPresetOptions(providerPresets),
    [providerPresets],
  );
  const initialPreset =
    presetOptions.find((item) => item.id === initialProvider) ||
    presetOptions.find((item) => item.id === "openai") ||
    buildProviderPresetOptions().find((item) => item.id === "openai");
  const [form, setForm] = useState<LLMProviderCreate>(() => {
    const baseForm: LLMProviderCreate = {
      name: "",
      provider: (initialPreset?.id || "openai") as LLMProviderCreate["provider"],
      api_key: "",
      api_base_url: initialPreset?.base_url ?? PROVIDER_PRESETS.openai.base_url,
      model_skim: "",
      model_deep: "",
      model_vision: "",
      embedding_provider: "",
      embedding_api_key: "",
      embedding_api_base_url: "",
      model_embedding: initialPreset?.models.model_embedding || PROVIDER_PRESETS.openai.models.model_embedding || "",
      model_fallback: "",
      image_provider: "",
      image_api_key: "",
      image_api_base_url: "",
      model_image: "",
    };
    const primaryModel = getPrimaryModelValue({
      model_deep: initialPreset?.models.model_deep || PROVIDER_PRESETS.openai.models.model_deep,
      model_skim: initialPreset?.models.model_skim || PROVIDER_PRESETS.openai.models.model_skim,
      model_fallback: initialPreset?.models.model_fallback || PROVIDER_PRESETS.openai.models.model_fallback,
      model_vision: initialPreset?.models.model_vision || PROVIDER_PRESETS.openai.models.model_vision,
    });
    return syncPrimaryModelFields(baseForm, primaryModel);
  });
  const [showKey, setShowKey] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [activateAfterCreate, setActivateAfterCreate] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const primaryModel = getPrimaryModelValue(form);

  const setField = (key: keyof LLMProviderCreate, value: string) =>
    setForm((prev) => ({ ...prev, [key]: value }));
  const setPrimaryModel = (value: string) =>
    setForm((prev) => syncPrimaryModelFields(prev, value));

  const handleProviderChange = (provider: string) => {
    setForm((prev) =>
      applyProviderPreset(prev, provider, presetOptions) as LLMProviderCreate,
    );
  };

  const handleSubmit = async () => {
    if (!form.name.trim()) {
      setError("请输入配置名称");
      return;
    }
    if (!form.api_key.trim()) {
      setError("请输入 API Key");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const created = await llmConfigApi.create(form);
      if (activateAfterCreate) {
        await llmConfigApi.activate(created.id);
      }
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      data-testid="llm-config-add-panel"
      className="space-y-3"
    >
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-sm font-semibold text-ink">新增配置</p>
        </div>
      </div>
      {error && (
        <div className="rounded-lg bg-error-light px-3 py-2 text-xs text-error">
          {error}
        </div>
      )}
      <SettingsFormCard
        icon={Server}
        title="基本信息"
        tone="accent"
      >
        <MiniInput
          label="配置名称"
          value={form.name}
          onChange={(v) => setField("name", v)}
          placeholder="如：我的研究助手配置"
        />
        <ProviderSelectionGrid
          value={form.provider}
          providerPresets={providerPresets}
          onSelect={handleProviderChange}
        />
      </SettingsFormCard>

      <SettingsFormCard
        icon={Shield}
        title="连接"
        action={(
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setShowAdvanced((current) => !current)}
          >
            {showAdvanced ? "收起高级项" : "高级项"}
          </Button>
        )}
      >
        <div className="grid gap-3 lg:grid-cols-3">
          <div className="relative">
            <MiniInput
              label="API Key"
              value={form.api_key}
              onChange={(v) => setField("api_key", v)}
              placeholder="sk-..."
              type={showKey ? "text" : "password"}
            />
            <button
              type="button"
              className="absolute right-2 top-6 text-ink-tertiary hover:text-ink"
              onClick={() => setShowKey(!showKey)}
            >
              {showKey ? (
                <EyeOff className="h-3.5 w-3.5" />
              ) : (
                <Eye className="h-3.5 w-3.5" />
              )}
            </button>
          </div>
          <MiniInput
            label="Base URL"
            value={form.api_base_url || ""}
            onChange={(v) => setField("api_base_url", v)}
            placeholder="输入 Base URL"
          />
          <MiniInput
            label="模型"
            value={primaryModel}
            onChange={setPrimaryModel}
            placeholder="输入模型"
          />
        </div>
        <div className="text-[11px] text-ink-tertiary">
          默认所有文本能力都跟随这个主模型。只有在高级项里显式填写覆写时，粗读 / 精读 / 视觉 / 降级才会分开走不同模型。
        </div>
      </SettingsFormCard>

      {showAdvanced ? (
        <>
          <SettingsFormCard
            icon={Cpu}
            title="文本模型覆写"
            description="留空则继承上面的主模型，只在确实需要分流时填写。"
          >
            <div className="grid gap-3 lg:grid-cols-2">
              <MiniInput
                label="粗读模型"
                value={form.model_skim || ""}
                onChange={(v) => setField("model_skim", v)}
                placeholder="留空则继承主模型"
              />
              <MiniInput
                label="精读模型"
                value={form.model_deep || ""}
                onChange={(v) => setField("model_deep", v)}
                placeholder="留空则继承主模型"
              />
              <MiniInput
                label="视觉模型"
                value={form.model_vision || ""}
                onChange={(v) => setField("model_vision", v)}
                placeholder="留空则继承主模型"
              />
              <MiniInput
                label="降级模型"
                value={form.model_fallback || ""}
                onChange={(v) => setField("model_fallback", v)}
                placeholder="留空则继承主模型"
              />
            </div>
          </SettingsFormCard>

          <SettingsFormCard
            icon={Zap}
            title="嵌入"
          >
            <div className="grid gap-3 lg:grid-cols-2">
              <div>
                <label className="mb-1 block text-[11px] font-medium text-ink-secondary">
                  提供方
                </label>
                <select
                  className="w-full rounded-xl border border-border bg-surface px-3 py-2 text-xs text-ink outline-none focus:border-primary"
                  value={form.embedding_provider || ""}
                  onChange={(e) => setField("embedding_provider", e.target.value)}
                >
                  {EMBEDDING_PROVIDER_OPTIONS.map((option) => (
                    <option key={option.value || "same"} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
              <MiniInput
                label="模型"
                value={form.model_embedding || ""}
                onChange={(v) => setField("model_embedding", v)}
              />
              <MiniInput
                label="Base URL"
                value={form.embedding_api_base_url || ""}
                onChange={(v) => setField("embedding_api_base_url", v)}
                placeholder="输入 Base URL"
              />
              <MiniInput
                label="API Key"
                value={form.embedding_api_key || ""}
                onChange={(v) => setField("embedding_api_key", v)}
                placeholder="留空则沿用主 API Key"
                type="password"
              />
            </div>
          </SettingsFormCard>

          <SettingsFormCard
            icon={ImagePlus}
            title="绘图"
          >
            <div className="grid gap-3 lg:grid-cols-2">
              <div>
                <label className="mb-1 block text-[11px] font-medium text-ink-secondary">
                  提供方
                </label>
                <select
                  className="w-full rounded-xl border border-border bg-surface px-3 py-2 text-xs text-ink outline-none focus:border-primary"
                  value={form.image_provider || ""}
                  onChange={(e) => setField("image_provider", e.target.value)}
                >
                  {IMAGE_PROVIDER_OPTIONS.map((option) => (
                    <option key={option.value || "disabled"} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
              <MiniInput
                label="模型"
                value={form.model_image || ""}
                onChange={(v) => setField("model_image", v)}
                placeholder="默认：gemini-2.5-flash-image"
              />
              <MiniInput
                label="Base URL"
                value={form.image_api_base_url || ""}
                onChange={(v) => setField("image_api_base_url", v)}
                placeholder="默认：官方 Gemini API"
              />
              <MiniInput
                label="API Key"
                value={form.image_api_key || ""}
                onChange={(v) => setField("image_api_key", v)}
                placeholder="留空则不启用绘图"
                type="password"
              />
            </div>
          </SettingsFormCard>
        </>
      ) : null}
      <div className="mt-2 flex flex-col gap-2 border-t border-border/70 pt-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
        <label className="flex items-center gap-2 text-[11px] text-ink-tertiary sm:mr-auto">
          <input
            type="checkbox"
            checked={activateAfterCreate}
            onChange={(e) => setActivateAfterCreate(e.target.checked)}
            className="rounded border-border"
          />
          创建后立即激活
        </label>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          取消
        </Button>
        <Button size="sm" onClick={handleSubmit} disabled={submitting}>
          {submitting ? <Spinner className="mr-1 h-3 w-3" /> : null}
          创建
        </Button>
      </div>
    </div>
  );
}

function EditConfigInline({
  config,
  providerPresets,
  onSaved,
  onCancel,
}: {
  config: LLMProviderConfig;
  providerPresets?: LLMProviderPreset[];
  onSaved: () => void;
  onCancel: () => void;
}) {
  const presetOptions = useMemo(
    () => buildProviderPresetOptions(providerPresets),
    [providerPresets],
  );
  const [form, setForm] = useState<LLMProviderUpdate>({
    name: config.name,
    provider: config.provider,
    api_base_url: config.api_base_url || "",
    model_skim: config.model_skim,
    model_deep: config.model_deep,
    model_vision: config.model_vision || "",
    embedding_provider: config.embedding_provider || "",
    embedding_api_base_url: config.embedding_api_base_url || "",
    model_embedding: config.model_embedding,
    model_fallback: config.model_fallback,
    image_provider: config.image_provider || "",
    image_api_base_url: config.image_api_base_url || "",
    model_image: config.model_image || "",
  });
  const [newApiKey, setNewApiKey] = useState("");
  const [newEmbeddingApiKey, setNewEmbeddingApiKey] = useState("");
  const [newImageApiKey, setNewImageApiKey] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const primaryModel = getPrimaryModelValue(form);

  const setField = (key: keyof LLMProviderUpdate, value: string) =>
    setForm((prev) => ({ ...prev, [key]: value }));
  const setPrimaryModel = (value: string) =>
    setForm((prev) => syncPrimaryModelFields(prev, value));

  const handleProviderChange = (provider: string) => {
    setForm((prev) =>
      applyProviderPreset(prev, provider, presetOptions) as LLMProviderUpdate,
    );
  };

  const handleSave = async () => {
    setSubmitting(true);
    setError("");
    try {
      const payload: LLMProviderUpdate = { ...form };
      if (newApiKey.trim()) payload.api_key = newApiKey;
      if (newEmbeddingApiKey.trim()) payload.embedding_api_key = newEmbeddingApiKey;
      if (newImageApiKey.trim()) payload.image_api_key = newImageApiKey;
      await llmConfigApi.update(config.id, payload);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      data-testid="llm-config-edit-panel"
      className="space-y-3"
    >
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-sm font-semibold text-ink">编辑：{config.name}</p>
        </div>
        {config.is_active ? (
          <div className="rounded-full bg-primary/10 px-3 py-1 text-[11px] font-medium text-primary">
            当前主配置
          </div>
        ) : null}
      </div>
      {error && (
        <div className="rounded-lg bg-error-light px-3 py-2 text-xs text-error">
          {error}
        </div>
      )}
      <SettingsFormCard
        icon={Server}
        title="基本信息"
        tone="accent"
      >
        <MiniInput
          label="名称"
          value={form.name || ""}
          onChange={(v) => setField("name", v)}
        />
        <ProviderSelectionGrid
          value={form.provider || config.provider}
          providerPresets={providerPresets}
          onSelect={handleProviderChange}
        />
      </SettingsFormCard>

      <SettingsFormCard
        icon={Shield}
        title="连接"
        action={(
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setShowAdvanced((current) => !current)}
          >
            {showAdvanced ? "收起高级项" : "高级项"}
          </Button>
        )}
      >
        <div className="grid gap-3 lg:grid-cols-3">
          <MiniInput
            label="新 API Key（留空则不修改）"
            value={newApiKey}
            onChange={setNewApiKey}
            placeholder="留空保持不变"
            type="password"
          />
          <MiniInput
            label="Base URL"
            value={form.api_base_url || ""}
            onChange={(v) => setField("api_base_url", v)}
            placeholder="输入 Base URL"
          />
          <MiniInput
            label="模型"
            value={primaryModel}
            onChange={setPrimaryModel}
            placeholder="输入模型"
          />
        </div>
        <div className="text-[11px] text-ink-tertiary">
          默认所有文本能力都跟随这个主模型。只有在高级项里显式填写覆写时，粗读 / 精读 / 视觉 / 降级才会分开走不同模型。
        </div>
        <div className="grid gap-2 text-[11px] text-ink-tertiary md:grid-cols-2">
          {config.api_key_masked ? <div>当前主 Key：{config.api_key_masked}</div> : <div>当前主 Key：未显示</div>}
          {config.embedding_api_key_masked ? (
            <div>当前独立嵌入 Key：{config.embedding_api_key_masked}</div>
          ) : (
            <div>当前独立嵌入 Key：未单独配置</div>
          )}
        </div>
      </SettingsFormCard>

      {showAdvanced ? (
        <>
          <SettingsFormCard
            icon={Cpu}
            title="文本模型覆写"
            description="留空则继承上面的主模型，只在确实需要分流时填写。"
          >
            <div className="grid gap-3 lg:grid-cols-2">
              <MiniInput
                label="粗读模型"
                value={form.model_skim || ""}
                onChange={(v) => setField("model_skim", v)}
                placeholder="留空则继承主模型"
              />
              <MiniInput
                label="精读模型"
                value={form.model_deep || ""}
                onChange={(v) => setField("model_deep", v)}
                placeholder="留空则继承主模型"
              />
              <MiniInput
                label="视觉模型"
                value={form.model_vision || ""}
                onChange={(v) => setField("model_vision", v)}
                placeholder="留空则继承主模型"
              />
              <MiniInput
                label="降级模型"
                value={form.model_fallback || ""}
                onChange={(v) => setField("model_fallback", v)}
                placeholder="留空则继承主模型"
              />
            </div>
          </SettingsFormCard>

          <SettingsFormCard
            icon={Zap}
            title="嵌入"
          >
            <div className="grid gap-3 lg:grid-cols-2">
              <div>
                <label className="mb-1 block text-[11px] font-medium text-ink-secondary">
                  提供方
                </label>
                <select
                  className="w-full rounded-xl border border-border bg-surface px-3 py-2 text-xs text-ink outline-none focus:border-primary"
                  value={form.embedding_provider || ""}
                  onChange={(e) => setField("embedding_provider", e.target.value)}
                >
                  {EMBEDDING_PROVIDER_OPTIONS.map((option) => (
                    <option key={option.value || "same"} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
              <MiniInput
                label="模型"
                value={form.model_embedding || ""}
                onChange={(v) => setField("model_embedding", v)}
              />
              <MiniInput
                label="Base URL"
                value={form.embedding_api_base_url || ""}
                onChange={(v) => setField("embedding_api_base_url", v)}
                placeholder="输入 Base URL"
              />
              <MiniInput
                label="新 API Key（留空则不修改）"
                value={newEmbeddingApiKey}
                onChange={setNewEmbeddingApiKey}
                placeholder="留空保持不变"
                type="password"
              />
            </div>
          </SettingsFormCard>

          <SettingsFormCard
            icon={ImagePlus}
            title="绘图"
          >
            <div className="grid gap-3 lg:grid-cols-2">
              <div>
                <label className="mb-1 block text-[11px] font-medium text-ink-secondary">
                  提供方
                </label>
                <select
                  className="w-full rounded-xl border border-border bg-surface px-3 py-2 text-xs text-ink outline-none focus:border-primary"
                  value={form.image_provider || ""}
                  onChange={(e) => setField("image_provider", e.target.value)}
                >
                  {IMAGE_PROVIDER_OPTIONS.map((option) => (
                    <option key={option.value || "disabled"} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
              <MiniInput
                label="模型"
                value={form.model_image || ""}
                onChange={(v) => setField("model_image", v)}
                placeholder="默认：gemini-2.5-flash-image"
              />
              <MiniInput
                label="Base URL"
                value={form.image_api_base_url || ""}
                onChange={(v) => setField("image_api_base_url", v)}
                placeholder="默认：官方 Gemini API"
              />
              <MiniInput
                label="新 API Key（留空则不修改）"
                value={newImageApiKey}
                onChange={setNewImageApiKey}
                placeholder="留空保持不变"
                type="password"
              />
            </div>
            <div className="grid gap-2 text-[11px] text-ink-tertiary md:grid-cols-2">
              {config.image_api_key_masked ? (
                <div>当前绘图 Key：{config.image_api_key_masked}</div>
              ) : (
                <div>当前绘图 Key：未单独配置</div>
              )}
            </div>
          </SettingsFormCard>
        </>
      ) : null}
      <div className="mt-2 flex flex-col gap-2 border-t border-border/70 pt-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          取消
        </Button>
        <Button size="sm" onClick={handleSave} disabled={submitting}>
          保存
        </Button>
      </div>
    </div>
  );
}

function MiniInput({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <div>
      <label className="mb-1 block text-[11px] font-medium text-ink-secondary">
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="theme-input min-h-11 w-full rounded-lg border border-border bg-surface px-3 py-2 font-mono text-sm text-ink placeholder:text-ink-placeholder outline-none focus:border-primary"
      />
    </div>
  );
}

function AssistantSettingsSection() {
  const {
    activeSkillIds,
    availableSkills,
    skillsError,
    skillsLoading,
    toggleSkill,
    replaceSkills,
    clearSkills,
    refreshSkills,
    settingsScopeLabel,
  } = useAssistantInstance();
  const displaySkills = useMemo(
    () => sortSkillsForDisplay(availableSkills),
    [availableSkills],
  );
  const enabledSkillCount = activeSkillIds.filter((skillId) =>
    availableSkills.some((skill) => skill.id === skillId),
  ).length;

  if (skillsLoading && availableSkills.length === 0 && !skillsError) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-border bg-page p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-semibold text-ink">Skills</h3>
              <Badge>{enabledSkillCount} / {availableSkills.length}</Badge>
              {settingsScopeLabel ? <Badge variant="info">{settingsScopeLabel}</Badge> : null}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="secondary" onClick={() => void refreshSkills()} loading={skillsLoading}>
              刷新
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => replaceSkills(availableSkills.map((skill) => skill.id))}
              disabled={availableSkills.length === 0 || enabledSkillCount === availableSkills.length}
            >
              全部启用
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => clearSkills()}
              disabled={enabledSkillCount === 0}
            >
              清空
            </Button>
          </div>
        </div>

        {skillsError ? (
          <div className="mt-4 rounded-[18px] border border-warning/20 bg-warning-light px-4 py-3 text-sm text-warning">
            {skillsError}
          </div>
        ) : null}

        {displaySkills.length === 0 ? (
          <div className="pt-6">
            <Empty title="暂无 Skills" icon={<Bot className="h-10 w-10" />} />
          </div>
        ) : (
          <div className="mt-4 space-y-2">
            {displaySkills.map((skill) => {
              const enabled = activeSkillIds.includes(skill.id);
              return (
                <div key={skill.id} className="rounded-2xl border border-border bg-surface px-3.5 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium text-ink">{skill.name}</span>
                        <Badge variant={skill.system ? "info" : "default"}>{skillSourceLabel(skill.source)}</Badge>
                        <Badge variant="default">{skill.relative_path || skill.name}</Badge>
                        <Badge variant={enabled ? "success" : "default"}>
                          {enabled ? "已启用" : "未启用"}
                        </Badge>
                      </div>
                      <div className="mt-2 text-[11px] leading-5 text-ink-secondary">
                        {skill.relative_path || skill.name}
                      </div>
                      <div className="mt-2 break-all text-[10px] text-ink-tertiary">
                        {skill.path}
                      </div>
                    </div>
                    <Button size="sm" variant={enabled ? "secondary" : "primary"} onClick={() => toggleSkill(skill.id)}>
                      {enabled ? "停用" : "启用"}
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function StorageSettingsSection() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [savingDefaultRoot, setSavingDefaultRoot] = useState(false);
  const [defaultRoot, setDefaultRoot] = useState("");
  const [defaultRootDraft, setDefaultRootDraft] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await workspaceRootApi.list();
      setDefaultRoot(result.default_projects_root || "");
      setDefaultRootDraft(result.default_projects_root || "");
    } catch (error) {
      toast("error", getErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-border bg-page p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-ink">默认项目根目录</h3>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Button
              size="sm"
              variant="secondary"
              onClick={async () => {
                setSavingDefaultRoot(true);
                try {
                  const result = await workspaceRootApi.setDefault(null);
                  setDefaultRoot(result.default_projects_root || "");
                  setDefaultRootDraft(result.default_projects_root || "");
                  toast("success", "已恢复内置默认目录");
                } catch (error) {
                  toast("error", getErrorMessage(error));
                } finally {
                  setSavingDefaultRoot(false);
                }
              }}
              disabled={savingDefaultRoot}
            >
              恢复内置
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setDefaultRootDraft(defaultRoot)} disabled={savingDefaultRoot || defaultRootDraft === defaultRoot}>
              重置
            </Button>
          </div>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-[minmax(0,1fr),auto]">
            <Input
              label="默认目录"
              value={defaultRootDraft}
              onChange={(event) => setDefaultRootDraft(event.target.value)}
              placeholder="输入默认目录"
            />
          <div className="flex items-end">
            <Button
              className="w-full"
              onClick={async () => {
                setSavingDefaultRoot(true);
                try {
                  const result = await workspaceRootApi.setDefault(defaultRootDraft.trim() || null);
                  setDefaultRoot(result.default_projects_root || "");
                  setDefaultRootDraft(result.default_projects_root || "");
                  toast("success", "默认项目根目录已更新");
                } catch (error) {
                  toast("error", getErrorMessage(error));
                } finally {
                  setSavingDefaultRoot(false);
                }
              }}
              disabled={savingDefaultRoot || defaultRootDraft.trim() === defaultRoot}
            >
              保存默认目录
            </Button>
          </div>
        </div>
        <div className="mt-3 rounded-2xl border border-border bg-surface px-4 py-3 font-mono text-xs text-ink">
          {defaultRoot || "未配置"}
        </div>
      </div>
    </div>
  );
}
