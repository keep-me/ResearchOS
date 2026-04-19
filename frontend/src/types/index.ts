/**
 * ResearchOS - TypeScript 类型定义
 * @author Bamzc
 */

/* ========== 系统 ========== */
export interface HealthResponse {
  status: string;
  app: string;
  env: string;
}

export interface SystemStatus {
  health: HealthResponse;
  counts: {
    topics: number;
    enabled_topics: number;
    papers_latest_200: number;
    runs_latest_50: number;
    failed_runs_latest_50: number;
  };
  latest_run: PipelineRun | null;
}

export interface TodaySummary {
  today_new: number;
  week_new: number;
  total_papers: number;
  deep_read_count?: number;
  recommendations: {
    id: string;
    title: string;
    arxiv_id: string;
    abstract: string;
    similarity: number;
    title_zh?: string;
    keywords?: string[];
    categories?: string[];
  }[];
  hot_keywords: { keyword: string; count: number }[];
}

/* ========== 主题 ========== */
export type ScheduleFrequency = "daily" | "twice_daily" | "weekdays" | "weekly";
export type TopicKind = "subscription" | "folder";
export type ArxivSortBy = "submittedDate" | "relevance" | "lastUpdatedDate" | "impact";
export type TopicSource = "arxiv" | "openalex" | "manual" | "hybrid";
export type TopicSearchField = "all" | "title" | "keywords" | "authors" | "arxiv_id";
export type TopicPriorityMode = "relevance" | "time" | "impact";
export type TopicVenueTier = "all" | "ccf_a";
export type TopicVenueType = "all" | "conference" | "journal";

export interface Topic {
  id: string;
  name: string;
  kind: TopicKind;
  query: string;
  sort_by: ArxivSortBy;
   source: TopicSource;
   search_field: TopicSearchField;
   priority_mode: TopicPriorityMode;
  venue_tier: TopicVenueTier;
  venue_type: TopicVenueType;
  venue_names: string[];
  from_year?: number | null;
   default_folder_id?: string | null;
  enabled: boolean;
  max_results_per_run: number;
  retry_limit: number;
  schedule_frequency: ScheduleFrequency;
  schedule_time_utc: number;
  enable_date_filter: boolean;
  date_filter_days: number;
  date_filter_start?: string | null;
  date_filter_end?: string | null;
  paper_count?: number;
  last_run_at?: string | null;
  last_run_count?: number | null;
}

export interface TopicCreate {
  name: string;
  kind?: TopicKind;
  query?: string;
  sort_by?: ArxivSortBy;
  source?: TopicSource;
  search_field?: TopicSearchField;
  priority_mode?: TopicPriorityMode;
  venue_tier?: TopicVenueTier;
  venue_type?: TopicVenueType;
  venue_names?: string[];
  from_year?: number | null;
  default_folder_id?: string | null;
  enabled?: boolean;
  max_results_per_run?: number;
  retry_limit?: number;
  schedule_frequency?: ScheduleFrequency;
  schedule_time_utc?: number;
  enable_date_filter?: boolean;
  date_filter_days?: number;
  date_filter_start?: string | null;
  date_filter_end?: string | null;
}

export interface TopicUpdate {
  name?: string;
  kind?: TopicKind;
  query?: string;
  sort_by?: ArxivSortBy;
  source?: TopicSource;
  search_field?: TopicSearchField;
  priority_mode?: TopicPriorityMode;
  venue_tier?: TopicVenueTier;
  venue_type?: TopicVenueType;
  venue_names?: string[] | null;
  from_year?: number | null;
  default_folder_id?: string | null;
  enabled?: boolean;
  max_results_per_run?: number;
  retry_limit?: number;
  schedule_frequency?: ScheduleFrequency;
  schedule_time_utc?: number;
  enable_date_filter?: boolean;
  date_filter_days?: number;
  date_filter_start?: string | null;
  date_filter_end?: string | null;
}

export interface KeywordSuggestion {
  name: string;
  query: string;
  reason: string;
}

/* ========== 抓取任务 ========== */
export interface TopicFetchResult {
  status: "started" | "already_running" | "ok" | "failed" | "no_new_papers";
  task_id?: string;
  topic_name?: string;
  topic_id?: string;
  message?: string;
  inserted?: number;
  new_count?: number;      // 新论文数量
  total_count?: number;    // 总抓取数量（包含重复）
  processed?: number;
  error?: string;
}

export interface TopicFetchStatus {
  status: "running" | "ok" | "failed" | "no_new_papers";
  task_id: string;
  progress_pct: number;
  message?: string;
  inserted?: number;
  new_count?: number;
  total_count?: number;
  processed?: number;
  error?: string;
  topic?: Partial<Topic>;
}

/* ========== 论文 ========== */
export type ReadStatus = "unread" | "skimmed" | "deep_read";

export interface PaperTopicAssignment {
  id: string;
  name: string;
  kind: TopicKind;
  query: string;
  enabled: boolean;
}

export interface Paper {
  id: string;
  title: string;
  arxiv_id: string;
  abstract: string;
  publication_date?: string;
  read_status: ReadStatus;
  pdf_path?: string;
  metadata?: Record<string, unknown>;
  has_embedding?: boolean;
  favorited?: boolean;
  categories?: string[];
  keywords?: string[];
  authors?: string[];
  title_zh?: string;
  abstract_zh?: string;
  citation_count?: number | null;
  topics?: string[];
  topic_details?: PaperTopicAssignment[];
  skim_report?: {
    summary_md: string;
    skim_score: number | null;
    key_insights: Record<string, unknown>;
  } | null;
  deep_report?: {
    deep_dive_md: string;
    key_insights: Record<string, unknown>;
  } | null;
  reasoning_chain?: Record<string, unknown> | null;
  analysis_rounds?: PaperAnalysisBundle | null;
}

export interface PaperAnalysisRound {
  title: string;
  markdown: string;
  updated_at?: string | null;
}

export interface PaperAnalysisBundle {
  detail_level?: string | null;
  reasoning_level?: string | null;
  evidence_mode?: string | null;
  content_source?: string | null;
  content_source_detail?: string | null;
  updated_at?: string | null;
  round_1?: PaperAnalysisRound | null;
  round_2?: PaperAnalysisRound | null;
  round_3?: PaperAnalysisRound | null;
  final_notes?: PaperAnalysisRound | null;
}

export type PaperReaderScope = "paper" | "selection" | "figure";
export type PaperReaderAction = "analyze" | "explain" | "translate" | "summarize" | "ask";
export type PaperReaderNoteKind = "general" | "text" | "figure";

export interface PaperReaderQueryResponse {
  scope: PaperReaderScope;
  action: PaperReaderAction;
  result: string;
  text?: string;
  figure_id?: string | null;
  page_number?: number | null;
  caption?: string | null;
}

export interface PaperReaderNote {
  id: string;
  kind: PaperReaderNoteKind;
  title: string;
  content: string;
  quote?: string | null;
  page_number?: number | null;
  figure_id?: string | null;
  color: "amber" | "blue" | "emerald" | "rose" | "violet" | "slate";
  tags: string[];
  pinned: boolean;
  created_at: string;
  updated_at: string;
}

/* ========== 项目 ========== */
export interface Project {
  id: string;
  name: string;
  description?: string | null;
  workdir?: string | null;
  workspace_server_id?: string | null;
  remote_workdir?: string | null;
  workspace_path?: string | null;
  created_at: string;
  updated_at: string;
  last_accessed_at?: string | null;
  paper_count: number;
  repo_count: number;
  idea_count: number;
  has_remote_workspace: boolean;
  target_count?: number;
  run_count?: number;
  primary_target_id?: string | null;
  latest_run_id?: string | null;
  papers?: ProjectPaper[];
  repos?: ProjectRepo[];
  ideas?: ProjectIdea[];
  reports?: ProjectReport[];
}

export interface ProjectCreate {
  name: string;
  description?: string;
  workdir?: string;
  workspace_server_id?: string;
  remote_workdir?: string;
}

export interface ProjectUpdate {
  name?: string;
  description?: string;
  workdir?: string;
  workspace_server_id?: string;
  remote_workdir?: string;
}

export interface ProjectPaper extends Paper {
  project_paper_id: string;
  added_at?: string | null;
  note?: string | null;
}

export interface ProjectRepo {
  id: string;
  project_id: string;
  repo_url: string;
  local_path?: string | null;
  cloned_at?: string | null;
  is_workdir_repo: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProjectRepoCommit {
  hash: string;
  short_hash: string;
  message: string;
  author: string;
  date: string;
}

export interface ProjectIdea {
  id: string;
  project_id: string;
  title: string;
  content: string;
  paper_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface ProjectReport {
  id: string;
  content_type: string;
  title: string;
  paper_id?: string | null;
  paper_title?: string | null;
  keyword?: string | null;
  excerpt?: string;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
}

export interface ProjectPaperRef {
  ref_id: string;
  source: string;
  status?: "library" | "candidate" | "linked" | "imported" | "failed" | string;
  paper_id?: string | null;
  external_id?: string | null;
  title: string;
  arxiv_id?: string | null;
  openalex_id?: string | null;
  authors?: string[];
  categories?: string[];
  year?: number | null;
  publication_year?: number | null;
  publication_date?: string | null;
  citation_count?: number | null;
  venue?: string | null;
  abstract?: string | null;
  abstract_available?: boolean;
  source_url?: string | null;
  pdf_url?: string | null;
  path?: string | null;
  match_reason?: string | null;
  selected?: boolean;
  project_linked?: boolean;
  importable?: boolean;
  linkable?: boolean;
  imported_paper_id?: string | null;
  error?: string | null;
  asset_status?: {
    pdf?: boolean;
    embedding?: boolean;
    skim?: boolean;
    deep?: boolean;
    analysis_rounds?: string[];
  };
}

export type ProjectWorkflowType =
  | "init_repo"
  | "autoresearch_claude_code"
  | "literature_review"
  | "idea_discovery"
  | "novelty_check"
  | "research_review"
  | "run_experiment"
  | "auto_review_loop"
  | "paper_plan"
  | "paper_figure"
  | "paper_write"
  | "paper_compile"
  | "paper_writing"
  | "rebuttal"
  | "paper_improvement"
  | "full_pipeline"
  | "monitor_experiment"
  | "sync_workspace"
  | "custom_run";

export type ProjectRunStatus =
  | "draft"
  | "queued"
  | "paused"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface ProjectRunPendingCheckpoint {
  type: string;
  label: string;
  status: "pending" | "approved" | "rejected";
  message?: string | null;
  completed_stage_id?: string | null;
  completed_stage_label?: string | null;
  resume_stage_id?: string | null;
  resume_stage_label?: string | null;
  stage_summary?: string | null;
  requested_at?: string | null;
  responded_at?: string | null;
  comment?: string | null;
  notification_recipients?: string[];
}

export type ProjectRunActionType =
  | "continue"
  | "run_experiment"
  | "monitor"
  | "review"
  | "retry"
  | "sync_workspace"
  | "custom";

export type ProjectWorkflowReadiness = "native" | "planned";
export type ProjectWorkflowAvailability = "active" | "planned";
export type ProjectStageExecutionTarget = "local" | "workspace_target" | "ssh";
export type ProjectStageStatus = "pending" | "running" | "completed" | "failed" | "cancelled";
export type ProjectStageModelRole = "executor" | "reviewer";

export interface ProjectEngineProfile {
  id: string;
  config_id: string;
  config_name: string;
  provider: string;
  channel: "deep" | "skim" | "fallback" | "vision";
  channel_label: string;
  label: string;
  model: string;
  default_variant?: string | null;
  is_active: boolean;
}

export interface ProjectAgentTemplate {
  id: string;
  label: string;
  kind: "native" | "cli" | "acp";
  description: string;
  supports_local: boolean;
  supports_remote: boolean;
  supports_mcp: boolean;
  accent?: string;
  model_channel?: string;
  reasoning_level?: string;
}

export interface ProjectWorkflowStagePreset {
  id: string;
  label: string;
  description: string;
  default_agent_id: string;
  selected_engine_id?: string | null;
  execution_target: ProjectStageExecutionTarget;
  model_role?: ProjectStageModelRole;
  mcp_required: boolean;
  checkpoint_required?: boolean;
  deliverable?: string;
  supported_agent_ids: string[];
}

export interface ProjectWorkflowStageBinding extends ProjectWorkflowStagePreset {
  order?: number;
  selected_agent_id: string;
  selected_engine_id?: string | null;
  mcp_enabled: boolean;
  checkpoint_required?: boolean;
  status?: ProjectStageStatus;
  notes?: string;
}

export interface ProjectWorkflowOrchestration {
  workflow_type: ProjectWorkflowType;
  preset_id: string;
  label: string;
  readiness: ProjectWorkflowReadiness;
  source_reference?: string;
  target_binding: string;
  workspace_server_id?: string | null;
  stages: ProjectWorkflowStageBinding[];
}

export interface ProjectWorkflowStageTrace {
  stage_id: string;
  label: string;
  description?: string;
  deliverable?: string;
  status: ProjectStageStatus;
  model_role?: ProjectStageModelRole;
  message?: string;
  progress_pct?: number;
  agent_id?: string;
  engine_id?: string | null;
  engine_label?: string | null;
  execution_target?: ProjectStageExecutionTarget;
  mcp_enabled?: boolean;
  checkpoint_required?: boolean;
  provider?: string | null;
  model?: string | null;
  variant?: string | null;
  model_source?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
}

export interface ProjectDeploymentTarget {
  id: string;
  project_id: string;
  label: string;
  workspace_server_id?: string | null;
  workdir?: string | null;
  remote_workdir?: string | null;
  workspace_path?: string | null;
  dataset_root?: string | null;
  checkpoint_root?: string | null;
  output_root?: string | null;
  enabled: boolean;
  is_primary: boolean;
  workspace_health?: ProjectWorkspaceHealth | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectRunAction {
  id: string;
  run_id: string;
  action_type: ProjectRunActionType;
  action_label: string;
  prompt: string;
  status: ProjectRunStatus;
  active_phase: string;
  summary: string;
  task_id?: string | null;
  log_path?: string | null;
  result_path?: string | null;
  metadata?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ProjectRun {
  id: string;
  project_id: string;
  target_id?: string | null;
  target_label?: string | null;
  workflow_type: ProjectWorkflowType;
  workflow_label: string;
  title: string;
  prompt: string;
  status: ProjectRunStatus;
  active_phase: string;
  summary: string;
  task_id?: string | null;
  workspace_server_id?: string | null;
  workdir?: string | null;
  remote_workdir?: string | null;
  workspace_path?: string | null;
  dataset_root?: string | null;
  checkpoint_root?: string | null;
  output_root?: string | null;
  log_path?: string | null;
  result_path?: string | null;
  run_directory?: string | null;
  retry_of_run_id?: string | null;
  max_iterations?: number | null;
  executor_engine_id?: string | null;
  executor_engine_label?: string | null;
  reviewer_engine_id?: string | null;
  reviewer_engine_label?: string | null;
  executor_model?: string | null;
  reviewer_model?: string | null;
  auto_proceed?: boolean;
  human_checkpoint_enabled?: boolean;
  checkpoint_state?: "disabled" | "pending" | "approved" | "rejected";
  pending_checkpoint?: ProjectRunPendingCheckpoint | null;
  notification_recipients?: string[];
  paper_ids?: string[];
  paper_index?: ProjectPaperRef[];
  literature_candidates?: ProjectPaperRef[];
  metadata?: Record<string, unknown>;
  orchestration?: ProjectWorkflowOrchestration | null;
  stage_trace?: ProjectWorkflowStageTrace[];
  artifact_refs?: ProjectArtifactRef[];
  next_actions?: ProjectRunActionPreset[];
  recent_logs?: string[];
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
  updated_at: string;
  actions?: ProjectRunAction[];
}

export interface ProjectWorkflowPreset {
  id: string;
  label: string;
  workflow_type: ProjectWorkflowType;
  prefill_prompt: string;
  description: string;
  intro?: string | null;
  when_to_use?: string[];
  required_inputs?: string[];
  usage_steps?: string[];
  expected_outputs?: string[];
  sample_prompt?: string | null;
  sample_execution_command?: string | null;
  sample_rebuttal_review_bundle?: string | null;
  readiness: ProjectWorkflowReadiness;
  availability?: ProjectWorkflowAvailability;
  source_reference?: string;
  entry_command?: string | null;
  source_skills?: string[];
  workflow_group?: string | null;
  workflow_order?: number | null;
  stages: ProjectWorkflowStagePreset[];
}

export interface ProjectArtifactRef {
  kind: string;
  path: string;
  relative_path?: string | null;
  size_bytes?: number | null;
  updated_at?: string | null;
}

export interface WorkspaceRuntimeProbe {
  available: boolean;
  detail?: string | null;
}

export interface ProjectWorkspaceHealth {
  status: "ready" | "error" | "warning";
  workspace_path?: string | null;
  exists: boolean;
  message?: string | null;
  tree?: string | null;
  git?: Record<string, unknown> | null;
  runtime?: Record<string, WorkspaceRuntimeProbe>;
  disk_free_gb?: number | null;
}

export interface ProjectRunActionPreset {
  id: string;
  label: string;
  action_type: ProjectRunActionType;
  workflow_type?: ProjectWorkflowType | null;
  workflow_label?: string | null;
  command?: string | null;
  source_skill?: string | null;
}

export interface ProjectWorkspaceContext {
  project: Project;
  targets: ProjectDeploymentTarget[];
  runs: ProjectRun[];
  workflow_presets: ProjectWorkflowPreset[];
  planned_workflow_presets?: ProjectWorkflowPreset[];
  action_items: ProjectRunActionPreset[];
  agent_templates: ProjectAgentTemplate[];
  role_templates?: ProjectAgentTemplate[];
  engine_profiles: ProjectEngineProfile[];
  workspace_health?: ProjectWorkspaceHealth | null;
  recent_logs?: string[];
  artifacts?: ProjectArtifactRef[];
  default_selections: {
    target_id?: string | null;
    run_id?: string | null;
    workflow_type?: ProjectWorkflowType | null;
    executor_engine_id?: string | null;
    reviewer_engine_id?: string | null;
  };
}

export interface CompanionSessionPreview {
  id: string;
  slug?: string | null;
  projectID?: string | null;
  workspaceID?: string | null;
  directory: string;
  parentID?: string | null;
  title: string;
  version: number;
  mode: string;
  workspace_path?: string | null;
  workspace_server_id?: string | null;
  summary?: {
    additions?: number;
    deletions?: number;
    files?: number;
    diffs?: number | null;
  } | null;
  share?: { url: string } | null;
  revert?: Record<string, unknown> | null;
  permission?: Record<string, unknown> | null;
  latest_message?: {
    id?: string | null;
    role?: string | null;
    created_at?: number | null;
    text: string;
  } | null;
  time: {
    created?: number | null;
    updated?: number | null;
    compacting?: number | null;
    archived?: number | null;
  };
}

export interface ProjectCompanionSnapshot {
  project: Project;
  workspace_context: ProjectWorkspaceContext;
  tasks: Record<string, unknown>[];
  sessions: CompanionSessionPreview[];
  latest_session_messages: Record<string, unknown>[];
  acp: Record<string, unknown>;
}

export interface ProjectCompanionOverviewItem extends Project {
  latest_run?: ProjectRun | null;
  active_task_count: number;
}

export interface AgentCliConfig {
  id: string;
  agent_type: string;
  label: string;
  kind: "native" | "cli" | "acp";
  description: string;
  enabled: boolean;
  command?: string | null;
  args?: string[];
  provider?: string | null;
  base_url?: string | null;
  default_model?: string | null;
  workspace_server_id?: string | null;
  execution_mode?: "auto" | "local" | "ssh";
  metadata?: Record<string, unknown>;
  installed?: boolean;
  chat_supported?: boolean;
  chat_ready?: boolean;
  chat_status?: "ready" | "missing_command" | "detection_only" | "requires_service";
  chat_status_label?: string;
  chat_blocked_reason?: string | null;
  acp_server_name?: string | null;
  acp_server_label?: string | null;
  acp_transport?: "stdio" | "http" | null;
  acp_connected?: boolean;
  command_path?: string | null;
  config_source?: string | null;
  has_api_key?: boolean;
  api_key_masked?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface AgentDetectionItem {
  agent_type: string;
  label: string;
  kind: "native" | "cli" | "acp";
  description: string;
  installed: boolean;
  supported: boolean;
  chat_supported?: boolean;
  chat_ready?: boolean;
  chat_status?: "ready" | "missing_command" | "detection_only" | "requires_service";
  chat_status_label?: string;
  chat_blocked_reason?: string | null;
  acp_server_name?: string | null;
  acp_server_label?: string | null;
  acp_transport?: "stdio" | "http" | null;
  acp_connected?: boolean;
  binary_path?: string | null;
  command?: string | null;
  provider?: string | null;
  base_url?: string | null;
  default_model?: string | null;
  config_source?: string | null;
  has_api_key?: boolean;
  api_key_masked?: string | null;
  message?: string | null;
}

/* ========== Pipeline ========== */
export type PipelineStatus = "pending" | "running" | "succeeded" | "failed";

export interface PipelineRun {
  id: string;
  pipeline_name: string;
  paper_id: string;
  status: PipelineStatus;
  decision_note?: string;
  elapsed_ms?: number;
  error_message?: string;
  created_at: string;
}

export interface SkimReport {
  one_liner: string;
  innovations: string[];
  relevance_score: number;
  keywords?: string[];
  title_zh?: string;
  abstract_zh?: string;
}

export interface DeepDiveReport {
  method_summary: string;
  experiments_summary: string;
  ablation_summary: string;
  reviewer_risks: string[];
}

/* ========== RAG ========== */
export interface AskRequest {
  question: string;
  top_k?: number;
}

export interface AskResponse {
  answer: string;
  cited_paper_ids: string[];
  evidence: Record<string, unknown>[];
  confidence?: number | null;
}

/* ========== 图谱 ========== */
export interface CitationEdge {
  source: string;
  target: string;
  depth: number;
}

export interface CitationNode {
  id: string;
  title: string;
  year?: number;
}

export interface CitationTree {
  root: string;
  root_title: string;
  ancestors: CitationEdge[];
  descendants: CitationEdge[];
  nodes: CitationNode[];
  edge_count: number;
}

export interface TimelineEntry {
  paper_id: string;
  title: string;
  title_zh?: string;
  year: number;
  indegree: number;
  outdegree: number;
  pagerank: number;
  seminal_score: number;
  why_seminal?: string;
}

export interface TimelineResponse {
  keyword: string;
  timeline: TimelineEntry[];
  seminal: TimelineEntry[];
  milestones: TimelineEntry[];
}

export interface GraphQuality {
  keyword: string;
  node_count: number;
  edge_count: number;
  density: number;
  connected_node_ratio: number;
  publication_date_coverage: number;
}

export interface YearBucket {
  year: number;
  paper_count: number;
  avg_seminal_score: number;
  top_titles: string[];
  top_titles_zh?: string[];
}

export interface EvolutionResponse {
  keyword: string;
  year_buckets: YearBucket[];
  summary: {
    trend_summary: string;
    phase_shift_signals: string;
    next_week_focus: string;
  };
}

export interface SurveyResponse {
  keyword: string;
  summary: {
    overview: string;
    stages: string[];
    reading_list: string[];
    open_questions: string[];
  };
  milestones: TimelineEntry[];
  seminal: TimelineEntry[];
}

/* ========== Wiki ========== */
export interface WikiSection {
  title: string;
  content: string;
  key_insight?: string;
}

export interface PdfExcerpt {
  title: string;
  excerpt: string;
}

export interface ScholarMetadataItem {
  title: string;
  year?: number;
  citationCount?: number;
  influentialCitationCount?: number;
  venue?: string;
  fieldsOfStudy?: string[];
  tldr?: string;
}

export interface WikiReadingItem {
  title: string;
  year?: number;
  reason: string;
}

export interface TopicWikiContent {
  overview: string;
  sections: WikiSection[];
  key_findings: string[];
  methodology_evolution: string;
  future_directions: string[];
  reading_list: WikiReadingItem[];
  citation_contexts?: string[];
  pdf_excerpts?: PdfExcerpt[];
  scholar_metadata?: ScholarMetadataItem[];
}

export interface PaperWikiContent {
  summary: string;
  contributions: string[];
  methodology: string;
  significance: string;
  limitations: string[];
  related_work_analysis: string;
  reading_suggestions: WikiReadingItem[];
  citation_contexts?: string[];
  pdf_excerpts?: PdfExcerpt[];
  scholar_metadata?: ScholarMetadataItem[];
}

export interface PaperWiki {
  paper_id: string;
  title?: string;
  markdown: string;
  wiki_content?: PaperWikiContent;
  graph: CitationTree;
  content_id?: string;
}

export interface TopicWiki {
  keyword: string;
  markdown: string;
  wiki_content?: TopicWikiContent;
  timeline: TimelineResponse;
  survey: SurveyResponse;
  content_id?: string;
}

/* ========== 推理链分析 ========== */
export interface ReasoningStep {
  step: string;
  thinking: string;
  conclusion: string;
}

export interface MethodChain {
  problem_definition: string;
  core_hypothesis: string;
  method_derivation: string;
  theoretical_basis: string;
  innovation_analysis: string;
}

export interface ExperimentChain {
  experimental_design: string;
  baseline_fairness: string;
  result_validation: string;
  ablation_insights: string;
}

export interface ImpactAssessment {
  novelty_score: number;
  rigor_score: number;
  impact_score: number;
  overall_assessment: string;
  strengths: string[];
  weaknesses: string[];
  future_suggestions: string[];
}

export interface ReasoningChainResult {
  reasoning_steps: ReasoningStep[];
  method_chain: MethodChain;
  experiment_chain: ExperimentChain;
  impact_assessment: ImpactAssessment;
}

export interface ReasoningAnalysisResponse {
  paper_id: string;
  title: string;
  reasoning: ReasoningChainResult;
}

/* ========== 研究空白识别 ========== */
export interface ResearchGap {
  gap_title: string;
  description: string;
  evidence: string;
  potential_impact: string;
  suggested_approach: string;
  difficulty: "easy" | "medium" | "hard";
  confidence: number;
}

export interface MethodComparisonEntry {
  name: string;
  scores: Record<string, string>;
  papers: string[];
}

export interface MethodComparison {
  dimensions: string[];
  methods: MethodComparisonEntry[];
  underexplored_combinations: string[];
}

export interface TrendAnalysis {
  hot_directions: string[];
  declining_areas: string[];
  emerging_opportunities: string[];
}

export interface ResearchGapsAnalysis {
  research_gaps: ResearchGap[];
  method_comparison: MethodComparison;
  trend_analysis: TrendAnalysis;
  overall_summary: string;
}

export interface ResearchGapsResponse {
  keyword: string;
  network_stats: {
    total_papers: number;
    edge_count: number;
    density: number;
    connected_ratio: number;
    isolated_count: number;
  };
  analysis: ResearchGapsAnalysis;
}

/* ========== 丰富引用详情 ========== */
export interface RichCitationEntry {
  scholar_id: string | null;
  title: string;
  title_zh?: string;
  year: number | null;
  venue: string | null;
  citation_count: number | null;
  arxiv_id: string | null;
  abstract: string | null;
  in_library: boolean;
  library_paper_id: string | null;
}

export interface CitationDetail {
  paper_id: string;
  paper_title: string;
  references: RichCitationEntry[];
  cited_by: RichCitationEntry[];
  stats: {
    total_references: number;
    total_cited_by: number;
    in_library_references: number;
    in_library_cited_by: number;
  };
}

export interface NetworkNode {
  id: string;
  title: string;
  year: number | null;
  arxiv_id: string | null;
  in_degree: number;
  out_degree: number;
  is_hub: boolean;
  is_external: boolean;
  co_citation_count?: number;
}

export interface NetworkEdge {
  source: string;
  target: string;
}

export interface TopicCitationNetwork {
  topic_id: string;
  topic_name: string;
  nodes: NetworkNode[];
  edges: NetworkEdge[];
  stats: {
    total_papers: number;
    total_edges: number;
    density: number;
    hub_papers: number;
    internal_papers?: number;
    external_papers?: number;
    internal_edges?: number;
    new_edges_synced?: number;
  };
  key_external_papers?: Array<{
    id: string;
    title: string;
    co_citation_count: number;
  }>;
}

/* ========== 图谱增强 ========== */
export interface OverviewNode {
  id: string;
  title: string;
  arxiv_id: string;
  year: number | null;
  in_degree: number;
  out_degree: number;
  pagerank: number;
  topics: string[];
  read_status: string;
}

export interface SimilarityMapPoint {
  id: string;
  title: string;
  x: number;
  y: number;
  year: number | null;
  read_status: string;
  topics: string[];
  topic: string;
  arxiv_id: string;
  title_zh?: string;
}

export interface SimilarityMapData {
  points: SimilarityMapPoint[];
  total?: number;
  message?: string;
}

export interface LibraryOverview {
  total_papers: number;
  total_edges: number;
  density: number;
  nodes: OverviewNode[];
  edges: NetworkEdge[];
  top_papers: OverviewNode[];
  topic_stats: Record<string, { count: number; edges: number }>;
}

export interface BridgePaper {
  id: string;
  title: string;
  arxiv_id: string;
  topics_citing: string[];
  cross_topic_count: number;
  own_topics: string[];
}

export interface BridgesResponse {
  bridges: BridgePaper[];
  total: number;
}

export interface FrontierPaper {
  id: string;
  title: string;
  arxiv_id: string;
  year: number;
  publication_date: string;
  citations_in_library: number;
  citation_velocity: number;
  read_status: string;
}

export interface FrontierResponse {
  period_days: number;
  total_recent: number;
  frontier: FrontierPaper[];
}

export interface CocitationCluster {
  size: number;
  papers: Array<{ id: string; title: string; arxiv_id: string }>;
}

export interface CocitationResponse {
  total_clusters: number;
  clusters: CocitationCluster[];
  cocitation_pairs: number;
}

/* ========== 简报 ========== */
export interface DailyBriefRequest {
  date?: string;
  recipient?: string;
}

export interface DailyBriefResponse {
  task_id: string;
  status: string;
  message: string;
}

/* ========== 生成内容 ========== */
export interface GeneratedContent {
  id: string;
  content_type: "topic_wiki" | "paper_wiki" | "daily_brief" | "graph_insight";
  title: string;
  keyword?: string;
  paper_id?: string;
  markdown: string;
  metadata_json?: Record<string, unknown>;
  created_at: string;
}

export interface GeneratedContentListItem {
  id: string;
  content_type: string;
  title: string;
  keyword?: string;
  paper_id?: string;
  created_at: string;
}

/* ========== 指标 ========== */
export interface CostStage {
  stage: string;
  calls: number;
  total_cost_usd: number;
  input_tokens: number;
  output_tokens: number;
}

export interface CostModel {
  provider: string;
  model: string;
  calls: number;
  total_cost_usd: number;
  input_tokens: number;
  output_tokens: number;
}

export interface CostMetrics {
  window_days: number;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  total_cost_usd: number;
  by_stage: CostStage[];
  by_model: CostModel[];
}

/* ========== 引用同步 ========== */
export interface CitationSyncResult {
  paper_id?: string;
  topic_id?: string;
  papers_processed?: number;
  edges_inserted: number;
  processed_papers?: number;
  strategy?: string;
  message?: string;  // 添加 message 属性
}

/* ========== 摄入 ========== */
export interface IngestPaper {
  id: string;
  title: string;
  arxiv_id?: string;
  publication_date?: string | null;
}

export type LiteratureSourceScope = "hybrid" | "arxiv" | "openalex";
export type LiteratureVenueTier = "all" | "ccf_a";
export type LiteratureVenueType = "all" | "conference" | "journal";

export interface ExternalLiteraturePaper {
  title: string;
  abstract: string;
  publication_year?: number | null;
  publication_date?: string | null;
  citation_count?: number | null;
  venue?: string | null;
  venue_type?: string | null;
  venue_tier?: string | null;
  authors?: string[];
  categories?: string[];
  arxiv_id?: string | null;
  openalex_id?: string | null;
  source_url?: string | null;
  pdf_url?: string | null;
  source?: string | null;
}

export interface ExternalLiteratureSearchResult {
  papers: ExternalLiteraturePaper[];
  count: number;
  query: string;
  source_scope: LiteratureSourceScope;
  source_counts: Record<string, number>;
  filters: {
    venue_tier: LiteratureVenueTier;
    venue_type: LiteratureVenueType;
    venue_names: string[];
    from_year?: number | null;
  };
  skipped_sources: string[];
  sort_mode: TopicPriorityMode;
  summary?: string;
}

export interface IngestResult {
  ingested: number;
  classified?: number;
  requested?: number;
  found?: number;
  duplicates?: number;
  missing_ids?: string[];
  papers?: IngestPaper[];
}

export interface PdfUploadResult {
  status: "created" | "updated";
  created: boolean;
  paper: IngestPaper;
  pdf_path: string;
  topic_id?: string | null;
}

/* ========== 聊天消息 ========== */
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  cited_paper_ids?: string[];
  evidence?: Record<string, unknown>[];
  timestamp: Date;
}

/* ========== LLM 配置 ========== */
export type LLMProtocol = "openai" | "anthropic";

export type LLMProviderFamily =
  | LLMProtocol
  | "zhipu"
  | "gemini"
  | "qwen"
  | "kimi"
  | "minimax"
  | "custom";

export type LLMProvider = LLMProviderFamily;

export interface LLMProviderConfig {
  id: string;
  name: string;
  provider: LLMProvider;
  api_key_masked: string;
  api_base_url?: string | null;
  model_skim: string;
  model_deep: string;
  model_vision?: string | null;
  embedding_provider?: LLMProvider | null;
  embedding_api_base_url?: string | null;
  embedding_api_key_masked?: string | null;
  model_embedding: string;
  model_fallback: string;
  image_provider?: LLMProvider | null;
  image_api_base_url?: string | null;
  image_api_key_masked?: string | null;
  model_image?: string | null;
  is_active: boolean;
  provider_family?: LLMProviderFamily;
  embedding_provider_family?: LLMProviderFamily;
  base_url_inferred_provider?: LLMProviderFamily | null;
  embedding_base_url_inferred_provider?: LLMProviderFamily | null;
  compatibility_warnings?: string[];
}

export interface LLMProviderPreset {
  id: string;
  label: string;
  provider: LLMProvider;
  base_url?: string;
  models: string[];
  description: string;
}

export interface LLMProviderCreate {
  name: string;
  provider: LLMProvider;
  api_key: string;
  api_base_url?: string;
  model_skim: string;
  model_deep: string;
  model_vision?: string;
  embedding_provider?: LLMProvider | "";
  embedding_api_key?: string;
  embedding_api_base_url?: string;
  model_embedding: string;
  model_fallback: string;
  image_provider?: LLMProvider | "";
  image_api_key?: string;
  image_api_base_url?: string;
  model_image?: string;
}

export interface LLMProviderUpdate {
  name?: string;
  provider?: string;
  api_key?: string;
  api_base_url?: string;
  model_skim?: string;
  model_deep?: string;
  model_vision?: string;
  embedding_provider?: LLMProvider | "";
  embedding_api_key?: string;
  embedding_api_base_url?: string;
  model_embedding?: string;
  model_fallback?: string;
  image_provider?: LLMProvider | "";
  image_api_key?: string;
  image_api_base_url?: string;
  model_image?: string;
}

export interface ActiveLLMConfig {
  source: "database" | "none";
  config: (LLMProviderConfig & { provider?: string }) | null;
}

export interface LLMProviderTestStatus {
  ok: boolean;
  provider?: string | null;
  model?: string | null;
  base_url?: string | null;
  transport: string;
  message: string;
  preview?: string;
  latency_ms?: number;
  dimension?: number;
}

export interface LLMProviderTestResult {
  config_id: string;
  name: string;
  config?: LLMProviderConfig;
  warnings?: string[];
  chat: LLMProviderTestStatus;
  embedding: LLMProviderTestStatus;
}

export interface WorkspaceRootItem {
  path: string;
  title?: string;
  source: "config" | "custom";
  removable: boolean;
  exists: boolean;
}

export interface WorkspaceRootListResponse {
  items: WorkspaceRootItem[];
  default_projects_root?: string;
}

export interface AssistantWorkspaceServer {
  id: string;
  label: string;
  kind: "native" | "sidecar" | "remote" | "ssh";
  available: boolean;
  phase?: string;
  message?: string | null;
  base_url?: string;
  host?: string;
  port?: number;
  username?: string | null;
  workspace_root?: string | null;
  has_password?: boolean;
  password_masked?: string | null;
  has_private_key?: boolean;
  private_key_masked?: string | null;
  has_passphrase?: boolean;
  passphrase_masked?: string | null;
  auth_mode?: "none" | "password" | "private_key" | "bearer" | "basic";
  editable?: boolean;
  removable?: boolean;
  enabled?: boolean;
}

export interface AssistantWorkspaceServerPayload {
  id?: string;
  label: string;
  host?: string;
  port?: number;
  username?: string;
  password?: string;
  private_key?: string;
  passphrase?: string;
  workspace_root?: string;
  enabled?: boolean;
  base_url?: string;
  api_token?: string;
  verify_tls?: boolean;
}

export interface AssistantWorkspaceSshProbePayload {
  host: string;
  port?: number;
  username?: string;
  password?: string;
  private_key?: string;
  passphrase?: string;
  workspace_root?: string;
}

export interface AssistantWorkspaceSshProbeResult {
  success: boolean;
  message: string;
  home_dir?: string;
  workspace_root?: string | null;
  workspace_exists?: boolean | null;
}

export interface AssistantWorkspaceGitEntry {
  path: string;
  code: string;
  index_status: string;
  worktree_status: string;
}

export interface AssistantWorkspaceGitOverview {
  available: boolean;
  is_repo: boolean;
  branch: string | null;
  remotes?: string[];
  entries: AssistantWorkspaceGitEntry[];
  changed_count: number;
  untracked_count: number;
  message?: string | null;
}

export interface AssistantWorkspaceOverview {
  workspace_path: string;
  tree: string;
  files: string[];
  total_entries: number;
  truncated: boolean;
  exists: boolean;
  git: AssistantWorkspaceGitOverview;
}

export interface AssistantWorkspaceGitInitResponse {
  ok: boolean;
  workspace_path: string;
  result: Record<string, unknown>;
  git: AssistantWorkspaceGitOverview;
}

export interface AssistantWorkspaceDiffResponse {
  workspace_path: string;
  file_path?: string | null;
  diff: string;
  truncated: boolean;
  git: AssistantWorkspaceGitOverview;
  message?: string;
}

export interface AssistantWorkspaceGitBranchResponse {
  ok: boolean;
  workspace_path: string;
  branch: string;
  created: boolean;
  checked_out: boolean;
  result: Record<string, unknown>;
  git: AssistantWorkspaceGitOverview;
}

export interface AssistantWorkspaceGitActionResponse {
  ok: boolean;
  workspace_path: string;
  action: string;
  file_path?: string | null;
  result: Record<string, unknown>;
  git: AssistantWorkspaceGitOverview;
}

export interface AssistantWorkspaceTerminalResult {
  workspace_path: string;
  cwd: string;
  command: string;
  shell_command: string[];
  exit_code: number;
  stdout: string;
  stderr: string;
  success: boolean;
}

export interface AssistantWorkspaceTerminalSessionInfo {
  session_id: string;
  server_id: string;
  kind: "local" | "ssh";
  workspace_path: string;
  shell: string;
  cols: number;
  rows: number;
  created_at: number;
  updated_at: number;
  closed: boolean;
  exit_code?: number | null;
  error?: string | null;
}

export interface AssistantWorkspaceRevealResponse {
  path: string;
  opened: boolean;
  message?: string | null;
}

export interface AssistantWorkspaceFileResponse {
  workspace_path: string;
  relative_path: string;
  content: string;
  truncated: boolean;
  size_bytes: number;
}

export interface AssistantWorkspaceFileWriteResponse {
  workspace_path: string;
  relative_path: string;
  created: boolean;
  overwritten: boolean;
  changed: boolean;
  size_bytes: number;
  previous_size_bytes: number;
  line_count: number;
  sha256: string;
  preview: string;
  diff_preview: string;
}

export interface AssistantWorkspaceUploadResponse {
  workspace_path: string;
  relative_path: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  created: boolean;
  overwritten: boolean;
}

export interface AssistantSkillItem {
  id: string;
  name: string;
  description: string;
  path: string;
  entry_file: string;
  source: "codex" | "agents" | "project";
  relative_path: string;
  system: boolean;
}

export interface AssistantSkillRoot {
  source: "codex" | "agents" | "project";
  path: string;
  exists: boolean;
}

export type AssistantWorkspaceAccess = "none" | "read" | "read_write";
export type AssistantCommandExecution = "deny" | "allowlist" | "full";
export type AssistantApprovalMode = "always" | "on_request" | "off";

export interface AssistantExecPolicy {
  workspace_access: AssistantWorkspaceAccess;
  command_execution: AssistantCommandExecution;
  approval_mode: AssistantApprovalMode;
  allowed_command_prefixes: string[];
}

/* ========== 写作助手 ========== */
export type WritingAction =
  | "zh_to_en" | "en_to_zh" | "zh_polish" | "en_polish"
  | "compress" | "expand" | "logic_check" | "deai"
  | "fig_caption" | "table_caption"
  | "experiment_analysis" | "reviewer" | "chart_recommend"
  | "ocr_extract" | "image_generate";

export interface WritingTemplate {
  action: WritingAction;
  label: string;
  description: string;
  icon: string;
  placeholder: string;
  supports_image?: boolean;
}

export interface WritingResult {
  action: string;
  label: string;
  content: string;
  kind?: "text" | "image";
  image_base64?: string;
  mime_type?: string;
  provider?: string;
  model?: string;
  aspect_ratio?: string;
  input_tokens?: number;
  output_tokens?: number;
  total_cost_usd?: number;
}

export interface WritingRefineMessage {
  role: "user" | "assistant";
  content: string;
}

export interface WritingRefineResult {
  content: string;
  input_tokens?: number;
  output_tokens?: number;
  total_cost_usd?: number;
}

/* ========== Agent ========== */
export interface AgentMessageTextPart {
  type: "text";
  text: string;
  content?: string;
  synthetic?: boolean;
  ignored?: boolean;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface AgentMessageFilePart {
  type: "file";
  url?: string;
  filename?: string;
  mime?: string;
  source?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface AgentMessageGenericPart {
  type: string;
  text?: string;
  content?: string;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export type AgentMessageContentPart =
  | AgentMessageTextPart
  | AgentMessageFilePart
  | AgentMessageGenericPart;

export type AgentMessageContent =
  | string
  | AgentMessageContentPart
  | AgentMessageContentPart[];

export type AgentMessageFormat =
  | string
  | Record<string, unknown>
  | Record<string, unknown>[];

export interface AgentMessage {
  role: "user" | "assistant" | "tool";
  content: AgentMessageContent;
  system?: string;
  tools?: Record<string, boolean>;
  variant?: string;
  format?: AgentMessageFormat;
  text_parts?: Record<string, unknown>[];
  reasoning_content?: string;
  reasoning_parts?: Record<string, unknown>[];
  provider_metadata?: Record<string, unknown>;
  tool_calls?: Record<string, unknown>[];
  tool_call_id?: string;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  tool_result?: Record<string, unknown>;
  provider_executed?: boolean;
}

export type AgentMode = "build" | "plan";
export type AgentReasoningLevel = "default" | "low" | "medium" | "high" | "xhigh";
export type AnalysisDetailLevel = "low" | "medium" | "high";
export type PaperEvidenceMode = "full" | "rough";

export interface OpenCodeRuntimeStatus {
  available: boolean;
  phase: "idle" | "bootstrapping" | "starting" | "ready" | "error" | "stopped";
  message: string;
  url: string | null;
  pid: number | null;
  host: string;
  port: number;
  repo_path: string;
  default_directory: string;
  active_provider: string | null;
  active_model: string | null;
  skills_paths: string[];
  log_path: string;
  install_log_path: string;
  last_error: string | null;
  updated_at: number;
}

export interface AssistantSessionDiffEntry {
  file?: string;
  path?: string;
  status?: "added" | "modified" | "deleted" | string;
  before?: string;
  after?: string;
  exists_before?: boolean;
  exists_after?: boolean;
  additions?: number;
  deletions?: number;
  workspace_path?: string | null;
  workspace_server_id?: string | null;
}

export interface AssistantSessionRevertInfo {
  message_id?: string | null;
  snapshot?: string | null;
  diffs?: AssistantSessionDiffEntry[];
}

export interface OpenCodeSessionSummary {
  additions: number;
  deletions: number;
  files: number;
  diffs?: AssistantSessionDiffEntry[];
}

export interface OpenCodeSessionInfo {
  id: string;
  slug?: string;
  projectID?: string;
  workspaceID?: string;
  directory: string;
  workspace_path?: string | null;
  workspace_server_id?: string | null;
  parentID?: string | null;
  title: string;
  version?: string;
  time: {
    created: number;
    updated: number;
    compacting?: number;
    archived?: number;
  };
  permission?: unknown[];
  summary?: OpenCodeSessionSummary;
  revert?: AssistantSessionRevertInfo | null;
}

export type OpenCodeSessionStatus =
  | { type: "idle" }
  | { type: "busy" }
  | { type: "retry"; attempt: number; message: string; next: number };

export interface OpenCodeProjectInfo {
  id: string;
  worktree: string;
  vcs?: string;
  name?: string;
  sandboxes: string[];
}

export interface OpenCodeVcsInfo {
  branch: string;
}

export interface OpenCodeAgentInfo {
  name: string;
  description?: string;
  mode: "subagent" | "primary" | "all";
  native?: boolean;
  hidden?: boolean;
  color?: string;
  model?: {
    providerID: string;
    modelID: string;
  };
}

export type OpenCodeMcpStatus =
  | { status: "connected" }
  | { status: "disabled" }
  | { status: "needs_auth" }
  | { status: "failed"; error: string }
  | { status: "needs_client_registration"; error: string };

export type OpenCodeMcpMap = Record<string, OpenCodeMcpStatus>;

export type McpTransport = "stdio" | "http";

export type AcpTransport = "stdio" | "http";

export interface AcpServerConfig {
  name: string;
  label: string;
  transport: AcpTransport;
  command?: string | null;
  args?: string[];
  cwd?: string | null;
  env?: Record<string, string>;
  url?: string | null;
  headers?: Record<string, string>;
  enabled: boolean;
  workspace_server_id?: string | null;
  timeout_sec: number;
}

export interface AcpServerInfo extends AcpServerConfig {
  status: "connected" | "disconnected" | "disabled";
  connected: boolean;
  last_error?: string | null;
  last_connected_at?: number | null;
  last_disconnected_at?: number | null;
  default?: boolean;
}

export interface AcpRegistryConfig {
  version: number;
  default_server?: string | null;
  servers: Record<string, AcpServerConfig>;
}

export interface AcpRuntimeStatus {
  available: boolean;
  connected_count: number;
  enabled_count: number;
  server_count: number;
  default_server?: string | null;
  message: string;
}

export interface McpServerConfig {
  name: string;
  label: string;
  transport: McpTransport;
  command?: string | null;
  args?: string[];
  cwd?: string | null;
  env?: Record<string, string>;
  url?: string | null;
  headers?: Record<string, string>;
  enabled: boolean;
  builtin: boolean;
  timeout_sec: number;
}

export interface McpServerInfo extends McpServerConfig {
  status: "connected" | "disconnected" | "disabled";
  connected: boolean;
  tool_count: number;
  tools: string[];
  last_error?: string | null;
  last_connected_at?: number | null;
  last_disconnected_at?: number | null;
  session_id?: string | null;
}

export interface McpRegistryConfig {
  version: number;
  servers: Record<string, McpServerConfig>;
}

export interface McpRuntimeStatus {
  available: boolean;
  connected_count: number;
  enabled_count: number;
  server_count: number;
  builtin_count: number;
  builtin_ready?: boolean;
  builtin_tool_count?: number;
  configured_count?: number;
  message: string;
}

export type OpenCodePermissionAction = "allow" | "ask" | "deny";
export type OpenCodePermissionRule = OpenCodePermissionAction | Record<string, OpenCodePermissionAction>;
export type OpenCodePermissionConfig = OpenCodePermissionAction | Record<string, OpenCodePermissionRule>;

export interface OpenCodeConfig {
  $schema?: string;
  model?: string;
  small_model?: string;
  default_agent?: string;
  share?: "manual" | "auto" | "disabled";
  permission?: OpenCodePermissionConfig;
  skills?: {
    paths?: string[];
    urls?: string[];
  };
  command?: Record<
    string,
    {
      template: string;
      description?: string;
      agent?: string;
      model?: string;
      subtask?: boolean;
    }
  >;
  mode?: Record<string, unknown>;
  plugin?: string[];
  provider?: Record<string, unknown>;
  mcp?: Record<string, unknown>;
  agent?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface AgentToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface SessionBusPayload {
  type: string;
  properties?: Record<string, unknown>;
}

export interface GlobalBusEnvelope {
  directory?: string | null;
  payload?: SessionBusPayload | null;
}
