import { Sparkles } from "@/lib/lucide";

import { Button } from "@/components/ui";
import type { KeywordSuggestion } from "@/types";

interface AIPromptHelperProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void | Promise<void>;
  onApply: (suggestion: KeywordSuggestion) => void;
  suggestions: KeywordSuggestion[];
  loading?: boolean;
  title?: string;
  description?: string;
  placeholder?: string;
  className?: string;
}

export default function AIPromptHelper({
  value,
  onChange,
  onSubmit,
  onApply,
  suggestions,
  loading = false,
  title = "AI 提示词助手",
  placeholder = "描述你的研究方向",
  className = "",
}: AIPromptHelperProps) {
  return (
    <div className={`rounded-[24px] border border-border/70 bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.01))] p-4 shadow-[0_18px_38px_-30px_rgba(15,23,35,0.28)] ${className}`.trim()}>
      <div className="mb-4 flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-primary" />
        <div>
          <h3 className="text-sm font-semibold text-ink">{title}</h3>
        </div>
      </div>

      <div className="flex flex-col gap-3 md:flex-row">
        <input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              void onSubmit();
            }
          }}
          placeholder={placeholder}
          className="form-input flex-1"
        />
        <Button variant="secondary" onClick={() => void onSubmit()} loading={loading}>
          生成建议
        </Button>
      </div>

      {suggestions.length > 0 ? (
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          {suggestions.map((suggestion, index) => (
            <button
              key={`${suggestion.query}-${index}`}
              type="button"
              onClick={() => onApply(suggestion)}
              className="rounded-2xl border border-border bg-page p-3 text-left transition hover:border-primary/20 hover:shadow-sm"
            >
              <p className="text-sm font-semibold text-ink">{suggestion.name}</p>
              <p className="mt-1 font-mono text-xs text-primary/80">{suggestion.query}</p>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
