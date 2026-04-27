/**
 * 研究日报 - 研究简报（重构：清晰排版 + 暗色适配 + 阅读体验优化）
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Spinner, Empty } from "@/components/ui";
import { useToast } from "@/contexts/ToastContext";
import DOMPurify from "dompurify";
import ConfirmDialog from "@/components/ConfirmDialog";
import { briefApi, generatedApi, tasksApi } from "@/services/api";
import type { GeneratedContentListItem, GeneratedContent } from "@/types";
import {
  Newspaper, Send, CheckCircle2, Mail, FileText, Calendar, Clock,
  Trash2, Sparkles, Plus, RefreshCw, X,
} from "@/lib/lucide";

function getBriefTitle(title: string): string {
  return title.replace(/^(Daily Brief|My Day|研究日报)\s*:\s*/i, "").trim() || title;
}

export default function DailyBrief() {
  const { toast } = useToast();
  const navigate = useNavigate();
  const briefRef = useRef<HTMLDivElement>(null);
  const [date, setDate] = useState("");
  const [recipient, setRecipient] = useState("");
  const [loading, setLoading] = useState(false);
  const [taskProgress, setTaskProgress] = useState<string>("");
  const [genDone, setGenDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [history, setHistory] = useState<GeneratedContentListItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [selectedContent, setSelectedContent] = useState<GeneratedContent | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [showGenPanel, setShowGenPanel] = useState(false);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try { const res = await generatedApi.list("daily_brief", 50); setHistory(res.items); }
    catch { toast("error", "加载研究日报历史失败"); } finally { setHistoryLoading(false); }
  }, [toast]);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  // 自动加载最新一份
  useEffect(() => {
    if (history.length > 0 && !selectedContent) {
      handleView(history[0]);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [history]);

  // 事件委托：点击简报中的论文卡片跳转到详情页
  useEffect(() => {
    const el = briefRef.current;
    if (!el) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      const card = target.closest<HTMLElement>("[data-paper-id]");
      if (card) {
        const paperId = card.dataset.paperId;
        if (paperId) navigate(`/papers/${paperId}`);
      }
    };
    el.addEventListener("click", handler);
    return () => el.removeEventListener("click", handler);
  }, [navigate, selectedContent]);

  const handleGenerate = async () => {
    setLoading(true); setError(null); setGenDone(false); setTaskProgress("正在提交任务...");
    try {
      const data: Record<string, string> = {};
      if (date) data.date = date;
      if (recipient) data.recipient = recipient;
      const res = await briefApi.daily(Object.keys(data).length > 0 ? data : undefined);
      const taskId = res.task_id;
      setTaskProgress("任务已提交，正在生成研究日报...");

      // 轮询任务状态，直到完成或失败
      const POLL_INTERVAL = 3000;
      const MAX_WAIT_MS = 5 * 60 * 1000; // 最多等 5 分钟
      const startTime = Date.now();

      await new Promise<void>((resolve, reject) => {
        const poll = async () => {
          if (Date.now() - startTime > MAX_WAIT_MS) {
            reject(new Error("生成超时，请稍后刷新查看结果"));
            return;
          }
          try {
            const status = await tasksApi.getStatus(taskId);
            const pct = Math.round(status.progress * 100);
            setTaskProgress(status.message || `生成中... ${pct}%`);
            if (status.status === "completed") { resolve(); return; }
            if (status.status === "failed" || status.status === "cancelled") {
              reject(new Error(status.error || (status.status === "cancelled" ? "任务已终止" : "生成失败")));
              return;
            }
          } catch {
            // 轮询出错不中断，继续重试
          }
          setTimeout(poll, POLL_INTERVAL);
        };
        poll();
      });

      setGenDone(true);
      setTaskProgress("");
      await loadHistory();
      setShowGenPanel(false);
      toast("success", "研究日报生成成功");
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成失败");
      setTaskProgress("");
    } finally {
      setLoading(false);
    }
  };

  const handleView = async (item: GeneratedContentListItem) => {
    setDetailLoading(true); setSelectedContent(null);
    try { setSelectedContent(await generatedApi.detail(item.id)); }
    catch { toast("error", "加载研究日报内容失败"); } finally { setDetailLoading(false); }
  };

  const handleDelete = async (id: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    try { await generatedApi.delete(id); setHistory((p) => p.filter((h) => h.id !== id)); if (selectedContent?.id === id) setSelectedContent(null); }
    catch { toast("error", "删除研究日报失败"); }
  };

  const fmtDate = (iso: string) => {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const now = new Date();
    const isToday = d.toDateString() === now.toDateString();
    const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
    const isYesterday = d.toDateString() === yesterday.toDateString();
    if (isToday) return `今天 ${d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
    if (isYesterday) return `昨天 ${d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
    return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" }) + " " + d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  };

  return (
    <div className="animate-fade-in space-y-5 sm:space-y-6">
      <div className="page-hero flex flex-col gap-4 rounded-[28px] p-4 sm:gap-5 sm:p-6 lg:flex-row lg:items-start lg:justify-between lg:rounded-[34px] lg:p-7">
        <div className="flex items-start gap-4">
          <div className="glass-segment flex h-14 w-14 items-center justify-center rounded-[22px]">
            <Newspaper className="h-6 w-6 text-primary" />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-bold tracking-[-0.04em] text-ink">研究日报</h1>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="glass-segment inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-xs text-ink-secondary">
                <FileText className="h-3.5 w-3.5" />
                已生成 {history.length} 份
              </span>
              {selectedContent && (
                <span className="glass-segment inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-xs text-ink-secondary">
                  <Clock className="h-3.5 w-3.5" />
                  当前查看 {fmtDate(selectedContent.created_at)}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            icon={showGenPanel ? <X className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
            onClick={() => setShowGenPanel(!showGenPanel)}
          >
            {showGenPanel ? "收起生成器" : "生成研究日报"}
          </Button>
        </div>
      </div>

      {showGenPanel && (
        <div className="glass-card glass-card-strong rounded-[24px] p-4 sm:rounded-[30px] sm:p-5 lg:p-6">
          <div className="flex flex-col gap-5">
            <div className="flex items-start gap-3">
              <div className="glass-segment flex h-10 w-10 items-center justify-center rounded-[18px]">
                <Sparkles className="h-4.5 w-4.5 text-primary" />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-ink">生成新一期研究日报</h2>
              </div>
            </div>

            <div className="grid gap-4 lg:grid-cols-[minmax(0,220px)_minmax(0,260px)_auto] lg:items-end">
              <div className="space-y-1.5">
                <label className="flex items-center gap-1.5 text-[11px] font-medium text-ink-secondary">
                  <Calendar className="h-3.5 w-3.5" />
                  指定日期
                </label>
                <input
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  className="form-input"
                />
              </div>

              <div className="space-y-1.5">
                <label className="flex items-center gap-1.5 text-[11px] font-medium text-ink-secondary">
                  <Mail className="h-3.5 w-3.5" />
                  邮件通知
                </label>
                <input
                  type="email"
                  value={recipient}
                  onChange={(e) => setRecipient(e.target.value)}
                  placeholder="可选，生成后同时发送通知"
                  className="form-input"
                />
              </div>

              <div className="flex flex-wrap gap-2">
                <Button
                  icon={loading ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                  onClick={handleGenerate}
                  loading={loading}
                >
                  立即生成
                </Button>
              </div>
            </div>

            {error && (
              <div className="glass-segment flex items-center gap-2 rounded-[20px] border-error/20 bg-error-light/85 px-4 py-3 text-sm text-error">
                <X className="h-4 w-4" />
                <span>{error}</span>
              </div>
            )}

            {taskProgress && !error && (
              <div className="glass-segment flex items-center gap-2 rounded-[20px] px-4 py-3 text-sm text-ink-secondary">
                <RefreshCw className="h-4 w-4 animate-spin text-primary" />
                <span>{taskProgress}</span>
              </div>
            )}

            {genDone && !loading && !error && (
              <div className="glass-segment flex items-center gap-2 rounded-[20px] border-success/20 bg-success/10 px-4 py-3 text-sm text-success">
                <CheckCircle2 className="h-4 w-4" />
                <span>生成成功，已刷新研究日报历史列表。</span>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[300px_minmax(0,1fr)] xl:gap-5">
        <aside className="glass-card glass-card-strong flex min-h-[620px] flex-col overflow-hidden rounded-[30px]">
          <div className="border-b border-border/70 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-ink">研究日报历史</p>
              </div>
              <span className="glass-segment rounded-full px-2.5 py-1 text-[11px] font-medium text-ink-secondary">
                {history.length}
              </span>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-3 py-3">
            {historyLoading ? (
              <div className="flex min-h-[220px] items-center justify-center">
                <Spinner text="" />
              </div>
            ) : history.length === 0 ? (
              <div className="flex min-h-[220px] flex-col items-center justify-center px-4 text-center text-sm text-ink-tertiary">
                <div className="glass-segment flex h-14 w-14 items-center justify-center rounded-[20px]">
                  <Newspaper className="h-6 w-6 text-ink-tertiary/50" />
                </div>
                <p className="mt-4">还没有生成过研究日报</p>
              </div>
            ) : (
              <div className="space-y-2">
                {history.map((item) => {
                  const active = selectedContent?.id === item.id;
                  return (
                    <div
                      role="button"
                      tabIndex={0}
                      key={item.id}
                      onClick={() => handleView(item)}
                      onKeyDown={(e) => { if (e.key === "Enter") handleView(item); }}
                      className={`group relative cursor-pointer rounded-[22px] border px-3 py-3 text-left transition-all ${
                        active
                          ? "border-primary/24 bg-primary/[0.08] shadow-[0_20px_38px_-34px_rgba(36,92,84,0.34)]"
                          : "border-transparent bg-surface/72 hover:-translate-y-0.5 hover:border-primary/18 hover:bg-surface/88"
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        <span className={`mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-[16px] ${
                          active ? "bg-primary/12 text-primary" : "bg-page text-ink-tertiary"
                        }`}>
                          <FileText className="h-4 w-4" />
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className={`truncate text-sm font-semibold ${active ? "text-primary" : "text-ink"}`}>
                            {getBriefTitle(item.title)}
                          </p>
                          <p className="mt-1 text-xs text-ink-tertiary">{fmtDate(item.created_at)}</p>
                        </div>
                        <button
                          aria-label="删除"
                          onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(item.id); }}
                          className={`shrink-0 rounded-lg p-1.5 transition-all ${
                            active
                              ? "text-primary/70 hover:bg-primary/10 hover:text-primary"
                              : "text-ink-tertiary opacity-0 hover:bg-error-light hover:text-error group-hover:opacity-100"
                          }`}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </aside>

        <section className="glass-card glass-card-strong flex min-h-[620px] flex-col overflow-hidden rounded-[30px]">
          {detailLoading && (
            <div className="flex min-h-[620px] items-center justify-center">
              <Spinner text="加载研究日报..." />
            </div>
          )}

          {!detailLoading && selectedContent && (
            <div className="animate-fade-in flex h-full flex-col">
              <div className="border-b border-border/70 px-5 py-5 lg:px-6">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div className="min-w-0">
                    <div className="flex items-start gap-3">
                      <div className="glass-segment flex h-12 w-12 items-center justify-center rounded-[20px]">
                        <Sparkles className="h-5 w-5 text-primary" />
                      </div>
                      <div className="min-w-0">
                        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-primary">研究日报</p>
                        <h2 className="mt-1 text-2xl font-bold tracking-[-0.04em] text-ink lg:text-[2rem]">
                          {getBriefTitle(selectedContent.title)}
                        </h2>
                      </div>
                    </div>
                    <p className="mt-3 flex flex-wrap items-center gap-2 text-sm text-ink-secondary">
                      <Clock className="h-4 w-4 text-ink-tertiary" />
                      {new Date(selectedContent.created_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}
                    </p>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <span className="glass-segment inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-xs text-ink-secondary">
                      <Newspaper className="h-3.5 w-3.5" />
                      AI 生成研究日报
                    </span>
                    <span className="glass-segment inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-xs text-ink-secondary">
                      <Calendar className="h-3.5 w-3.5" />
                      {fmtDate(selectedContent.created_at)}
                    </span>
                  </div>
                </div>
              </div>

              <div className="flex-1 px-4 py-5 lg:px-6 lg:py-6">
                <div className="glass-segment rounded-[28px] p-4 lg:p-6">
                  <div
                    ref={briefRef}
                    className="brief-content"
                    dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(selectedContent.markdown, { ADD_ATTR: ["data-paper-id", "data-arxiv-id"] }) }}
                  />
                </div>
              </div>
            </div>
          )}

          {!detailLoading && !selectedContent && (
            <div className="flex min-h-[620px] items-center justify-center p-6">
              <Empty
                className="mx-auto max-w-xl"
                icon={<Sparkles className="h-12 w-12 text-primary/60" />}
                title="还没有研究日报"
                action={
                  <Button size="sm" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setShowGenPanel(true)}>
                    打开生成器
                  </Button>
                }
              />
            </div>
          )}
        </section>
      </div>

      {/* 简报内容样式覆盖 */}
      <style>{briefContentStyles}</style>

      <ConfirmDialog
        open={!!confirmDeleteId}
        title="删除研究日报"
        description="确定要删除这份研究日报吗？"
        variant="danger"
        confirmLabel="删除"
        onConfirm={async () => { if (confirmDeleteId) { await handleDelete(confirmDeleteId); setConfirmDeleteId(null); } }}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </div>
  );
}

/**
 * 覆盖后端生成的 HTML 简报样式，适配 app 主题 + 暗色模式
 */
const briefContentStyles = `
.brief-content {
  --brief-border: color-mix(in srgb, var(--color-border) 86%, transparent);
  --brief-border-strong: color-mix(in srgb, var(--color-border) 94%, transparent);
  --brief-surface-soft: color-mix(in srgb, var(--color-surface) 74%, var(--color-page));
  --brief-surface: color-mix(in srgb, var(--color-surface) 88%, var(--color-page));
  --brief-surface-strong: color-mix(in srgb, var(--color-surface) 94%, var(--color-page));
  --brief-surface-accent: color-mix(in srgb, var(--color-primary) 10%, var(--color-surface));
  --brief-shadow: 0 18px 36px -34px color-mix(in srgb, var(--color-ink) 42%, transparent);
  --brief-shadow-hover: 0 24px 42px -34px color-mix(in srgb, var(--color-ink) 52%, transparent);
  --brief-score-high-bg: color-mix(in srgb, var(--color-success) 18%, var(--color-surface));
  --brief-score-mid-bg: color-mix(in srgb, var(--color-warning) 18%, var(--color-surface));
  --brief-score-low-bg: color-mix(in srgb, var(--color-error) 18%, var(--color-surface));
  --brief-innovation-bg: color-mix(in srgb, var(--color-warning) 14%, var(--color-surface));
  --brief-deep-badge-bg: color-mix(in srgb, var(--color-primary) 14%, var(--color-surface));
  max-width: 980px;
  margin: 0 auto;
  color: var(--color-ink, #16202a);
  font-family: inherit;
  line-height: 1.72;
}

html[data-visual-style="linear-style"] .brief-content {
  --brief-border: color-mix(in srgb, var(--color-border) 94%, transparent);
  --brief-border-strong: color-mix(in srgb, var(--color-border) 98%, transparent);
  --brief-surface-soft: color-mix(in srgb, var(--color-surface) 70%, var(--color-page));
  --brief-surface: color-mix(in srgb, var(--color-surface) 82%, var(--color-page));
  --brief-surface-strong: color-mix(in srgb, var(--color-surface) 90%, var(--color-page));
  --brief-surface-accent: color-mix(in srgb, var(--color-primary) 10%, var(--color-surface));
  --brief-shadow: 0 18px 36px -34px rgba(0, 0, 0, 0.62);
  --brief-shadow-hover: 0 24px 42px -34px rgba(0, 0, 0, 0.72);
}

.brief-content body,
.brief-content html {
  all: unset;
  display: block;
}

.brief-content * {
  box-sizing: border-box;
  font-family: inherit !important;
}

.brief-content a {
  color: var(--color-primary, #245c54);
  text-decoration: none;
}

.brief-content a:hover {
  text-decoration: underline;
}

.brief-content h1 {
  margin: 0 0 0.4rem;
  font-size: clamp(2rem, 2.8vw, 2.9rem);
  font-weight: 800;
  letter-spacing: -0.05em;
  color: var(--color-ink, #0f1723);
}

.brief-content .subtitle {
  margin-bottom: 1.75rem;
  color: var(--color-ink-tertiary, #8a95a4);
  font-size: 0.85rem;
}

.brief-content p {
  margin: 0 0 1rem;
  color: var(--color-ink-secondary, #52606d);
}

.brief-content ul,
.brief-content ol {
  margin: 0.5rem 0 1rem 1.25rem;
  color: var(--color-ink-secondary, #52606d);
}

.brief-content li {
  margin-bottom: 0.4rem;
}

.brief-content .stats {
  display: grid !important;
  grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
  gap: 14px;
  margin: 2rem 0 2.35rem;
}

.brief-content .stat-card {
  border: 1px solid var(--brief-border) !important;
  border-radius: 24px !important;
  background: linear-gradient(145deg, var(--brief-surface-strong), var(--brief-surface)) !important;
  box-shadow: var(--brief-shadow);
  padding: 20px 18px !important;
  text-align: center;
}

.brief-content .stat-num {
  color: var(--color-primary, #245c54) !important;
  font-size: clamp(2rem, 3.4vw, 2.9rem) !important;
  font-weight: 800 !important;
  line-height: 1.08;
}

.brief-content .stat-label {
  margin-top: 6px;
  color: var(--color-ink-tertiary, #8a95a4) !important;
  font-size: 0.74rem !important;
  font-weight: 600 !important;
  letter-spacing: 0.04em;
}

.brief-content .section {
  margin: 2rem 0;
}

.brief-content .section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 0.95rem;
  padding-bottom: 0.7rem;
  border-bottom: 2px solid color-mix(in srgb, var(--color-primary) 26%, transparent) !important;
  color: var(--color-ink, #0f1723) !important;
  font-size: 1.08rem !important;
  font-weight: 700 !important;
}

.brief-content .rec-card,
.brief-content .paper-item,
.brief-content .deep-card {
  margin-bottom: 12px;
  border: 1px solid var(--brief-border) !important;
  border-radius: 22px !important;
  background: linear-gradient(145deg, var(--brief-surface-strong), var(--brief-surface)) !important;
  box-shadow: var(--brief-shadow);
  padding: 16px 18px !important;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}

.brief-content .rec-card:hover,
.brief-content .paper-item:hover,
.brief-content .deep-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--brief-shadow-hover);
}

.brief-content .rec-card {
  border-left: 3px solid color-mix(in srgb, var(--color-primary) 52%, var(--color-border)) !important;
}

.brief-content .deep-card {
  border-left: 4px solid var(--color-primary) !important;
  background: linear-gradient(
    145deg,
    var(--brief-surface-accent),
    var(--brief-surface)
  ) !important;
}

.brief-content .rec-title,
.brief-content .paper-title,
.brief-content .deep-title {
  color: var(--color-ink, #0f1723) !important;
  font-weight: 700 !important;
  line-height: 1.45;
}

.brief-content .rec-title,
.brief-content .paper-title {
  font-size: 0.95rem !important;
}

.brief-content .deep-title {
  font-size: 1rem !important;
}

.brief-content .rec-meta,
.brief-content .paper-id {
  margin-top: 4px;
  color: var(--color-ink-tertiary, #8a95a4) !important;
  font-size: 0.72rem !important;
  font-family: ui-monospace, monospace !important;
}

.brief-content .rec-reason,
.brief-content .paper-summary,
.brief-content .deep-text {
  margin-top: 0.65rem !important;
  color: var(--color-ink-secondary, #52606d) !important;
  font-size: 0.86rem !important;
  line-height: 1.68;
  white-space: pre-wrap;
}

.brief-content .topic-group {
  margin-bottom: 1.3rem;
}

.brief-content .topic-name {
  display: flex !important;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  color: var(--color-primary, #245c54) !important;
  font-size: 0.9rem !important;
  font-weight: 700 !important;
}

.brief-content .topic-name::before {
  content: "";
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: linear-gradient(180deg, var(--color-primary), color-mix(in srgb, var(--color-primary) 55%, var(--color-surface)));
}

.brief-content .kw-tag {
  display: inline-flex !important;
  align-items: center;
  margin: 3px !important;
  border: 1px solid color-mix(in srgb, var(--color-primary) 18%, transparent);
  border-radius: 999px !important;
  background: color-mix(in srgb, var(--color-primary) 10%, transparent) !important;
  color: var(--color-primary, #245c54) !important;
  padding: 4px 12px !important;
  font-size: 0.72rem !important;
  font-weight: 600 !important;
}

.brief-content .deep-section {
  margin-top: 10px !important;
}

.brief-content .deep-section-label {
  margin-bottom: 4px !important;
  color: var(--color-primary, #245c54) !important;
  font-size: 0.72rem !important;
  font-weight: 700 !important;
}

.brief-content .risk-list {
  margin: 4px 0 0 16px !important;
  padding: 0 !important;
  color: var(--color-warning) !important;
  font-size: 0.72rem !important;
}

.brief-content .risk-list li {
  margin-bottom: 2px;
}

.brief-content .score-badge {
  border-radius: 9999px !important;
  font-weight: 700 !important;
  white-space: nowrap;
}

.brief-content .score-sm {
  padding: 1px 6px !important;
  font-size: 0.65rem !important;
}

.brief-content .score-high {
  background: var(--brief-score-high-bg) !important;
  color: var(--color-success) !important;
}

.brief-content .score-mid {
  background: var(--brief-score-mid-bg) !important;
  color: var(--color-warning) !important;
}

.brief-content .score-low {
  background: var(--brief-score-low-bg) !important;
  color: var(--color-error) !important;
}

.brief-content .innovation-tags {
  display: flex !important;
  flex-wrap: wrap !important;
  gap: 4px !important;
  margin-top: 6px !important;
}

.brief-content .innovation-tag {
  display: inline-block !important;
  border-radius: 8px !important;
  background: var(--brief-innovation-bg) !important;
  color: var(--color-warning) !important;
  padding: 3px 8px !important;
  font-size: 0.68rem !important;
}

.brief-content .deep-badge {
  border-radius: 6px !important;
  background: var(--brief-deep-badge-bg) !important;
  color: var(--color-primary, #245c54) !important;
  padding: 1px 6px !important;
  font-size: 0.6rem !important;
  font-weight: 700 !important;
}

.brief-content .paper-header {
  display: flex !important;
  align-items: flex-start !important;
  justify-content: space-between !important;
  gap: 12px !important;
}

.brief-content .footer {
  margin-top: 2.5rem;
  padding-top: 1.2rem;
  border-top: 1px solid var(--brief-border-strong) !important;
  color: var(--color-ink-tertiary, #8a95a4) !important;
  text-align: center;
  font-size: 0.72rem !important;
}

@media (max-width: 768px) {
  .brief-content {
    max-width: none;
  }

  .brief-content .stats {
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
  }

  .brief-content .paper-header {
    flex-direction: column !important;
  }

  .brief-content h1 {
    font-size: 1.9rem;
  }
}

@media (max-width: 520px) {
  .brief-content .stats {
    grid-template-columns: 1fr !important;
  }
}
`;
