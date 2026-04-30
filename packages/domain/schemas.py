from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PaperCreate(BaseModel):
    arxiv_id: str
    title: str
    abstract: str
    publication_date: date | None = None
    metadata: dict = Field(default_factory=dict)


class SkimReport(BaseModel):
    one_liner: str
    innovations: list[str]
    keywords: list[str] = []
    title_zh: str = ""
    abstract_zh: str = ""
    relevance_score: float


class DeepDiveReport(BaseModel):
    method_summary: str
    experiments_summary: str
    ablation_summary: str
    reviewer_risks: list[str]


class AskRequest(BaseModel):
    question: str
    top_k: int = 5


class AskResponse(BaseModel):
    answer: str
    cited_paper_ids: list[UUID]
    evidence: list[dict] = Field(default_factory=list)
    rounds: int = 1


class DailyBriefRequest(BaseModel):
    date: datetime | None = None
    recipient: str | None = None


class TopicCreate(BaseModel):
    name: str
    kind: Literal["subscription", "folder"] = "subscription"
    query: str = ""
    sort_by: Literal["submittedDate", "relevance", "lastUpdatedDate", "impact"] = "submittedDate"
    source: Literal["arxiv", "openalex", "manual", "hybrid"] = "arxiv"
    search_field: Literal["all", "title", "keywords", "authors", "arxiv_id"] = "all"
    priority_mode: Literal["relevance", "time", "impact"] = "time"
    venue_tier: Literal["all", "ccf_a"] = "all"
    venue_type: Literal["all", "conference", "journal"] = "all"
    venue_names: list[str] = Field(default_factory=list)
    from_year: int | None = Field(default=None, ge=1900, le=2100)
    default_folder_id: str | None = None
    enabled: bool = True
    max_results_per_run: int = 20
    retry_limit: int = 2
    schedule_frequency: Literal["daily", "twice_daily", "weekdays", "weekly"] = "daily"
    schedule_time_utc: int = 21
    enable_date_filter: bool = False
    date_filter_days: int = 7
    date_filter_start: date | None = None
    date_filter_end: date | None = None


class TopicUpdate(BaseModel):
    name: str | None = None
    kind: Literal["subscription", "folder"] | None = None
    query: str | None = None
    sort_by: Literal["submittedDate", "relevance", "lastUpdatedDate", "impact"] | None = None
    source: Literal["arxiv", "openalex", "manual", "hybrid"] | None = None
    search_field: Literal["all", "title", "keywords", "authors", "arxiv_id"] | None = None
    priority_mode: Literal["relevance", "time", "impact"] | None = None
    venue_tier: Literal["all", "ccf_a"] | None = None
    venue_type: Literal["all", "conference", "journal"] | None = None
    venue_names: list[str] | None = None
    from_year: int | None = Field(default=None, ge=1900, le=2100)
    default_folder_id: str | None = None
    enabled: bool | None = None
    max_results_per_run: int | None = None
    retry_limit: int | None = None
    schedule_frequency: Literal["daily", "twice_daily", "weekdays", "weekly"] | None = None
    schedule_time_utc: int | None = None
    enable_date_filter: bool | None = None
    date_filter_days: int | None = None
    date_filter_start: date | None = None
    date_filter_end: date | None = None


# ---------- LLM Provider Config ----------


class LLMProviderCreate(BaseModel):
    name: str
    provider: str  # openai / anthropic / zhipu / ...
    api_key: str
    api_base_url: str | None = None
    model_skim: str
    model_deep: str
    model_vision: str | None = None
    embedding_provider: str | None = None
    embedding_api_key: str | None = None
    embedding_api_base_url: str | None = None
    model_embedding: str
    model_fallback: str
    image_provider: str | None = None
    image_api_key: str | None = None
    image_api_base_url: str | None = None
    model_image: str | None = None


class LLMProviderUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None  # openai / anthropic / zhipu / ...
    api_key: str | None = None
    api_base_url: str | None = None
    model_skim: str | None = None
    model_deep: str | None = None
    model_vision: str | None = None
    embedding_provider: str | None = None
    embedding_api_key: str | None = None
    embedding_api_base_url: str | None = None
    model_embedding: str | None = None
    model_fallback: str | None = None
    image_provider: str | None = None
    image_api_key: str | None = None
    image_api_base_url: str | None = None
    model_image: str | None = None


# ---------- Agent ----------

AgentMessageContent = str | dict[str, Any] | list[str | dict[str, Any]]
AgentMessageFormat = str | dict[str, Any] | list[dict[str, Any]]


class AgentMessage(BaseModel):
    """Agent 对话消息"""

    role: str  # user / assistant / tool
    content: AgentMessageContent = ""
    system: str | None = None
    tools: dict[str, bool] | None = None
    variant: str | None = None
    format: AgentMessageFormat | None = None
    text_parts: list[dict[str, Any]] | None = None
    reasoning_content: str | None = None
    reasoning_parts: list[dict[str, Any]] | None = None
    provider_metadata: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    provider_executed: bool | None = None


class AgentChatRequest(BaseModel):
    """Agent 对话请求"""

    messages: list[AgentMessage]
    confirmed_action_id: str | None = None
    session_id: str | None = None
    agent_backend_id: str | None = None
    mode: Literal["build", "plan", "general"] = "build"
    workspace_path: str | None = None
    workspace_server_id: str | None = None
    reasoning_level: Literal["default", "low", "medium", "high", "xhigh"] = "default"
    active_skill_ids: list[str] = Field(default_factory=list)


# ---------- API Request Bodies ----------


class ReferenceImportReq(BaseModel):
    source_paper_id: str
    source_paper_title: str = ""
    entries: list[dict]
    topic_ids: list[str] = []


class SuggestKeywordsReq(BaseModel):
    description: str
    source_scope: Literal["hybrid", "arxiv", "openalex"] = "hybrid"
    search_field: Literal["all", "title", "keywords", "authors", "arxiv_id"] = "all"


class AIExplainReq(BaseModel):
    text: str
    action: str = "analyze"
    question: str | None = None


class PaperReaderQueryReq(BaseModel):
    scope: Literal["paper", "selection", "figure"] = "selection"
    action: Literal["analyze", "explain", "translate", "summarize", "ask"] = "analyze"
    text: str | None = None
    question: str | None = None
    figure_id: str | None = None
    image_base64: str | None = None
    page_number: int | None = Field(default=None, ge=1)


class PaperReaderNoteReq(BaseModel):
    id: str | None = None
    kind: Literal["general", "text", "figure"] = "general"
    title: str | None = None
    content: str = ""
    quote: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    figure_id: str | None = None
    color: str | None = None
    tags: list[str] = Field(default_factory=list)
    pinned: bool = False
    status: Literal["draft", "saved"] | None = None
    source: Literal["manual", "ai_draft"] | None = None
    anchor_source: Literal["pdf_selection", "ocr_block"] | None = None
    anchor_id: str | None = None
    section_id: str | None = None
    section_title: str | None = None


class PaperReaderNoteDraftReq(BaseModel):
    text: str
    quote: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    anchor_source: Literal["pdf_selection", "ocr_block"] | None = None
    anchor_id: str | None = None
    section_id: str | None = None
    section_title: str | None = None


class PaperReaderDocumentSection(BaseModel):
    id: str
    title: str
    level: int = 1
    order: int = 0
    page_start: int | None = Field(default=None, ge=1)


class PaperReaderDocumentBlock(BaseModel):
    id: str
    section_id: str
    page_number: int | None = Field(default=None, ge=1)
    order: int = 0
    type: Literal["heading", "text", "aside_text", "list", "equation", "image", "table"] = "text"
    text: str
    markdown: str = ""
    bbox: dict[str, float] | None = None
    bbox_normalized: bool = False


class PaperReaderDocumentResp(BaseModel):
    paper_id: str
    available: bool = False
    source: Literal["mineru_structured", "mineru_markdown", "none"] = "none"
    markdown: str = ""
    sections: list[PaperReaderDocumentSection] = Field(default_factory=list)
    blocks: list[PaperReaderDocumentBlock] = Field(default_factory=list)


class PaperBatchDeleteReq(BaseModel):
    paper_ids: list[str] = Field(default_factory=list)
    delete_pdf_files: bool = True


class PaperMetadataUpdateReq(BaseModel):
    title: str | None = None
    abstract: str | None = None
    keywords: list[str] | None = None
    title_zh: str | None = None
    abstract_zh: str | None = None
    auto_translate: bool = True


class PaperAutoClassifyReq(BaseModel):
    paper_ids: list[str] = Field(default_factory=list)
    only_unclassified: bool = True
    max_papers: int = Field(default=200, ge=1, le=2000)
    max_topics_per_paper: int = Field(default=2, ge=1, le=5)
    min_score: float = Field(default=1.2, ge=0.0, le=10.0)
    use_graph: bool = True
    dry_run: bool = False


class PaperFigureAnalyzeReq(BaseModel):
    figure_ids: list[str] = Field(default_factory=list)


class PaperFigureDeleteReq(BaseModel):
    figure_ids: list[str] = Field(default_factory=list)


class ArxivIdIngestReq(BaseModel):
    arxiv_ids: list[str] = Field(default_factory=list)
    topic_id: str | None = None
    download_pdf: bool = False


class ExternalLiteratureEntry(BaseModel):
    title: str
    abstract: str = ""
    publication_year: int | None = None
    publication_date: str | None = None
    citation_count: int | None = None
    venue: str | None = None
    venue_type: str | None = None
    venue_tier: str | None = None
    authors: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    arxiv_id: str | None = None
    openalex_id: str | None = None
    source_url: str | None = None
    pdf_url: str | None = None
    source: str | None = None


class ExternalLiteratureSearchReq(BaseModel):
    query: str
    max_results: int = Field(default=20, ge=1, le=200)
    source_scope: Literal["hybrid", "arxiv", "openalex"] = "hybrid"
    sort_mode: Literal["relevance", "time", "impact"] = "relevance"
    venue_tier: Literal["all", "ccf_a"] = "all"
    venue_type: Literal["all", "conference", "journal"] = "all"
    venue_names: list[str] = Field(default_factory=list)
    from_year: int | None = Field(default=None, ge=1900, le=2100)
    date_from: date | None = None
    date_to: date | None = None


class ExternalLiteratureIngestReq(BaseModel):
    entries: list[ExternalLiteratureEntry] = Field(default_factory=list)
    topic_id: str | None = None


class GraphRAGBuildReq(BaseModel):
    paper_ids: list[str] = Field(default_factory=list)
    limit: int = Field(default=12, ge=1, le=200)
    force: bool = False


class GraphRAGQueryReq(BaseModel):
    query: str
    top_k: int = Field(default=6, ge=1, le=20)
    paper_ids: list[str] = Field(default_factory=list)


class WritingProcessReq(BaseModel):
    action: str
    topic: str = ""
    style: str = ""
    content: str = ""
    template_type: str = ""


class WritingRefineReq(BaseModel):
    messages: list[dict] = []


class WritingMultimodalReq(BaseModel):
    action: str
    content: str = ""
    image_base64: str


class WritingImageGenerateReq(BaseModel):
    prompt: str
    image_base64: str | None = None
    aspect_ratio: str = "4:3"
