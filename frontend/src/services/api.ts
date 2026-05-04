/**
 * ResearchOS - API 服务层
 */
import type {
  SystemStatus,
  Topic,
  TopicCreate,
  TopicFetchResult,
  TopicFetchStatus,
  TopicKind,
  TopicSearchField,
  TopicUpdate,
  TodaySummary,
  ArxivSortBy,
  Paper,
  PaperTopicAssignment,
  PipelineRun,
  SkimReport,
  DeepDiveReport,
  AskRequest,
  AskResponse,
  CitationTree,
  TimelineResponse,
  GraphQuality,
  EvolutionResponse,
  SurveyResponse,
  PaperWiki,
  TopicWiki,
  DailyBriefRequest,
  DailyBriefResponse,
  CostMetrics,
  CitationSyncResult,
  IngestResult,
  ExternalLiteraturePaper,
  ExternalLiteratureSearchResult,
  LiteratureSourceScope,
  LiteratureVenueTier,
  LiteratureVenueType,
  PdfUploadResult,
  KeywordSuggestion,
  ReasoningAnalysisResponse,
  ResearchGapsResponse,
  CitationDetail,
  TopicCitationNetwork,
  LibraryOverview,
  SimilarityMapData,
  BridgesResponse,
  FrontierResponse,
  CocitationResponse,
  AssistantExecPolicy,
  AssistantWorkspaceDiffResponse,
  AssistantWorkspaceFileResponse,
  AssistantWorkspaceFileWriteResponse,
  AssistantWorkspaceGitActionResponse,
  AssistantWorkspaceGitBranchResponse,
  AssistantWorkspaceGitInitResponse,
  AssistantWorkspaceOverview,
  AssistantWorkspaceRevealResponse,
  AssistantWorkspaceServer,
  AssistantWorkspaceServerPayload,
  AssistantWorkspaceSshProbePayload,
  AssistantWorkspaceSshProbeResult,
  AssistantWorkspaceTerminalResult,
  AssistantWorkspaceTerminalSessionInfo,
  AssistantWorkspaceUploadResponse,
  WorkspaceRootItem,
  AssistantSkillRoot,
  AssistantSessionDiffEntry,
  OpenCodeRuntimeStatus,
  OpenCodeAgentInfo,
  OpenCodeConfig,
  OpenCodeMcpMap,
  AcpRegistryConfig,
  AcpRuntimeStatus,
  AcpServerInfo,
  McpRegistryConfig,
  McpRuntimeStatus,
  McpServerInfo,
  OpenCodeProjectInfo,
  OpenCodeSessionInfo,
  OpenCodeSessionStatus,
  OpenCodeVcsInfo,
  WorkspaceRootListResponse,
  AgentMode,
  AgentReasoningLevel,
  AnalysisDetailLevel,
  PaperEvidenceMode,
  PaperAnalysisBundle,
  PaperReaderAction,
  PaperReaderDocumentResponse,
  PaperReaderNote,
  PaperReaderQueryResponse,
  PaperReaderScope,
  Project,
  ProjectCreate,
  ProjectUpdate,
  ProjectWorkspaceContext,
  ProjectPaper,
  ProjectRepo,
  ProjectRepoCommit,
  ProjectIdea,
  ProjectReport,
  ProjectPaperRef,
  ProjectDeploymentTarget,
  ProjectRun,
  ProjectRunAction,
  ProjectAgentTemplate,
  ProjectEngineProfile,
  ProjectWorkflowPreset,
  ProjectRunActionPreset,
  ProjectWorkflowType,
  ProjectRunActionType,
  ProjectCompanionOverviewItem,
  ProjectCompanionSnapshot,
  AgentCliConfig,
  AgentDetectionItem,
} from "@/types";

import {
  normalizeRecordArray,
  normalizeSessionDiffEntries,
  normalizeSessionInfo,
  normalizeSessionStatus,
} from "@/features/assistantInstance/sessionProtocol";
import {
  buildWebSocketUrl,
  canSignApiAssetUrl,
  clearAuth,
  del,
  fetchSSE,
  getAuthToken,
  get,
  getApiBase,
  getPathAccessToken,
  patch,
  post,
  postForm,
  put,
  request,
} from "./http";
export {
  ApiHttpError,
  canSignApiAssetUrl,
  clearAuth,
  getAuthToken,
  getPathAccessToken,
  isAuthenticated,
  request,
  resolveApiAssetUrl,
  resolveSignedApiAssetUrl,
} from "./http";

export type FigureExtractMode = "arxiv_source" | "mineru";
export type PaperContentSource = "pdf" | "markdown";

export interface OpenCodePromptTextPart {
  type: "text";
  text: string;
  synthetic?: boolean;
  ignored?: boolean;
  metadata?: Record<string, unknown>;
}

export interface OpenCodePromptFilePart {
  type: "file";
  url: string;
  filename: string;
  mime: string;
}

export type OpenCodePromptPart = OpenCodePromptTextPart | OpenCodePromptFilePart;

/* ========== 系统 ========== */
export const systemApi = {
  health: () => get<{ status: string; app: string; env: string }>("/health"),
  status: () => get<SystemStatus>("/system/status"),
};

/* ========== 今日速览 ========== */
export const todayApi = {
  summary: () => get<TodaySummary>("/today"),
};

export interface DashboardHomeSnapshot {
  today: TodaySummary | null;
  folders: FolderStats | null;
  projects: ProjectCompanionOverviewItem[];
  tasks: TaskStatus[];
  graph: LibraryOverview | null;
  arxiv_trend: ArxivTrendSnapshot | null;
  library_focus?: LibraryFocusSnapshot | null;
  topics: Topic[];
  acp: Record<string, unknown> | null;
}

export interface LibraryTopicProgress {
  deep_read: number;
  skimmed: number;
  unread: number;
  completion_pct: number;
}

export interface LibraryFeaturedTopic {
  label: string;
  kind?: "folder" | "subscription" | string;
  paper_count: number;
  citation_count: number;
  active_30d: number;
  progress: LibraryTopicProgress;
}

export interface LibraryFocusSnapshot {
  window_label: string;
  paper_count: number;
  topic_cards?: LibraryFeaturedTopic[];
  keywords: { keyword: string; count: number }[];
}

export interface ArxivTrendRow {
  key: string;
  label: string;
  label_zh?: string | null;
  count: number;
  sample_ratio: number;
}

export interface ArxivTrendPaper {
  arxiv_id?: string | null;
  title: string;
  primary_category?: string | null;
  categories?: string[];
  published_at?: string | null;
}

export interface ArxivTrendDirection {
  key: string;
  label: string;
  count: number;
  sample_ratio: number;
  summary?: string;
  keywords?: { keyword: string; count: number }[];
  example_title?: string | null;
}

export interface ArxivTrendSubdomainOption {
  key: string;
  label: string;
}

export interface ArxivTrendSnapshot {
  available: boolean;
  source: string;
  scope?: string;
  query?: string;
  subdomain_key?: string;
  subdomain_label?: string;
  subdomains?: ArxivTrendSubdomainOption[];
  query_date: string;
  window_label: string;
  total_submissions: number;
  sample_size: number;
  archives: ArxivTrendRow[];
  categories: ArxivTrendRow[];
  directions?: ArxivTrendDirection[];
  keywords?: { keyword: string; term?: string; count: number; example_title?: string | null }[];
  top_terms: { term: string; count: number }[];
  recent_papers: ArxivTrendPaper[];
  direction: string;
  fetched_at?: string;
  message?: string;
}

export const dashboardApi = {
  home: (params?: { projectLimit?: number; taskLimit?: number; trendSubdomain?: string }) => {
    const query = new URLSearchParams();
    if (params?.projectLimit) query.set("project_limit", String(params.projectLimit));
    if (params?.taskLimit) query.set("task_limit", String(params.taskLimit));
    if (params?.trendSubdomain) query.set("trend_subdomain", String(params.trendSubdomain));
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return get<DashboardHomeSnapshot>(`/dashboard/home${suffix}`);
  },
  arxivTrend: (subdomain = "all", refresh = false) => {
    const query = new URLSearchParams();
    query.set("subdomain", subdomain);
    if (refresh) query.set("refresh", "true");
    return get<ArxivTrendSnapshot>(`/dashboard/arxiv-trend?${query.toString()}`);
  },
};

/* ========== 主题 ========== */
export const topicApi = {
  list: (enabledOnly = false, kind?: TopicKind) => {
    const params = new URLSearchParams();
    params.set("enabled_only", String(enabledOnly));
    if (kind) params.set("kind", kind);
    return get<{ items: Topic[] }>(`/topics?${params}`);
  },
  create: (data: TopicCreate) => post<Topic>("/topics", data),
  update: (id: string, data: TopicUpdate) => patch<Topic>(`/topics/${id}`, data),
  delete: (id: string) => del<{ deleted: string }>(`/topics/${id}`),
  fetch: (id: string) =>
    post<TopicFetchResult>(`/topics/${id}/fetch`),
  fetchStatus: (id: string) =>
    get<TopicFetchStatus>(`/topics/${id}/fetch-status`),
  suggestKeywords: (
    description: string,
    options?: {
      source_scope?: LiteratureSourceScope;
      search_field?: TopicSearchField;
    },
  ) =>
    post<{ suggestions: KeywordSuggestion[] }>("/topics/suggest-keywords", {
      description,
      source_scope: options?.source_scope || "hybrid",
      search_field: options?.search_field || "all",
    }),
};

/* ========== 论文 ========== */
export interface FolderStats {
  total: number;
  favorites: number;
  recent_7d: number;
  unclassified: number;
  by_topic: { topic_id: string; topic_name: string; count: number }[];
  by_subscription: { topic_id: string; topic_name: string; count: number }[];
  by_status: Record<string, number>;
  by_date: { date: string; count: number }[];
}

export interface PaperListResponse {
  items: Paper[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface PaperDeleteResponse {
  deleted: string;
  removed_pdf_files: number;
}

export interface PaperBatchDeleteResponse {
  requested: number;
  deleted: number;
  deleted_ids: string[];
  missing_ids: string[];
  removed_pdf_files: number;
}

export interface PaperAutoClassifyResponse {
  requested: number;
  scanned_papers: number;
  classified_papers: number;
  linked_topics: number;
  dry_run: boolean;
  items: {
    paper_id: string;
    title: string;
    matched_topics: {
      topic_id: string;
      topic_name: string;
      score: number;
      keyword_score: number;
      graph_score: number;
    }[];
  }[];
  message?: string;
}

export interface PaperKeywordFacet {
  keyword: string;
  count: number;
}

export const paperApi = {
  latest: (opts: {
    page?: number;
    pageSize?: number;
    status?: string;
    topicId?: string;
    folder?: string;
    date?: string;
    dateFrom?: string;
    dateTo?: string;
    search?: string;
    keywords?: string[];
    sortBy?: string;
    sortOrder?: string;
  } = {}) => {
    const params = new URLSearchParams();
    params.set("page", String(opts.page || 1));
    params.set("page_size", String(opts.pageSize || 20));
    if (opts.status) params.append("status", opts.status);
    if (opts.topicId) params.append("topic_id", opts.topicId);
    if (opts.folder) params.append("folder", opts.folder);
    if (opts.date) params.append("date", opts.date);
    if (opts.dateFrom) params.append("date_from", opts.dateFrom);
    if (opts.dateTo) params.append("date_to", opts.dateTo);
    if (opts.search) params.append("search", opts.search);
    for (const keyword of opts.keywords || []) params.append("keywords", keyword);
    if (opts.sortBy) params.append("sort_by", opts.sortBy);
    if (opts.sortOrder) params.append("sort_order", opts.sortOrder);
    return get<PaperListResponse>(`/papers/latest?${params}`);
  },
  keywordStats: (opts: {
    status?: string;
    topicId?: string;
    folder?: string;
    date?: string;
    dateFrom?: string;
    dateTo?: string;
    search?: string;
    limit?: number;
  } = {}) => {
    const params = new URLSearchParams();
    if (opts.status) params.append("status", opts.status);
    if (opts.topicId) params.append("topic_id", opts.topicId);
    if (opts.folder) params.append("folder", opts.folder);
    if (opts.date) params.append("date", opts.date);
    if (opts.dateFrom) params.append("date_from", opts.dateFrom);
    if (opts.dateTo) params.append("date_to", opts.dateTo);
    if (opts.search) params.append("search", opts.search);
    params.set("limit", String(opts.limit || 30));
    return get<{ items: PaperKeywordFacet[] }>(`/papers/keywords?${params}`);
  },
  folderStats: () => get<FolderStats>("/papers/folder-stats"),
  detail: (id: string) => get<Paper>(`/papers/${id}`),
  listTopics: (id: string) =>
    get<{ items: PaperTopicAssignment[] }>(`/papers/${id}/topics`),
  addTopic: (id: string, topicId: string) =>
    post<{ paper_id: string; topic_id: string; status: string }>(`/papers/${id}/topics`, {
      topic_id: topicId,
    }),
  removeTopic: (id: string, topicId: string) =>
    del<{ paper_id: string; topic_id: string; removed: boolean }>(
      `/papers/${id}/topics/${topicId}`,
    ),
  delete: (id: string, deletePdf = true) =>
    del<PaperDeleteResponse>(`/papers/${id}?delete_pdf=${deletePdf}`),
  batchDelete: (paperIds: string[], deletePdfFiles = true) =>
    post<PaperBatchDeleteResponse>("/papers/batch-delete", {
      paper_ids: paperIds,
      delete_pdf_files: deletePdfFiles,
    }),
  autoClassify: (body: {
    paper_ids?: string[];
    only_unclassified?: boolean;
    max_papers?: number;
    max_topics_per_paper?: number;
    min_score?: number;
    use_graph?: boolean;
    dry_run?: boolean;
  } = {}) =>
    post<PaperAutoClassifyResponse>("/papers/auto-classify", body),
  similar: (id: string, topK = 5) =>
    get<{ paper_id: string; similar_ids: string[]; items?: { id: string; title: string; arxiv_id?: string; read_status?: string }[] }>(`/papers/${id}/similar?top_k=${topK}`),
  toggleFavorite: (id: string) =>
    patch<{ id: string; favorited: boolean }>(`/papers/${id}/favorite`),
  getFigures: (id: string) =>
    get<{ items: FigureAnalysisItem[] }>(`/papers/${id}/figures`),
  getOcrStatus: (id: string) =>
    get<PaperOcrStatus>(`/papers/${id}/ocr/status`),
  processOcrAsync: (id: string, force = false) =>
    post<{ task_id: string; status: string; message?: string }>(
      `/papers/${id}/ocr/process-async?force=${force ? "true" : "false"}`,
    ),
  extractFigures: (id: string, maxFigures = 80, extractMode?: FigureExtractMode) => {
    const params = new URLSearchParams();
    params.set("max_figures", String(maxFigures));
    if (extractMode) params.set("extract_mode", extractMode);
    return post<{ paper_id: string; count: number; items: FigureAnalysisItem[] }>(
      `/papers/${id}/figures/extract?${params}`,
    );
  },
  extractFiguresAsync: (id: string, maxFigures = 80, extractMode?: FigureExtractMode) => {
    const params = new URLSearchParams();
    params.set("max_figures", String(maxFigures));
    if (extractMode) params.set("extract_mode", extractMode);
    return post<{ task_id: string; status: string; message?: string }>(
      `/papers/${id}/figures/extract-async?${params}`,
    );
  },
  analyzeFigures: (id: string, maxFigures = 10) =>
    post<{ paper_id: string; count: number; items: FigureAnalysisItem[] }>(
      `/papers/${id}/figures/analyze?max_figures=${maxFigures}`,
    ),
  analyzeSelectedFigures: (id: string, figureIds: string[]) =>
    post<{ paper_id: string; count: number; items: FigureAnalysisItem[] }>(
      `/papers/${id}/figures/analyze`,
      { figure_ids: figureIds },
    ),
  analyzeSelectedFiguresAsync: (id: string, figureIds: string[]) =>
    post<{ task_id: string; status: string; message?: string }>(
      `/papers/${id}/figures/analyze-async`,
      { figure_ids: figureIds },
    ),
  deleteFigure: (id: string, figureId: string) =>
    del<{ paper_id: string; deleted: string; count: number; items: FigureAnalysisItem[] }>(
      `/papers/${id}/figures/${figureId}`,
    ),
  deleteFigures: (id: string, figureIds: string[]) =>
    post<{
      paper_id: string;
      deleted_ids: string[];
      deleted_count: number;
      count: number;
      items: FigureAnalysisItem[];
    }>(
      `/papers/${id}/figures/delete`,
      { figure_ids: figureIds },
    ),
  reasoningAnalysis: (
    id: string,
    options?: {
      reasoningLevel?: AgentReasoningLevel;
      detailLevel?: AnalysisDetailLevel;
      contentSource?: PaperContentSource;
      evidenceMode?: PaperEvidenceMode;
    },
  ) => {
    const params = new URLSearchParams();
    if (options?.reasoningLevel) params.set("reasoning_level", options.reasoningLevel);
    if (options?.detailLevel) params.set("detail_level", options.detailLevel);
    if (options?.contentSource) params.set("content_source", options.contentSource);
    if (options?.evidenceMode) params.set("evidence_mode", options.evidenceMode);
    const query = params.toString();
    return post<ReasoningAnalysisResponse>(`/papers/${id}/reasoning${query ? `?${query}` : ""}`);
  },
  reasoningAnalysisAsync: (
    id: string,
    options?: {
      reasoningLevel?: AgentReasoningLevel;
      detailLevel?: AnalysisDetailLevel;
      contentSource?: PaperContentSource;
      evidenceMode?: PaperEvidenceMode;
    },
  ) => {
    const params = new URLSearchParams();
    if (options?.reasoningLevel) params.set("reasoning_level", options.reasoningLevel);
    if (options?.detailLevel) params.set("detail_level", options.detailLevel);
    if (options?.contentSource) params.set("content_source", options.contentSource);
    if (options?.evidenceMode) params.set("evidence_mode", options.evidenceMode);
    const query = params.toString();
    return post<{ task_id: string; status: string; message?: string }>(
      `/papers/${id}/reasoning/async${query ? `?${query}` : ""}`,
    );
  },
  pdfUrl: (id: string, _arxivId?: string) => {
    return `/papers/${id}/pdf`;
  },
  downloadPdf: (id: string) =>
    post<{ status: string; pdf_path: string }>(`/papers/${id}/download-pdf`),
  downloadPdfAsync: (id: string) =>
    post<{ task_id: string; status: string; message?: string }>(`/papers/${id}/download-pdf-async`),
  updateSource: (
    id: string,
    body: {
      source_url?: string | null;
      pdf_url?: string | null;
      doi?: string | null;
      arxiv_id?: string | null;
    },
  ) =>
    patch<{
      status: string;
      paper_id: string;
      local_pdf_cleared: boolean;
      metadata: Record<string, unknown>;
      arxiv_id: string;
      pdf_path?: string | null;
    }>(`/papers/${id}/source`, body),
  updateMetadata: (
    id: string,
    body: {
      title?: string | null;
      abstract?: string | null;
      keywords?: string[] | null;
      title_zh?: string | null;
      abstract_zh?: string | null;
      auto_translate?: boolean;
    },
  ) => patch<Paper>(`/papers/${id}/metadata`, body),
  replacePdf: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return postForm<{ status: string; paper_id: string; pdf_path: string }>(
      `/papers/${id}/upload-pdf`,
      form,
    );
  },
  uploadPdf: (data: {
    file: File;
    title?: string;
    arxivId?: string;
    topicId?: string;
  }) => {
    const form = new FormData();
    form.append("file", data.file);
    if (data.title) form.append("title", data.title);
    if (data.arxivId) form.append("arxiv_id", data.arxivId);
    if (data.topicId) form.append("topic_id", data.topicId);
    return postForm<PdfUploadResult>("/papers/upload-pdf", form);
  },
  figureImageUrl: (paperId: string, figureId: string) => {
    return `/papers/${paperId}/figures/${figureId}/image`;
  },
  aiExplain: (
    id: string,
    text: string,
    action: PaperReaderAction,
    question?: string,
  ) =>
    post<{ action: string; result: string }>(`/papers/${id}/ai/explain`, { text, action, question }),
  readerQuery: (
    id: string,
    body: {
      scope: PaperReaderScope;
      action: PaperReaderAction;
      text?: string;
      question?: string;
      figure_id?: string;
      image_base64?: string;
      page_number?: number;
    },
  ) => post<PaperReaderQueryResponse>(`/papers/${id}/reader/query`, body),
  getReaderDocument: (id: string) =>
    get<PaperReaderDocumentResponse>(`/papers/${id}/reader/document`),
  getReaderNotes: (id: string) =>
    get<{ items: PaperReaderNote[] }>(`/papers/${id}/reader/notes`),
  generateReaderNoteDraft: (
    id: string,
    body: {
      text: string;
      quote?: string;
      page_number?: number;
      anchor_source?: "pdf_selection" | "ocr_block" | null;
      anchor_id?: string | null;
      section_id?: string | null;
      section_title?: string | null;
    },
  ) => post<{ item: PaperReaderNote }>(`/papers/${id}/reader/note-draft`, body),
  saveReaderNote: (
    id: string,
    body: {
      id?: string;
      kind?: "general" | "text" | "figure";
      title?: string;
      content: string;
      quote?: string;
      page_number?: number;
      figure_id?: string;
      color?: "amber" | "blue" | "emerald" | "rose" | "violet" | "slate";
      tags?: string[];
      pinned?: boolean;
      status?: "draft" | "saved";
      source?: "manual" | "ai_draft";
      anchor_source?: "pdf_selection" | "ocr_block" | null;
      anchor_id?: string | null;
      section_id?: string | null;
      section_title?: string | null;
    },
  ) => put<{ item: PaperReaderNote; items: PaperReaderNote[] }>(`/papers/${id}/reader/notes`, body),
  deleteReaderNote: (id: string, noteId: string) =>
    del<{ deleted: string; items: PaperReaderNote[] }>(`/papers/${id}/reader/notes/${encodeURIComponent(noteId)}`),
  analyzeAsync: (
    id: string,
    body?: {
      detail_level?: AnalysisDetailLevel;
      reasoning_level?: AgentReasoningLevel;
      content_source?: PaperContentSource;
      evidence_mode?: PaperEvidenceMode;
    },
  ) => post<{ task_id: string; status: string; message?: string }>(`/papers/${id}/analyze`, body || {}),
  analysis: (id: string) => get<{ item: PaperAnalysisBundle | null }>(`/papers/${id}/analysis`),
  retryAnalysis: (
    id: string,
    body?: {
      detail_level?: AnalysisDetailLevel;
      reasoning_level?: AgentReasoningLevel;
      content_source?: PaperContentSource;
      evidence_mode?: PaperEvidenceMode;
    },
  ) => post<{ task_id: string; status: string; message?: string }>(`/papers/${id}/analysis/retry`, body || {}),
};

export interface FigureAnalysisItem {
  id?: string;
  page_number: number;
  image_index?: number;
  image_type: string;
  figure_label?: string | null;
  caption: string;
  description: string;
  ocr_markdown?: string;
  analysis_markdown?: string;
  candidate_source?: string | null;
  analyzed?: boolean;
  image_url?: string | null;
  has_image?: boolean;
}

export interface PaperOcrStatus {
  paper_id: string;
  status: string;
  available: boolean;
  updated_at?: string | null;
  markdown_chars: number;
  has_structured_output: boolean;
  error?: string | null;
  output_root?: string | null;
  model_dir?: string | null;
}

export const projectApi = {
  workflowPresets: () =>
    get<{
      items: ProjectWorkflowPreset[];
      planned_items?: ProjectWorkflowPreset[];
      action_items: ProjectRunActionPreset[];
      agent_templates: ProjectAgentTemplate[];
      role_templates?: ProjectAgentTemplate[];
      engine_profiles?: ProjectEngineProfile[];
      default_engine_bindings?: {
        executor_engine_id?: string | null;
        reviewer_engine_id?: string | null;
      };
    }>("/projects/workflow-presets"),
  companionOverview: (params?: { project_limit?: number; task_limit?: number }) => {
    const query = new URLSearchParams();
    if (params?.project_limit) query.set("project_limit", String(Math.min(Math.max(params.project_limit, 1), 100)));
    if (params?.task_limit) query.set("task_limit", String(Math.min(Math.max(params.task_limit, 1), 100)));
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return get<{
      items: ProjectCompanionOverviewItem[];
      tasks: Record<string, unknown>[];
      acp: Record<string, unknown>;
    }>(`/projects/companion/overview${suffix}`);
  },
  list: () => get<{ items: Project[] }>("/projects"),
  detail: (projectId: string) => get<{ item: Project }>(`/projects/${encodeURIComponent(projectId)}`),
  workspaceContext: (projectId: string) =>
    get<{ item: ProjectWorkspaceContext }>(`/projects/${encodeURIComponent(projectId)}/workspace-context`),
  companionSnapshot: (
    projectId: string,
    params?: {
      task_limit?: number;
      session_limit?: number;
      include_latest_session_messages?: boolean;
      latest_session_message_limit?: number;
    },
  ) => {
    const query = new URLSearchParams();
    if (params?.task_limit) query.set("task_limit", String(params.task_limit));
    if (params?.session_limit) query.set("session_limit", String(params.session_limit));
    if (params?.include_latest_session_messages !== undefined) {
      query.set("include_latest_session_messages", String(params.include_latest_session_messages));
    }
    if (params?.latest_session_message_limit) {
      query.set("latest_session_message_limit", String(params.latest_session_message_limit));
    }
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return get<{ item: ProjectCompanionSnapshot }>(
      `/projects/${encodeURIComponent(projectId)}/companion-snapshot${suffix}`,
    );
  },
  create: (body: ProjectCreate) => post<{ item: Project }>("/projects", body),
  update: (projectId: string, body: ProjectUpdate) =>
    patch<{ item: Project }>(`/projects/${encodeURIComponent(projectId)}`, body),
  delete: (projectId: string) => del<{ deleted: string }>(`/projects/${encodeURIComponent(projectId)}`),
  touch: (projectId: string) => post<{ ok: boolean; project_id: string; last_accessed_at?: string | null }>(`/projects/${encodeURIComponent(projectId)}/touch`),
  listPapers: (projectId: string) =>
    get<{ items: ProjectPaper[] }>(`/projects/${encodeURIComponent(projectId)}/papers`),
  addPaper: (projectId: string, paperId: string, note?: string) =>
    post<{ item: ProjectPaper }>(`/projects/${encodeURIComponent(projectId)}/papers`, {
      paper_id: paperId,
      note,
    }),
  removePaper: (projectId: string, paperId: string) =>
    del<{ deleted: string }>(
      `/projects/${encodeURIComponent(projectId)}/papers/${encodeURIComponent(paperId)}`,
    ),
  listReports: (projectId: string, limit = 50) =>
    get<{ items: ProjectReport[] }>(
      `/projects/${encodeURIComponent(projectId)}/reports?limit=${limit}`,
    ),
  listTargets: (projectId: string) =>
    get<{ items: ProjectDeploymentTarget[] }>(`/projects/${encodeURIComponent(projectId)}/targets`),
  createTarget: (
    projectId: string,
    body: {
      label: string;
      workspace_server_id?: string;
      workdir?: string;
      remote_workdir?: string;
      dataset_root?: string;
      checkpoint_root?: string;
      output_root?: string;
      enabled?: boolean;
      is_primary?: boolean;
    },
  ) => post<{ item: ProjectDeploymentTarget }>(`/projects/${encodeURIComponent(projectId)}/targets`, body),
  updateTarget: (
    projectId: string,
    targetId: string,
    body: {
      label?: string;
      workspace_server_id?: string;
      workdir?: string;
      remote_workdir?: string;
      dataset_root?: string;
      checkpoint_root?: string;
      output_root?: string;
      enabled?: boolean;
      is_primary?: boolean;
    },
  ) =>
    patch<{ item: ProjectDeploymentTarget }>(
      `/projects/${encodeURIComponent(projectId)}/targets/${encodeURIComponent(targetId)}`,
      body,
    ),
  deleteTarget: (projectId: string, targetId: string) =>
    del<{ deleted: string }>(
      `/projects/${encodeURIComponent(projectId)}/targets/${encodeURIComponent(targetId)}`,
    ),
  listRuns: (projectId: string, limit = 50) =>
    get<{ items: ProjectRun[] }>(`/projects/${encodeURIComponent(projectId)}/runs?limit=${limit}`),
  createRun: (
    projectId: string,
    body: {
      target_id?: string;
      workflow_type: ProjectWorkflowType;
      title?: string;
      prompt: string;
      paper_ids?: string[];
      execution_command?: string;
      max_iterations?: number;
      executor_engine_id?: string;
      reviewer_engine_id?: string;
      executor_model?: string;
      reviewer_model?: string;
      auto_proceed?: boolean;
      human_checkpoint_enabled?: boolean;
      notification_recipients?: string[];
      metadata?: Record<string, unknown>;
    },
  ) => post<{ item: ProjectRun }>(`/projects/${encodeURIComponent(projectId)}/runs`, body),
  getRun: (runId: string) => get<{ item: ProjectRun }>(`/project-runs/${encodeURIComponent(runId)}`),
  listRunLiteratureCandidates: (runId: string) =>
    get<{ paper_index: ProjectPaperRef[]; items: ProjectPaperRef[] }>(
      `/project-runs/${encodeURIComponent(runId)}/literature-candidates`,
    ),
  importRunLiteratureCandidates: (
    runId: string,
    body: { candidate_ref_ids: string[]; link_to_project?: boolean },
  ) =>
    post<{ imported_paper_ids: string[]; linked_paper_ids: string[]; item: ProjectRun }>(
      `/project-runs/${encodeURIComponent(runId)}/literature-candidates/import`,
      body,
    ),
  deleteRun: (runId: string, options?: { deleteArtifacts?: boolean }) =>
    del<{
      deleted: string;
      artifacts_deleted: boolean;
      deleted_paths: string[];
      skipped_paths: string[];
      deleted_task_ids: string[];
      deleted_generated_content_ids: string[];
    }>(
      `/project-runs/${encodeURIComponent(runId)}?delete_artifacts=${options?.deleteArtifacts ? "true" : "false"}`,
    ),
  respondRunCheckpoint: (
    runId: string,
    body: {
      action: "approve" | "reject";
      comment?: string;
    },
  ) => post<{ item: ProjectRun }>(`/project-runs/${encodeURIComponent(runId)}/checkpoint/respond`, body),
  retryRun: (runId: string) =>
    post<{ item: ProjectRun }>(`/project-runs/${encodeURIComponent(runId)}/retry`),
  createRunAction: (
    runId: string,
    body: {
      action_type: ProjectRunActionType;
      prompt: string;
      workflow_type?: ProjectWorkflowType;
      metadata?: Record<string, unknown>;
    },
  ) =>
    post<{ item: ProjectRunAction }>(`/project-runs/${encodeURIComponent(runId)}/actions`, body),
  listRepos: (projectId: string) =>
    get<{ items: ProjectRepo[] }>(`/projects/${encodeURIComponent(projectId)}/repos`),
  createRepo: (
    projectId: string,
    body: {
      repo_url: string;
      local_path?: string;
      cloned_at?: string;
      is_workdir_repo?: boolean;
    },
  ) => post<{ item: ProjectRepo }>(`/projects/${encodeURIComponent(projectId)}/repos`, body),
  updateRepo: (
    projectId: string,
    repoId: string,
    body: {
      repo_url?: string;
      local_path?: string;
      cloned_at?: string;
      is_workdir_repo?: boolean;
    },
  ) =>
    patch<{ item: ProjectRepo }>(
      `/projects/${encodeURIComponent(projectId)}/repos/${encodeURIComponent(repoId)}`,
      body,
    ),
  deleteRepo: (projectId: string, repoId: string) =>
    del<{ deleted: string }>(
      `/projects/${encodeURIComponent(projectId)}/repos/${encodeURIComponent(repoId)}`,
    ),
  repoCommits: (projectId: string, repoId: string, limit = 20) =>
    get<{ items: ProjectRepoCommit[] }>(
      `/projects/${encodeURIComponent(projectId)}/repos/${encodeURIComponent(repoId)}/commits?limit=${limit}`,
    ),
  listIdeas: (projectId: string) =>
    get<{ items: ProjectIdea[] }>(`/projects/${encodeURIComponent(projectId)}/ideas`),
  createIdea: (
    projectId: string,
    body: { title: string; content: string; paper_ids?: string[] },
  ) => post<{ item: ProjectIdea }>(`/projects/${encodeURIComponent(projectId)}/ideas`, body),
  generateIdea: (
    projectId: string,
    body: { paper_ids?: string[]; repo_ids?: string[]; focus?: string },
  ) =>
    post<{ item: ProjectIdea }>(
      `/projects/${encodeURIComponent(projectId)}/ideas/generate`,
      body,
    ),
  generateIdeaAsync: (
    projectId: string,
    body: { paper_ids?: string[]; repo_ids?: string[]; focus?: string },
  ) =>
    post<{ task_id: string; status: string; message: string }>(
      `/projects/${encodeURIComponent(projectId)}/ideas/generate/async`,
      body,
    ),
  updateIdea: (
    projectId: string,
    ideaId: string,
    body: { title?: string; content?: string; paper_ids?: string[] },
  ) =>
    patch<{ item: ProjectIdea }>(
      `/projects/${encodeURIComponent(projectId)}/ideas/${encodeURIComponent(ideaId)}`,
      body,
    ),
  deleteIdea: (projectId: string, ideaId: string) =>
    del<{ deleted: string }>(
      `/projects/${encodeURIComponent(projectId)}/ideas/${encodeURIComponent(ideaId)}`,
    ),
};

export const agentConfigsApi = {
  listConfigs: () => get<{ items: AgentCliConfig[] }>("/agents/configs"),
  saveConfig: (body: {
    agent_type: string;
    label?: string;
    enabled?: boolean;
    command?: string;
    args?: string[];
    provider?: string;
    base_url?: string;
    api_key?: string;
    default_model?: string;
    workspace_server_id?: string;
    execution_mode?: "auto" | "local" | "ssh";
    metadata?: Record<string, unknown>;
  }) => post<{ item: AgentCliConfig }>("/agents/configs", body),
  updateConfig: (
    configId: string,
    body: {
      agent_type?: string;
      label?: string;
      enabled?: boolean;
      command?: string;
      args?: string[];
      provider?: string;
      base_url?: string;
      api_key?: string;
      default_model?: string;
      workspace_server_id?: string;
      execution_mode?: "auto" | "local" | "ssh";
      metadata?: Record<string, unknown>;
    },
  ) => patch<{ item: AgentCliConfig }>(`/agents/configs/${encodeURIComponent(configId)}`, body),
  deleteConfig: (configId: string) =>
    del<{ deleted: string }>(`/agents/configs/${encodeURIComponent(configId)}`),
  detect: () => post<{ items: AgentDetectionItem[] }>("/agents/detect"),
  testConfig: (
    configId: string,
    body?: {
      prompt?: string;
      workspace_path?: string;
      workspace_server_id?: string;
      timeout_sec?: number;
    },
  ) => post<{ item: Record<string, unknown> }>(`/agents/configs/${encodeURIComponent(configId)}/test`, body || {}),
};

/* ========== 摄入 ========== */
export interface ReferenceImportEntry {
  scholar_id: string | null;
  title: string;
  year: number | null;
  venue: string | null;
  citation_count: number | null;
  arxiv_id: string | null;
  abstract: string | null;
  direction?: string;
}

export interface ImportTaskStatus {
  task_id: string;
  status: "running" | "completed" | "failed";
  total: number;
  completed: number;
  imported: number;
  skipped: number;
  failed: number;
  current: string;
  error?: string;
  results: { title: string; status: string; reason?: string; paper_id?: string; source?: string }[];
}

export const ingestApi = {
  searchLiterature: (data: {
    query: string;
    max_results?: number;
    source_scope?: LiteratureSourceScope;
    sort_mode?: "relevance" | "time" | "impact";
    venue_tier?: LiteratureVenueTier;
    venue_type?: LiteratureVenueType;
    venue_names?: string[];
    from_year?: number;
    date_from?: string;
    date_to?: string;
  }) =>
    post<ExternalLiteratureSearchResult>("/ingest/literature/search", data),
  importLiterature: (data: {
    entries: ExternalLiteraturePaper[];
    topic_id?: string;
  }) =>
    post<IngestResult>("/ingest/literature", data),
  arxiv: (
    query: string,
    maxResults = 20,
    topicId?: string,
    sortBy: ArxivSortBy = "submittedDate",
    dateFrom?: string,
    dateTo?: string,
  ) => {
    const params = new URLSearchParams({ query, max_results: String(maxResults), sort_by: sortBy });
    if (topicId) params.append("topic_id", topicId);
    if (dateFrom) params.append("date_from", dateFrom);
    if (dateTo) params.append("date_to", dateTo);
    return post<IngestResult>(`/ingest/arxiv?${params}`);
  },
  arxivIds: (arxivIds: string[], topicId?: string, downloadPdf = false) =>
    post<IngestResult>("/ingest/arxiv-ids", {
      arxiv_ids: arxivIds,
      topic_id: topicId,
      download_pdf: downloadPdf,
    }),
  importReferences: (data: {
    source_paper_id: string;
    source_paper_title: string;
    entries: ReferenceImportEntry[];
    topic_ids?: string[];
  }) => post<{ task_id: string; total: number }>("/ingest/references", data),
  importStatus: (taskId: string) =>
    get<ImportTaskStatus>(`/ingest/references/status/${taskId}`),
};

/* ========== Pipeline ========== */
export const pipelineApi = {
  skim: (paperId: string) => post<SkimReport>(`/pipelines/skim/${paperId}`),
  skimAsync: (paperId: string) => post<{ task_id: string; status: string; message?: string }>(`/pipelines/skim/${paperId}/async`),
  deep: (
    paperId: string,
    options?: { detailLevel?: AnalysisDetailLevel; contentSource?: PaperContentSource; evidenceMode?: PaperEvidenceMode },
  ) => {
    const params = new URLSearchParams();
    if (options?.detailLevel) params.set("detail_level", options.detailLevel);
    if (options?.contentSource) params.set("content_source", options.contentSource);
    if (options?.evidenceMode) params.set("evidence_mode", options.evidenceMode);
    const query = params.toString();
    return post<DeepDiveReport>(`/pipelines/deep/${paperId}${query ? `?${query}` : ""}`);
  },
  deepAsync: (
    paperId: string,
    options?: { detailLevel?: AnalysisDetailLevel; contentSource?: PaperContentSource; evidenceMode?: PaperEvidenceMode },
  ) => {
    const params = new URLSearchParams();
    if (options?.detailLevel) params.set("detail_level", options.detailLevel);
    if (options?.contentSource) params.set("content_source", options.contentSource);
    if (options?.evidenceMode) params.set("evidence_mode", options.evidenceMode);
    const query = params.toString();
    return post<{ task_id: string; status: string; message?: string }>(
      `/pipelines/deep/${paperId}/async${query ? `?${query}` : ""}`,
    );
  },
  embed: (paperId: string) => post<{ status: string; paper_id: string }>(`/pipelines/embed/${paperId}`),
  embedAsync: (paperId: string) => post<{ task_id: string; status: string; message?: string }>(`/pipelines/embed/${paperId}/async`),
  runs: (limit = 30) => get<{ items: PipelineRun[] }>(`/pipelines/runs?limit=${limit}`),
};

/* ========== RAG ========== */
export const ragApi = {
  ask: (data: AskRequest) => post<AskResponse>("/rag/ask", data),
};

/* ========== 引用 ========== */
export const citationApi = {
  syncPaper: (paperId: string, limit = 8) =>
    post<CitationSyncResult>(`/citations/sync/${paperId}?limit=${limit}`),
  syncTopic: (topicId: string, paperLimit = 30, edgeLimit = 6) =>
    post<CitationSyncResult>(`/citations/sync/topic/${topicId}?paper_limit=${paperLimit}&edge_limit_per_paper=${edgeLimit}`),
  syncIncremental: (paperLimit = 40, edgeLimit = 6) =>
    post<CitationSyncResult>(`/citations/sync/incremental?paper_limit=${paperLimit}&edge_limit_per_paper=${edgeLimit}`),
};

/* ========== 行动记录 ========== */
export interface CollectionAction {
  id: string;
  action_type: string;
  title: string;
  query: string | null;
  topic_id: string | null;
  paper_count: number;
  created_at: string;
}

export const actionApi = {
  list: (opts: { actionType?: string; topicId?: string; limit?: number; offset?: number } = {}) => {
    const params = new URLSearchParams();
    if (opts.actionType) params.set("action_type", opts.actionType);
    if (opts.topicId) params.set("topic_id", opts.topicId);
    if (opts.limit) params.set("limit", String(opts.limit));
    if (opts.offset) params.set("offset", String(opts.offset));
    return get<{ items: CollectionAction[]; total: number }>(`/actions?${params}`);
  },
  detail: (id: string) => get<CollectionAction>(`/actions/${id}`),
  delete: (id: string) => del<{ deleted: string }>(`/actions/${id}`),
  papers: (id: string, limit = 200) =>
    get<{ action_id: string; items: { id: string; title: string; arxiv_id: string; publication_date: string | null; read_status: string; citation_count?: number | null }[] }>(
      `/actions/${id}/papers?limit=${limit}`
    ),
};

/* ========== 图谱 ========== */
export const graphApi = {
  citationTree: (paperId: string, depth = 2) =>
    get<CitationTree>(`/graph/citation-tree/${paperId}?depth=${depth}`),
  timeline: (keyword: string, limit = 100) =>
    get<TimelineResponse>(`/graph/timeline?keyword=${encodeURIComponent(keyword)}&limit=${limit}`),
  quality: (keyword: string, limit = 120) =>
    get<GraphQuality>(`/graph/quality?keyword=${encodeURIComponent(keyword)}&limit=${limit}`),
  evolution: (keyword: string, limit = 160) =>
    get<EvolutionResponse>(`/graph/evolution/weekly?keyword=${encodeURIComponent(keyword)}&limit=${limit}`),
  survey: (keyword: string, limit = 120) =>
    get<SurveyResponse>(`/graph/survey?keyword=${encodeURIComponent(keyword)}&limit=${limit}`),
  researchGaps: (keyword: string, limit = 120) =>
    get<ResearchGapsResponse>(`/graph/research-gaps?keyword=${encodeURIComponent(keyword)}&limit=${limit}`),
  citationDetailAsync: (paperId: string, depth = 2) =>
    post<{ task_id: string; status: string; message?: string }>(
      `/graph/citation-detail/${paperId}/async?depth=${depth}`,
    ),
  topicNetworkAsync: (topicId: string) =>
    post<{ task_id: string; status: string; message?: string }>(
      `/graph/citation-network/topic/${topicId}/async`,
    ),
  topicDeepTraceAsync: (topicId: string) =>
    post<{ task_id: string; status: string; message?: string }>(
      `/graph/citation-network/topic/${topicId}/deep-trace-async`,
    ),
  insightAsync: (keyword: string, limit = 120) =>
    post<{ task_id: string; status: string; message?: string }>(
      `/graph/insight/async?keyword=${encodeURIComponent(keyword)}&limit=${limit}`,
    ),
  citationDetail: (paperId: string, options?: { refresh?: boolean }) =>
    get<CitationDetail>(`/graph/citation-detail/${paperId}${options?.refresh ? "?refresh=true" : ""}`),
  topicNetwork: (topicId: string) =>
    get<TopicCitationNetwork>(`/graph/citation-network/topic/${topicId}`),
  topicDeepTrace: (topicId: string) =>
    post<TopicCitationNetwork>(`/graph/citation-network/topic/${topicId}/deep-trace`),
  overview: () => get<LibraryOverview>('/graph/overview'),
  bridges: () => get<BridgesResponse>('/graph/bridges'),
  frontier: (days = 90) => get<FrontierResponse>(`/graph/frontier?days=${days}`),
  cocitationClusters: (minCocite = 2) =>
    get<CocitationResponse>(`/graph/cocitation-clusters?min_cocite=${minCocite}`),
  autoLink: (paperIds: string[]) =>
    post<{ papers: number; edges_linked: number; errors: number }>('/graph/auto-link', paperIds),
  similarityMap: (topicId?: string, limit = 200) =>
    get<SimilarityMapData>(`/graph/similarity-map?topic_id=${topicId || ""}&limit=${limit}`),
};

/* ========== Wiki ========== */
export const wikiApi = {
  paper: (paperId: string) => get<PaperWiki>(`/wiki/paper/${paperId}`),
  topic: (keyword: string, limit = 120) =>
    get<TopicWiki>(`/wiki/topic?keyword=${encodeURIComponent(keyword)}&limit=${limit}`),
};

/* ========== 简报 ========== */
export const briefApi = {
  daily: (data?: DailyBriefRequest) => post<DailyBriefResponse>("/brief/daily", data),
};

/* ========== 生成内容历史 ========== */
import type { GeneratedContent, GeneratedContentListItem } from "@/types";

export const generatedApi = {
  list: (type: string, limit = 50) =>
    get<{ items: GeneratedContentListItem[] }>(`/generated/list?type=${type}&limit=${limit}`),
  detail: (id: string) => get<GeneratedContent>(`/generated/${id}`),
  delete: (id: string) => del<{ deleted: string }>(`/generated/${id}`),
};

/* ========== 指标 ========== */
export const metricsApi = {
  costs: (days = 7) => get<CostMetrics>(`/metrics/costs?days=${days}`),
};

/* ========== LLM 配置 ========== */
import type {
  LLMProviderConfig,
  LLMProviderCreate,
  LLMProviderUpdate,
  ActiveLLMConfig,
  LLMProviderTestResult,
  LLMProviderPreset,
  AssistantSkillItem,
} from "@/types";

export const llmConfigApi = {
  list: () => get<{ items: LLMProviderConfig[] }>("/settings/llm-providers"),
  presets: () => get<{ items: LLMProviderPreset[] }>("/settings/llm-provider-presets"),
  create: (data: LLMProviderCreate) => post<LLMProviderConfig>("/settings/llm-providers", data),
  update: (id: string, data: LLMProviderUpdate) => patch<LLMProviderConfig>(`/settings/llm-providers/${id}`, data),
  delete: (id: string) => del<{ deleted: string }>(`/settings/llm-providers/${id}`),
  activate: (id: string) => post<LLMProviderConfig>(`/settings/llm-providers/${id}/activate`),
  deactivate: () => post<{ status: string }>("/settings/llm-providers/deactivate"),
  active: () => get<ActiveLLMConfig>("/settings/llm-providers/active"),
  test: (id: string) => post<LLMProviderTestResult>(`/settings/llm-providers/${id}/test`),
};

export const workspaceRootApi = {
  list: () => get<WorkspaceRootListResponse>("/settings/workspace-roots"),
  create: (path: string, title?: string) =>
    post<{ item: WorkspaceRootItem; items: WorkspaceRootItem[]; default_projects_root?: string }>("/settings/workspace-roots", { path, title }),
  update: (path: string, title: string) =>
    put<{ item: WorkspaceRootItem; items: WorkspaceRootItem[]; default_projects_root?: string }>("/settings/workspace-roots", { path, title }),
  setDefault: (path?: string | null) =>
    put<WorkspaceRootListResponse>("/settings/workspace-roots/default", { path: path || null }),
  delete: (path: string) => del<{ deleted: string; items: WorkspaceRootItem[]; default_projects_root?: string }>(`/settings/workspace-roots?path=${encodeURIComponent(path)}`),
};

export const assistantExecPolicyApi = {
  get: () => get<AssistantExecPolicy>("/settings/assistant-exec-policy"),
  update: (body: Partial<AssistantExecPolicy>) =>
    put<AssistantExecPolicy>("/settings/assistant-exec-policy", body),
};

export const assistantSkillApi = {
  list: () => get<{ items: AssistantSkillItem[]; roots: AssistantSkillRoot[] }>("/settings/assistant-skills"),
};

export const opencodeRuntimeApi = {
  status: () => get<OpenCodeRuntimeStatus>("/opencode/runtime"),
  start: (forceRestart = false) =>
    post<OpenCodeRuntimeStatus>(`/opencode/runtime/start?force_restart=${forceRestart}`),
  stop: () => post<OpenCodeRuntimeStatus>("/opencode/runtime/stop"),
};

export const mcpBridgeApi = {
  researchosHealth: () =>
    get<{
      ok: boolean;
      name: string;
      transport: string;
      endpoint: string;
      tool_count: number;
      tools: string[];
      auth_required: boolean;
    }>("/mcp/health/researchos"),
};

let dedicatedMcpApiSupportPromise: Promise<boolean> | null = null;

function isMcpEndpointMissing(error: unknown): boolean {
  return error instanceof Error && /^404\b/.test(error.message.trim());
}

async function supportsDedicatedMcpApi(): Promise<boolean> {
  if (!dedicatedMcpApiSupportPromise) {
    dedicatedMcpApiSupportPromise = request<{ paths?: Record<string, unknown> }>("/openapi.json")
      .then((schema) => Boolean(schema?.paths?.["/mcp/runtime"]))
      .catch(() => false);
  }
  return dedicatedMcpApiSupportPromise;
}

async function loadLegacyMcpCompat() {
  const health = await mcpBridgeApi.researchosHealth();
  const server: McpServerInfo = {
    name: health.name || "researchos",
    label: "ResearchOS MCP",
    transport: "stdio",
    command: health.endpoint || undefined,
    args: [],
    cwd: undefined,
    env: {},
    url: undefined,
    headers: {},
    enabled: true,
    builtin: true,
    timeout_sec: 30,
    status: health.ok ? "connected" : "disabled",
    connected: Boolean(health.ok),
    tool_count: health.tool_count || 0,
    tools: health.tools || [],
    last_error: health.ok ? null : "内置 MCP 健康检查未通过",
    last_connected_at: null,
    last_disconnected_at: null,
    session_id: null,
  };

  return {
    runtime: {
      available: true,
      connected_count: health.ok ? 1 : 0,
      enabled_count: 1,
      server_count: 1,
      builtin_count: 1,
      builtin_ready: Boolean(health.ok),
      builtin_tool_count: health.tool_count || 0,
      configured_count: 0,
      message: "ResearchOS 内置工具会在对话时自动提供给当前助手；这里仅管理扩展 MCP 配置。",
    } satisfies McpRuntimeStatus,
    servers: {
      items: [server],
    } satisfies { items: McpServerInfo[] },
    config: {
      version: 1,
      servers: {
        [server.name]: {
          name: server.name,
          label: server.label,
          transport: server.transport,
          command: server.command,
          args: server.args,
          cwd: server.cwd,
          env: server.env,
          url: server.url,
          headers: server.headers,
          enabled: server.enabled,
          builtin: server.builtin,
          timeout_sec: server.timeout_sec,
        },
      },
    } satisfies McpRegistryConfig,
  };
}

export const mcpApi = {
  runtime: async () => {
    if (!(await supportsDedicatedMcpApi())) {
      return (await loadLegacyMcpCompat()).runtime;
    }
    try {
      return await get<McpRuntimeStatus>("/mcp/runtime");
    } catch (error) {
      if (!isMcpEndpointMissing(error)) throw error;
      return (await loadLegacyMcpCompat()).runtime;
    }
  },
  servers: async () => {
    if (!(await supportsDedicatedMcpApi())) {
      return (await loadLegacyMcpCompat()).servers;
    }
    try {
      return await get<{ items: McpServerInfo[] }>("/mcp/servers");
    } catch (error) {
      if (!isMcpEndpointMissing(error)) throw error;
      return (await loadLegacyMcpCompat()).servers;
    }
  },
  connect: async (name: string) => {
    throw new Error(`MCP 配置 ${name} 不支持后端直连。ResearchOS 内置工具会在对话时自动提供给当前助手。`);
  },
  disconnect: async (name: string) => {
    throw new Error(`MCP 配置 ${name} 不支持后端断开。ResearchOS 内置工具会在对话时自动提供给当前助手。`);
  },
  config: async () => {
    if (!(await supportsDedicatedMcpApi())) {
      return (await loadLegacyMcpCompat()).config;
    }
    try {
      return await get<McpRegistryConfig>("/mcp/config");
    } catch (error) {
      if (!isMcpEndpointMissing(error)) throw error;
      return (await loadLegacyMcpCompat()).config;
    }
  },
  updateConfig: async (body: McpRegistryConfig) => {
    if (!(await supportsDedicatedMcpApi())) {
      throw new Error("当前后端未启用 MCP 配置接口");
    }
    try {
      return await put<McpRegistryConfig>("/mcp/config", body);
    } catch (error) {
      if (!isMcpEndpointMissing(error)) throw error;
      throw new Error("当前后端仍在兼容模式，需重启到最新后端后才能保存 MCP 配置");
    }
  },
};

export const acpApi = {
  runtime: () => get<AcpRuntimeStatus>("/acp/runtime"),
  servers: () => get<{ items: AcpServerInfo[] }>("/acp/servers"),
  config: () => get<AcpRegistryConfig>("/acp/config"),
  updateConfig: (body: AcpRegistryConfig) => put<AcpRegistryConfig>("/acp/config", body),
  connect: (name: string) => post<{ item: AcpServerInfo }>(`/acp/servers/${encodeURIComponent(name)}/connect`),
  disconnect: (name: string) => post<{ item: AcpServerInfo }>(`/acp/servers/${encodeURIComponent(name)}/disconnect`),
  test: (
    name: string,
    body?: {
      prompt?: string;
      workspace_path?: string;
      workspace_server_id?: string;
      timeout_sec?: number;
    },
  ) => post<{ item: Record<string, unknown> }>(`/acp/servers/${encodeURIComponent(name)}/test`, body || {}),
};

export const opencodeApi = {
  projectList: (directory: string) =>
    get<OpenCodeProjectInfo[]>(`/opencode/api/project?directory=${encodeURIComponent(directory)}`),
  projectCurrent: (directory: string) =>
    get<OpenCodeProjectInfo>(`/opencode/api/project/current?directory=${encodeURIComponent(directory)}`),
  initGit: (directory: string) =>
    post<boolean>(`/opencode/api/project/git/init?directory=${encodeURIComponent(directory)}`),
  config: (directory: string) =>
    get<OpenCodeConfig>(`/opencode/api/config?directory=${encodeURIComponent(directory)}`),
  updateConfig: (directory: string, body: OpenCodeConfig) =>
    patch<OpenCodeConfig>(`/opencode/api/config?directory=${encodeURIComponent(directory)}`, body),
  mcpStatus: (directory: string) =>
    get<OpenCodeMcpMap>(`/opencode/api/mcp?directory=${encodeURIComponent(directory)}`),
  connectMcp: (directory: string, name: string) =>
    post<void>(`/opencode/api/mcp/${encodeURIComponent(name)}/connect?directory=${encodeURIComponent(directory)}`),
  disconnectMcp: (directory: string, name: string) =>
    post<void>(`/opencode/api/mcp/${encodeURIComponent(name)}/disconnect?directory=${encodeURIComponent(directory)}`),
  vcs: (directory: string) =>
    get<OpenCodeVcsInfo>(`/opencode/api/vcs?directory=${encodeURIComponent(directory)}`),
  agents: (directory: string) =>
    get<OpenCodeAgentInfo[]>(`/opencode/api/agent?directory=${encodeURIComponent(directory)}`),
  sessionStatus: (directory: string) =>
    get<Record<string, OpenCodeSessionStatus>>(
      `/opencode/api/session/status?directory=${encodeURIComponent(directory)}`,
    ),
  createSession: (
    directory: string,
    body: {
      title?: string;
      parentID?: string;
      workspaceID?: string;
      permission?: unknown[];
    } = {},
  ) =>
    post<OpenCodeSessionInfo>(`/opencode/api/session?directory=${encodeURIComponent(directory)}`, body),
  getSession: (directory: string, sessionId: string) =>
    get<OpenCodeSessionInfo>(
      `/opencode/api/session/${encodeURIComponent(sessionId)}?directory=${encodeURIComponent(directory)}`,
    ),
  deleteSession: (directory: string, sessionId: string) =>
    del<boolean>(
      `/opencode/api/session/${encodeURIComponent(sessionId)}?directory=${encodeURIComponent(directory)}`,
    ),
  abortSession: (directory: string, sessionId: string) =>
    post<void>(
      `/opencode/api/session/${encodeURIComponent(sessionId)}/abort?directory=${encodeURIComponent(directory)}`,
    ),
  promptAsync: (
    directory: string,
    sessionId: string,
    body: {
      noReply?: boolean;
      agent?: string;
      system?: string;
      parts: OpenCodePromptPart[];
    },
  ) =>
    post<void>(
      `/opencode/api/session/${encodeURIComponent(sessionId)}/prompt_async?directory=${encodeURIComponent(directory)}`,
      body,
  ),
};

export const assistantWorkspaceApi = {
  servers: () => get<{ items: AssistantWorkspaceServer[] }>("/agent/workspace/servers"),
  createServer: (body: AssistantWorkspaceServerPayload) =>
    post<{ item: AssistantWorkspaceServer }>("/agent/workspace/servers", body),
  updateServer: (serverId: string, body: AssistantWorkspaceServerPayload) =>
    put<{ item: AssistantWorkspaceServer }>(`/agent/workspace/servers/${encodeURIComponent(serverId)}`, body),
  deleteServer: (serverId: string) =>
    del<{ deleted: string }>(`/agent/workspace/servers/${encodeURIComponent(serverId)}`),
  probeSsh: (body: AssistantWorkspaceSshProbePayload) =>
    post<AssistantWorkspaceSshProbeResult>("/agent/workspace/ssh/probe", body),
  overview: (path: string, depth = 2, maxEntries = 0, serverId = "local") =>
    get<AssistantWorkspaceOverview>(
      `/agent/workspace/overview?path=${encodeURIComponent(path)}&depth=${depth}&max_entries=${maxEntries}&server_id=${encodeURIComponent(serverId)}`,
    ),
  initGit: (path: string, serverId = "local") =>
    post<AssistantWorkspaceGitInitResponse>("/agent/workspace/git/init", { path, server_id: serverId }),
  createGitBranch: (path: string, branchName: string, checkout = true, serverId = "local") =>
    post<AssistantWorkspaceGitBranchResponse>("/agent/workspace/git/branch", {
      path,
      branch_name: branchName,
      checkout,
      server_id: serverId,
    }),
  stageGit: (path: string, filePath?: string, serverId = "local") =>
    post<AssistantWorkspaceGitActionResponse>("/agent/workspace/git/stage", {
      path,
      file_path: filePath || null,
      server_id: serverId,
    }),
  unstageGit: (path: string, filePath?: string, serverId = "local") =>
    post<AssistantWorkspaceGitActionResponse>("/agent/workspace/git/unstage", {
      path,
      file_path: filePath || null,
      server_id: serverId,
    }),
  discardGit: (path: string, filePath: string, serverId = "local") =>
    post<AssistantWorkspaceGitActionResponse>("/agent/workspace/git/discard", {
      path,
      file_path: filePath,
      server_id: serverId,
    }),
  commitGit: (path: string, message: string, serverId = "local") =>
    post<AssistantWorkspaceGitActionResponse>("/agent/workspace/git/commit", {
      path,
      message,
      server_id: serverId,
    }),
  syncGit: (path: string, action: "fetch" | "pull" | "push", serverId = "local") =>
    post<AssistantWorkspaceGitActionResponse>("/agent/workspace/git/sync", {
      path,
      action,
      server_id: serverId,
    }),
  gitDiff: (path: string, filePath?: string, serverId = "local") =>
    get<AssistantWorkspaceDiffResponse>(
      `/agent/workspace/git/diff?path=${encodeURIComponent(path)}${filePath ? `&file_path=${encodeURIComponent(filePath)}` : ""}&server_id=${encodeURIComponent(serverId)}`,
    ),
  runTerminal: (path: string, command: string, timeoutSec = 240, serverId = "local") =>
    post<AssistantWorkspaceTerminalResult>("/agent/workspace/terminal/run", {
      path,
      server_id: serverId,
      command,
      timeout_sec: timeoutSec,
    }),
  createTerminalSession: (path: string, cols = 120, rows = 32, serverId = "local") =>
    post<{ session: AssistantWorkspaceTerminalSessionInfo }>("/agent/workspace/terminal/session", {
      path,
      server_id: serverId,
      cols,
      rows,
    }),
  closeTerminalSession: (sessionId: string) =>
    del<void>(`/agent/workspace/terminal/session/${encodeURIComponent(sessionId)}`),
  terminalWebSocketUrl: (sessionId: string) => {
    const path = `/agent/workspace/terminal/session/${encodeURIComponent(sessionId)}/ws`;
    return getPathAccessToken(path).then((token) => (
      buildWebSocketUrl(path, token ? { token } : undefined)
    ));
  },
  readFile: (path: string, relativePath: string, maxChars = 120000, serverId = "local") =>
    get<AssistantWorkspaceFileResponse>(
      `/agent/workspace/file?path=${encodeURIComponent(path)}&relative_path=${encodeURIComponent(relativePath)}&max_chars=${maxChars}&server_id=${encodeURIComponent(serverId)}`,
    ),
  writeFile: (body: {
    path: string;
    server_id?: string;
    relative_path: string;
    content: string;
    create_dirs?: boolean;
    overwrite?: boolean;
  }) =>
    put<AssistantWorkspaceFileWriteResponse>("/agent/workspace/file", body),
  uploadFile: (path: string, file: File, relativePath?: string, serverId = "local") => {
    const form = new FormData();
    form.append("path", path);
    form.append("server_id", serverId);
    if (relativePath && relativePath.trim()) {
      form.append("relative_path", relativePath.trim());
    }
    form.append("file", file);
    return postForm<AssistantWorkspaceUploadResponse>("/agent/workspace/upload", form);
  },
  reveal: (path: string, serverId = "local") =>
    post<AssistantWorkspaceRevealResponse>("/agent/workspace/reveal", { path, server_id: serverId }),
};

/* ========== 写作助手 ========== */
import type { WritingTemplate, WritingResult, WritingRefineMessage, WritingRefineResult } from "@/types";

export const writingApi = {
  templates: () => get<{ items: WritingTemplate[] }>("/writing/templates"),
  process: (action: string, text: string, maxTokens = 4096) =>
    post<WritingResult>("/writing/process", { action, content: text, max_tokens: maxTokens }),
  processMultimodal: (action: string, content: string, imageBase64: string) =>
    post<WritingResult>("/writing/process-multimodal", { action, content, image_base64: imageBase64 }),
  generateImage: (prompt: string, imageBase64?: string | null, aspectRatio = "4:3") =>
    post<WritingResult>("/writing/generate-image", {
      prompt,
      image_base64: imageBase64 || undefined,
      aspect_ratio: aspectRatio,
    }),
  refine: (messages: WritingRefineMessage[], maxTokens = 4096) =>
    post<WritingRefineResult>("/writing/refine", { messages, max_tokens: maxTokens }),
};

/* ========== Agent ========== */
import type { AgentMessage } from "@/types";

export const agentApi = {
  chat: async (
    messages: AgentMessage[],
    options?: {
      confirmedActionId?: string;
      sessionId?: string;
      agentBackendId?: string | null;
      mode?: AgentMode;
      workspacePath?: string | null;
      workspaceServerId?: string | null;
      reasoningLevel?: AgentReasoningLevel;
      activeSkillIds?: string[];
    },
  ): Promise<Response> => {
    const url = `${getApiBase().replace(/\/+$/, "")}/agent/chat`;
    return fetchSSE(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages,
        confirmed_action_id: options?.confirmedActionId || null,
        session_id: options?.sessionId || null,
        agent_backend_id: options?.agentBackendId || null,
        mode: options?.mode || "build",
        workspace_path: options?.workspacePath || null,
        workspace_server_id: options?.workspaceServerId || null,
        reasoning_level: options?.reasoningLevel || "default",
        active_skill_ids: options?.activeSkillIds || [],
      }),
    });
  },
  confirm: async (actionId: string): Promise<Response> => {
    const url = `${getApiBase().replace(/\/+$/, "")}/agent/confirm/${actionId}`;
    return fetchSSE(url, { method: "POST" });
  },
  reject: async (actionId: string): Promise<Response> => {
    const url = `${getApiBase().replace(/\/+$/, "")}/agent/reject/${actionId}`;
    return fetchSSE(url, { method: "POST" });
  },
};

export const sessionApi = {
  create: (body?: {
    id?: string | null;
    directory?: string | null;
    workspace_path?: string | null;
    workspace_server_id?: string | null;
    agent_backend_id?: string | null;
    title?: string | null;
    mode?: AgentMode;
  }) =>
    post<OpenCodeSessionInfo>("/session", {
      id: body?.id || null,
      directory: body?.directory || body?.workspace_path || null,
      workspace_path: body?.workspace_path || body?.directory || null,
      workspace_server_id: body?.workspace_server_id || null,
      agent_backend_id: body?.agent_backend_id || null,
      title: body?.title || null,
      mode: body?.mode || "build",
    }).then((payload) => normalizeSessionInfo(payload as unknown as Record<string, unknown>, {
      id: body?.id || null,
      directory: body?.directory || body?.workspace_path || null,
      title: body?.title || null,
    }) as OpenCodeSessionInfo),
  status: () =>
    get<Record<string, OpenCodeSessionStatus>>("/session/status"),
  diff: (sessionId: string) =>
    get<unknown>(`/session/${encodeURIComponent(sessionId)}/diff`).then((payload) => normalizeSessionDiffEntries(payload)),
  state: (sessionId: string) =>
    get<{
      session: Record<string, unknown> | null;
      messages: unknown;
      permissions: unknown;
      status: unknown;
    }>(`/session/${encodeURIComponent(sessionId)}/state`).then((payload) => ({
      session: normalizeSessionInfo(payload.session, { id: sessionId }),
      messages: normalizeRecordArray(payload.messages),
      permissions: normalizeRecordArray(payload.permissions),
      status: normalizeSessionStatus(payload.status),
    })),
  messages: (sessionId: string, limit = 2000) =>
    get<Array<Record<string, unknown>>>(`/session/${encodeURIComponent(sessionId)}/message?limit=${limit}`),
  permissions: (sessionId: string) =>
    get<Array<Record<string, unknown>>>(`/session/${encodeURIComponent(sessionId)}/permissions`),
  deleteMessage: (sessionId: string, messageId: string) =>
    del<boolean>(`/session/${encodeURIComponent(sessionId)}/message/${encodeURIComponent(messageId)}`),
  revert: (sessionId: string, messageId: string) =>
    post<Record<string, unknown>>(
      `/session/${encodeURIComponent(sessionId)}/revert`,
      { message_id: messageId },
    ).then((payload) => normalizeSessionInfo(payload, { id: sessionId }) as OpenCodeSessionInfo),
  unrevert: (sessionId: string) =>
    post<Record<string, unknown>>(
      `/session/${encodeURIComponent(sessionId)}/unrevert`,
    ).then((payload) => normalizeSessionInfo(payload, { id: sessionId }) as OpenCodeSessionInfo),
  abort: (sessionId: string) =>
    post<boolean>(`/session/${encodeURIComponent(sessionId)}/abort`),
  prompt: async (
    sessionId: string,
    body: {
      parts: Array<Record<string, unknown>>;
      display_text?: string | null;
      mode?: AgentMode;
      workspace_path?: string | null;
      workspace_server_id?: string | null;
      agent_backend_id?: string | null;
      tools?: Record<string, boolean> | null;
      system?: string | null;
      variant?: string | null;
      reasoning_level?: AgentReasoningLevel;
      active_skill_ids?: string[];
      mounted_paper_ids?: string[];
      mounted_primary_paper_id?: string | null;
      noReply?: boolean;
    },
    options?: {
      signal?: AbortSignal;
    },
  ): Promise<Response> => {
    const url = `${getApiBase().replace(/\/+$/, "")}/session/${encodeURIComponent(sessionId)}/message`;
    return fetchSSE(url, {
      method: "POST",
      signal: options?.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        parts: body.parts,
        display_text: body.display_text || null,
        mode: body.mode || "build",
        workspace_path: body.workspace_path || null,
        workspace_server_id: body.workspace_server_id || null,
        agent_backend_id: body.agent_backend_id || null,
        tools: body.tools || null,
        system: body.system || null,
        variant: body.variant || null,
        reasoning_level: body.reasoning_level || body.variant || "default",
        active_skill_ids: body.active_skill_ids || [],
        mounted_paper_ids: body.mounted_paper_ids || [],
        mounted_primary_paper_id: body.mounted_primary_paper_id || null,
        noReply: body.noReply === true,
      }),
    });
  },
  promptDetached: (
    sessionId: string,
    body: {
      parts: Array<Record<string, unknown>>;
      display_text?: string | null;
      mode?: AgentMode;
      workspace_path?: string | null;
      workspace_server_id?: string | null;
      agent_backend_id?: string | null;
      tools?: Record<string, boolean> | null;
      system?: string | null;
      variant?: string | null;
      reasoning_level?: AgentReasoningLevel;
      active_skill_ids?: string[];
      mounted_paper_ids?: string[];
      mounted_primary_paper_id?: string | null;
    },
    options?: {
      signal?: AbortSignal;
    },
  ) =>
    post<{ accepted: boolean; session_id: string }>(
      `/session/${encodeURIComponent(sessionId)}/message/detached`,
      {
        parts: body.parts,
        display_text: body.display_text || null,
        mode: body.mode || "build",
        workspace_path: body.workspace_path || null,
        workspace_server_id: body.workspace_server_id || null,
        agent_backend_id: body.agent_backend_id || null,
        tools: body.tools || null,
        system: body.system || null,
        variant: body.variant || null,
        reasoning_level: body.reasoning_level || body.variant || "default",
        active_skill_ids: body.active_skill_ids || [],
        mounted_paper_ids: body.mounted_paper_ids || [],
        mounted_primary_paper_id: body.mounted_primary_paper_id || null,
      },
      { signal: options?.signal },
    ),
  replyPermission: async (
    sessionId: string,
    permissionId: string,
    body: {
      response: string;
      message?: string | null;
      answers?: string[][];
    },
    options?: {
      signal?: AbortSignal;
    },
  ): Promise<Response> => {
    const url = `${getApiBase().replace(/\/+$/, "")}/session/${encodeURIComponent(sessionId)}/permissions/${encodeURIComponent(permissionId)}`;
    return fetchSSE(url, {
      method: "POST",
      signal: options?.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        response: body.response,
        message: body.message || null,
        answers: Array.isArray(body.answers) ? body.answers : undefined,
      }),
    });
  },
  replyPermissionDetached: (
    sessionId: string,
    permissionId: string,
    body: {
      response: string;
      message?: string | null;
      answers?: string[][];
    },
    options?: {
      signal?: AbortSignal;
    },
  ) =>
    post<{ accepted: boolean; session_id: string; permission_id: string }>(
      `/session/${encodeURIComponent(sessionId)}/permissions/${encodeURIComponent(permissionId)}/detached`,
      {
        response: body.response,
        message: body.message || null,
        answers: Array.isArray(body.answers) ? body.answers : undefined,
      },
      { signal: options?.signal },
    ),
};

export const globalApi = {
  events: async (opts?: { signal?: AbortSignal }): Promise<Response> => {
    const url = `${getApiBase().replace(/\/+$/, "")}/global/event`;
    return fetchSSE(url, {
      method: "GET",
      signal: opts?.signal,
    });
  },
};

/* ========== 邮箱配置 ========== */
export interface EmailConfig {
  id: string;
  name: string;
  smtp_server: string;
  smtp_port: number;
  smtp_use_tls: boolean;
  sender_email: string;
  sender_name: string;
  username: string;
  is_active: boolean;
  created_at: string;
}

export interface EmailConfigForm {
  name: string;
  smtp_server: string;
  smtp_port: number;
  smtp_use_tls: boolean;
  sender_email: string;
  sender_name: string;
  username: string;
  password: string;
}

export const emailConfigApi = {
  list: () => get<EmailConfig[]>("/settings/email-configs"),
  create: (data: EmailConfigForm) => post<EmailConfig>("/settings/email-configs", data),
  update: (id: string, data: Partial<EmailConfigForm>) => patch<EmailConfig>(`/settings/email-configs/${id}`, data),
  delete: (id: string) => del<{ deleted: string }>(`/settings/email-configs/${id}`),
  activate: (id: string) => post<EmailConfig>(`/settings/email-configs/${id}/activate`),
  test: (id: string) => post<{ status: string }>(`/settings/email-configs/${id}/test`),
  smtpPresets: () => get<Record<string, { smtp_server: string; smtp_port: number; smtp_use_tls: boolean }>>("/settings/smtp-presets"),
};

/* ========== 后台任务 ========== */
export interface TaskStatus {
  task_id: string;
  task_type: string;
  title: string;
  current: number;
  total: number;
  message: string;
  elapsed_seconds: number;
  progress_pct: number;
  finished: boolean;
  success: boolean;
  error: string | null;
  status: "running" | "paused" | "completed" | "failed" | "cancelled";
  progress: number;
  created_at: number;
  updated_at: number;
  finished_at?: number | null;
  has_result: boolean;
  cancel_requested?: boolean;
  cancelled?: boolean;
  source?: string | null;
  source_id?: string | null;
  project_id?: string | null;
  paper_id?: string | null;
  run_id?: string | null;
  action_id?: string | null;
  log_path?: string | null;
  artifact_refs?: Array<Record<string, unknown>>;
  metadata?: Record<string, unknown>;
  retry_supported?: boolean;
  retry_label?: string | null;
  retry_metadata?: Record<string, unknown>;
  log_count?: number;
  token_usage?: TaskTokenUsage | null;
}

export interface TaskTokenUsage {
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens?: number;
  total_tokens: number;
  total_cost_usd?: number;
  calls?: number;
  stage?: string | null;
  source?: "metadata" | "prompt_trace" | "unavailable" | string;
  category?: string;
}

export type WorkflowRegressionMode = "quick" | "full";

export interface WorkflowRegressionWorkflowResult {
  workflow: string;
  run_id?: string;
  status?: string;
  report_title?: string;
  excerpt?: string;
  artifacts?: string[];
  details?: Record<string, unknown> | null;
}

export interface WorkflowRegressionResult {
  mode: WorkflowRegressionMode;
  command: string[];
  root: string;
  started_at: string;
  finished_at: string;
  duration_seconds: number;
  return_code: number;
  success: boolean;
  status: string;
  workflow_count: number;
  failed_workflow_count: number;
  failed_workflows?: Array<Record<string, unknown>>;
  items: WorkflowRegressionWorkflowResult[];
  stdout_tail?: string;
  report_path?: string;
}

export const tasksApi = {
  active: () => get<{ tasks: ActiveTaskInfo[] }>("/tasks/active"),
  runWorkflowRegression: (mode: WorkflowRegressionMode = "quick") =>
    post<{ task_id: string; status: string; message?: string }>(
      `/jobs/workflow-regression/run-once?mode=${encodeURIComponent(mode)}`,
    ),
  startTopicWiki: (keyword: string, limit = 120) =>
    post<{ task_id: string; status: string }>(
      `/tasks/wiki/topic?keyword=${encodeURIComponent(keyword)}&limit=${limit}`
    ),
  startPaperWiki: (paperId: string) =>
    post<{ task_id: string; status: string }>(
      `/tasks/wiki/paper/${encodeURIComponent(paperId)}`
    ),
  getStatus: (taskId: string) =>
    get<TaskStatus>(`/tasks/${taskId}`),
  getResult: (taskId: string) =>
    get<Record<string, unknown>>(`/tasks/${taskId}/result`),
  getLogs: (taskId: string, limit = 120) =>
    get<{ task_id: string; items: Array<{ timestamp: number; level: string; message: string }> }>(
      `/tasks/${taskId}/logs?limit=${limit}`,
    ),
  cancel: (taskId: string) =>
    post<{ ok: boolean; task_id: string; status: TaskStatus }>(`/tasks/${taskId}/cancel`),
  retry: (taskId: string) =>
    post<Record<string, unknown>>(`/tasks/${taskId}/retry`),
  list: (taskType?: string, limit = 20) =>
    get<{ tasks: TaskStatus[] }>(
      `/tasks?${taskType ? `task_type=${taskType}&` : ""}limit=${limit}`
    ),
  track: (body: { action: string; task_id: string; task_type?: string; title?: string; total?: number; current?: number; message?: string; success?: boolean; error?: string; metadata?: Record<string, unknown> }) =>
    post<{ ok: boolean }>("/tasks/track", body),
};

export interface ActiveTaskInfo {
  task_id: string;
  task_type: string;
  title: string;
  current: number;
  total: number;
  message: string;
  elapsed_seconds: number;
  progress_pct: number;
  finished: boolean;
  success: boolean;
  error: string | null;
}

/* ========== 认证 ========== */
export interface LoginResponse {
  access_token: string;
  token_type: string;
}

export interface AuthStatusResponse {
  auth_enabled: boolean;
}

export const authApi = {
  login: (password: string) =>
    post<LoginResponse>("/auth/login", { password }),
  status: () => get<AuthStatusResponse>("/auth/status"),
};
