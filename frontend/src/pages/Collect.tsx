import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Empty, Spinner } from "@/components/ui";
import ConfirmDialog from "@/components/ConfirmDialog";
import AIPromptHelper from "@/components/AIPromptHelper";
import { useToast } from "@/contexts/ToastContext";
import { ingestApi, paperApi, topicApi, type FolderStats } from "@/services/api";
import type {
  ArxivSortBy,
  ExternalLiteraturePaper,
  IngestResult,
  KeywordSuggestion,
  LiteratureVenueTier,
  LiteratureVenueType,
  PdfUploadResult,
  ScheduleFrequency,
  Topic,
  TopicCreate,
  TopicFetchResult,
  TopicPriorityMode,
  TopicSearchField,
  TopicSource,
} from "@/types";
import {
  AlertTriangle,
  ArrowUpDown,
  Calendar,
  CheckCircle2,
  Clock,
  Download,
  ExternalLink,
  FileText,
  Folder,
  FolderOpen,
  Hash,
  Library,
  Loader2,
  Pencil,
  Play,
  Plus,
  Power,
  PowerOff,
  RefreshCw,
  Rss,
  Search,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react";

type SearchResult = {
  query: string;
  displayQuery: string;
  searchField: TopicSearchField;
  priorityMode: TopicPriorityMode;
  source: Exclude<TopicSource, "manual">;
  targetFolderId: string;
  venueTier: LiteratureVenueTier;
  venueType: LiteratureVenueType;
  venueNames: string[];
  fromYear?: number;
  found: number;
  sourceCounts: Record<string, number>;
  skippedSources: string[];
  papers: ExternalLiteraturePaper[];
  selectedKeys: string[];
  importing: boolean;
  importResult: IngestResult | null;
  createdAt: string;
  expanded: boolean;
};

type UploadBatchSummary = {
  total: number;
  completed: number;
  created: number;
  updated: number;
  failed: Array<{ name: string; reason: string }>;
  items: PdfUploadResult[];
};

type SubscriptionFormState = {
  name: string;
  query: string;
  source: Exclude<TopicSource, "manual">;
  searchField: TopicSearchField;
  priorityMode: TopicPriorityMode;
  venueTier: LiteratureVenueTier;
  venueType: LiteratureVenueType;
  venueNameInput: string;
  fromYear: string;
  defaultFolderId: string;
  maxResults: number;
  frequency: ScheduleFrequency;
  timeBj: number;
  enabled: boolean;
  enableDateFilter: boolean;
  dateFilterDays: number;
};

const FREQ_OPTIONS: Array<{ value: ScheduleFrequency; label: string; desc: string }> = [
  { value: "daily", label: "每天", desc: "每天固定时间自动收集" },
  { value: "twice_daily", label: "每天两次", desc: "上午和下午各收集一次" },
  { value: "weekdays", label: "工作日", desc: "仅周一到周五执行" },
  { value: "weekly", label: "每周", desc: "每周固定时间执行" },
];

const FREQ_LABEL: Record<ScheduleFrequency, string> = {
  daily: "每天",
  twice_daily: "每天两次",
  weekdays: "工作日",
  weekly: "每周",
};

const SEARCH_FIELD_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "title", label: "标题" },
  { value: "keywords", label: "关键词" },
  { value: "authors", label: "作者" },
  { value: "arxiv_id", label: "arXiv ID" },
] satisfies Array<{ value: TopicSearchField; label: string }>;

const PRIORITY_OPTIONS = [
  { value: "relevance", label: "相关性" },
  { value: "time", label: "时间" },
  { value: "impact", label: "影响力" },
] satisfies Array<{ value: TopicPriorityMode; label: string }>;

const SOURCE_OPTIONS = [
  { value: "hybrid", label: "Hybrid" },
  { value: "openalex", label: "OpenAlex" },
  { value: "arxiv", label: "arXiv" },
] satisfies Array<{ value: Exclude<TopicSource, "manual">; label: string }>;

const VENUE_TIER_OPTIONS = [
  { value: "ccf_a", label: "CCF A" },
  { value: "all", label: "全部" },
] satisfies Array<{ value: LiteratureVenueTier; label: string }>;

const VENUE_TYPE_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "conference", label: "会议" },
  { value: "journal", label: "期刊" },
] satisfies Array<{ value: LiteratureVenueType; label: string }>;

function utcToBj(utcHour: number) {
  return (utcHour + 8) % 24;
}

function bjToUtc(bjHour: number) {
  return (bjHour - 8 + 24) % 24;
}

function hourOptions() {
  return Array.from({ length: 24 }, (_, index) => ({
    value: index,
    label: `${String(index).padStart(2, "0")}:00`,
  }));
}

function relativeTime(iso: string) {
  const date = new Date(iso);
  const diffMinutes = Math.floor((Date.now() - date.getTime()) / 60000);
  if (diffMinutes < 1) return "刚刚";
  if (diffMinutes < 60) return `${diffMinutes} 分钟前`;
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours} 小时前`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays} 天前`;
  return date.toLocaleDateString("zh-CN");
}

function splitArxivIds(value: string) {
  return Array.from(
    new Set(
      value
        .split(/[\s,;，；]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  );
}

function toPrioritySort(mode: TopicPriorityMode): ArxivSortBy {
  if (mode === "impact") return "impact";
  if (mode === "relevance") return "relevance";
  return "submittedDate";
}

function buildArxivQuery(query: string, field: TopicSearchField) {
  const normalized = query.trim().replace(/\s+/g, " ");
  if (!normalized) return "";
  const escaped = normalized.replace(/"/g, "");
  const structured = /(?:^|\s)(all|ti|au|abs|cat|jr|id_list):/i.test(normalized);
  if (field === "all") return structured ? normalized : `all:"${escaped}"`;
  if (field === "title") return `ti:"${escaped}"`;
  if (field === "keywords") return `abs:"${escaped}" OR all:"${escaped}"`;
  if (field === "authors") return `au:"${escaped}"`;
  return escaped;
}

function buildSearchQuery(query: string, field: TopicSearchField, source: Exclude<TopicSource, "manual">) {
  const trimmed = query.trim();
  if (!trimmed) return "";
  return source === "arxiv" ? buildArxivQuery(trimmed, field) : trimmed;
}

function normalizeVenueNames(value: string) {
  return Array.from(
    new Set(
      value
        .split(/[\n,;，；]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  );
}

function normalizeYearInput(value: string) {
  return value.replace(/[^\d]/g, "").slice(0, 4);
}

function resolveSearchFromYear(fromYearInput: string, dateFrom: string) {
  const candidates: number[] = [];
  if (fromYearInput.trim()) {
    const normalized = Number.parseInt(fromYearInput.trim(), 10);
    if (Number.isFinite(normalized)) candidates.push(normalized);
  }
  if (dateFrom) {
    const normalized = Number.parseInt(dateFrom.slice(0, 4), 10);
    if (Number.isFinite(normalized)) candidates.push(normalized);
  }
  if (!candidates.length) return undefined;
  return Math.max(...candidates);
}

function externalPaperKey(paper: ExternalLiteraturePaper) {
  return paper.openalex_id || paper.arxiv_id || paper.source_url || `${paper.title}:${paper.publication_date || paper.publication_year || ""}`;
}

function topicSourceLabel(source: TopicSource) {
  return SOURCE_OPTIONS.find((item) => item.value === source)?.label || (source === "manual" ? "手动" : source);
}

function priorityLabel(priorityMode: TopicPriorityMode) {
  return PRIORITY_OPTIONS.find((item) => item.value === priorityMode)?.label || priorityMode;
}

function searchFieldLabel(searchField: TopicSearchField) {
  return SEARCH_FIELD_OPTIONS.find((item) => item.value === searchField)?.label || searchField;
}

function venueTierLabel(venueTier: LiteratureVenueTier) {
  return VENUE_TIER_OPTIONS.find((item) => item.value === venueTier)?.label || venueTier;
}

function venueTypeLabel(venueType: LiteratureVenueType) {
  return VENUE_TYPE_OPTIONS.find((item) => item.value === venueType)?.label || venueType;
}

function defaultSubscriptionForm(): SubscriptionFormState {
  return {
    name: "",
    query: "",
    source: "hybrid",
    searchField: "all",
    priorityMode: "time",
    venueTier: "ccf_a",
    venueType: "all",
    venueNameInput: "",
    fromYear: "",
    defaultFolderId: "",
    maxResults: 20,
    frequency: "daily",
    timeBj: 9,
    enabled: true,
    enableDateFilter: false,
    dateFilterDays: 7,
  };
}

export default function Collect() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const fetchPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);

  const [topics, setTopics] = useState<Topic[]>([]);
  const [folderStats, setFolderStats] = useState<FolderStats | null>(null);
  const [loadingTopics, setLoadingTopics] = useState(true);
  const [error, setError] = useState("");
  const [fetchingTopicId, setFetchingTopicId] = useState<string | null>(null);

  const [searchInput, setSearchInput] = useState("");
  const [searchField, setSearchField] = useState<TopicSearchField>("all");
  const [priorityMode, setPriorityMode] = useState<TopicPriorityMode>("time");
  const [collectionSource, setCollectionSource] = useState<Exclude<TopicSource, "manual">>("hybrid");
  const [venueTier, setVenueTier] = useState<LiteratureVenueTier>("ccf_a");
  const [venueType, setVenueType] = useState<LiteratureVenueType>("all");
  const [venueNameInput, setVenueNameInput] = useState("");
  const [maxResults, setMaxResults] = useState(20);
  const [searchFolderId, setSearchFolderId] = useState("");
  const [searchFromYear, setSearchFromYear] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);

  const [arxivIdText, setArxivIdText] = useState("");
  const [arxivIdFolderId, setArxivIdFolderId] = useState("");
  const [arxivIdDownloadPdf, setArxivIdDownloadPdf] = useState(false);
  const [importingArxivIds, setImportingArxivIds] = useState(false);
  const [arxivIdResult, setArxivIdResult] = useState<IngestResult | null>(null);

  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploadTopicId, setUploadTopicId] = useState("");
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadArxivId, setUploadArxivId] = useState("");
  const [uploadingPdf, setUploadingPdf] = useState(false);
  const [uploadingLabel, setUploadingLabel] = useState("");
  const [uploadSummary, setUploadSummary] = useState<UploadBatchSummary | null>(null);

  const [folderDraftName, setFolderDraftName] = useState("");
  const [folderEditingId, setFolderEditingId] = useState<string | null>(null);
  const [folderEditingName, setFolderEditingName] = useState("");
  const [folderSaving, setFolderSaving] = useState(false);
  const [folderDeletingId, setFolderDeletingId] = useState<string | null>(null);

  const [showSubscriptionForm, setShowSubscriptionForm] = useState(false);
  const [editingSubscriptionId, setEditingSubscriptionId] = useState<string | null>(null);
  const [subscriptionForm, setSubscriptionForm] = useState<SubscriptionFormState>(defaultSubscriptionForm);
  const [subscriptionSaving, setSubscriptionSaving] = useState(false);
  const [subscriptionDeleteId, setSubscriptionDeleteId] = useState<string | null>(null);

  const [aiDesc, setAiDesc] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [suggestions, setSuggestions] = useState<KeywordSuggestion[]>([]);

  const loadTopics = useCallback(async () => {
    setLoadingTopics(true);
    try {
      const [response, stats] = await Promise.all([
        topicApi.list(false),
        paperApi.folderStats(),
      ]);
      setTopics(response.items);
      setFolderStats(stats);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载论文收集配置失败");
    } finally {
      setLoadingTopics(false);
    }
  }, []);

  useEffect(() => {
    void loadTopics();
    return () => {
      if (fetchPollRef.current) clearInterval(fetchPollRef.current);
    };
  }, [loadTopics]);

  const folderTopics = useMemo(() => topics.filter((item) => item.kind === "folder"), [topics]);
  const subscriptionTopics = useMemo(() => topics.filter((item) => item.kind === "subscription"), [topics]);
  const effectiveSearchFromYear = useMemo(() => resolveSearchFromYear(searchFromYear, dateFrom), [searchFromYear, dateFrom]);
  const arxivVenueFiltersDisabled = collectionSource === "arxiv";

  useEffect(() => {
    if (!arxivVenueFiltersDisabled) return;
    setVenueTier("all");
    setVenueType("all");
    setVenueNameInput("");
  }, [arxivVenueFiltersDisabled]);

  const stats = useMemo(() => ({
    folders: folderTopics.length,
    papers: folderStats?.total ?? folderTopics.reduce((sum, item) => sum + (item.paper_count || 0), 0),
    subscriptions: subscriptionTopics.length,
    enabled: subscriptionTopics.filter((item) => item.enabled).length,
  }), [folderStats?.total, folderTopics, subscriptionTopics]);

  const resetSubscriptionForm = useCallback(() => {
    setEditingSubscriptionId(null);
    setSubscriptionForm(defaultSubscriptionForm());
    setShowSubscriptionForm(false);
    setAiDesc("");
    setSuggestions([]);
  }, []);

  const openEditSubscription = useCallback((topic: Topic) => {
    setSearchInput(topic.query);
    setSearchField(topic.search_field);
    setPriorityMode(topic.priority_mode);
    setCollectionSource((topic.source === "manual" ? "arxiv" : topic.source) as Exclude<TopicSource, "manual">);
    setVenueTier(topic.venue_tier || "all");
    setVenueType(topic.venue_type || "all");
    setVenueNameInput((topic.venue_names || []).join(", "));
    setSearchFromYear(topic.from_year ? String(topic.from_year) : "");
    setSearchFolderId(topic.default_folder_id || "");
    setMaxResults(topic.max_results_per_run);
    setDateFrom("");
    setDateTo("");
    setEditingSubscriptionId(topic.id);
    setSubscriptionForm({
      name: topic.name,
      query: topic.query,
      source: (topic.source === "manual" ? "arxiv" : topic.source) as Exclude<TopicSource, "manual">,
      searchField: topic.search_field,
      priorityMode: topic.priority_mode,
      venueTier: topic.venue_tier || "all",
      venueType: topic.venue_type || "all",
      venueNameInput: (topic.venue_names || []).join(", "),
      fromYear: topic.from_year ? String(topic.from_year) : "",
      defaultFolderId: topic.default_folder_id || "",
      maxResults: topic.max_results_per_run,
      frequency: topic.schedule_frequency,
      timeBj: utcToBj(topic.schedule_time_utc ?? 21),
      enabled: topic.enabled,
      enableDateFilter: topic.enable_date_filter,
      dateFilterDays: topic.date_filter_days || 7,
    });
    setShowSubscriptionForm(true);
    setAiDesc(topic.name);
    setSuggestions([]);
  }, []);

  const prefillSubscriptionFromSearch = useCallback(() => {
    const trimmed = searchInput.trim();
    if (!trimmed) return;
    setEditingSubscriptionId(null);
    setSubscriptionForm({
      name: trimmed,
      query: buildSearchQuery(trimmed, searchField, collectionSource),
      source: collectionSource,
      searchField,
      priorityMode,
      venueTier,
      venueType,
      venueNameInput,
      fromYear: effectiveSearchFromYear ? String(effectiveSearchFromYear) : "",
      defaultFolderId: searchFolderId,
      maxResults,
      frequency: "daily",
      timeBj: 9,
      enabled: true,
      enableDateFilter: Boolean(dateFrom || dateTo),
      dateFilterDays: 7,
    });
    setShowSubscriptionForm(true);
    setAiDesc(trimmed);
    setSuggestions([]);
  }, [collectionSource, dateFrom, dateTo, effectiveSearchFromYear, maxResults, priorityMode, searchField, searchFolderId, searchInput, venueNameInput, venueTier, venueType]);

  const handleSearch = useCallback(async () => {
    const trimmed = searchInput.trim();
    if (!trimmed) {
      toast("warning", "请先输入检索内容");
      return;
    }
    setSearching(true);
    setError("");
    try {
      const query = buildSearchQuery(trimmed, searchField, collectionSource);
      const venueNames = normalizeVenueNames(venueNameInput);
      const result = await ingestApi.searchLiterature({
        query,
        max_results: maxResults,
        source_scope: collectionSource,
        sort_mode: priorityMode,
        venue_tier: venueTier,
        venue_type: venueType,
        venue_names: venueNames,
        from_year: effectiveSearchFromYear,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      });
      const papers = result.papers || [];
      setSearchResults((current) => [
        {
          query,
          displayQuery: trimmed,
          searchField,
          priorityMode,
          source: collectionSource,
          targetFolderId: searchFolderId,
          venueTier,
          venueType,
          venueNames,
          fromYear: result.filters?.from_year || effectiveSearchFromYear,
          found: result.count || papers.length,
          sourceCounts: result.source_counts || {},
          skippedSources: result.skipped_sources || [],
          papers,
          selectedKeys: papers.map((paper) => externalPaperKey(paper)),
          importing: false,
          importResult: null,
          createdAt: new Date().toISOString(),
          expanded: true,
        },
        ...current.map((item) => ({ ...item, expanded: false })),
      ]);
      toast(papers.length > 0 ? "success" : "info", papers.length > 0 ? `找到 ${papers.length} 篇候选论文` : "没有找到符合条件的论文");
    } catch (err) {
      setError(err instanceof Error ? err.message : "即时搜索失败");
    } finally {
      setSearching(false);
    }
  }, [collectionSource, dateFrom, dateTo, effectiveSearchFromYear, maxResults, priorityMode, searchField, searchFolderId, searchInput, toast, venueNameInput, venueTier, venueType]);

  const handleToggleResultSelection = useCallback((resultIndex: number, paperKey: string) => {
    setSearchResults((current) => current.map((result, index) => {
      if (index !== resultIndex) return result;
      const selected = new Set(result.selectedKeys);
      if (selected.has(paperKey)) selected.delete(paperKey);
      else selected.add(paperKey);
      return { ...result, selectedKeys: Array.from(selected) };
    }));
  }, []);

  const handleSelectAllResultPapers = useCallback((resultIndex: number, selected: boolean) => {
    setSearchResults((current) => current.map((result, index) => {
      if (index !== resultIndex) return result;
      return {
        ...result,
        selectedKeys: selected ? result.papers.map((paper) => externalPaperKey(paper)) : [],
      };
    }));
  }, []);

  const handleImportSearchResult = useCallback(async (resultIndex: number, mode: "selected" | "all") => {
    const currentResult = searchResults[resultIndex];
    if (!currentResult) return;
    const entries = mode === "all"
      ? currentResult.papers
      : currentResult.papers.filter((paper) => currentResult.selectedKeys.includes(externalPaperKey(paper)));
    if (!entries.length) {
      toast("warning", "请先选择要导入的论文");
      return;
    }
    setSearchResults((current) => current.map((result, index) => index === resultIndex ? { ...result, importing: true } : result));
    try {
      const importResult = await ingestApi.importLiterature({
        entries,
        topic_id: currentResult.targetFolderId || undefined,
      });
      setSearchResults((current) => current.map((result, index) => {
        if (index !== resultIndex) return result;
        return {
          ...result,
          importing: false,
          importResult,
        };
      }));
      toast(
        importResult.ingested > 0 ? "success" : "info",
        importResult.ingested > 0
          ? `已入库 ${importResult.ingested} 篇论文`
          : `没有新增论文${typeof importResult.duplicates === "number" && importResult.duplicates > 0 ? `，重复 ${importResult.duplicates} 篇` : ""}`,
      );
      await loadTopics();
    } catch (err) {
      setSearchResults((current) => current.map((result, index) => index === resultIndex ? { ...result, importing: false } : result));
      setError(err instanceof Error ? err.message : "外部论文导入失败");
    }
  }, [loadTopics, searchResults, toast]);

  const handleImportArxivIds = useCallback(async () => {
    const ids = splitArxivIds(arxivIdText);
    if (!ids.length) {
      toast("warning", "请至少输入一个 arXiv ID");
      return;
    }
    setImportingArxivIds(true);
    try {
      const result = await ingestApi.arxivIds(ids, arxivIdFolderId || undefined, arxivIdDownloadPdf);
      setArxivIdResult(result);
      setArxivIdText("");
      toast("success", `已处理 ${result.ingested || 0} 篇论文`);
      await loadTopics();
    } catch (err) {
      setError(err instanceof Error ? err.message : "按 arXiv ID 导入失败");
    } finally {
      setImportingArxivIds(false);
    }
  }, [arxivIdDownloadPdf, arxivIdFolderId, arxivIdText, loadTopics, toast]);

  const handleUploadPdf = useCallback(async () => {
    if (!uploadFiles.length) {
      toast("warning", "请先选择要入库的 PDF 文件");
      return;
    }
    const summary: UploadBatchSummary = { total: uploadFiles.length, completed: 0, created: 0, updated: 0, failed: [], items: [] };
    setUploadingPdf(true);
    setUploadingLabel("");
    setUploadSummary(summary);
    try {
      for (const file of uploadFiles) {
        setUploadingLabel(file.name);
        try {
          const result = await paperApi.uploadPdf({
            file,
            title: uploadFiles.length === 1 ? uploadTitle.trim() || undefined : undefined,
            arxivId: uploadFiles.length === 1 ? uploadArxivId.trim() || undefined : undefined,
            topicId: uploadTopicId || undefined,
          });
          summary.items.push(result);
          if (result.created) summary.created += 1;
          else summary.updated += 1;
        } catch (err) {
          summary.failed.push({ name: file.name, reason: err instanceof Error ? err.message : "未知错误" });
        } finally {
          summary.completed += 1;
          setUploadSummary({ ...summary });
        }
      }
      setUploadFiles([]);
      setUploadTitle("");
      setUploadArxivId("");
      toast(summary.failed.length > 0 ? "warning" : "success", summary.failed.length > 0 ? `成功 ${summary.created + summary.updated}，失败 ${summary.failed.length}` : `已完成 ${summary.completed} 份 PDF 入库`);
      await loadTopics();
    } finally {
      setUploadingPdf(false);
      setUploadingLabel("");
    }
  }, [loadTopics, toast, uploadArxivId, uploadFiles, uploadTitle, uploadTopicId]);

  const mergeUploadFiles = useCallback((incoming: File[]) => {
    if (!incoming.length) return;
    const pdfFiles = incoming.filter((file) => file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf"));
    if (!pdfFiles.length) {
      toast("warning", "仅支持 PDF 文件入库");
      return;
    }
    if (pdfFiles.length !== incoming.length) {
      toast("warning", "已自动忽略非 PDF 文件");
    }
    setUploadFiles((current) => {
      const seen = new Set(current.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
      const next = [...current];
      for (const file of pdfFiles) {
        const key = `${file.name}:${file.size}:${file.lastModified}`;
        if (seen.has(key)) continue;
        seen.add(key);
        next.push(file);
      }
      return next;
    });
    setUploadSummary(null);
  }, [toast]);

  const handleUploadInputChange = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    mergeUploadFiles(Array.from(event.target.files || []));
    event.target.value = "";
  }, [mergeUploadFiles]);

  const handleUploadDrop = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    mergeUploadFiles(Array.from(event.dataTransfer.files || []));
  }, [mergeUploadFiles]);

  const handleCreateFolder = useCallback(async () => {
    const name = folderDraftName.trim();
    if (!name) {
      toast("warning", "请输入文件夹名称");
      return;
    }
    setFolderSaving(true);
    try {
      await topicApi.create({ name, kind: "folder", enabled: false, source: "manual", query: "" });
      setFolderDraftName("");
      toast("success", "文件夹已创建");
      await loadTopics();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建文件夹失败");
    } finally {
      setFolderSaving(false);
    }
  }, [folderDraftName, loadTopics, toast]);

  const handleRenameFolder = useCallback(async () => {
    if (!folderEditingId) return;
    const name = folderEditingName.trim();
    if (!name) {
      toast("warning", "请输入文件夹名称");
      return;
    }
    setFolderSaving(true);
    try {
      await topicApi.update(folderEditingId, { name });
      setFolderEditingId(null);
      setFolderEditingName("");
      toast("success", "文件夹名称已更新");
      await loadTopics();
    } catch (err) {
      setError(err instanceof Error ? err.message : "重命名文件夹失败");
    } finally {
      setFolderSaving(false);
    }
  }, [folderEditingId, folderEditingName, loadTopics, toast]);

  const handleDeleteFolder = useCallback(async (folderId: string) => {
    try {
      await topicApi.delete(folderId);
      setFolderDeletingId(null);
      toast("success", "文件夹已删除");
      await loadTopics();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除文件夹失败");
    }
  }, [loadTopics, toast]);

  const handleAiSuggest = useCallback(async () => {
    const description = aiDesc.trim() || searchInput.trim() || subscriptionForm.name.trim();
    if (!description) {
      toast("warning", "请先描述你的研究方向");
      return;
    }
    setAiLoading(true);
    try {
      const result = await topicApi.suggestKeywords(description, {
        source_scope: collectionSource,
        search_field: searchField,
      });
      setSuggestions(result.suggestions || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "AI 建议失败");
    } finally {
      setAiLoading(false);
    }
  }, [aiDesc, collectionSource, searchField, searchInput, subscriptionForm.name, toast]);

  const applySuggestion = useCallback((suggestion: KeywordSuggestion) => {
    setSearchInput(suggestion.query);
    setAiDesc(suggestion.name);
    setSearchField("all");
    setSubscriptionForm((current) => ({ ...current, name: suggestion.name, query: suggestion.query, searchField: "all" }));
    setSuggestions([]);
  }, []);

  const handleSaveSubscription = useCallback(async () => {
    const name = subscriptionForm.name.trim() || searchInput.trim();
    const query = (
      buildSearchQuery(searchInput.trim(), searchField, collectionSource).trim()
      || subscriptionForm.query.trim()
    );
    if (!name || !query) {
      toast("warning", "请填写订阅名称和检索表达式");
      return;
    }
    setSubscriptionSaving(true);
    const payload: TopicCreate = {
      name,
      kind: "subscription",
      query,
      source: collectionSource,
      search_field: searchField,
      priority_mode: priorityMode,
      venue_tier: venueTier,
      venue_type: venueType,
      venue_names: normalizeVenueNames(venueNameInput),
      from_year: effectiveSearchFromYear ?? null,
      sort_by: toPrioritySort(priorityMode),
      default_folder_id: searchFolderId || null,
      enabled: subscriptionForm.enabled,
      max_results_per_run: maxResults,
      schedule_frequency: subscriptionForm.frequency,
      schedule_time_utc: bjToUtc(subscriptionForm.timeBj),
      enable_date_filter: subscriptionForm.enableDateFilter,
      date_filter_days: subscriptionForm.dateFilterDays,
    };
    try {
      if (editingSubscriptionId) {
        await topicApi.update(editingSubscriptionId, payload);
        toast("success", "订阅已更新");
      } else {
        await topicApi.create(payload);
        toast("success", "订阅已创建");
      }
      resetSubscriptionForm();
      await loadTopics();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存订阅失败");
    } finally {
      setSubscriptionSaving(false);
    }
  }, [
    collectionSource,
    editingSubscriptionId,
    effectiveSearchFromYear,
    loadTopics,
    maxResults,
    priorityMode,
    resetSubscriptionForm,
    searchField,
    searchFolderId,
    searchInput,
    subscriptionForm,
    toast,
    venueNameInput,
    venueTier,
    venueType,
  ]);

  const handleToggleSubscription = useCallback(async (topic: Topic) => {
    try {
      await topicApi.update(topic.id, { enabled: !topic.enabled });
      await loadTopics();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "切换订阅状态失败");
    }
  }, [loadTopics, toast]);

  const handleDeleteSubscription = useCallback(async (topicId: string) => {
    try {
      await topicApi.delete(topicId);
      setSubscriptionDeleteId(null);
      toast("success", "订阅已删除");
      await loadTopics();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "删除订阅失败");
    }
  }, [loadTopics, toast]);

  const handleManualFetch = useCallback(async (topicId: string) => {
    setFetchingTopicId(topicId);
    try {
      const result: TopicFetchResult = await topicApi.fetch(topicId);
      if (result.status === "started" || result.status === "already_running") {
        if (fetchPollRef.current) clearInterval(fetchPollRef.current);
        fetchPollRef.current = setInterval(async () => {
          const status = await topicApi.fetchStatus(topicId);
          if (status.status === "running") return;
          if (fetchPollRef.current) {
            clearInterval(fetchPollRef.current);
            fetchPollRef.current = null;
          }
          setFetchingTopicId(null);
          await loadTopics();
          if (status.status === "ok") toast("success", `抓取完成，新增 ${status.inserted || 0} 篇论文`);
          else if (status.status === "no_new_papers") toast("info", "本次没有发现新的论文");
          else if (status.status === "failed") toast("error", status.error || "抓取失败");
        }, 3000);
        return;
      }
      await loadTopics();
    } catch (err) {
      setError(err instanceof Error ? err.message : "抓取失败");
    } finally {
      setFetchingTopicId(null);
    }
  }, [loadTopics, toast]);

  return (
    <div className="animate-fade-in space-y-6 pb-8">
      <section className="flex flex-col gap-3 rounded-[24px] border border-border/70 bg-surface/88 px-5 py-4 shadow-[0_20px_48px_-40px_rgba(0,0,0,0.42)] backdrop-blur-xl sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="inline-flex rounded-2xl bg-primary/10 p-2.5 text-primary">
            <Download className="h-4 w-4" />
          </div>
          <h1 className="text-2xl font-semibold tracking-[-0.04em] text-ink">论文收集</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <MetricPill label="文件夹" value={stats.folders} />
          <MetricPill label="论文" value={stats.papers} />
          <MetricPill label="订阅" value={stats.subscriptions} />
          <MetricPill label="运行中" value={stats.enabled} />
        </div>
      </section>

      {error && (
        <div className="flex items-center gap-3 rounded-2xl border border-error/20 bg-error-light px-4 py-3 text-sm text-error">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <p className="flex-1">{error}</p>
          <button type="button" onClick={() => setError("")} className="rounded-lg p-1 hover:bg-error/10">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      <section className="rounded-[28px] border border-border bg-surface p-6 shadow-sm">
        <div className="mb-5 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Search className="h-4 w-4 text-primary" />
            <h2 className="text-lg font-semibold text-ink">检索与订阅</h2>
          </div>
        </div>

        <div className="space-y-4">
          <div className="relative">
            <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-tertiary" />
            <input
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void handleSearch();
                }
              }}
              placeholder="输入关键词或论文编号"
              className="h-12 w-full rounded-2xl border border-border bg-page pl-11 pr-4 text-sm text-ink outline-none transition focus:border-primary/30 focus:ring-4 focus:ring-primary/10"
            />
          </div>

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <FieldBlock label="搜索字段" icon={<Hash className="h-3.5 w-3.5" />}>
              <select value={searchField} onChange={(event) => setSearchField(event.target.value as TopicSearchField)} className="form-input">
                {SEARCH_FIELD_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </FieldBlock>

            <FieldBlock label="收集优先级" icon={<ArrowUpDown className="h-3.5 w-3.5" />}>
              <select value={priorityMode} onChange={(event) => setPriorityMode(event.target.value as TopicPriorityMode)} className="form-input">
                {PRIORITY_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </FieldBlock>

            <FieldBlock label="检索源" icon={<Library className="h-3.5 w-3.5" />}>
              <select value={collectionSource} onChange={(event) => setCollectionSource(event.target.value as Exclude<TopicSource, "manual">)} className="form-input">
                {SOURCE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </FieldBlock>

            <FieldBlock label="质量层级" icon={<Sparkles className="h-3.5 w-3.5" />}>
              <select
                value={venueTier}
                onChange={(event) => setVenueTier(event.target.value as LiteratureVenueTier)}
                className="form-input"
                disabled={arxivVenueFiltersDisabled}
              >
                {VENUE_TIER_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </FieldBlock>

            <FieldBlock label="论文类型" icon={<Library className="h-3.5 w-3.5" />}>
              <select
                value={venueType}
                onChange={(event) => setVenueType(event.target.value as LiteratureVenueType)}
                className="form-input"
                disabled={arxivVenueFiltersDisabled}
              >
                {VENUE_TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </FieldBlock>

            <FieldBlock label="最大数量" icon={<Hash className="h-3.5 w-3.5" />}>
              <select value={maxResults} onChange={(event) => setMaxResults(Number(event.target.value))} className="form-input">
                {[10, 20, 50, 100].map((item) => <option key={item} value={item}>{item} 篇</option>)}
              </select>
            </FieldBlock>

            <FieldBlock label="默认归档文件夹" icon={<Folder className="h-3.5 w-3.5" />}>
              <select value={searchFolderId} onChange={(event) => setSearchFolderId(event.target.value)} className="form-input">
                <option value="">主论文库</option>
                {folderTopics.map((topic) => <option key={topic.id} value={topic.id}>{topic.name}</option>)}
              </select>
            </FieldBlock>

            <FieldBlock label="收录日期范围" icon={<Calendar className="h-3.5 w-3.5" />}>
              <div className="grid grid-cols-2 gap-2">
                <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} className="form-input" />
                <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} className="form-input" />
              </div>
            </FieldBlock>

            <FieldBlock label="起始年份" icon={<Calendar className="h-3.5 w-3.5" />}>
              <input value={searchFromYear} onChange={(event) => setSearchFromYear(normalizeYearInput(event.target.value))} placeholder="输入年份" className="form-input" />
            </FieldBlock>

            <FieldBlock label="指定 venue" icon={<Rss className="h-3.5 w-3.5" />}>
              <input
                value={venueNameInput}
                onChange={(event) => setVenueNameInput(event.target.value)}
                placeholder={arxivVenueFiltersDisabled ? "arXiv 模式不支持 venue 过滤" : "输入 venue"}
                className="form-input"
                disabled={arxivVenueFiltersDisabled}
              />
            </FieldBlock>
          </div>

          {arxivVenueFiltersDisabled && (
            <div className="rounded-2xl border border-border/70 bg-page/70 px-4 py-3 text-xs text-ink-tertiary">
              当前为 `arXiv` 检索源，仅支持关键词、时间、年份和日期范围过滤，不支持 CCF/会议/期刊/venue 名称过滤。
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <Button icon={searching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />} onClick={() => void handleSearch()} loading={searching} disabled={!searchInput.trim()}>
              检索候选论文
            </Button>
            <Button variant="secondary" onClick={prefillSubscriptionFromSearch} disabled={!searchInput.trim()}>保存为自动订阅</Button>
            {(searchFromYear || dateFrom || dateTo || venueNameInput.trim()) && <Button variant="ghost" onClick={() => { setSearchFromYear(""); setDateFrom(""); setDateTo(""); setVenueNameInput(""); }}>清空筛选</Button>}
          </div>

          <AIPromptHelper
            value={aiDesc}
            onChange={setAiDesc}
            onSubmit={handleAiSuggest}
            onApply={applySuggestion}
            suggestions={suggestions}
            loading={aiLoading}
          />

          {showSubscriptionForm && (
            <div className="rounded-[24px] border border-border/70 bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.015))] p-5 shadow-[0_18px_38px_-30px_rgba(15,23,35,0.28)]">
              <div className="mb-4 flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-base font-semibold text-ink">{editingSubscriptionId ? "编辑订阅" : "保存为自动订阅"}</h3>
                </div>
                <button type="button" onClick={resetSubscriptionForm} className="rounded-xl p-2 text-ink-tertiary transition hover:bg-hover hover:text-ink">
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <FieldBlock label="订阅名称">
                  <input
                    value={subscriptionForm.name}
                    onChange={(event) => setSubscriptionForm((current) => ({ ...current, name: event.target.value }))}
                    placeholder="输入订阅名称"
                    className="form-input"
                  />
                </FieldBlock>
                <FieldBlock label="执行频率">
                  <select
                    value={subscriptionForm.frequency}
                    onChange={(event) => setSubscriptionForm((current) => ({ ...current, frequency: event.target.value as ScheduleFrequency }))}
                    className="form-input"
                  >
                    {FREQ_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </FieldBlock>
                <FieldBlock label="执行时间（北京时间）">
                  <select
                    value={subscriptionForm.timeBj}
                    onChange={(event) => setSubscriptionForm((current) => ({ ...current, timeBj: Number(event.target.value) }))}
                    className="form-input"
                  >
                    {hourOptions().map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </FieldBlock>
                <FieldBlock label="订阅状态">
                  <label className="flex h-11 items-center gap-3 rounded-2xl border border-border bg-page px-4 text-sm text-ink-secondary">
                    <input
                      type="checkbox"
                      checked={subscriptionForm.enabled}
                      onChange={(event) => setSubscriptionForm((current) => ({ ...current, enabled: event.target.checked }))}
                      className="h-4 w-4 rounded border-border/70"
                    />
                    启用自动收集
                  </label>
                </FieldBlock>
                <FieldBlock label="最近 N 天范围">
                  <div className="flex items-center gap-3 rounded-2xl border border-border bg-page px-4 py-3">
                    <label className="inline-flex items-center gap-2 text-sm text-ink-secondary">
                      <input
                        type="checkbox"
                        checked={subscriptionForm.enableDateFilter}
                        onChange={(event) => setSubscriptionForm((current) => ({ ...current, enableDateFilter: event.target.checked }))}
                        className="h-4 w-4 rounded border-border/70"
                      />
                      启用
                    </label>
                    <input
                      type="number"
                      min={1}
                      max={365}
                      value={subscriptionForm.dateFilterDays}
                      onChange={(event) => setSubscriptionForm((current) => ({ ...current, dateFilterDays: Number(event.target.value) || 7 }))}
                      disabled={!subscriptionForm.enableDateFilter}
                      className="h-10 w-28 rounded-xl border border-border bg-page px-3 text-sm text-ink outline-none disabled:cursor-not-allowed disabled:opacity-60"
                    />
                    <span className="text-sm text-ink-tertiary">天</span>
                  </div>
                </FieldBlock>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                <Button onClick={() => void handleSaveSubscription()} loading={subscriptionSaving}>{editingSubscriptionId ? "保存订阅" : "创建订阅"}</Button>
                <Button variant="secondary" onClick={resetSubscriptionForm}>取消</Button>
              </div>
            </div>
          )}
        </div>

        {searchResults.length > 0 && (
          <div className="mt-5 space-y-3">
            {searchResults.map((item, index) => (
              <SearchResultCard
                key={`${item.createdAt}-${index}`}
                result={item}
                onToggle={() => setSearchResults((current) => current.map((entry, entryIndex) => entryIndex === index ? { ...entry, expanded: !entry.expanded } : entry))}
                onToggleSelect={(paperKey) => handleToggleResultSelection(index, paperKey)}
                onSelectAll={(selected) => handleSelectAllResultPapers(index, selected)}
                onImportSelected={() => void handleImportSearchResult(index, "selected")}
                onImportAll={() => void handleImportSearchResult(index, "all")}
                onNavigate={(paperId) => navigate(`/papers/${paperId}`)}
              />
            ))}
          </div>
        )}
      </section>

      <div className="grid gap-6 xl:grid-cols-[1.2fr_1fr]">
        <div className="space-y-6">
          <section className="rounded-[28px] border border-border bg-surface p-6 shadow-sm">
            <div className="mb-4 flex items-center gap-3">
              <div className="rounded-2xl bg-primary/10 p-3 text-primary"><Hash className="h-4 w-4" /></div>
              <div>
                <h2 className="text-lg font-semibold text-ink">按 arXiv ID 导入</h2>
              </div>
            </div>

            <div className="space-y-4">
              <textarea value={arxivIdText} onChange={(event) => setArxivIdText(event.target.value)} placeholder={"2403.01234\n2401.00001v2\ncs/9901001"} className="min-h-[132px] w-full rounded-2xl border border-border bg-page px-4 py-3 text-sm text-ink outline-none transition focus:border-primary/30 focus:ring-4 focus:ring-primary/10" />
              <div className="grid gap-3 sm:grid-cols-2">
                <FieldBlock label="归档到文件夹" icon={<Folder className="h-3.5 w-3.5" />}>
                  <select value={arxivIdFolderId} onChange={(event) => setArxivIdFolderId(event.target.value)} className="form-input">
                    <option value="">主论文库</option>
                    {folderTopics.map((topic) => <option key={topic.id} value={topic.id}>{topic.name}</option>)}
                  </select>
                </FieldBlock>
                <label className="flex items-center gap-3 rounded-2xl border border-border bg-page px-4 py-3 text-sm text-ink-secondary">
                  <input type="checkbox" checked={arxivIdDownloadPdf} onChange={(event) => setArxivIdDownloadPdf(event.target.checked)} className="h-4 w-4 rounded border-border/70" />
                  同时下载 PDF
                </label>
              </div>
              <Button icon={importingArxivIds ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />} onClick={() => void handleImportArxivIds()} disabled={importingArxivIds}>
                批量导入
              </Button>
              {arxivIdResult && <ImportSummaryCard title="arXiv ID 导入结果" result={arxivIdResult} onNavigate={(paperId) => navigate(`/papers/${paperId}`)} />}
            </div>
          </section>

          <section className="rounded-[28px] border border-border bg-surface p-6 shadow-sm">
            <div className="mb-4 flex items-center gap-3">
              <div className="rounded-2xl bg-primary/10 p-3 text-primary"><Upload className="h-4 w-4" /></div>
              <div>
                <h2 className="text-lg font-semibold text-ink">PDF 手动入库</h2>
              </div>
            </div>

            <div className="space-y-4">
              <input ref={uploadInputRef} type="file" accept="application/pdf" multiple onChange={handleUploadInputChange} className="hidden" />
              <div
                onDragOver={(event) => event.preventDefault()}
                onDrop={handleUploadDrop}
                className="rounded-[24px] border border-dashed border-primary/25 bg-[linear-gradient(135deg,rgba(113,112,255,0.10),rgba(255,255,255,0.03)_55%,rgba(255,255,255,0.015))] p-5"
              >
                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div>
                    <p className="text-sm font-semibold text-ink">PDF 入库</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => uploadInputRef.current?.click()}
                      className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white transition hover:bg-primary-hover"
                    >
                      {uploadFiles.length > 0 ? "继续添加 PDF" : "选择 PDF"}
                    </button>
                    {uploadFiles.length > 0 && (
                      <button
                        type="button"
                        onClick={() => {
                          setUploadFiles([]);
                          setUploadSummary(null);
                        }}
                        className="rounded-xl border border-border bg-page px-4 py-2 text-sm font-medium text-ink-secondary transition hover:border-primary/20 hover:text-ink"
                      >
                        清空队列
                      </button>
                    )}
                  </div>
                </div>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <FieldBlock label="标题（单文件时可选）"><input value={uploadTitle} onChange={(event) => setUploadTitle(event.target.value)} placeholder={uploadFiles.length > 1 ? "批量入库时将自动识别标题" : "仅单文件时覆盖自动识别标题"} disabled={uploadFiles.length > 1} className="form-input disabled:cursor-not-allowed disabled:bg-page/70 disabled:text-ink-tertiary" /></FieldBlock>
                <FieldBlock label="arXiv ID（单文件时可选）"><input value={uploadArxivId} onChange={(event) => setUploadArxivId(event.target.value)} placeholder={uploadFiles.length > 1 ? "批量模式不可填" : "输入 arXiv ID"} disabled={uploadFiles.length > 1} className="form-input disabled:cursor-not-allowed disabled:bg-page/70 disabled:text-ink-tertiary" /></FieldBlock>
              </div>
              <FieldBlock label="默认归档文件夹" icon={<Folder className="h-3.5 w-3.5" />}>
                <select value={uploadTopicId} onChange={(event) => setUploadTopicId(event.target.value)} className="form-input">
                  <option value="">主论文库</option>
                  {folderTopics.map((topic) => <option key={topic.id} value={topic.id}>{topic.name}</option>)}
                </select>
              </FieldBlock>

              {uploadFiles.length > 0 && (
                <div className="rounded-2xl border border-border bg-page p-4">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium text-ink">已选择 {uploadFiles.length} 份 PDF</p>
                    <button type="button" onClick={() => { setUploadFiles([]); setUploadSummary(null); }} className="text-xs text-ink-tertiary transition hover:text-ink">清空</button>
                  </div>
                  <div className="mt-3 grid gap-2 md:grid-cols-2">
                    {uploadFiles.slice(0, 8).map((file) => (
                      <div key={`${file.name}:${file.size}:${file.lastModified}`} className="flex items-center gap-2 rounded-xl border border-border/70 bg-surface/80 px-3 py-2 text-sm text-ink-secondary">
                        <FileText className="h-4 w-4 shrink-0 text-primary" />
                        <span className="truncate">{file.name}</span>
                      </div>
                    ))}
                  </div>
                  {uploadFiles.length > 8 && (
                    <p className="mt-3 text-xs text-ink-tertiary">
                      +{uploadFiles.length - 8} 份文件
                    </p>
                  )}
                </div>
              )}

              <Button icon={uploadingPdf ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />} onClick={() => void handleUploadPdf()} disabled={uploadingPdf || uploadFiles.length === 0}>
                {uploadFiles.length > 1 ? "批量上传入库" : "上传入库"}
              </Button>
              {(uploadingPdf || uploadSummary) && <UploadSummaryCard summary={uploadSummary} loading={uploadingPdf} currentName={uploadingLabel} />}
            </div>
          </section>
        </div>

        <div className="space-y-6">
          <section className="rounded-[28px] border border-border bg-surface p-6 shadow-sm">
            <div className="mb-4 flex items-center gap-3">
              <div className="rounded-2xl bg-primary/10 p-3 text-primary"><Folder className="h-4 w-4" /></div>
              <div>
                <h2 className="text-lg font-semibold text-ink">归档文件夹</h2>
              </div>
            </div>

            <div className="space-y-4">
              <div className="flex gap-2">
                <input value={folderDraftName} onChange={(event) => setFolderDraftName(event.target.value)} placeholder="文件夹名称" className="form-input flex-1" />
                <Button onClick={() => void handleCreateFolder()} loading={folderSaving} icon={<Plus className="h-4 w-4" />}>创建</Button>
              </div>

              {folderTopics.length === 0 ? (
                <Empty icon={<Folder className="h-10 w-10" />} title="还没有文件夹" />
              ) : (
                <div className="space-y-3">
                  {folderTopics.map((folder) => {
                    const isEditing = folderEditingId === folder.id;
                    return (
                      <div key={folder.id} className="rounded-2xl border border-border bg-page p-4">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0 flex-1">
                            {isEditing ? (
                              <div className="flex gap-2">
                                <input value={folderEditingName} onChange={(event) => setFolderEditingName(event.target.value)} className="form-input flex-1" />
                                <Button size="sm" onClick={() => void handleRenameFolder()} loading={folderSaving}>保存</Button>
                              </div>
                            ) : (
                              <>
                                <div className="flex items-center gap-2">
                                  <FolderOpen className="h-4 w-4 text-primary" />
                                  <p className="truncate text-sm font-semibold text-ink">{folder.name}</p>
                                </div>
                                <div className="mt-2 flex flex-wrap gap-2 text-xs text-ink-tertiary">
                                  <span className="rounded-full border border-border/70 bg-page/85 px-2.5 py-1">{folder.paper_count || 0} 篇论文</span>
                                  {subscriptionTopics.some((item) => item.default_folder_id === folder.id) && <span className="rounded-full bg-primary/10 px-2.5 py-1 text-primary">已被订阅设为默认归档</span>}
                                </div>
                              </>
                            )}
                          </div>

                          <div className="flex items-center gap-1">
                            {!isEditing && (
                              <>
                                <button type="button" onClick={() => navigate(`/papers?topicId=${folder.id}`)} className="rounded-xl p-2 text-ink-tertiary transition hover:bg-hover hover:text-ink" title="在论文库查看"><Library className="h-4 w-4" /></button>
                                <button type="button" onClick={() => { setFolderEditingId(folder.id); setFolderEditingName(folder.name); }} className="rounded-xl p-2 text-ink-tertiary transition hover:bg-hover hover:text-ink" title="重命名文件夹"><Pencil className="h-4 w-4" /></button>
                                <button type="button" onClick={() => setFolderDeletingId(folder.id)} className="rounded-xl p-2 text-ink-tertiary transition hover:bg-error/10 hover:text-error" title="删除文件夹"><Trash2 className="h-4 w-4" /></button>
                              </>
                            )}
                            {isEditing && <Button variant="ghost" size="sm" onClick={() => { setFolderEditingId(null); setFolderEditingName(""); }}>取消</Button>}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </section>
        </div>
      </div>

      <section className="rounded-[28px] border border-border bg-surface p-6 shadow-sm">
        <div className="mb-5 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <Rss className="h-4 w-4 text-primary" />
              <h2 className="text-lg font-semibold text-ink">订阅列表</h2>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" icon={<RefreshCw className="h-4 w-4" />} onClick={() => void loadTopics()}>刷新</Button>
          </div>
        </div>

        {loadingTopics ? (
          <Spinner text="加载订阅与文件夹..." />
        ) : subscriptionTopics.length === 0 ? (
          <Empty icon={<Rss className="h-12 w-12" />} title="还没有订阅" />
        ) : (
          <div className="space-y-3">
            {subscriptionTopics.map((topic) => (
              <SubscriptionCard
                key={topic.id}
                topic={topic}
                fetching={fetchingTopicId === topic.id}
                onEdit={() => openEditSubscription(topic)}
                onToggle={() => void handleToggleSubscription(topic)}
                onDelete={() => setSubscriptionDeleteId(topic.id)}
                onFetch={() => void handleManualFetch(topic.id)}
                onNavigate={() => navigate(`/papers?topicId=${topic.id}`)}
              />
            ))}
          </div>
        )}
      </section>

      <ConfirmDialog open={!!folderDeletingId} title="删除文件夹" description="删除后不会删除论文本身，但会移除这个文件夹入口。" confirmLabel="删除文件夹" variant="danger" onCancel={() => setFolderDeletingId(null)} onConfirm={async () => { if (folderDeletingId) await handleDeleteFolder(folderDeletingId); }} />
      <ConfirmDialog open={!!subscriptionDeleteId} title="删除自动订阅" description="删除后将停止该主题的自动收集，已经入库的论文会保留。" confirmLabel="删除订阅" variant="danger" onCancel={() => setSubscriptionDeleteId(null)} onConfirm={async () => { if (subscriptionDeleteId) await handleDeleteSubscription(subscriptionDeleteId); }} />
    </div>
  );
}

function MetricPill({ label, value }: { label: string; value: number }) {
  return (
    <div className="inline-flex items-center gap-2 rounded-full border border-border/70 bg-page/82 px-3 py-1.5 text-sm text-ink-secondary">
      <span>{label}</span>
      <span className="font-semibold text-ink">{value}</span>
    </div>
  );
}

function FieldBlock({
  label,
  icon,
  children,
}: {
  label: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label className="flex items-center gap-1.5 text-xs font-medium text-ink-secondary">
        {icon}
        {label}
      </label>
      {children}
    </div>
  );
}

function SearchResultCard({
  result,
  onToggle,
  onToggleSelect,
  onSelectAll,
  onImportSelected,
  onImportAll,
  onNavigate,
}: {
  result: SearchResult;
  onToggle: () => void;
  onToggleSelect: (paperKey: string) => void;
  onSelectAll: (selected: boolean) => void;
  onImportSelected: () => void;
  onImportAll: () => void;
  onNavigate: (paperId: string) => void;
}) {
  const selectedCount = result.selectedKeys.length;
  const allSelected = result.papers.length > 0 && selectedCount === result.papers.length;

  return (
    <div className="rounded-[24px] border border-success/20 bg-success/[0.03]">
      <button type="button" onClick={onToggle} className="flex w-full items-start gap-3 px-4 py-4 text-left">
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm font-semibold text-ink">{result.displayQuery}</p>
            <span className="rounded-full bg-success/10 px-2.5 py-0.5 text-[11px] font-medium text-success">候选 {result.found} 篇</span>
            {result.importResult && <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-[11px] font-medium text-primary">已入库 {result.importResult.ingested}</span>}
            <span className="rounded-full border border-border/70 bg-page/80 px-2.5 py-0.5 text-[11px] text-ink-tertiary">{searchFieldLabel(result.searchField)}</span>
            <span className="rounded-full border border-border/70 bg-page/80 px-2.5 py-0.5 text-[11px] text-ink-tertiary">{priorityLabel(result.priorityMode)}</span>
            <span className="rounded-full border border-border/70 bg-page/80 px-2.5 py-0.5 text-[11px] text-ink-tertiary">{topicSourceLabel(result.source)}</span>
            <span className="rounded-full border border-border/70 bg-page/80 px-2.5 py-0.5 text-[11px] text-ink-tertiary">{venueTierLabel(result.venueTier)}</span>
            {result.venueType !== "all" && <span className="rounded-full border border-border/70 bg-page/80 px-2.5 py-0.5 text-[11px] text-ink-tertiary">{venueTypeLabel(result.venueType)}</span>}
            {result.fromYear && <span className="rounded-full border border-border/70 bg-page/80 px-2.5 py-0.5 text-[11px] text-ink-tertiary">自 {result.fromYear} 年起</span>}
          </div>
          {!result.expanded && result.papers.length > 0 && <p className="mt-2 truncate text-xs text-ink-secondary">{result.papers.slice(0, 3).map((item) => item.title).join(" · ")}</p>}
        </div>
        <span className="shrink-0 text-xs text-ink-tertiary">{relativeTime(result.createdAt)}</span>
      </button>
      {result.expanded && result.papers.length > 0 && (
        <div className="border-t border-success/10 px-4 pb-4 pt-3">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-2 rounded-xl border border-border bg-page/85 px-3 py-2 text-xs text-ink-secondary">
              <input type="checkbox" checked={allSelected} onChange={(event) => onSelectAll(event.target.checked)} className="h-4 w-4 rounded border-border/70" />
              全选 {result.papers.length} 篇
            </label>
            <span className="text-xs text-ink-tertiary">已选 {selectedCount} 篇</span>
            <Button size="sm" onClick={onImportSelected} disabled={result.importing || selectedCount === 0}>
              {result.importing ? "导入中..." : "导入已选"}
            </Button>
            <Button size="sm" variant="secondary" onClick={onImportAll} disabled={result.importing || result.papers.length === 0}>
              全部导入
            </Button>
            {result.skippedSources.length > 0 && <span className="text-xs text-warning">部分来源因 venue 过滤已跳过：{result.skippedSources.join("、")}</span>}
          </div>
          <div className="space-y-2">
            {result.papers.map((paper) => (
              <div key={externalPaperKey(paper)} className="flex items-start gap-3 rounded-2xl border border-border bg-page/78 px-3 py-3">
                <input
                  type="checkbox"
                  checked={result.selectedKeys.includes(externalPaperKey(paper))}
                  onChange={() => onToggleSelect(externalPaperKey(paper))}
                  className="mt-1 h-4 w-4 rounded border-border/70"
                />
                <FileText className="mt-0.5 h-4 w-4 shrink-0 text-ink-tertiary" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-ink">{paper.title}</p>
                  <p className="mt-1 text-xs leading-6 text-ink-tertiary">
                    {[
                      paper.source?.toUpperCase(),
                      paper.venue,
                      paper.publication_year || paper.publication_date,
                      paper.citation_count != null ? `引用 ${paper.citation_count}` : "",
                    ].filter(Boolean).join(" · ") || "候选论文"}
                  </p>
                  {paper.authors && paper.authors.length > 0 && <p className="mt-1 text-xs text-ink-secondary">{paper.authors.slice(0, 4).join("、")}</p>}
                </div>
                <button
                  type="button"
                  onClick={() => {
                    if (paper.source_url) window.open(paper.source_url, "_blank", "noopener,noreferrer");
                  }}
                  disabled={!paper.source_url}
                  className="rounded-xl p-2 text-ink-tertiary transition hover:bg-primary/10 hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
                  title="打开来源"
                >
                  <ExternalLink className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
          {result.importResult && <div className="mt-3"><ImportSummaryCard title="本次导入结果" result={result.importResult} onNavigate={onNavigate} /></div>}
        </div>
      )}
    </div>
  );
}

function ImportSummaryCard({
  title,
  result,
  onNavigate,
}: {
  title: string;
  result: IngestResult;
  onNavigate: (paperId: string) => void;
}) {
  return (
    <div className="rounded-[24px] border border-success/20 bg-success/[0.03] p-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-semibold text-ink">{title}</p>
        <span className="rounded-full bg-success/10 px-2.5 py-0.5 text-[11px] font-medium text-success">入库 {result.ingested}</span>
        {typeof result.duplicates === "number" && result.duplicates > 0 && <span className="rounded-full bg-warning/10 px-2.5 py-0.5 text-[11px] font-medium text-warning">重复 {result.duplicates}</span>}
      </div>
      {result.missing_ids && result.missing_ids.length > 0 && <p className="mt-2 text-xs text-warning">未命中：{result.missing_ids.join("、")}</p>}
      {result.papers && result.papers.length > 0 && (
        <div className="mt-3 space-y-2">
          {result.papers.map((paper) => (
            <div key={paper.id} className="flex items-start gap-3 rounded-2xl border border-border bg-page/78 px-3 py-3">
              <FileText className="mt-0.5 h-4 w-4 shrink-0 text-ink-tertiary" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-ink">{paper.title}</p>
                <p className="mt-1 text-xs text-ink-tertiary">{paper.arxiv_id || paper.publication_date || "已入库"}</p>
              </div>
              <button type="button" onClick={() => onNavigate(paper.id)} className="rounded-xl p-2 text-ink-tertiary transition hover:bg-primary/10 hover:text-primary">
                <ExternalLink className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function UploadSummaryCard({
  summary,
  loading,
  currentName,
}: {
  summary: UploadBatchSummary | null;
  loading: boolean;
  currentName: string;
}) {
  const completed = summary?.completed || 0;
  const total = summary?.total || 0;
  const progressPct = total > 0 ? Math.round((completed / total) * 100) : 0;

  return (
    <div className="rounded-[24px] border border-border bg-page p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-semibold text-ink">PDF 入库进度</p>
          <p className="mt-1 text-xs leading-6 text-ink-tertiary">{loading ? `正在处理 ${currentName || "当前文件"}` : `已处理 ${completed} / ${total} 份文件`}</p>
        </div>
        {loading && <Loader2 className="h-4 w-4 animate-spin text-primary" />}
      </div>
      {summary && summary.total > 0 && (
        <>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-border">
            <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${progressPct}%` }} />
          </div>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <span className="rounded-full bg-success/10 px-2.5 py-1 text-success">新建 {summary.created}</span>
            <span className="rounded-full bg-primary/10 px-2.5 py-1 text-primary">更新 {summary.updated}</span>
            <span className="rounded-full bg-error/10 px-2.5 py-1 text-error">失败 {summary.failed.length}</span>
          </div>
        </>
      )}
    </div>
  );
}

function SubscriptionCard({
  topic,
  fetching,
  onEdit,
  onToggle,
  onDelete,
  onFetch,
  onNavigate,
}: {
  topic: Topic;
  fetching: boolean;
  onEdit: () => void;
  onToggle: () => void;
  onDelete: () => void;
  onFetch: () => void;
  onNavigate: () => void;
}) {
  const scheduleLabel = `${FREQ_LABEL[topic.schedule_frequency]} ${String(utcToBj(topic.schedule_time_utc || 21)).padStart(2, "0")}:00`;

  return (
    <div className={`rounded-[24px] border p-4 transition-all ${topic.enabled ? "border-border bg-page" : "border-border/70 bg-page/70 opacity-80"}`}>
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-base font-semibold text-ink">{topic.name}</p>
            <span className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${topic.enabled ? "bg-success/10 text-success" : "border border-border/70 bg-page/85 text-ink-tertiary"}`}>{topic.enabled ? "运行中" : "已暂停"}</span>
          </div>
          <p className="mt-2 break-all font-mono text-xs leading-6 text-ink-tertiary">{topic.query}</p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <span className="rounded-full border border-border/70 bg-page/85 px-2.5 py-1 text-ink-secondary">{topicSourceLabel(topic.source)}</span>
            <span className="rounded-full border border-border/70 bg-page/85 px-2.5 py-1 text-ink-secondary">{searchFieldLabel(topic.search_field)}</span>
            <span className="rounded-full border border-border/70 bg-page/85 px-2.5 py-1 text-ink-secondary">{priorityLabel(topic.priority_mode)}</span>
            <span className="rounded-full border border-border/70 bg-page/85 px-2.5 py-1 text-ink-secondary">{venueTierLabel(topic.venue_tier || "all")}</span>
            {topic.venue_type !== "all" && <span className="rounded-full border border-border/70 bg-page/85 px-2.5 py-1 text-ink-secondary">{venueTypeLabel(topic.venue_type)}</span>}
            {topic.from_year != null && <span className="rounded-full border border-border/70 bg-page/85 px-2.5 py-1 text-ink-secondary">{topic.from_year} 年起</span>}
            <span className="rounded-full border border-border/70 bg-page/85 px-2.5 py-1 text-ink-secondary">{scheduleLabel}</span>
            <span className="rounded-full border border-border/70 bg-page/85 px-2.5 py-1 text-ink-secondary">每次 {topic.max_results_per_run} 篇</span>
            {topic.enable_date_filter && <span className="rounded-full border border-border/70 bg-page/85 px-2.5 py-1 text-ink-secondary">最近 {topic.date_filter_days} 天</span>}
            {topic.venue_names && topic.venue_names.length > 0 && <span className="rounded-full border border-border/70 bg-page/85 px-2.5 py-1 text-ink-secondary">{topic.venue_names.slice(0, 3).join(" / ")}</span>}
          </div>
          <div className="mt-3 flex flex-wrap gap-3 text-xs text-ink-tertiary">
            <span>{topic.paper_count || 0} 篇已收集</span>
            {topic.last_run_at && <span>上次抓取 {relativeTime(topic.last_run_at)}</span>}
            {topic.last_run_count != null && <span>上次新增 {topic.last_run_count} 篇</span>}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" icon={fetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} onClick={onFetch} disabled={fetching}>
            {fetching ? "抓取中" : "立即抓取"}
          </Button>
          <Button size="sm" variant="secondary" onClick={onNavigate}>查看结果</Button>
          <button type="button" onClick={onEdit} className="rounded-xl p-2 text-ink-tertiary transition hover:bg-hover hover:text-ink" title="编辑订阅"><Pencil className="h-4 w-4" /></button>
          <button type="button" onClick={onToggle} className={`rounded-xl p-2 transition ${topic.enabled ? "text-success hover:bg-success-light" : "text-ink-tertiary hover:bg-hover hover:text-ink"}`} title={topic.enabled ? "暂停订阅" : "启用订阅"}>{topic.enabled ? <Power className="h-4 w-4" /> : <PowerOff className="h-4 w-4" />}</button>
          <button type="button" onClick={onDelete} className="rounded-xl p-2 text-ink-tertiary transition hover:bg-error/10 hover:text-error" title="删除订阅"><Trash2 className="h-4 w-4" /></button>
        </div>
      </div>
    </div>
  );
}
