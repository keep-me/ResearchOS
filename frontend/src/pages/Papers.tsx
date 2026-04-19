/**
 * Papers - 论文库（分页 + 文件夹/日期分类导航）
 * @author Bamzc
 */
import { useEffect, useState, useCallback, useMemo, useRef, memo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button, Badge, Empty, Spinner, Modal } from "@/components/ui";
import { PaperListSkeleton } from "@/components/Skeleton";
import { useToast } from "@/contexts/ToastContext";
import { paperApi, topicApi, pipelineApi, actionApi, tasksApi, type FolderStats, type CollectionAction, type PaperKeywordFacet } from "@/services/api";
import ConfirmDialog from "@/components/ConfirmDialog";
import { formatDate, truncate } from "@/lib/utils";
import type { Paper, Topic } from "@/types";
import {
  FileText,
  Download,
  Search,
  RefreshCw,
  ExternalLink,
  BookOpen,
  Eye,
  BookMarked,
  ChevronRight,
  ChevronLeft,
  Cpu,
  Zap,
  CheckCircle2,
  Heart,
  LayoutGrid,
  LayoutList,
  Folder,
  FolderSearch,
  FolderOpen,
  FolderPlus,
  Clock,
  Inbox,
  Library,
  Tag,
  Calendar,
  ChevronsLeft,
  ChevronsRight,
  Bot,
  CalendarClock,
  ArrowUp,
  ArrowDown,
  TrendingUp,
  Trash2,
} from "lucide-react";

/* ========== 类型 ========== */
interface FolderItem {
  id: string;
  type: "special" | "topic" | "subscription" | "date";
  name: string;
  icon: React.ReactNode;
  count: number;
  color: string;
  /** 日期筛选用 */
  dateStr?: string;
}

const statusBadge: Record<string, { label: string; variant: "default" | "warning" | "success" }> = {
  unread: { label: "未读", variant: "default" },
  skimmed: { label: "已粗读", variant: "warning" },
  deep_read: { label: "已精读", variant: "success" },
};

/**
 * 格式化日期为中文标签
 */
function formatDateLabel(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  if (Number.isNaN(d.getTime())) return dateStr;
  const currentYear = new Date().getFullYear();
  const year = d.getFullYear();
  const m = d.getMonth() + 1;
  const day = d.getDate();
  return year === currentYear ? `${m}月${day}日` : `${year}年${m}月${day}日`;
}

export default function Papers() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { toast } = useToast();
  const [papers, setPapers] = useState<Paper[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchProgress, setBatchProgress] = useState("");
  const [batchPct, setBatchPct] = useState(0);
  const [selectAllLoading, setSelectAllLoading] = useState(false);
  const [viewMode, setViewMode] = useState<"list" | "grid">("list");
  const [confirmDeletePaperId, setConfirmDeletePaperId] = useState<string | null>(null);
  const [confirmBatchDeleteOpen, setConfirmBatchDeleteOpen] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [assignFolderOpen, setAssignFolderOpen] = useState(false);
  const [assignFolderId, setAssignFolderId] = useState("");
  const [folderOptions, setFolderOptions] = useState<Topic[]>([]);
  const [folderOptionsLoading, setFolderOptionsLoading] = useState(false);
  const [assignFolderLoading, setAssignFolderLoading] = useState(false);

  /* 分页 */
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);

  /* 文件夹相关 */
  const [folderStats, setFolderStats] = useState<FolderStats | null>(null);
  const [activeFolder, setActiveFolder] = useState("all");
  const [activeDate, setActiveDate] = useState<string | undefined>();
  const [statsLoading, setStatsLoading] = useState(true);
  /* 行动记录 */
  const [actionsList, setActionsList] = useState<CollectionAction[]>([]);
  const [actionSectionOpen, setActionSectionOpen] = useState(false);
  const [activeActionId, setActiveActionId] = useState<string | undefined>();
  const [deletingActionId, setDeletingActionId] = useState<string | null>(null);

  /* 搜索防抖 */
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const searchTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  /* 排序 + 状态筛选 */
  const [sortBy, setSortBy] = useState("created_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [statusFilter, setStatusFilter] = useState("");
  const [selectedKeywords, setSelectedKeywords] = useState<string[]>([]);
  const [keywordFacets, setKeywordFacets] = useState<PaperKeywordFacet[]>([]);
  const [keywordsLoading, setKeywordsLoading] = useState(false);

  useEffect(() => {
    clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => {
      setDebouncedSearch(searchTerm.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(searchTimerRef.current);
  }, [searchTerm]);

  const paperQueryContext = useMemo(() => {
    let folder: string | undefined;
    let topicId: string | undefined;

    if (activeFolder === "all") {
      // default
    } else if (activeFolder === "favorites" || activeFolder === "recent" || activeFolder === "unclassified") {
      folder = activeFolder;
    } else if (activeFolder.startsWith("folder:") || activeFolder.startsWith("subscription:")) {
      topicId = activeFolder.split(":")[1];
    }

    return {
      folder,
      topicId,
      date: activeDate,
      search: debouncedSearch || undefined,
      status: statusFilter || undefined,
    };
  }, [activeFolder, activeDate, debouncedSearch, statusFilter]);

  /* 加载文件夹统计 + 行动记录 */
  const loadFolderStats = useCallback(async () => {
    setStatsLoading(true);
    try {
      const [stats, actionsRes] = await Promise.all([
        paperApi.folderStats(),
        actionApi.list({ limit: 30 }).catch(() => ({ items: [] as CollectionAction[], total: 0 })),
      ]);
      setFolderStats(stats);
      setActionsList(actionsRes.items);
    } catch { toast("error", "加载文件夹统计失败"); } finally { setStatsLoading(false); }
  }, [toast]);

  /* 加载论文列表 */
  const loadPapers = useCallback(async () => {
    setLoading(true);
    try {
      // 按行动筛选走独立接口
      if (activeActionId) {
        const res = await actionApi.papers(activeActionId, 200);
        setPapers(res.items.map((p) => ({ ...p, abstract: "", metadata: {} } as unknown as Paper)));
        setTotal(res.items.length);
        setTotalPages(1);
        setSelected(new Set());
        setLoading(false);
        return;
      }

      const res = await paperApi.latest({
        page,
        pageSize,
        topicId: paperQueryContext.topicId,
        folder: paperQueryContext.folder,
        date: paperQueryContext.date,
        search: paperQueryContext.search,
        status: paperQueryContext.status,
        keywords: selectedKeywords,
        sortBy,
        sortOrder,
      });
      setPapers(res.items);
      setTotal(res.total);
      setTotalPages(res.total_pages);
      setSelected(new Set());
    } catch { toast("error", "加载论文列表失败"); } finally { setLoading(false); }
  }, [activeActionId, page, pageSize, paperQueryContext, selectedKeywords, sortBy, sortOrder, toast]);

  const loadKeywordFacets = useCallback(async () => {
    if (activeActionId) return;
    setKeywordsLoading(true);
    try {
      const res = await paperApi.keywordStats({
        topicId: paperQueryContext.topicId,
        folder: paperQueryContext.folder,
        date: paperQueryContext.date,
        search: paperQueryContext.search,
        status: paperQueryContext.status,
        limit: 24,
      });
      setKeywordFacets(res.items);
    } catch {
      setKeywordFacets([]);
    } finally {
      setKeywordsLoading(false);
    }
  }, [activeActionId, paperQueryContext]);

  useEffect(() => { loadFolderStats(); }, [loadFolderStats]);
  useEffect(() => { loadPapers(); }, [loadPapers]);
  useEffect(() => { if (!activeActionId) loadKeywordFacets(); }, [activeActionId, loadKeywordFacets]);
  useEffect(() => {
    if (!assignFolderOpen) return;
    let cancelled = false;
    setFolderOptionsLoading(true);
    topicApi.list(false, "folder")
      .then((res) => {
        if (!cancelled) {
          setFolderOptions(res.items || []);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFolderOptions([]);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setFolderOptionsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [assignFolderOpen]);

  useEffect(() => {
    if (!activeActionId) return;
    const counts = new Map<string, number>();
    for (const paper of papers) {
      for (const rawKeyword of paper.keywords || []) {
        const keyword = String(rawKeyword || "").trim();
        if (!keyword) continue;
        counts.set(keyword, (counts.get(keyword) || 0) + 1);
      }
    }
    const items = [...counts.entries()]
      .sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0]))
      .slice(0, 24)
      .map(([keyword, count]) => ({ keyword, count }));
    setKeywordFacets(items);
  }, [activeActionId, papers]);

  const specialFolders = useMemo((): FolderItem[] => {
    if (!folderStats) return [];
    const items: FolderItem[] = [
      { id: "all", type: "special", name: "全部论文", icon: <Library className="h-4 w-4" />, count: folderStats.total, color: "text-ink" },
      { id: "favorites", type: "special", name: "收藏", icon: <Heart className="h-4 w-4" />, count: folderStats.favorites, color: "text-red-500" },
      { id: "recent", type: "special", name: "最近 7 天", icon: <Clock className="h-4 w-4" />, count: folderStats.recent_7d, color: "text-info" },
    ];
    if (folderStats.unclassified > 0) {
      items.push({ id: "unclassified", type: "special", name: "未分类", icon: <Inbox className="h-4 w-4" />, count: folderStats.unclassified, color: "text-ink-tertiary" });
    }
    return items;
  }, [folderStats]);

  const customFolders = useMemo((): FolderItem[] => {
    if (!folderStats) return [];
    return folderStats.by_topic.map((topic) => ({
      id: `folder:${topic.topic_id}`,
      type: "topic" as const,
      name: topic.topic_name,
      icon: <Folder className="h-4 w-4" />,
      count: topic.count,
      color: "text-primary",
    }));
  }, [folderStats]);

  const subscriptionFolders = useMemo((): FolderItem[] => {
    if (!folderStats) return [];
    return folderStats.by_subscription.map((topic) => ({
      id: `subscription:${topic.topic_id}`,
      type: "subscription" as const,
      name: topic.topic_name,
      icon: <Bot className="h-4 w-4" />,
      count: topic.count,
      color: "text-amber-600",
    }));
  }, [folderStats]);

  /* 日期条目 */
  const dateEntries = useMemo(() => {
    if (!folderStats?.by_date) return [];
    return folderStats.by_date.map((d) => ({
      dateStr: d.date,
      label: formatDateLabel(d.date),
      count: d.count,
    }));
  }, [folderStats]);

  useEffect(() => {
    const topicId = searchParams.get("topicId");
    if (!topicId || !folderStats) return;
    const nextFolder = folderStats.by_topic.some((topic) => topic.topic_id === topicId)
      ? `folder:${topicId}`
      : folderStats.by_subscription.some((topic) => topic.topic_id === topicId)
        ? `subscription:${topicId}`
        : null;
    if (nextFolder && activeFolder !== nextFolder) {
      setActiveFolder(nextFolder);
      setActiveDate(undefined);
      setActiveActionId(undefined);
      setPage(1);
    }
  }, [activeFolder, folderStats, searchParams]);

  const filtered = useMemo(() => {
    if (!selectedKeywords.length) return papers;
    return papers.filter((paper) =>
      selectedKeywords.every((selectedKeyword) =>
        (paper.keywords || []).some(
          (keyword) => String(keyword).toLowerCase() === selectedKeyword.toLowerCase()
        )
      )
    );
  }, [papers, selectedKeywords]);

  const visibleTotal = activeActionId && selectedKeywords.length ? filtered.length : total;

  const toggleSelect = useCallback((id: string) => {
    setSelected((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }, []);
  const toggleSelectCurrentPage = useCallback(() => {
    setSelected((prev) => {
      const next = new Set(prev);
      const pageIds = filtered.map((p) => p.id);
      const allPageSelected = pageIds.length > 0 && pageIds.every((id) => next.has(id));
      if (allPageSelected) {
        for (const id of pageIds) next.delete(id);
      } else {
        for (const id of pageIds) next.add(id);
      }
      return next;
    });
  }, [filtered]);

  const handleSelectAllResults = useCallback(async () => {
    if (selectAllLoading) return;
    setSelectAllLoading(true);
    try {
      const allIds = new Set<string>();
      if (activeActionId) {
        const res = await actionApi.papers(activeActionId, 500);
        for (const item of res.items || []) {
          if (item.id) allIds.add(item.id);
        }
        if (visibleTotal > allIds.size) {
          toast("warning", `动作记录最多可一次选中 500 篇，当前已选中 ${allIds.size} 篇`);
        }
      } else {
        const pageSizeAll = 100;
        const first = await paperApi.latest({
          page: 1,
          pageSize: pageSizeAll,
          topicId: paperQueryContext.topicId,
          folder: paperQueryContext.folder,
          date: paperQueryContext.date,
          search: paperQueryContext.search,
          status: paperQueryContext.status,
          keywords: selectedKeywords,
          sortBy,
          sortOrder,
        });
        for (const item of first.items || []) {
          if (item.id) allIds.add(item.id);
        }

        const maxPages = Math.min(first.total_pages || 1, 50);
        for (let currentPage = 2; currentPage <= maxPages; currentPage += 1) {
          const next = await paperApi.latest({
            page: currentPage,
            pageSize: pageSizeAll,
            topicId: paperQueryContext.topicId,
            folder: paperQueryContext.folder,
            date: paperQueryContext.date,
            search: paperQueryContext.search,
            status: paperQueryContext.status,
            keywords: selectedKeywords,
            sortBy,
            sortOrder,
          });
          for (const item of next.items || []) {
            if (item.id) allIds.add(item.id);
          }
        }
        if ((first.total_pages || 1) > maxPages) {
          toast("warning", `结果过多，仅自动选中前 ${maxPages * pageSizeAll} 篇`);
        }
      }

      setSelected(allIds);
      toast("success", `已选中 ${allIds.size} 篇论文`);
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "选择全部失败");
    } finally {
      setSelectAllLoading(false);
    }
  }, [
    activeActionId,
    paperQueryContext,
    selectedKeywords,
    selectAllLoading,
    sortBy,
    sortOrder,
    toast,
    visibleTotal,
  ]);

  const handleToggleFavorite = useCallback(async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    /* 乐观更新 */
    setPapers((prev) => prev.map((p) => (p.id === id ? { ...p, favorited: !p.favorited } : p)));
    try {
      const res = await paperApi.toggleFavorite(id);
      setPapers((prev) => prev.map((p) => (p.id === res.id ? { ...p, favorited: res.favorited } : p)));
      loadFolderStats();
    } catch {
      toast("error", "收藏操作失败");
      setPapers((prev) => prev.map((p) => (p.id === id ? { ...p, favorited: !p.favorited } : p)));
    }
  }, [loadFolderStats, toast]);

  const handleDeletePaper = useCallback(async (paperId: string) => {
    setDeleteBusy(true);
    try {
      await paperApi.delete(paperId, true);
      setPapers((prev) => prev.filter((p) => p.id !== paperId));
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(paperId);
        return next;
      });
      toast("success", "论文已删除");
      await loadFolderStats();
      await loadPapers();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "删除论文失败");
    } finally {
      setDeleteBusy(false);
      setConfirmDeletePaperId(null);
    }
  }, [loadFolderStats, loadPapers, toast]);

  const handleBatchDelete = useCallback(async () => {
    const ids = [...selected];
    if (!ids.length) return;
    setDeleteBusy(true);
    try {
      const res = await paperApi.batchDelete(ids, true);
      setSelected(new Set());
      const deleted = res.deleted ?? 0;
      toast("success", `已删除 ${deleted} 篇论文`);
      await loadFolderStats();
      await loadPapers();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "批量删除失败");
    } finally {
      setDeleteBusy(false);
      setConfirmBatchDeleteOpen(false);
    }
  }, [selected, loadFolderStats, loadPapers, toast]);

  const handleAssignSelectedToFolder = useCallback(async () => {
    const ids = [...selected];
    if (!assignFolderId) {
      toast("error", "请先选择目标文件夹");
      return;
    }
    if (!ids.length) {
      toast("error", "请先选择至少一篇论文");
      return;
    }

    setAssignFolderLoading(true);
    let success = 0;
    let failed = 0;

    try {
      for (const paperId of ids) {
        try {
          await paperApi.addTopic(paperId, assignFolderId);
          success += 1;
        } catch {
          failed += 1;
        }
      }

      await loadFolderStats();
      await loadPapers();
      setAssignFolderOpen(false);
      setAssignFolderId("");
      setSelected(new Set());

      if (failed > 0) {
        toast("warning", `已加入 ${success} 篇，失败 ${failed} 篇`);
      } else {
        toast("success", `已把 ${success} 篇论文加入文件夹`);
      }
    } finally {
      setAssignFolderLoading(false);
    }
  }, [assignFolderId, loadFolderStats, loadPapers, selected, toast]);

  const handleBatchSkim = async () => {
    const ids = [...selected].filter((id) => { const p = papers.find((pp) => pp.id === id); return p && p.read_status === "unread"; });
    if (!ids.length) { setBatchProgress("没有可粗读的未读论文"); setBatchPct(0); return; }
    setBatchRunning(true); setBatchPct(0);
    const tid = `batch_skim_${Date.now()}`;
    tasksApi.track({ action: "start", task_id: tid, task_type: "batch_skim", title: `批量粗读 ${ids.length} 篇`, total: ids.length }).catch(() => {});
    let done = 0, failed = 0;
    for (const id of ids) {
      done++;
      setBatchProgress(`粗读中 ${done}/${ids.length}`);
      setBatchPct(Math.round((done / ids.length) * 100));
      tasksApi.track({ action: "update", task_id: tid, current: done, message: `粗读中 ${done}/${ids.length}` }).catch(() => {});
      try { await pipelineApi.skim(id); } catch { failed++; }
    }
    tasksApi.track({ action: "finish", task_id: tid, success: failed === 0, error: failed > 0 ? `${failed} 篇失败` : undefined }).catch(() => {});
    setBatchProgress(failed > 0 ? `完成 ${done - failed} 篇，${failed} 篇失败` : `完成 ${done} 篇`);
    setBatchPct(100);
    if (failed > 0) toast("warning", `${failed} 篇粗读失败`);
    else toast("success", `粗读完成 ${done} 篇`);
    setBatchRunning(false); await loadPapers();
  };

  const handleBatchDeep = async () => {
    const ids = [...selected].filter((id) => {
      const paper = papers.find((item) => item.id === id);
      return paper && paper.read_status !== "deep_read";
    });
    if (!ids.length) { setBatchProgress("当前选择里没有需要精读的论文"); setBatchPct(0); return; }
    setBatchRunning(true); setBatchPct(0);
    const tid = `batch_deep_${Date.now()}`;
    tasksApi.track({ action: "start", task_id: tid, task_type: "batch_deep", title: `批量精读 ${ids.length} 篇`, total: ids.length }).catch(() => {});
    let done = 0, failed = 0;
    for (const id of ids) {
      done++;
      setBatchProgress(`精读中 ${done}/${ids.length}`);
      setBatchPct(Math.round((done / ids.length) * 100));
      tasksApi.track({ action: "update", task_id: tid, current: done, message: `精读中 ${done}/${ids.length}` }).catch(() => {});
      try { await pipelineApi.deep(id); } catch { failed++; }
    }
    tasksApi.track({ action: "finish", task_id: tid, success: failed === 0, error: failed > 0 ? `${failed} 篇失败` : undefined }).catch(() => {});
    setBatchProgress(failed > 0 ? `完成 ${done - failed} 篇，${failed} 篇失败` : `完成 ${done} 篇`);
    setBatchPct(100);
    if (failed > 0) toast("warning", `${failed} 篇精读失败`);
    else toast("success", `精读完成 ${done} 篇`);
    setBatchRunning(false); await loadPapers();
  };

  const handleBatchEmbed = async () => {
    const ids = [...selected].filter((id) => { const p = papers.find((pp) => pp.id === id); return p && !p.has_embedding; });
    if (!ids.length) { setBatchProgress("已全部嵌入"); setBatchPct(0); return; }
    setBatchRunning(true); setBatchPct(0);
    const tid = `batch_embed_${Date.now()}`;
    tasksApi.track({ action: "start", task_id: tid, task_type: "batch_embed", title: `批量嵌入 ${ids.length} 篇`, total: ids.length }).catch(() => {});
    let done = 0, failed = 0;
    for (const id of ids) {
      done++;
      setBatchProgress(`嵌入中 ${done}/${ids.length}`);
      setBatchPct(Math.round((done / ids.length) * 100));
      tasksApi.track({ action: "update", task_id: tid, current: done, message: `嵌入中 ${done}/${ids.length}` }).catch(() => {});
      try { await pipelineApi.embed(id); } catch { failed++; }
    }
    tasksApi.track({ action: "finish", task_id: tid, success: failed === 0, error: failed > 0 ? `${failed} 篇失败` : undefined }).catch(() => {});
    setBatchProgress(failed > 0 ? `完成 ${done - failed} 篇，${failed} 篇失败` : `完成 ${done} 篇`);
    setBatchPct(100);
    if (failed > 0) toast("warning", `${failed} 篇嵌入失败`);
    else toast("success", `嵌入完成 ${done} 篇`);
    setBatchRunning(false); await loadPapers();
  };

  const handleFolderClick = useCallback((folderId: string) => {
    setActiveFolder(folderId);
    setActiveDate(undefined);
    setActiveActionId(undefined);
    setSearchTerm("");
    setPage(1);
  }, []);

  const handleDateClick = useCallback((dateStr: string) => {
    setActiveFolder("date:" + dateStr);
    setActiveDate(dateStr);
    setActiveActionId(undefined);
    setSearchTerm("");
    setPage(1);
  }, []);

  const toggleKeyword = useCallback((keyword: string) => {
    setSelectedKeywords((prev) =>
      prev.includes(keyword) ? prev.filter((item) => item !== keyword) : [...prev, keyword]
    );
    setPage(1);
  }, []);

  const activeFolderName = useMemo(() => {
    if (activeActionId) {
      return actionsList.find((action) => action.id === activeActionId)?.title || "收集记录";
    }
    if (activeDate) return formatDateLabel(activeDate) + " 收录";
    const allFolders = [...specialFolders, ...customFolders, ...subscriptionFolders];
    return allFolders.find((folder) => folder.id === activeFolder)?.name || "全部论文";
  }, [actionsList, activeActionId, activeDate, activeFolder, customFolders, specialFolders, subscriptionFolders]);

  const refresh = useCallback(async () => {
    await Promise.all([loadFolderStats(), loadPapers()]);
  }, [loadFolderStats, loadPapers]);

  const handleDeleteAction = useCallback(async (actionId: string) => {
    setDeletingActionId(actionId);
    try {
      await actionApi.delete(actionId);
      setActionsList((prev) => prev.filter((action) => action.id !== actionId));
      if (activeActionId === actionId) {
        setActiveActionId(undefined);
        setPage(1);
      }
      toast("success", "收集记录已删除");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "删除收集记录失败");
    } finally {
      setDeletingActionId(null);
    }
  }, [activeActionId, toast]);

  /* 分页导航 */
  const goPage = useCallback((p: number) => {
    setPage(Math.max(1, Math.min(p, totalPages)));
  }, [totalPages]);

  const renderFolderButton = useCallback((folder: FolderItem) => {
    const isActive = activeFolder === folder.id && !activeDate;
    return (
      <button
        key={folder.id}
        onClick={() => handleFolderClick(folder.id)}
        className={`group flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-sm transition-all ${
          isActive
            ? "bg-primary/10 font-medium text-primary"
            : "text-ink-secondary hover:bg-hover hover:text-ink"
        }`}
      >
        <span className={isActive ? "text-primary" : folder.color}>
          {isActive && folder.type === "topic"
            ? <FolderOpen className="h-4 w-4" />
            : folder.icon}
        </span>
        <span className="flex-1 truncate">{folder.name}</span>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
          isActive
            ? "bg-primary/15 text-primary"
            : "bg-page text-ink-tertiary"
        }`}>
          {folder.count}
        </span>
      </button>
    );
  }, [activeDate, activeFolder, handleFolderClick]);

  return (
    <div className="animate-fade-in">
      <main className="flex flex-1 flex-col overflow-visible rounded-[24px] border border-border/80 bg-surface sm:rounded-[28px]">
        {/* 头部 */}
        <div className="flex flex-col gap-3 border-b border-border/75 px-4 py-4 sm:px-5 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-lg font-bold text-ink">{activeFolderName}</h1>
              <span className="rounded-full border border-border bg-page px-2.5 py-0.5 text-xs font-medium text-ink-secondary">
                {visibleTotal} 篇
              </span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {/* 视图切换 */}
            <div className="flex rounded-[18px] border border-border bg-page p-0.5">
              <button
                aria-label="列表视图"
                onClick={() => setViewMode("list")}
                className={`rounded-md p-1.5 transition-colors ${viewMode === "list" ? "bg-primary/10 text-primary" : "text-ink-tertiary hover:text-ink"}`}
              >
                <LayoutList className="h-3.5 w-3.5" />
              </button>
              <button
                aria-label="网格视图"
                onClick={() => setViewMode("grid")}
                className={`rounded-md p-1.5 transition-colors ${viewMode === "grid" ? "bg-primary/10 text-primary" : "text-ink-tertiary hover:text-ink"}`}
              >
                <LayoutGrid className="h-3.5 w-3.5" />
              </button>
            </div>
            <Button variant="secondary" size="sm" icon={<RefreshCw className="h-3.5 w-3.5" />} onClick={refresh}>刷新</Button>
          </div>
        </div>

        {/* 搜索 + 排序筛选 */}
        <div className="flex flex-col gap-2 border-b border-border-light/80 px-4 py-3 sm:px-5 lg:flex-row lg:flex-wrap lg:items-center">
          <div className="relative max-w-sm flex-1">
            <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-tertiary" />
            <input
              type="text"
              placeholder="按标题、摘要、关键词、arXiv ID 搜索..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="h-9 w-full rounded-[16px] border border-border bg-surface pl-8 pr-3 text-xs text-ink placeholder:text-ink-placeholder focus:border-primary/30 focus:outline-none focus:ring-4 focus:ring-primary/10"
            />
          </div>

          {/* 排序筛选控件 */}
          {selected.size === 0 && (
            <div className="flex flex-wrap items-center gap-1.5 shrink-0">
              {/* 状态筛选 */}
              <div className="flex items-center rounded-[18px] border border-border bg-page p-0.5">
                {([
                  { value: "", label: "全部" },
                  { value: "unread", label: "未读" },
                  { value: "skimmed", label: "已粗读" },
                  { value: "deep_read", label: "已精读" },
                ] as { value: string; label: string }[]).map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => { setStatusFilter(opt.value); setPage(1); }}
                    className={`rounded-[14px] px-2.5 py-1 text-[11px] font-medium transition-colors ${statusFilter === opt.value ? "bg-surface text-ink shadow-sm" : "text-ink-tertiary hover:text-ink"}`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>

              {/* 分隔线 */}
              <div className="h-4 w-px bg-border-light/80" />

              {/* 排序字段 */}
              <select
                value={sortBy}
                onChange={(e) => { setSortBy(e.target.value); setPage(1); }}
                className="h-9 cursor-pointer rounded-[16px] border border-border bg-surface px-3 text-[11px] text-ink-secondary focus:border-primary/30 focus:outline-none focus:ring-4 focus:ring-primary/10"
              >
                <option value="created_at">入库时间</option>
                <option value="publication_date">发表时间</option>
                <option value="title">标题</option>
                <option value="impact">影响力（引用数）</option>
              </select>

              {/* 排序方向 */}
              <button
                onClick={() => { setSortOrder((o) => (o === "desc" ? "asc" : "desc")); setPage(1); }}
                className="flex h-9 items-center rounded-[18px] border border-border bg-page px-2.5 text-ink-tertiary transition-colors hover:text-ink"
                title="切换排序"
              >
                {sortOrder === "desc"
                  ? <ArrowDown className="h-3.5 w-3.5" />
                  : <ArrowUp className="h-3.5 w-3.5" />}
              </button>

            </div>
          )}

          {/* 批量操作 */}
          {selected.size > 0 && (
            <div className="glass-segment flex items-center gap-2 rounded-[20px] border-primary/20 bg-primary/6 px-3 py-1.5">
              <span className="text-xs font-medium text-primary">
                已选 {selected.size} 篇{selected.size >= visibleTotal ? "（当前筛选结果全集）" : ""}
              </span>
              <Button size="sm" variant="secondary" onClick={handleBatchSkim} disabled={batchRunning} icon={<Zap className="h-3 w-3" />}>粗读</Button>
              <Button size="sm" variant="secondary" onClick={handleBatchDeep} disabled={batchRunning} icon={<BookMarked className="h-3 w-3" />}>精读</Button>
              <Button size="sm" variant="secondary" onClick={handleBatchEmbed} disabled={batchRunning} icon={<Cpu className="h-3 w-3" />}>嵌入</Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setAssignFolderOpen(true)}
                disabled={batchRunning}
                icon={<FolderPlus className="h-3 w-3" />}
              >
                加入文件夹
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setConfirmBatchDeleteOpen(true)}
                disabled={batchRunning || deleteBusy}
                icon={<Trash2 className="h-3 w-3" />}
              >
                删除
              </Button>
              <button onClick={() => setSelected(new Set())} className="text-[10px] text-ink-tertiary hover:text-ink">取消</button>
              {batchProgress && (
                <div className="flex items-center gap-2">
                  {batchRunning && (
                    <div className="h-1.5 w-24 overflow-hidden rounded-full bg-border">
                      <div className="h-full rounded-full bg-primary transition-all duration-300" style={{ width: `${batchPct}%` }} />
                    </div>
                  )}
                  <span className="text-[10px] text-ink-secondary">{batchProgress}</span>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="border-b border-border-light px-4 py-4 sm:px-5">
          <div className="flex items-start gap-3">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-[16px] border border-border bg-page text-primary">
              <FolderSearch className="h-4 w-4" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold text-ink">范围筛选</span>
              </div>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <ScopeLabel label="常用范围" />
            {specialFolders.map((folder) => (
              <FilterChip
                key={folder.id}
                active={activeFolder === folder.id && !activeDate}
                label={folder.name}
                count={folder.count}
                onClick={() => handleFolderClick(folder.id)}
              />
            ))}

            {customFolders.length > 0 && (
              <>
                <ScopeLabel label="文件夹" />
                {customFolders.map((folder) => (
                  <FilterChip
                    key={folder.id}
                    active={activeFolder === folder.id && !activeDate}
                    label={folder.name}
                    count={folder.count}
                    onClick={() => handleFolderClick(folder.id)}
                  />
                ))}
              </>
            )}

            {subscriptionFolders.length > 0 && (
              <>
                <ScopeLabel label="订阅" />
                {subscriptionFolders.map((folder) => (
                  <FilterChip
                    key={folder.id}
                    active={activeFolder === folder.id && !activeDate}
                    label={folder.name}
                    count={folder.count}
                    onClick={() => handleFolderClick(folder.id)}
                  />
                ))}
              </>
            )}

          </div>
        </div>

        {actionsList.length > 0 && (
          <div className="border-b border-border-light px-4 py-4 sm:px-5">
            <button
              onClick={() => setActionSectionOpen(!actionSectionOpen)}
              className="flex w-full items-center gap-3 text-left"
            >
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-[16px] border border-border bg-page text-primary">
                <Download className="h-4 w-4" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-ink">收集记录</span>
                  <span className="rounded-full border border-border/70 bg-page/88 px-2 py-0.5 text-[10px] font-medium text-ink-tertiary">
                    {actionsList.length} 条
                  </span>
                </div>
              </div>
              <ChevronRight className={`h-4 w-4 text-ink-tertiary transition-transform ${actionSectionOpen ? "rotate-90" : ""}`} />
            </button>

            {actionSectionOpen && (
              <div className="mt-4 grid gap-2 xl:grid-cols-2">
                {actionsList.map((action) => {
                  const isActive = activeActionId === action.id;
                  return (
                    <div
                      key={action.id}
                      className={`group flex items-center gap-1 rounded-xl px-1 py-0.5 transition-all ${
                        isActive ? "bg-primary/10" : "hover:bg-hover"
                      }`}
                    >
                      <button
                        onClick={() => {
                          setActiveActionId(isActive ? undefined : action.id);
                          if (!isActive) {
                            setActiveFolder("all");
                            setActiveDate(undefined);
                          }
                          setPage(1);
                        }}
                        className={`flex min-w-0 flex-1 items-center gap-2 rounded-xl px-2 py-1.5 text-left text-[13px] transition-all ${
                          isActive
                            ? "font-medium text-primary"
                            : "text-ink-secondary hover:text-ink"
                        }`}
                      >
                        <ActionBadge type={action.action_type} />
                        <span className="min-w-0 flex-1 truncate">{action.title}</span>
                        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                          isActive ? "bg-primary/15 text-primary" : "bg-page text-ink-tertiary"
                        }`}>
                          {action.paper_count}
                        </span>
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleDeleteAction(action.id);
                        }}
                        disabled={deletingActionId === action.id}
                        className="rounded-lg p-1.5 text-ink-tertiary opacity-0 transition-all hover:bg-error/10 hover:text-error group-hover:opacity-100 disabled:opacity-100"
                        title="删除这条收集记录"
                      >
                        {deletingActionId === action.id ? (
                          <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Trash2 className="h-3.5 w-3.5" />
                        )}
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        <div className="border-b border-border-light px-4 py-4 sm:px-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex items-start gap-3">
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-[16px] border border-border bg-page text-primary">
                <Tag className="h-4 w-4" />
              </span>
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-ink">关键词筛选</span>
                  {selectedKeywords.length > 0 && (
                    <span className="rounded-full border border-primary/14 bg-primary/8 px-2 py-0.5 text-[10px] font-medium text-primary">
                      已选 {selectedKeywords.length}
                    </span>
                  )}
                </div>
              </div>
            </div>
            {selectedKeywords.length > 0 && (
              <button
                onClick={() => {
                  setSelectedKeywords([]);
                  setPage(1);
                }}
                className="rounded-full border border-border/70 bg-page/88 px-3 py-1 text-[11px] text-ink-secondary transition-colors hover:border-primary/18 hover:text-ink"
              >
                清空关键词
              </button>
            )}
          </div>

          {keywordsLoading ? (
            <div className="mt-3 flex items-center gap-2 text-xs text-ink-tertiary">
              <Spinner text="" />
              <span>加载关键词中...</span>
            </div>
          ) : keywordFacets.length > 0 ? (
            <div className="mt-3.5 flex flex-wrap gap-2">
              {keywordFacets.map((item) => {
                const active = selectedKeywords.includes(item.keyword);
                return (
                  <button
                    key={item.keyword}
                    onClick={() => toggleKeyword(item.keyword)}
                    className={`group/keyword min-w-[110px] rounded-[16px] border px-2.5 py-2 text-left transition-colors ${
                      active
                        ? "border-primary/28 bg-primary/10 text-primary"
                        : "border-border/70 bg-surface text-ink-secondary hover:border-primary/18 hover:text-ink"
                    }`}
                  >
                    <div className="flex items-start gap-2.5">
                      <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${active ? "bg-primary" : "bg-ink-tertiary/50"}`} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-start justify-between gap-2">
                          <span className={`truncate text-[10px] font-semibold ${active ? "text-primary" : "text-ink"}`}>
                            {item.keyword}
                          </span>
                          <span className={`shrink-0 rounded-full px-1.5 py-0.5 text-[8px] font-semibold ${
                            active
                              ? "bg-surface/86 text-primary"
                              : "bg-page/92 text-ink-tertiary"
                          }`}>
                            {item.count}
                          </span>
                        </div>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          ) : (
            <p className="mt-3 text-xs text-ink-tertiary">暂无关键词</p>
          )}
        </div>

        {/* 论文列表 */}
        <div className="overflow-visible">
          {loading ? (
            <div className="p-4">
              <PaperListSkeleton />
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex items-center justify-center py-16">
              <Empty
                icon={<FileText className="h-14 w-14" />}
                title={searchTerm ? "没有匹配的论文" : "该文件夹暂无论文"}
              />
            </div>
          ) : (
            <div className="p-4">
              {/* 全选 */}
              <div className="mb-2 flex flex-wrap items-center gap-2 px-1">
                <input
                  type="checkbox"
                  checked={filtered.length > 0 && filtered.every((paper) => selected.has(paper.id))}
                  onChange={toggleSelectCurrentPage}
                  className="h-3.5 w-3.5 rounded border-border text-primary focus:ring-primary/30"
                />
                <span className="text-[11px] text-ink-tertiary">
                  本页 {filtered.length} 篇 / 当前筛选共 {visibleTotal} 篇
                </span>
                {visibleTotal > filtered.length && (
                  <button
                    onClick={() => { void handleSelectAllResults(); }}
                    disabled={selectAllLoading || selected.size >= visibleTotal}
                    className="rounded-md border border-primary/30 px-2 py-1 text-[10px] text-primary transition-colors hover:bg-primary/10 disabled:opacity-60"
                  >
                    {selectAllLoading ? "正在加载全部..." : selected.size >= visibleTotal ? `已选中当前筛选全部 ${visibleTotal} 篇` : `选择当前筛选全部 ${visibleTotal} 篇`}
                  </button>
                )}
              </div>

              {viewMode === "list" ? (
                <div className="space-y-1.5">
                  {filtered.map((paper) => (
                    <PaperListItem
                      key={paper.id}
                      paper={paper}
                      selected={selected.has(paper.id)}
                      onSelect={() => toggleSelect(paper.id)}
                      onFavorite={(e) => handleToggleFavorite(e, paper.id)}
                      onDelete={(e) => {
                        e.stopPropagation();
                        setConfirmDeletePaperId(paper.id);
                      }}
                      onClick={() => navigate(`/papers/${paper.id}`)}
                    />
                  ))}
                </div>
              ) : (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {filtered.map((paper) => (
                    <PaperGridItem
                      key={paper.id}
                      paper={paper}
                      onFavorite={(e) => handleToggleFavorite(e, paper.id)}
                      onDelete={(e) => {
                        e.stopPropagation();
                        setConfirmDeletePaperId(paper.id);
                      }}
                      onClick={() => navigate(`/papers/${paper.id}`)}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* ========== 分页 ========== */}
        {totalPages > 1 && (
          <div className="flex flex-col gap-2 border-t border-border px-4 py-3 text-center sm:px-5 sm:text-left md:flex-row md:items-center md:justify-between">
            <span className="text-xs text-ink-tertiary">
              共 {total} 篇，第 {page}/{totalPages} 页
            </span>
            <div className="flex items-center gap-1">
              <button
                aria-label="首页"
                onClick={() => goPage(1)}
                disabled={page <= 1}
                className="rounded-lg p-1.5 text-ink-secondary transition-colors hover:bg-hover disabled:opacity-30"
                title="首页"
              >
                <ChevronsLeft className="h-4 w-4" />
              </button>
              <button
                aria-label="上一页"
                onClick={() => goPage(page - 1)}
                disabled={page <= 1}
                className="rounded-lg p-1.5 text-ink-secondary transition-colors hover:bg-hover disabled:opacity-30"
                title="上一页"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>

              {/* 页码按钮 */}
              {(() => {
                const items: (number | "dots")[] = [];
                const start = Math.max(1, page - 2);
                const end = Math.min(totalPages, page + 2);

                if (start > 1) items.push(1);
                if (start > 2) items.push("dots");
                for (let i = start; i <= end; i++) items.push(i);
                if (end < totalPages - 1) items.push("dots");
                if (end < totalPages) items.push(totalPages);

                return items.map((item, idx) => {
                  if (item === "dots") {
                    return <span key={`dots-${idx}`} className="px-1 text-xs text-ink-tertiary">...</span>;
                  }
                  return (
                    <button
                      key={item}
                      onClick={() => goPage(item)}
                      className={`min-w-[2rem] rounded-lg px-2 py-1.5 text-xs font-medium transition-colors ${
                        item === page
                          ? "bg-primary text-white"
                          : "text-ink-secondary hover:bg-hover"
                      }`}
                    >
                      {item}
                    </button>
                  );
                });
              })()}

              <button
                aria-label="下一页"
                onClick={() => goPage(page + 1)}
                disabled={page >= totalPages}
                className="rounded-lg p-1.5 text-ink-secondary transition-colors hover:bg-hover disabled:opacity-30"
                title="下一页"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
              <button
                aria-label="末页"
                onClick={() => goPage(totalPages)}
                disabled={page >= totalPages}
                className="rounded-lg p-1.5 text-ink-secondary transition-colors hover:bg-hover disabled:opacity-30"
                title="末页"
              >
                <ChevronsRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}
      </main>

      <ConfirmDialog
        open={!!confirmDeletePaperId}
        title="删除论文"
        description="此操作会删除论文记录并移除本地 PDF 文件，且不可恢复。"
        confirmLabel="删除"
        variant="danger"
        onCancel={() => setConfirmDeletePaperId(null)}
        onConfirm={async () => {
          if (!confirmDeletePaperId) return;
          await handleDeletePaper(confirmDeletePaperId);
        }}
      />

      <ConfirmDialog
        open={confirmBatchDeleteOpen}
        title="批量删除论文"
        description={`将删除已选中的 ${selected.size} 篇论文并移除本地 PDF 文件，且不可恢复。`}
        confirmLabel="批量删除"
        variant="danger"
        onCancel={() => setConfirmBatchDeleteOpen(false)}
        onConfirm={handleBatchDelete}
      />

      <Modal
        open={assignFolderOpen}
        onClose={() => {
          if (assignFolderLoading) return;
          setAssignFolderOpen(false);
          setAssignFolderId("");
        }}
        title="把选中论文加入文件夹"
      >
        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-page p-4 text-sm leading-6 text-ink-secondary">
            已选 {selected.size} 篇论文
          </div>
          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-ink">目标文件夹</label>
            <select
              value={assignFolderId}
              onChange={(e) => setAssignFolderId(e.target.value)}
              disabled={folderOptionsLoading || assignFolderLoading}
              className="h-10 w-full rounded-lg border border-border bg-surface px-3 text-sm text-ink focus:border-primary focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
            >
              <option value="">{folderOptionsLoading ? "正在加载文件夹..." : "请选择文件夹"}</option>
              {folderOptions.map((folder) => (
                <option key={folder.id} value={folder.id}>{folder.name}</option>
              ))}
            </select>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="secondary"
              onClick={() => {
                setAssignFolderOpen(false);
                setAssignFolderId("");
              }}
              disabled={assignFolderLoading}
            >
              取消
            </Button>
            <Button
              onClick={() => void handleAssignSelectedToFolder()}
              loading={assignFolderLoading}
              disabled={!assignFolderId || selected.size === 0}
            >
              加入文件夹
            </Button>
          </div>
        </div>
      </Modal>

    </div>
  );
}

/* ========== 论文卡片：列表模式 ========== */
const PaperListItem = memo(function PaperListItem({ paper, selected, onSelect, onFavorite, onDelete, onClick }: {
  paper: Paper;
  selected: boolean;
  onSelect: () => void;
  onFavorite: (e: React.MouseEvent) => void;
  onDelete: (e: React.MouseEvent) => void;
  onClick: () => void;
}) {
  const sc = statusBadge[paper.read_status] || statusBadge.unread;
  return (
    <div className={`group rounded-xl border bg-surface transition-all hover:shadow-sm ${
      selected ? "border-primary/30 ring-1 ring-primary/10" : "border-border/60"
    }`}>
      <div className="flex items-start gap-3 px-3.5 py-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onSelect}
          onClick={(e) => e.stopPropagation()}
          className="mt-1 h-3.5 w-3.5 shrink-0 rounded border-border text-primary focus:ring-primary/30"
        />
        <button className="flex min-w-0 flex-1 items-start gap-2.5 text-left" onClick={onClick}>
          {/* 状态图标 */}
          <div className={`mt-0.5 shrink-0 rounded-lg p-1.5 ${
            paper.read_status === "deep_read" ? "bg-success-light" :
            paper.read_status === "skimmed" ? "bg-warning-light" : "bg-page"
          }`}>
            {paper.read_status === "deep_read" ? <BookMarked className="h-3.5 w-3.5 text-success" /> :
             paper.read_status === "skimmed" ? <Eye className="h-3.5 w-3.5 text-warning" /> :
             <BookOpen className="h-3.5 w-3.5 text-ink-tertiary" />}
          </div>
          {/* 内容 */}
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex items-start gap-2">
              <h3 className="text-[13px] font-semibold leading-snug text-ink transition-colors group-hover:text-primary">
                {paper.title}
              </h3>
              <Badge variant={sc.variant} className="shrink-0">{sc.label}</Badge>
              {paper.has_embedding && (
                <span className="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-info-light px-1.5 py-0.5 text-[9px] font-medium text-info">
                  <CheckCircle2 className="h-2.5 w-2.5" /> 嵌入
                </span>
              )}
            </div>
            {paper.title_zh && <p className="text-[11px] text-ink-tertiary">{paper.title_zh}</p>}
            {paper.abstract && (
              <p className="text-[11px] leading-relaxed text-ink-secondary">{truncate(paper.abstract, 140)}</p>
            )}
            {/* 标签行 */}
            <div className="flex flex-wrap items-center gap-1">
              {paper.topics?.map((t) => (
                <span key={t} className="inline-flex items-center gap-0.5 rounded-md bg-primary/8 px-1.5 py-0.5 text-[9px] font-medium text-primary">
                  <Tag className="h-2 w-2" />{t}
                </span>
              ))}
              {paper.keywords?.slice(0, 3).map((kw) => (
                <span key={kw} className="rounded-md bg-page px-1.5 py-0.5 text-[9px] text-ink-tertiary">{kw}</span>
              ))}
            </div>
            {/* 元信息 */}
            <div className="flex items-center gap-3 text-[10px] text-ink-tertiary">
              {paper.arxiv_id && (
                <span className="flex items-center gap-0.5">
                  <ExternalLink className="h-2.5 w-2.5" />{paper.arxiv_id}
                </span>
              )}
              {paper.publication_date && <span>{formatDate(paper.publication_date)}</span>}
              {typeof paper.citation_count === "number" && (
                <span className="flex items-center gap-0.5">
                  <TrendingUp className="h-2.5 w-2.5" />
                  引用 {paper.citation_count.toLocaleString()}
                </span>
              )}
            </div>
          </div>
          <ChevronRight className="mt-2 h-3.5 w-3.5 shrink-0 text-ink-tertiary opacity-0 transition-opacity group-hover:opacity-100" />
        </button>
        <div className="mt-0.5 flex shrink-0 items-center gap-1">
          <button aria-label={paper.favorited ? "取消收藏" : "收藏"} onClick={onFavorite} className="rounded-lg p-1 transition-colors hover:bg-error/10">
            <Heart className={`h-3.5 w-3.5 ${paper.favorited ? "fill-red-500 text-red-500" : "text-ink-tertiary"}`} />
          </button>
          <button aria-label="删除论文" onClick={onDelete} className="rounded-lg p-1 text-ink-tertiary transition-colors hover:bg-error/10 hover:text-error">
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
});

/* ========== 论文卡片：网格模式 ========== */
const PaperGridItem = memo(function PaperGridItem({ paper, onFavorite, onDelete, onClick }: {
  paper: Paper;
  onFavorite: (e: React.MouseEvent) => void;
  onDelete: (e: React.MouseEvent) => void;
  onClick: () => void;
}) {
  const sc = statusBadge[paper.read_status] || statusBadge.unread;
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => e.key === "Enter" && onClick()}
      className="group flex cursor-pointer flex-col rounded-xl border border-border/60 bg-surface p-3.5 text-left transition-all hover:shadow-sm"
    >
      <div className="mb-2 flex items-center justify-between">
        <Badge variant={sc.variant}>{sc.label}</Badge>
        <div className="flex items-center gap-1">
          <button aria-label={paper.favorited ? "取消收藏" : "收藏"} onClick={onFavorite} className="rounded-lg p-1 transition-colors hover:bg-error/10">
            <Heart className={`h-3.5 w-3.5 ${paper.favorited ? "fill-red-500 text-red-500" : "text-ink-tertiary"}`} />
          </button>
          <button aria-label="删除论文" onClick={onDelete} className="rounded-lg p-1 text-ink-tertiary transition-colors hover:bg-error/10 hover:text-error">
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      <h3 className="line-clamp-2 text-[13px] font-semibold leading-snug text-ink transition-colors group-hover:text-primary">
        {paper.title}
      </h3>
      {paper.title_zh && <p className="mt-0.5 line-clamp-1 text-[11px] text-ink-tertiary">{paper.title_zh}</p>}
      {paper.abstract && <p className="mt-1.5 line-clamp-3 text-[11px] leading-relaxed text-ink-secondary">{truncate(paper.abstract, 100)}</p>}
      <div className="mt-auto pt-2.5">
        <div className="flex flex-wrap gap-1">
          {paper.topics?.slice(0, 2).map((t) => (
            <span key={t} className="inline-flex items-center gap-0.5 rounded-md bg-primary/8 px-1.5 py-0.5 text-[9px] font-medium text-primary">
              <Tag className="h-2 w-2" />{t}
            </span>
          ))}
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[10px] text-ink-tertiary">
          {paper.arxiv_id && <span>{paper.arxiv_id}</span>}
          {paper.publication_date && <span>{formatDate(paper.publication_date)}</span>}
          {typeof paper.citation_count === "number" && (
            <span className="inline-flex items-center gap-0.5">
              <TrendingUp className="h-2.5 w-2.5" />
              引用 {paper.citation_count.toLocaleString()}
            </span>
          )}
        </div>
      </div>
    </div>
  );
});

function ActionBadge({ type }: { type: string }) {
  const cls = "h-3 w-3 shrink-0";
  switch (type) {
    case "agent_collect": return <Bot className={`${cls} text-info`} />;
    case "auto_collect": return <CalendarClock className={`${cls} text-success`} />;
    case "manual_collect": return <Download className={`${cls} text-primary`} />;
    case "subscription_ingest": return <Tag className={`${cls} text-warning`} />;
    default: return <FileText className={`${cls} text-ink-tertiary`} />;
  }
}

function FilterChip({
  active,
  label,
  count,
  onClick,
}: {
  active: boolean;
  label: string;
  count: number;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[11px] font-medium transition-all ${
        active
          ? "border-primary/28 bg-primary/10 text-primary"
          : "border-border/70 bg-surface text-ink-secondary hover:border-primary/18 hover:text-ink"
      }`}
    >
      <span className="truncate">{label}</span>
      <span
        className={`rounded-full px-1.5 py-0.5 text-[9px] font-semibold ${
          active ? "bg-surface/86 text-primary" : "bg-page text-ink-tertiary"
        }`}
      >
        {count}
      </span>
    </button>
  );
}

function ScopeLabel({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center rounded-full border border-border/70 bg-page/70 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-tertiary">
      {label}
    </span>
  );
}
