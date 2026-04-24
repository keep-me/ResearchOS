import { Check, Palette } from "@/lib/lucide";
import { useVisualStyle } from "@/contexts/VisualStyleContext";
import { cn } from "@/lib/utils";
import { getNextVisualStyle, visualStyles } from "@/lib/visualStyle";

interface VisualStyleSwitcherProps {
  collapsed?: boolean;
}

function buildPreview(colors: readonly string[]) {
  if (colors.length === 0) return undefined;
  if (colors.length === 1) return colors[0];
  const stops = colors.map((color, index) => {
    const position = Math.round((index / (colors.length - 1)) * 100);
    return `${color} ${position}%`;
  });
  return `linear-gradient(135deg, ${stops.join(", ")})`;
}

export default function VisualStyleSwitcher({ collapsed = false }: VisualStyleSwitcherProps) {
  const { visualStyle, setVisualStyle } = useVisualStyle();
  const currentStyle = visualStyles.find((style) => style.id === visualStyle) || visualStyles[0];

  const cycleVisualStyle = () => {
    setVisualStyle(getNextVisualStyle(visualStyle));
  };

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={cycleVisualStyle}
        className="theme-control inline-flex h-10 w-10 items-center justify-center rounded-md border border-border bg-page text-ink-secondary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active"
        title={`切换视觉风格（当前：${currentStyle.label}）`}
        aria-label={`切换视觉风格，当前为 ${currentStyle.label}`}
      >
        <Palette className="h-4 w-4" />
      </button>
    );
  }

  return (
    <section className="style-switcher-card theme-surface rounded-lg border border-border bg-page p-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-medium tracking-[0.02em] text-ink-tertiary">视觉风格</p>
          <h3 className="mt-1 text-sm font-semibold text-ink">{currentStyle.label}</h3>
        </div>
        <button
          type="button"
          onClick={cycleVisualStyle}
          className="theme-control inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border bg-page text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active"
          title="依次切换风格"
          aria-label="依次切换风格"
        >
          <Palette className="h-4 w-4" />
        </button>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-1.5">
        {visualStyles.map((style) => {
          const active = style.id === visualStyle;
          return (
            <button
              key={style.id}
              type="button"
              onClick={() => setVisualStyle(style.id)}
              className={cn(
                "style-chip theme-control flex items-center gap-2 rounded-md border px-2.5 py-2 text-left transition-colors duration-150",
                active
                  ? "bg-active text-ink"
                  : "bg-page text-ink-secondary hover:bg-hover hover:text-ink active:bg-active",
              )}
              aria-pressed={active}
            >
              <span
                className="style-chip-swatch inline-flex h-3.5 w-3.5 shrink-0 rounded-[5px] border shadow-sm"
                style={{
                  background: buildPreview(style.preview),
                  borderColor: active ? "rgba(113,112,255,0.42)" : "rgba(15,23,42,0.08)",
                }}
                aria-hidden="true"
              />
              <span className="min-w-0 flex-1">
                <span className="style-chip-label block truncate text-[12px] font-medium">{style.label}</span>
              </span>
              {active ? <Check className="h-3.5 w-3.5 shrink-0 text-primary" /> : null}
            </button>
          );
        })}
      </div>
    </section>
  );
}
