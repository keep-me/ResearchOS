import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  FolderOpen,
  Loader2,
  Menu,
  MessageSquare,
  Pencil,
  Plus,
  Settings,
  Trash2,
  X,
} from "lucide-react";
import { cn, timeAgo } from "@/lib/utils";
import { useConversationCtx } from "@/contexts/ConversationContext";
import { workspaceRootApi } from "@/services/api";
import type { WorkspaceRootItem } from "@/types";
import { getErrorMessage } from "@/lib/errorHandler";
import ConfirmDialog from "@/components/ConfirmDialog";
import { Modal } from "@/components/ui/Modal";
import LogoIcon from "@/assets/logo-icon.svg?react";
import VisualStyleSwitcher from "@/components/VisualStyleSwitcher";
import { getConversationWorkspaceKey, isUntouchedConversation, normalizeWorkspacePath, type ConversationWorkspace } from "@/hooks/useConversations";
import { shellNavSections } from "@/components/shell/navigation";
import { joinWorkspaceRootPath, validateWorkspaceDirectoryName } from "@/lib/workspaceRoots";

const DEFAULT_GROUP_KEY = "__default__";
const SIDEBAR_COLLAPSED_STORAGE_KEY = "researchos.sidebar.collapsed";
const SIDEBAR_EXPANDED_WIDTH = 256;
const SIDEBAR_COLLAPSED_WIDTH = 72;

interface WorkspaceGroup {
  key: string;
  path: string;
  effectivePath: string;
  title: string;
  serverId: string | null;
  serverLabel: string | null;
  chats: ReturnType<typeof useConversationCtx>["metas"];
  removable: boolean;
  authorized: boolean;
  exists: boolean;
}

interface WorkspaceDialogState {
  mode: "create" | "import" | "rename";
  dirName?: string;
  path: string;
  title: string;
  effectivePath?: string;
  serverId?: string | null;
  serverLabel?: string | null;
  authorized?: boolean;
}

function isLegacyUpstreamWorkspacePath(path: string | null | undefined): boolean {
  const normalizedPath = String(path || "").trim().replace(/\\/g, "/").toLowerCase();
  if (!normalizedPath) return false;
  return /(^|\/)(aris)(\/|$)/.test(normalizedPath) || /(^|\/)auto-claude-code-research-in-sleep(\/|$)/.test(normalizedPath);
}

function normalizeLegacyWorkspaceTitle(title: string | null | undefined, fallbackPath = ""): string {
  const value = String(title || "").trim();
  if (!value) return deriveWorkspaceTitle(fallbackPath);
  if (isLegacyUpstreamWorkspacePath(fallbackPath)) return value;
  const normalized = value.toLowerCase();
  if (
    normalized === "aris"
    || normalized === "aris workspace"
    || normalized === "aris workbench"
    || value === "ARIS 工作区"
    || value === "ARIS 项目工作区"
  ) {
    return "项目工作区";
  }
  return value;
}

function readSidebarCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === "1";
}

function SectionLabel({ children, collapsed }: { children: string; collapsed: boolean }) {
  if (collapsed) {
    return <div className="h-5" aria-hidden="true" />;
  }
  return (
    <p className="px-2 pb-1 text-[11px] font-medium tracking-[0.02em] text-ink-tertiary">
      {children}
    </p>
  );
}

export default function Sidebar() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [deleteChatId, setDeleteChatId] = useState<string | null>(null);
  const [removeWorkspaceTarget, setRemoveWorkspaceTarget] = useState<WorkspaceGroup | null>(null);
  const [roots, setRoots] = useState<WorkspaceRootItem[]>([]);
  const [defaultProjectsRoot, setDefaultProjectsRoot] = useState("");
  const [loadingRoots, setLoadingRoots] = useState(true);
  const [savingWorkspace, setSavingWorkspace] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ [DEFAULT_GROUP_KEY]: true });
  const [workspaceDialog, setWorkspaceDialog] = useState<WorkspaceDialogState | null>(null);
  const [desktopCollapsed, setDesktopCollapsed] = useState(readSidebarCollapsed);

  const location = useLocation();
  const navigate = useNavigate();
  const { metas, activeId, activeWorkspace, createConversation, switchConversation, deleteConversation, renameWorkspaceConversations, clearWorkspaceConversations } = useConversationCtx();

  const loadRoots = useCallback(async () => {
    setLoadingRoots(true);
    try {
      const result = await workspaceRootApi.list().catch(() => ({ items: [] as WorkspaceRootItem[], default_projects_root: "" }));
      setRoots(
        (result.items || []).map((item) => ({
          ...item,
          title: normalizeLegacyWorkspaceTitle(item.title, item.path),
        })),
      );
      setDefaultProjectsRoot(result.default_projects_root || "");
    } finally {
      setLoadingRoots(false);
    }
  }, []);

  useEffect(() => { void loadRoots(); }, [loadRoots]);
  useEffect(() => { setMobileOpen(false); }, [location.pathname]);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const width = desktopCollapsed ? SIDEBAR_COLLAPSED_WIDTH : SIDEBAR_EXPANDED_WIDTH;
    document.documentElement.style.setProperty("--shell-sidebar-width", `${width}px`);
    window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, desktopCollapsed ? "1" : "0");
  }, [desktopCollapsed]);

  const defaultChats = useMemo(
    () => metas.filter((meta) => !getConversationWorkspaceKey(meta)).sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()),
    [metas],
  );

  const workspaceGroups = useMemo<WorkspaceGroup[]>(() => {
    const groups = new Map<string, WorkspaceGroup>();
    for (const root of roots) {
      const key = getConversationWorkspaceKey({ path: root.path, effectivePath: root.path, serverId: "local" });
      groups.set(key, {
        key,
        path: root.path,
        effectivePath: root.path,
        title: normalizeLegacyWorkspaceTitle(root.title, root.path),
        serverId: null,
        serverLabel: null,
        chats: [],
        removable: root.removable,
        authorized: true,
        exists: root.exists,
      });
    }
    for (const meta of metas) {
      const path = (meta.workspacePath || meta.effectiveWorkspacePath || "").trim();
      const effectivePath = (meta.effectiveWorkspacePath || meta.workspacePath || "").trim();
      const key = getConversationWorkspaceKey(meta);
      if (!key || !path) continue;
      const existing = groups.get(key);
      const next = existing || {
        key,
        path,
        effectivePath: effectivePath || path,
        title: normalizeLegacyWorkspaceTitle(meta.workspaceTitle, effectivePath || path),
        serverId: meta.workspaceServerId || null,
        serverLabel: meta.workspaceServerLabel || null,
        chats: [],
        removable: true,
        authorized: false,
        exists: true,
      };
      next.path = next.path || path;
      next.effectivePath = next.effectivePath || effectivePath || path;
      next.serverId = next.serverId || meta.workspaceServerId || null;
      next.serverLabel = next.serverLabel || meta.workspaceServerLabel || null;
      next.chats = [...next.chats, meta].sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
      groups.set(key, next);
    }
    return Array.from(groups.values()).sort((a, b) => {
      const aActive = a.chats.some((chat) => chat.id === activeId) ? 1 : 0;
      const bActive = b.chats.some((chat) => chat.id === activeId) ? 1 : 0;
      if (aActive !== bActive) return bActive - aActive;
      return a.title.localeCompare(b.title, "zh-CN");
    });
  }, [activeId, metas, roots]);

  useEffect(() => {
    const current = metas.find((meta) => meta.id === activeId);
    const key = getConversationWorkspaceKey(current) || DEFAULT_GROUP_KEY;
    setExpanded((prev) => ({ ...prev, [key]: true }));
  }, [activeId, metas]);

  const openAssistant = useCallback(() => {
    navigate("/assistant");
    setMobileOpen(false);
  }, [navigate]);

  const findReusableAssistantConversationId = useCallback(() => {
    const activeMeta = metas.find((meta) => meta.id === activeId) || null;
    if (isUntouchedConversation(activeMeta)) {
      return activeMeta?.id || null;
    }

    const workspaceKey = getConversationWorkspaceKey(activeWorkspace);
    if (workspaceKey) {
      const workspaceDraft = metas.find((meta) =>
        meta.id !== activeId
        && getConversationWorkspaceKey(meta) === workspaceKey
        && isUntouchedConversation(meta),
      );
      if (workspaceDraft) return workspaceDraft.id;
      return null;
    }

    const defaultDraft = metas.find((meta) =>
      meta.id !== activeId
      && !getConversationWorkspaceKey(meta)
      && isUntouchedConversation(meta),
    );
    return defaultDraft?.id || null;
  }, [activeId, activeWorkspace, metas]);

  const handleAssistantNavClick = useCallback(() => {
    const reusableConversationId = findReusableAssistantConversationId();
    if (reusableConversationId) {
      switchConversation(reusableConversationId);
    } else if (activeWorkspace) {
      createConversation(activeWorkspace, { persist: false });
    }
    navigate("/assistant");
    setMobileOpen(false);
  }, [activeWorkspace, createConversation, findReusableAssistantConversationId, navigate, switchConversation]);

  const isNavRouteActive = useCallback((to: string) => {
    if (to === "/assistant") {
      return location.pathname.startsWith("/assistant");
    }
    return location.pathname === to || location.pathname.startsWith(`${to}/`);
  }, [location.pathname]);

  const handleSelectChat = useCallback((id: string) => {
    switchConversation(id);
    openAssistant();
  }, [openAssistant, switchConversation]);

  const handleNewWorkspaceChat = useCallback((group: WorkspaceGroup) => {
    const reusableChat = group.chats.find((chat) =>
      isUntouchedConversation(chat),
    );
    if (reusableChat) {
      switchConversation(reusableChat.id);
      setExpanded((prev) => ({ ...prev, [group.key]: true }));
      openAssistant();
      return;
    }
    createConversation({
      path: group.path,
      title: group.title,
      effectivePath: group.effectivePath || group.path,
      serverId: group.serverId || null,
      serverLabel: group.serverLabel || null,
    }, { persist: false });
    setExpanded((prev) => ({ ...prev, [group.key]: true }));
    openAssistant();
  }, [createConversation, openAssistant, switchConversation]);

  const handleSaveWorkspace = useCallback(async () => {
    if (!workspaceDialog) return;
    setSavingWorkspace(true);
    try {
      if (workspaceDialog.mode === "create") {
        const rootPath = defaultProjectsRoot.trim();
        const dirName = workspaceDialog.dirName?.trim() || "";
        const dirError = validateWorkspaceDirectoryName(dirName);
        if (!rootPath) throw new Error("请先在设置中配置默认项目根目录");
        if (dirError) throw new Error(dirError);
        const path = joinWorkspaceRootPath(rootPath, dirName);
        const title = normalizeLegacyWorkspaceTitle(workspaceDialog.title.trim() || dirName, path);
        await workspaceRootApi.create(path, title || undefined);
      } else if (workspaceDialog.mode === "import") {
        const path = workspaceDialog.path.trim();
        if (!path) throw new Error("工作区目录为空");
        const title = normalizeLegacyWorkspaceTitle(workspaceDialog.title.trim(), path);
        await workspaceRootApi.create(path, title || undefined);
      } else {
        const path = workspaceDialog.path.trim();
        const title = normalizeLegacyWorkspaceTitle(workspaceDialog.title.trim(), path);
        const workspace: ConversationWorkspace = {
          path,
          title,
          effectivePath: workspaceDialog.effectivePath || path,
          serverId: workspaceDialog.serverId || null,
          serverLabel: workspaceDialog.serverLabel || null,
        };
        if (!path) throw new Error("工作区目录为空");
        if (workspaceDialog.authorized !== false) {
          await workspaceRootApi.update(path, title);
        }
        renameWorkspaceConversations(workspace, title);
      }
      setWorkspaceDialog(null);
      await loadRoots();
    } catch (error) {
      alert(`保存工作区失败：${getErrorMessage(error)}`);
    } finally {
      setSavingWorkspace(false);
    }
  }, [defaultProjectsRoot, loadRoots, renameWorkspaceConversations, workspaceDialog]);

  const handleRemoveWorkspace = useCallback(async () => {
    if (!removeWorkspaceTarget) return;
    setSavingWorkspace(true);
    try {
      if (removeWorkspaceTarget.authorized) {
        const result = await workspaceRootApi.delete(removeWorkspaceTarget.path);
        setRoots(result.items || []);
      }
      clearWorkspaceConversations({
        path: removeWorkspaceTarget.path,
        title: removeWorkspaceTarget.title,
        effectivePath: removeWorkspaceTarget.effectivePath || removeWorkspaceTarget.path,
        serverId: removeWorkspaceTarget.serverId || null,
        serverLabel: removeWorkspaceTarget.serverLabel || null,
      });
      setRemoveWorkspaceTarget(null);
    } catch (error) {
      alert(`移除工作区失败：${getErrorMessage(error)}`);
    } finally {
      setSavingWorkspace(false);
    }
  }, [clearWorkspaceConversations, removeWorkspaceTarget]);

  const toggleDesktopCollapsed = useCallback(() => {
    setDesktopCollapsed((current) => !current);
  }, []);

  const handleCollapsedGroupOpen = useCallback((group: WorkspaceGroup) => {
    const targetChat = group.chats[0];
    if (targetChat) {
      handleSelectChat(targetChat.id);
      return;
    }
    handleNewWorkspaceChat(group);
  }, [handleNewWorkspaceChat, handleSelectChat]);

  const openCreateWorkspaceDialog = useCallback(() => {
    void loadRoots();
    setWorkspaceDialog({ mode: "create", dirName: "", path: "", title: "" });
  }, [loadRoots]);

  const openImportWorkspaceDialog = useCallback(() => {
    void loadRoots();
    setWorkspaceDialog({ mode: "import", path: "", title: "" });
  }, [loadRoots]);

  return (
    <>
      <button
        type="button"
        onClick={() => setMobileOpen(true)}
        className="fixed left-3 top-3 z-40 inline-flex h-9 w-9 items-center justify-center rounded-md border border-border bg-white text-ink shadow-sm transition-colors duration-150 hover:bg-hover active:bg-active lg:hidden"
        aria-label="打开菜单"
      >
        <Menu className="h-4 w-4" />
      </button>
      {mobileOpen && <div className="fixed inset-0 z-40 bg-black/40 lg:hidden" onClick={() => setMobileOpen(false)} />}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r border-border bg-sidebar transition-transform duration-150 lg:w-[var(--shell-sidebar-width)]",
          mobileOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0",
        )}
      >
        <div className={cn("flex items-center gap-2 border-b border-border px-3 py-3", desktopCollapsed && "justify-center px-2")}>
          <Link
            to="/"
            onClick={() => setMobileOpen(false)}
            className={cn("flex min-w-0 items-center gap-2.5", desktopCollapsed && "justify-center")}
          >
            <LogoIcon className="h-7 w-7 text-primary" />
            {!desktopCollapsed ? (
              <div className="min-w-0">
                <div className="truncate text-[14px] font-semibold text-ink">ResearchOS</div>
              </div>
            ) : null}
          </Link>

          <div className={cn("ml-auto flex items-center gap-1", desktopCollapsed && "hidden")}>
            <button
              type="button"
              onClick={toggleDesktopCollapsed}
              className="hidden h-8 w-8 items-center justify-center rounded-md text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active lg:inline-flex"
              aria-label={desktopCollapsed ? "展开侧栏" : "收起侧栏"}
              title={desktopCollapsed ? "展开侧栏" : "收起侧栏"}
            >
              {desktopCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
            </button>
            <button
              type="button"
              onClick={() => setMobileOpen(false)}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active lg:hidden"
              aria-label="关闭菜单"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className={cn("border-b border-border px-2 pb-3", desktopCollapsed && "px-1.5")}>
          <SectionLabel collapsed={desktopCollapsed}>导航</SectionLabel>
          <nav className="space-y-0.5">
            {shellNavSections.map((section) => section.items.filter((item) => item.to !== "/settings")).flat().map((item) => {
              const active = isNavRouteActive(item.to);
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  onClick={(event) => {
                    if (item.to === "/assistant") {
                      event.preventDefault();
                      handleAssistantNavClick();
                      return;
                    }
                    setMobileOpen(false);
                  }}
                  title={item.label}
                  className={cn(
                    "group relative flex items-center rounded-md text-[13px] text-ink-secondary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active",
                    desktopCollapsed ? "justify-center px-0 py-2.5" : "gap-2 px-3 py-2.5",
                    active && "bg-active text-ink",
                  )}
                >
                  {active ? <span className={cn("absolute bottom-1.5 left-1 top-1.5 w-0.5 rounded-full bg-primary", desktopCollapsed && "left-0.5")} /> : null}
                  <item.icon className="h-4 w-4 shrink-0" />
                  {!desktopCollapsed ? <span className="min-w-0 truncate">{item.label}</span> : null}
                </NavLink>
              );
            })}
          </nav>
        </div>

        {!desktopCollapsed ? (
          <div className="flex min-h-0 flex-1 flex-col px-2 py-3">
            <div className="mb-2 flex items-center justify-between px-1">
              <p className="text-[11px] font-medium tracking-[0.02em] text-ink-tertiary">历史</p>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={openCreateWorkspaceDialog}
                  className="inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active"
                  title="新建目录"
                >
                  <Plus className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={openImportWorkspaceDialog}
                  className="inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active"
                  title="导入目录"
                >
                  <FolderOpen className="h-4 w-4" />
                </button>
              </div>
            </div>

            <div className="min-h-0 space-y-3 overflow-y-auto pr-1">
              {loadingRoots ? (
                <div className="flex items-center gap-2 rounded-md px-2 py-3 text-xs text-ink-tertiary">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  正在读取历史目录...
                </div>
              ) : workspaceGroups.length === 0 && defaultChats.length === 0 ? (
                <p className="rounded-md px-2 py-3 text-xs text-ink-tertiary">还没有历史记录</p>
              ) : (
                <>
                  {defaultChats.length > 0 ? (
                    <section className="overflow-hidden rounded-lg border border-border bg-white">
                      <div className="border-b border-border-light px-3 py-2 text-[12px] font-medium text-ink-secondary">普通会话</div>
                      <div className="space-y-0.5 p-2">
                        {defaultChats.map((meta) => (
                          <div key={meta.id} className="group flex items-center gap-1">
                            <button
                              type="button"
                              onClick={() => handleSelectChat(meta.id)}
                              title={meta.title}
                              className={cn(
                                "flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-2 text-left text-[13px] transition-colors duration-150",
                                activeId === meta.id ? "bg-active text-ink" : "text-ink-secondary hover:bg-hover hover:text-ink active:bg-active",
                              )}
                            >
                              <MessageSquare className="h-3.5 w-3.5 shrink-0" />
                              <span className="min-w-0 flex-1 truncate">{meta.title}</span>
                              <span className="text-[10px] text-ink-tertiary">{timeAgo(meta.updatedAt)}</span>
                            </button>
                            <button
                              type="button"
                              onClick={() => setDeleteChatId(meta.id)}
                              className="hidden rounded-md p-1 text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-error group-hover:block"
                              aria-label="删除对话"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        ))}
                      </div>
                    </section>
                  ) : null}

                  {workspaceGroups.map((group) => {
                    const open = expanded[group.key] !== false;
                    return (
                      <section key={group.key} className="overflow-hidden rounded-lg border border-border bg-white">
                        <div className="flex items-center gap-1 border-b border-border-light px-2 py-2">
                          <button
                            type="button"
                            onClick={() => setExpanded((prev) => ({ ...prev, [group.key]: !open }))}
                            className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] font-medium text-ink transition-colors duration-150 hover:bg-hover active:bg-active"
                          >
                            {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0" />}
                            <span className="min-w-0 flex-1 truncate">{group.title}</span>
                            <span className="rounded-md border border-border bg-page px-1.5 py-0.5 text-[10px] font-normal text-ink-tertiary">
                              {group.chats.length}
                            </span>
                          </button>
                          <button
                            type="button"
                            onClick={() => handleNewWorkspaceChat(group)}
                            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active"
                            title="在此目录中开始新对话"
                          >
                            <Plus className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => setWorkspaceDialog({ mode: "rename", path: group.path, title: group.title, effectivePath: group.effectivePath, serverId: group.serverId, serverLabel: group.serverLabel, authorized: group.authorized })}
                            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active"
                            title="重命名目录"
                          >
                            <Pencil className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => setRemoveWorkspaceTarget(group)}
                            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-error active:bg-active"
                            title="移除目录"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>

                        {open ? (
                          <div className="space-y-1 p-2">
                            {(!group.authorized || !group.exists || group.serverLabel || normalizeWorkspacePath(group.effectivePath) !== normalizeWorkspacePath(group.path)) ? (
                              <div className="flex flex-wrap gap-1 px-1 pb-1">
                                {group.serverLabel ? <span className="rounded-md border border-border bg-page px-2 py-0.5 text-[10px] text-ink-tertiary">{group.serverLabel}</span> : null}
                                {!group.authorized ? <span className="rounded-md border border-warning/25 bg-warning-light px-2 py-0.5 text-[10px] text-warning">未同步</span> : null}
                                {!group.exists ? <span className="rounded-md border border-error/25 bg-error-light px-2 py-0.5 text-[10px] text-error">目录缺失</span> : null}
                              </div>
                            ) : null}

                            {group.chats.length === 0 ? (
                              <button
                                type="button"
                                onClick={() => handleNewWorkspaceChat(group)}
                                className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-[13px] text-ink-secondary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active"
                                title={group.effectivePath || group.path}
                              >
                                <Plus className="h-3.5 w-3.5 shrink-0" />
                                开始对话
                              </button>
                            ) : (
                              group.chats.map((meta) => (
                                <div key={meta.id} className="group flex items-center gap-1">
                                  <button
                                    type="button"
                                    onClick={() => handleSelectChat(meta.id)}
                                    title={meta.title}
                                    className={cn(
                                      "flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-2 text-left text-[13px] transition-colors duration-150",
                                      activeId === meta.id ? "bg-active text-ink" : "text-ink-secondary hover:bg-hover hover:text-ink active:bg-active",
                                    )}
                                  >
                                    <MessageSquare className="h-3.5 w-3.5 shrink-0" />
                                    <span className="min-w-0 flex-1 truncate">{meta.title}</span>
                                    <span className="text-[10px] text-ink-tertiary">{timeAgo(meta.updatedAt)}</span>
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => setDeleteChatId(meta.id)}
                                    className="hidden rounded-md p-1 text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-error group-hover:block"
                                    aria-label="删除对话"
                                  >
                                    <Trash2 className="h-3.5 w-3.5" />
                                  </button>
                                </div>
                              ))
                            )}
                          </div>
                        ) : null}
                      </section>
                    );
                  })}
                </>
              )}
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col items-center gap-1 overflow-y-auto px-1.5 py-3">
            <button
              type="button"
              onClick={openCreateWorkspaceDialog}
              className="inline-flex h-10 w-10 items-center justify-center rounded-md border border-border bg-white text-ink-secondary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active"
              title="新建目录"
            >
              <Plus className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={openImportWorkspaceDialog}
              className="inline-flex h-10 w-10 items-center justify-center rounded-md border border-border bg-white text-ink-secondary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active"
              title="导入目录"
            >
              <FolderOpen className="h-4 w-4" />
            </button>

            {defaultChats.slice(0, 8).map((meta) => (
              <button
                key={meta.id}
                type="button"
                onClick={() => handleSelectChat(meta.id)}
                className={cn(
                  "inline-flex h-10 w-10 items-center justify-center rounded-md border text-ink-secondary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active",
                  activeId === meta.id ? "border-primary/30 bg-active text-ink" : "border-transparent bg-transparent",
                )}
                title={meta.title}
              >
                <MessageSquare className="h-4 w-4" />
              </button>
            ))}

            {workspaceGroups.map((group) => (
              <button
                key={group.key}
                type="button"
                onClick={() => handleCollapsedGroupOpen(group)}
                className={cn(
                  "inline-flex h-10 w-10 items-center justify-center rounded-md border text-[12px] font-medium transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active",
                  group.chats.some((chat) => chat.id === activeId) ? "border-primary/30 bg-active text-ink" : "border-transparent bg-transparent text-ink-secondary",
                )}
                title={group.title}
              >
                {group.title.slice(0, 1).toUpperCase()}
              </button>
            ))}
          </div>
        )}

        <div className={cn("border-t border-border px-2 py-3", desktopCollapsed && "px-1.5")}>
          <div className={cn("mb-3", desktopCollapsed && "flex justify-center")}>
            <VisualStyleSwitcher collapsed={desktopCollapsed} />
          </div>

          <div className={cn("flex items-center gap-1", desktopCollapsed ? "flex-col" : "justify-between")}>
            <Link
              to="/settings"
              onClick={() => setMobileOpen(false)}
              className={cn(
                "theme-control inline-flex items-center rounded-md border border-border bg-white text-sm text-ink-secondary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active",
                desktopCollapsed ? "h-10 w-10 justify-center" : "gap-2 px-3 py-2",
                location.pathname.startsWith("/settings") && "bg-active text-ink",
              )}
              aria-label="打开设置"
              title="设置"
            >
              <Settings className="h-4 w-4 shrink-0" />
              {!desktopCollapsed ? <span>设置</span> : null}
            </Link>

            {desktopCollapsed ? (
              <button
                type="button"
                onClick={toggleDesktopCollapsed}
                className="theme-control hidden h-10 w-10 items-center justify-center rounded-md border border-border bg-white text-ink-secondary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active lg:inline-flex"
                aria-label="展开侧栏"
                title="展开侧栏"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            ) : null}
          </div>
        </div>
      </aside>

      <Modal
        open={!!workspaceDialog}
        onClose={() => !savingWorkspace && setWorkspaceDialog(null)}
        title={
          workspaceDialog?.mode === "rename"
            ? "重命名目录"
            : workspaceDialog?.mode === "create"
              ? "新建目录"
              : "导入现有目录"
        }
        maxWidth="md"
      >
        <div className="space-y-4">
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-ink">显示名称</span>
            <input
              value={workspaceDialog?.title || ""}
              onChange={(event) => setWorkspaceDialog((prev) => prev ? { ...prev, title: event.target.value } : prev)}
              placeholder={workspaceDialog?.mode === "create" ? "默认使用目录名称" : "目录标题"}
              className="form-input"
            />
          </label>
          {workspaceDialog?.mode === "create" ? (
            <>
              <label className="block">
                <span className="mb-2 block text-sm font-medium text-ink">目录名称</span>
                <input
                  value={workspaceDialog?.dirName || ""}
                  onChange={(event) => setWorkspaceDialog((prev) => prev ? { ...prev, dirName: event.target.value } : prev)}
                  placeholder="输入目录名称"
                  className="form-input"
                />
              </label>
            </>
          ) : (
            <label className="block">
              <span className="mb-2 block text-sm font-medium text-ink">目录路径</span>
              <input
                value={workspaceDialog?.path || ""}
                onChange={(event) => setWorkspaceDialog((prev) => prev ? { ...prev, path: event.target.value } : prev)}
                placeholder={defaultProjectsRoot || "D:\\Projects"}
                className="form-input"
                disabled={workspaceDialog?.mode === "rename"}
              />
            </label>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={() => setWorkspaceDialog(null)} className="rounded-lg border border-border px-4 py-2 text-sm text-ink-secondary transition hover:bg-hover" disabled={savingWorkspace}>取消</button>
            <button
              type="button"
              onClick={() => void handleSaveWorkspace()}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white transition hover:bg-primary-hover disabled:opacity-60"
              disabled={savingWorkspace || (workspaceDialog?.mode === "create" && !defaultProjectsRoot.trim())}
            >
              {savingWorkspace ? "保存中..." : "保存"}
            </button>
          </div>
        </div>
      </Modal>

      <ConfirmDialog open={!!deleteChatId} title="删除对话" description="删除后无法恢复，确定要删除这个对话吗？" variant="danger" confirmLabel="删除" onConfirm={() => { if (deleteChatId) { deleteConversation(deleteChatId); setDeleteChatId(null); } }} onCancel={() => setDeleteChatId(null)} />
      <ConfirmDialog open={!!removeWorkspaceTarget} title="移除目录" description="这会移除该目录及其对话入口，确定继续吗？" variant="danger" confirmLabel="移除" onConfirm={() => void handleRemoveWorkspace()} onCancel={() => setRemoveWorkspaceTarget(null)} />
    </>
  );
}

function deriveWorkspaceTitle(path: string): string {
  const cleaned = path.replace(/[\\/]+$/, "");
  const parts = cleaned.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path || "未命名项目";
}
