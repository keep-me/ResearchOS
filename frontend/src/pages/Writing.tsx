/**
 * Writing Assistant - 学术写作助手（支持多轮微调对话）
 * Prompt 模板来源：https://github.com/Leey21/awesome-ai-research-writing
 * @author Color2333
 */
import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { Button, Spinner } from "@/components/ui";
import { useToast } from "@/contexts/ToastContext";
import { writingApi } from "@/services/api";
import type { WritingTemplate, WritingResult, WritingRefineMessage } from "@/types";
import {
  PenTool,
  Languages,
  BookOpen,
  PenLine,
  Sparkles,
  Minimize2,
  Maximize2,
  ShieldCheck,
  Eraser,
  Image,
  Table,
  BarChart3,
  Eye,
  PieChart,
  Send,
  Copy,
  Check,
  RotateCcw,
  Clock,
  Coins,
  ExternalLink,
  MessageCircle,
  User,
  Bot,
  ScanText,
  ImagePlus,
  type LucideIcon,
} from "@/lib/lucide";
import ReactMarkdown from "react-markdown";
import ImageUploader from "@/components/ImageUploader";

const ICON_MAP: Record<string, LucideIcon> = {
  Languages, BookOpen, PenLine, Sparkles, Minimize2, Maximize2,
  ShieldCheck, Eraser, Image, Table, BarChart3, Eye, PieChart, ScanText,
};

interface HistoryItem {
  id: string;
  action: string;
  label: string;
  inputPreview: string;
  content: string;
  timestamp: Date;
}

export default function Writing() {
  const { toast } = useToast();
  const [templates, setTemplates] = useState<WritingTemplate[]>([]);
  const [selected, setSelected] = useState<WritingTemplate | null>(null);
  const [inputText, setInputText] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<WritingResult | null>(null);
  const [copied, setCopied] = useState(false);
  const [history, setHistory] = useState<HistoryItem[]>([]);

  const [imageBase64, setImageBase64] = useState<string | null>(null);
  const [aspectRatio, setAspectRatio] = useState("4:3");

  // 多轮微调对话
  const [refineMsgs, setRefineMsgs] = useState<WritingRefineMessage[]>([]);
  const [refineInput, setRefineInput] = useState("");
  const [refining, setRefining] = useState(false);
  const refineEndRef = useRef<HTMLDivElement>(null);

  const supportsImage = selected?.supports_image ?? false;
  const isImageAction = selected?.action === "image_generate";
  const isImageResult = result?.kind === "image" && !!result.image_base64;

  useEffect(() => {
    writingApi.templates().then((res) => {
      setTemplates(res.items);
      if (res.items.length > 0) setSelected(res.items[0]);
    }).catch(() => toast("error", "加载模板列表失败"));
  }, [toast]);

  // 结果出来后自动滚到对话末尾
  useEffect(() => {
    refineEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [refineMsgs]);

  const handleProcess = useCallback(async () => {
    if (!selected) return;
    const hasText = !!inputText.trim();
    const hasImage = !!imageBase64;
    if (!hasText && !hasImage) return;

    setLoading(true);
    setResult(null);
    setRefineMsgs([]);
    try {
      let res: WritingResult;
      if (selected.action === "image_generate") {
        if (!inputText.trim()) {
          toast("warning", "请先输入绘图提示词");
          return;
        }
        res = await writingApi.generateImage(inputText.trim(), imageBase64, aspectRatio);
      } else if (hasImage) {
        res = await writingApi.processMultimodal(selected.action, inputText.trim(), imageBase64!);
      } else {
        res = await writingApi.process(selected.action, inputText.trim());
      }
      setResult(res);
      const inputSummary = hasImage ? `[图片] ${inputText.trim() || "(无附加文字)"}` : inputText.trim();
      if (res.kind !== "image") {
        setRefineMsgs([
          { role: "user", content: `[${res.label}] ${inputSummary}` },
          { role: "assistant", content: res.content },
        ]);
        setHistory((prev) => [
          {
            id: crypto.randomUUID(),
            action: res.action,
            label: res.label,
            inputPreview: inputSummary.slice(0, 60),
            content: res.content,
            timestamp: new Date(),
          },
          ...prev,
        ].slice(0, 20));
      }
      toast("success", `${res.label}处理完成`);
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "处理失败");
    } finally {
      setLoading(false);
    }
  }, [selected, inputText, imageBase64, aspectRatio, toast]);

  const handleRefine = useCallback(async () => {
    if (!refineInput.trim() || refineMsgs.length < 2) return;
    const instruction = refineInput.trim();
    setRefineInput("");
    const newMsgs: WritingRefineMessage[] = [
      ...refineMsgs,
      { role: "user", content: instruction },
    ];
    setRefineMsgs(newMsgs);
    setRefining(true);
    try {
      const res = await writingApi.refine(newMsgs);
      const updatedMsgs: WritingRefineMessage[] = [
        ...newMsgs,
        { role: "assistant", content: res.content },
      ];
      setRefineMsgs(updatedMsgs);
      // 更新 result 为最新版本
      setResult((prev) => prev ? { ...prev, content: res.content, input_tokens: res.input_tokens, output_tokens: res.output_tokens } : prev);
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "微调失败");
      // 回滚掉用户消息
      setRefineMsgs(refineMsgs);
    } finally {
      setRefining(false);
    }
  }, [refineMsgs, refineInput, toast]);

  const handleRefineKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleRefine();
    }
  }, [handleRefine]);

  const handleCopy = useCallback(async () => {
    if (!result || result.kind === "image") return;
    await navigator.clipboard.writeText(result.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [result]);

  const handleCopyMsg = useCallback(async (content: string) => {
    await navigator.clipboard.writeText(content);
    toast("success", "已复制到剪贴板");
  }, [toast]);

  const handleReset = useCallback(() => {
    setInputText("");
    setImageBase64(null);
    setAspectRatio("4:3");
    setResult(null);
    setRefineMsgs([]);
    setRefineInput("");
  }, []);

  const handleDownloadImage = useCallback(() => {
    if (!result?.image_base64) return;
    const mimeType = result.mime_type || "image/png";
    const link = document.createElement("a");
    link.href = `data:${mimeType};base64,${result.image_base64}`;
    link.download = `researchos-writing-${Date.now()}.${mimeType.includes("jpeg") ? "jpg" : "png"}`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, [result]);

  const handleHistoryClick = useCallback((item: HistoryItem) => {
    const tpl = templates.find((t) => t.action === item.action);
    if (tpl) setSelected(tpl);
    const res: WritingResult = { action: item.action, label: item.label, content: item.content };
    setResult(res);
    setRefineMsgs([
      { role: "user", content: `[${item.label}] ${item.inputPreview}` },
      { role: "assistant", content: item.content },
    ]);
  }, [templates]);

  const categories = useMemo(() => {
    const trans = templates.filter((t) => ["zh_to_en", "en_to_zh"].includes(t.action));
    const polish = templates.filter((t) => ["zh_polish", "en_polish", "compress", "expand"].includes(t.action));
    const check = templates.filter((t) => ["logic_check", "deai"].includes(t.action));
    const gen = templates.filter((t) => ["fig_caption", "table_caption", "experiment_analysis", "reviewer", "chart_recommend"].includes(t.action));
    const vision = templates.filter((t) => ["ocr_extract"].includes(t.action));
    const draw = templates.filter((t) => ["image_generate"].includes(t.action));
    return [
      { label: "翻译", items: trans },
      { label: "润色与调整", items: polish },
      { label: "检查与优化", items: check },
      { label: "生成与分析", items: gen },
      { label: "图像工具", items: vision },
      { label: "AI 绘图", items: draw },
    ].filter((c) => c.items.length > 0);
  }, [templates]);

  // 对话链中跳过第一条用户消息和第一条AI消息（已在结果区展示）
  const refineConversation = refineMsgs.slice(2);

  return (
    <div className="animate-fade-in space-y-5 sm:space-y-6">
      {/* 页面头 */}
      <div className="page-hero rounded-2xl p-4 sm:p-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <div className="rounded-xl bg-primary/10 p-2.5">
              <PenTool className="h-5 w-5 text-primary" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-ink">写作助手</h1>
              <p className="mt-0.5 text-sm text-ink-secondary">
                AI 驱动的学术写作工具箱，覆盖翻译、润色、去AI味等全场景
              </p>
            </div>
          </div>
          <a
            href="https://github.com/Leey21/awesome-ai-research-writing"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-ink-secondary transition-colors hover:bg-hover hover:text-ink"
          >
            <ExternalLink className="h-3 w-3" />
            Prompt 来源
          </a>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-12 lg:gap-6">
        {/* 左侧：模板选择 */}
        <div className="lg:col-span-3">
          <div className="rounded-2xl border border-border bg-surface p-4 shadow-sm">
            <h3 className="mb-3 text-sm font-semibold text-ink">写作工具</h3>
            <div className="space-y-4">
              {categories.map((cat) => (
                <div key={cat.label}>
                  <p className="mb-1 px-1 text-[10px] font-semibold uppercase tracking-wider text-ink-tertiary">
                    {cat.label}
                  </p>
                  <div className="space-y-0.5">
                    {cat.items.map((tpl) => {
                      const Icon = ICON_MAP[tpl.icon] || PenTool;
                      const isActive = selected?.action === tpl.action;
                      return (
                        <button
                          key={tpl.action}
                          onClick={() => { setSelected(tpl); setResult(null); setRefineMsgs([]); setImageBase64(null); }}
                          className={`flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left transition-all ${
                            isActive
                              ? "bg-primary/8 text-primary shadow-sm"
                              : "text-ink-secondary hover:bg-hover hover:text-ink"
                          }`}
                        >
                          <Icon className="h-4 w-4 shrink-0" />
                          <div className="min-w-0 flex-1">
                            <p className="text-sm font-medium">{tpl.label}</p>
                          </div>
                          {tpl.supports_image && (
                            <div className="shrink-0" title="支持图片输入">
                              <ImagePlus className="h-3 w-3 text-ink-tertiary/50" />
                            </div>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>

            {/* 历史记录 */}
            {history.length > 0 && (
              <div className="mt-6 border-t border-border pt-4">
                <p className="mb-2 px-1 text-[10px] font-semibold uppercase tracking-wider text-ink-tertiary">
                  最近使用
                </p>
                <div className="max-h-48 space-y-0.5 overflow-y-auto">
                  {history.map((item) => (
                    <button
                      key={item.id}
                      onClick={() => handleHistoryClick(item)}
                      className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs text-ink-secondary transition-colors hover:bg-hover hover:text-ink"
                    >
                      <Clock className="h-3 w-3 shrink-0 text-ink-tertiary" />
                      <span className="flex-1 truncate">
                        {item.label}: {item.inputPreview}...
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* 右侧：工作区 */}
        <div className="space-y-4 lg:col-span-9">
          {/* 输入区 */}
          {selected && (
            <div className="rounded-2xl border border-border bg-surface p-5 shadow-sm">
              <div className="mb-4 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {(() => { const Icon = ICON_MAP[selected.icon] || PenTool; return <Icon className="h-4 w-4 text-primary" />; })()}
                  <h3 className="text-sm font-semibold text-ink">{selected.label}</h3>
                </div>
              </div>

              {supportsImage && (
                <div className="mb-3">
                  <ImageUploader
                    value={imageBase64}
                    onChange={setImageBase64}
                  />
                </div>
              )}

              {isImageAction && (
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <span className="text-[11px] font-medium text-ink-secondary">画布比例</span>
                  {["1:1", "4:3", "3:4", "16:9", "9:16"].map((ratio) => (
                    <button
                      key={ratio}
                      type="button"
                      onClick={() => setAspectRatio(ratio)}
                      className={`rounded-full border px-2.5 py-1 text-[11px] transition-colors ${
                        aspectRatio === ratio
                          ? "border-primary/30 bg-primary/10 text-primary"
                          : "border-border bg-page text-ink-tertiary hover:text-ink"
                      }`}
                    >
                      {ratio}
                    </button>
                  ))}
                </div>
              )}

              <textarea
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                placeholder={
                  supportsImage && imageBase64
                    ? (isImageAction ? "描述你希望如何参考这张图进行生成或重绘" : "可补充文字说明（可选）")
                    : selected.placeholder
                }
                rows={supportsImage ? 4 : 8}
                className="w-full rounded-xl border border-border bg-page p-4 text-sm text-ink placeholder:text-ink-tertiary/50 focus:border-primary/30 focus:outline-none focus:ring-2 focus:ring-primary/10 transition-all resize-y"
              />

              <div className="mt-3 flex items-center justify-between">
                <span className="text-[10px] text-ink-tertiary">
                  {inputText.length > 0 && `${inputText.length} 字符`}
                </span>
                <div className="flex items-center gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<RotateCcw className="h-3.5 w-3.5" />}
                    onClick={handleReset}
                    disabled={!inputText && !result}
                  >
                    重置
                  </Button>
                  <Button
                    icon={<Send className="h-4 w-4" />}
                    onClick={handleProcess}
                    loading={loading}
                    disabled={isImageAction ? !inputText.trim() : (!inputText.trim() && !imageBase64)}
                  >
                    {loading ? "处理中..." : (isImageAction ? "生成配图" : `执行${selected.label}`)}
                  </Button>
                </div>
              </div>
            </div>
          )}

          {/* 加载中（首次处理） */}
          {loading && <Spinner text="AI 正在处理..." />}

          {/* 结果展示 */}
          {!loading && result && (
            <div className="animate-fade-in rounded-2xl border border-border bg-surface p-5 shadow-sm">
              <div className="mb-4 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-warning" />
                  <h3 className="text-sm font-semibold text-ink">
                    {result.label} 结果
                    {!isImageResult && refineConversation.length > 0 && (
                      <span className="ml-2 text-[10px] font-normal text-ink-tertiary">
                        (已微调 {Math.floor(refineConversation.length / 2)} 轮)
                      </span>
                    )}
                  </h3>
                </div>
                <div className="flex items-center gap-2">
                  {isImageResult && result.model && (
                    <span className="rounded-full bg-page px-2.5 py-1 text-[10px] text-ink-tertiary">
                      {result.provider || "gemini"} / {result.model} / {result.aspect_ratio || aspectRatio}
                    </span>
                  )}
                  {result.input_tokens != null && (
                    <span className="flex items-center gap-1 text-[10px] text-ink-tertiary">
                      <Coins className="h-3 w-3" />
                      {result.input_tokens} in / {result.output_tokens} out
                    </span>
                  )}
                  {isImageResult ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      icon={<ImagePlus className="h-3.5 w-3.5" />}
                      onClick={handleDownloadImage}
                    >
                      下载图片
                    </Button>
                  ) : (
                    <Button
                      variant="secondary"
                      size="sm"
                      icon={copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
                      onClick={handleCopy}
                    >
                      {copied ? "已复制" : "复制最新"}
                    </Button>
                  )}
                </div>
              </div>

              {/* 首次结果 */}
              {isImageResult ? (
                <div className="space-y-4">
                  <div className="overflow-hidden rounded-xl border border-border bg-page p-3">
                    <img
                      src={`data:${result.mime_type || "image/png"};base64,${result.image_base64}`}
                      alt={result.label}
                      className="mx-auto max-h-[70vh] w-auto max-w-full rounded-lg object-contain"
                    />
                  </div>
                  <div className="rounded-xl border border-border bg-page p-5">
                    <div className="prose-custom max-w-none text-sm leading-relaxed text-ink">
                      <ReactMarkdown>{result.content}</ReactMarkdown>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="rounded-xl border border-border bg-page p-5">
                  <div className="prose-custom max-w-none text-sm leading-relaxed text-ink">
                    <ReactMarkdown>{refineMsgs.length >= 2 ? refineMsgs[1].content : result.content}</ReactMarkdown>
                  </div>
                </div>
              )}

              {/* 微调对话链 */}
              {!isImageResult && refineConversation.length > 0 && (
                <div className="mt-4 space-y-3">
                  <div className="flex items-center gap-2">
                    <MessageCircle className="h-3.5 w-3.5 text-primary" />
                    <span className="text-xs font-medium text-ink-secondary">微调对话</span>
                  </div>
                  <div className="max-h-[50vh] space-y-2 overflow-y-auto rounded-xl border border-border bg-page p-3">
                    {refineConversation.map((msg, idx) => (
                      <div key={idx} className={`flex gap-2.5 ${msg.role === "user" ? "" : ""}`}>
                        <div className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${
                          msg.role === "user" ? "bg-primary/10" : "bg-warning/10"
                        }`}>
                          {msg.role === "user"
                            ? <User className="h-3 w-3 text-primary" />
                            : <Bot className="h-3 w-3 text-warning" />
                          }
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="mb-0.5 flex items-center gap-2">
                            <span className="text-[10px] font-medium text-ink-tertiary">
                              {msg.role === "user" ? "你" : "AI"}
                            </span>
                            {msg.role === "assistant" && (
                              <button
                                onClick={() => handleCopyMsg(msg.content)}
                                className="rounded p-0.5 text-ink-tertiary opacity-0 transition-opacity hover:text-primary group-hover:opacity-100 [.space-y-2:hover_&]:opacity-100"
                              >
                                <Copy className="h-2.5 w-2.5" />
                              </button>
                            )}
                          </div>
                          <div className={`rounded-lg px-3 py-2 text-sm ${
                            msg.role === "user"
                              ? "bg-primary/5 text-ink"
                              : "bg-surface text-ink"
                          }`}>
                            <div className="prose-custom max-w-none text-sm leading-relaxed">
                              <ReactMarkdown>{msg.content}</ReactMarkdown>
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                    {refining && (
                      <div className="flex gap-2.5">
                        <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-warning/10">
                          <Bot className="h-3 w-3 text-warning" />
                        </div>
                        <div className="flex items-center gap-2 px-3 py-2">
                          <div className="flex gap-1">
                            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink-tertiary [animation-delay:0ms]" />
                            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink-tertiary [animation-delay:150ms]" />
                            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink-tertiary [animation-delay:300ms]" />
                          </div>
                          <span className="text-xs text-ink-tertiary">AI 正在微调...</span>
                        </div>
                      </div>
                    )}
                    <div ref={refineEndRef} />
                  </div>
                </div>
              )}

              {/* 微调输入 */}
              {!isImageResult && (
                <div className="mt-4 flex items-end gap-2">
                  <div className="relative flex-1">
                    <textarea
                      value={refineInput}
                      onChange={(e) => setRefineInput(e.target.value)}
                      onKeyDown={handleRefineKeyDown}
                      placeholder="继续微调"
                      rows={1}
                      className="w-full rounded-xl border border-border bg-page px-4 py-2.5 pr-10 text-sm text-ink placeholder:text-ink-tertiary/40 focus:border-primary/30 focus:outline-none focus:ring-2 focus:ring-primary/10 transition-all resize-none"
                      style={{ minHeight: "40px", maxHeight: "120px" }}
                      onInput={(e) => {
                        const target = e.target as HTMLTextAreaElement;
                        target.style.height = "auto";
                        target.style.height = Math.min(target.scrollHeight, 120) + "px";
                      }}
                    />
                    <MessageCircle className="absolute right-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-tertiary/30" />
                  </div>
                  <Button
                    size="sm"
                    icon={<Send className="h-3.5 w-3.5" />}
                    onClick={handleRefine}
                    loading={refining}
                    disabled={!refineInput.trim() || refining}
                  >
                    微调
                  </Button>
                </div>
              )}
            </div>
          )}

          {/* 空状态 */}
          {!loading && !result && selected && (
            <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border py-16">
              <div className="rounded-2xl bg-page p-6">
                <PenTool className="h-10 w-10 text-ink-tertiary/30" />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
