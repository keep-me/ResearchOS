import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Spinner } from "@/components/ui";
import { useToast } from "@/contexts/ToastContext";
import { graphApi, ingestApi } from "@/services/api";
import type { CitationDetail, Paper, RichCitationEntry } from "@/types";
import { BookOpen, ExternalLink, Library, Loader2, PackagePlus, RefreshCw } from "@/lib/lucide";

const TOP_OPTIONS = [10, 15, 20, 30, 50];

type ColumnTone = "amber" | "blue";

function isRealArxivId(value: string | null | undefined) {
  if (!value) return false;
  const normalized = value.trim();
  return /^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?\/\d{7})(?:v\d+)?$/i.test(normalized);
}

function sortEntries(entries: RichCitationEntry[]) {
  return [...entries].sort((a, b) => {
    const citationDiff = (b.citation_count || 0) - (a.citation_count || 0);
    if (citationDiff !== 0) return citationDiff;
    return (b.year || 0) - (a.year || 0);
  });
}

export default function PaperCitationExpansion({ paper }: { paper: Paper }) {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<CitationDetail | null>(null);
  const [topN, setTopN] = useState(15);
  const [importingIds, setImportingIds] = useState<Set<string>>(new Set());

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    try {
      const result = await graphApi.citationDetail(paper.id, { refresh });
      setData(result);
    } catch (err) {
      const message = err instanceof Error ? err.message : "加载引用扩展失败";
      toast("error", message);
    } finally {
      setLoading(false);
    }
  }, [paper.id, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const references = useMemo(() => sortEntries(data?.references || []).slice(0, topN), [data, topN]);
  const citedBy = useMemo(() => sortEntries(data?.cited_by || []).slice(0, topN), [data, topN]);
  const libraryHits = (data?.stats.in_library_references || 0) + (data?.stats.in_library_cited_by || 0);
  const shownCount = references.length + citedBy.length;
  const totalEntries = (data?.references.length || 0) + (data?.cited_by.length || 0);

  const handleImport = useCallback(
    async (entry: RichCitationEntry) => {
      const key = entry.arxiv_id || entry.scholar_id || entry.title;
      if (!key) return;

      setImportingIds((prev) => {
        const next = new Set(prev);
        next.add(key);
        return next;
      });

      try {
        await ingestApi.importReferences({
          source_paper_id: paper.id,
          source_paper_title: paper.title,
          entries: [
            {
              scholar_id: entry.scholar_id,
              title: entry.title,
              year: entry.year,
              venue: entry.venue,
              citation_count: entry.citation_count,
              arxiv_id: entry.arxiv_id,
              abstract: entry.abstract,
            },
          ],
        });
        toast("success", `已提交导入任务：${entry.title}`);
      } catch (err) {
        toast("error", err instanceof Error ? err.message : "提交导入失败");
      } finally {
        setImportingIds((prev) => {
          const next = new Set(prev);
          next.delete(key);
          return next;
        });
      }
    },
    [paper.id, paper.title, toast],
  );

  return (
    <section className="rounded-2xl border border-border bg-surface p-5 shadow-sm">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <BookOpen className="h-4 w-4 text-primary" />
            <h3 className="text-sm font-semibold text-ink">高影响引文</h3>
            <Badge variant="info">默认 Top 15</Badge>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs text-ink-tertiary">展示数量</label>
          <select
            value={topN}
            onChange={(event) => setTopN(Number(event.target.value))}
            className="h-9 rounded-lg border border-border bg-page px-3 text-sm text-ink focus:border-primary focus:outline-none"
          >
            {TOP_OPTIONS.map((option) => (
              <option key={option} value={option}>
                Top {option}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            variant="secondary"
            icon={<RefreshCw className="h-3.5 w-3.5" />}
            onClick={() => void load(true)}
            disabled={loading}
          >
            刷新
          </Button>
        </div>
      </div>

      {loading ? (
        <Spinner text="加载高影响引用中..." />
      ) : !data || totalEntries === 0 ? (
        <div className="mt-4 rounded-xl border border-dashed border-border bg-page px-5 py-8 text-center">
          <p className="text-sm font-medium text-ink-secondary">暂无外部引用数据</p>
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard label="参考文献总数" value={data.stats.total_references} tone="amber" />
            <StatCard label="被引论文总数" value={data.stats.total_cited_by} tone="blue" />
            <StatCard label="当前展示条目" value={shownCount} tone="slate" />
            <StatCard label="库内命中" value={libraryHits} tone="emerald" />
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <EntryColumn
              title={`高影响参考文献 Top ${topN}`}
              entries={references}
              tone="amber"
              importingIds={importingIds}
              onImport={handleImport}
            />
            <EntryColumn
              title={`高影响被引论文 Top ${topN}`}
              entries={citedBy}
              tone="blue"
              importingIds={importingIds}
              onImport={handleImport}
            />
          </div>
        </div>
      )}
    </section>
  );
}

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "amber" | "blue" | "emerald" | "slate";
}) {
  const toneClass: Record<string, string> = {
    amber: "border-amber-500/20 bg-amber-500/5 text-amber-500",
    blue: "border-blue-500/20 bg-blue-500/5 text-blue-500",
    emerald: "border-emerald-500/20 bg-emerald-500/5 text-emerald-500",
    slate: "border-slate-400/20 bg-slate-500/5 text-slate-400",
  };

  return (
    <div className={`rounded-2xl border p-4 ${toneClass[tone]}`}>
      <p className="text-[11px] font-medium tracking-[0.08em] opacity-80">{label}</p>
      <p className="mt-2 text-2xl font-bold">{value}</p>
    </div>
  );
}

function EntryColumn({
  title,
  entries,
  tone,
  importingIds,
  onImport,
}: {
  title: string;
  entries: RichCitationEntry[];
  tone: ColumnTone;
  importingIds: Set<string>;
  onImport: (entry: RichCitationEntry) => void;
}) {
  const toneClass =
    tone === "amber"
      ? "border-amber-500/20 bg-[linear-gradient(180deg,rgba(245,158,11,0.14),rgba(255,255,255,0.02))]"
      : "border-blue-500/20 bg-[linear-gradient(180deg,rgba(59,130,246,0.14),rgba(255,255,255,0.02))]";

  return (
    <div className={`min-w-0 rounded-2xl border p-4 ${toneClass}`}>
      <div className="mb-3 flex items-center gap-2">
        <Library className="h-4 w-4 text-primary" />
        <p className="text-sm font-semibold text-ink">{title}</p>
      </div>

      {entries.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border bg-page px-4 py-6 text-sm text-ink-tertiary">
          暂无条目
        </p>
      ) : (
        <div className="max-h-[680px] space-y-3 overflow-y-auto pr-1">
          {entries.map((entry, index) => {
            const importKey = entry.arxiv_id || entry.scholar_id || entry.title;
            const importing = importingIds.has(importKey);

            return (
              <article
                key={`${entry.title}-${index}`}
                className="min-w-0 rounded-2xl border border-border bg-page/92 p-4 shadow-[0_16px_34px_-30px_rgba(15,23,35,0.46)] backdrop-blur-sm"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-[15px] font-semibold leading-6 text-ink break-words">{entry.title}</p>
                    {entry.title_zh && (
                      <p className="mt-1 text-[12px] leading-5 text-ink-tertiary break-words">{entry.title_zh}</p>
                    )}
                    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[12px] leading-5 text-ink-secondary">
                      {entry.year && <span>{entry.year}</span>}
                      {entry.venue && <span className="break-words">{entry.venue}</span>}
                      {entry.citation_count != null && <span>引用 {entry.citation_count}</span>}
                    </div>
                  </div>
                  <div className="shrink-0">
                    {entry.in_library ? (
                      <Badge variant="success">在库</Badge>
                    ) : (
                      <Badge variant="default">外部</Badge>
                    )}
                  </div>
                </div>

                <div className="mt-3 flex flex-wrap gap-2">
                  {entry.in_library && entry.library_paper_id ? (
                    <Link
                      to={`/papers/${entry.library_paper_id}`}
                      className="inline-flex items-center gap-1 rounded-lg bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      查看详情
                    </Link>
                  ) : (
                    <button
                      type="button"
                      onClick={() => onImport(entry)}
                      disabled={importing}
                      className="inline-flex items-center gap-1 rounded-lg bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {importing ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <PackagePlus className="h-3.5 w-3.5" />
                      )}
                      导入到论文库
                    </button>
                  )}

                  {isRealArxivId(entry.arxiv_id) && (
                    <a
                      href={`https://arxiv.org/abs/${entry.arxiv_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded-lg bg-page px-3 py-1.5 text-xs font-medium text-ink-secondary transition-colors hover:text-ink"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      arXiv
                    </a>
                  )}

                  {entry.scholar_id && (
                    <a
                      href={entry.scholar_id.startsWith("http") ? entry.scholar_id : `https://www.semanticscholar.org/paper/${entry.scholar_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded-lg bg-page px-3 py-1.5 text-xs font-medium text-ink-secondary transition-colors hover:text-ink"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      来源
                    </a>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
