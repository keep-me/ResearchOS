import { memo, useEffect, useId, useMemo, useState } from "react";
import { cn } from "@/lib/utils";

interface MermaidBlockProps {
  chart: string;
  className?: string;
}

type MermaidApi = Awaited<typeof import("mermaid")>["default"];

let initializedTheme: "default" | "dark" | null = null;
let mermaidPromise: Promise<MermaidApi> | null = null;

function getMermaidModule(mod: unknown): MermaidApi {
  if (mod && typeof mod === "object" && "default" in mod) {
    return (mod as { default: MermaidApi }).default;
  }
  return mod as MermaidApi;
}

function isDynamicImportFetchError(error: unknown): boolean {
  const message = getErrorMessage(error).toLowerCase();
  return (
    message.includes("failed to fetch dynamically imported module")
    || message.includes("/.vite/deps/")
  );
}

async function loadMermaid(): Promise<MermaidApi> {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid/dist/mermaid.esm.mjs")
      .then(getMermaidModule)
      .catch(async (distError) => {
        try {
          return getMermaidModule(await import("mermaid"));
        } catch (moduleError) {
          mermaidPromise = null;
          throw isDynamicImportFetchError(moduleError) ? moduleError : distError;
        }
      });
  }
  try {
    return await mermaidPromise;
  } catch (error) {
    mermaidPromise = null;
    throw error;
  }
}

async function ensureMermaid(theme: "default" | "dark") {
  const mermaid = await loadMermaid();
  if (initializedTheme !== theme) {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme,
    });
    initializedTheme = theme;
  }
  return mermaid;
}

function getErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message.trim();
  }
  return "Mermaid 图表渲染失败";
}

function isMermaidSyntaxError(error: unknown): boolean {
  const message = getErrorMessage(error).toLowerCase();
  return (
    message.includes("syntax error")
    || message.includes("parse error")
    || message.includes("lexical error")
    || message.includes("unexpected token")
  );
}

function isMermaidErrorSvg(svg: string): boolean {
  const normalized = String(svg || "").toLowerCase();
  if (!normalized) return false;
  let textContent = "";
  if (typeof DOMParser !== "undefined") {
    try {
      const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
      textContent = String(doc.documentElement?.textContent || "").toLowerCase();
    } catch {
      textContent = "";
    }
  }
  return (
    normalized.includes("syntax error in text")
    || normalized.includes("parse error")
    || normalized.includes("lexical error")
    || normalized.includes("mermaid version")
    || normalized.includes("aria-roledescription=\"error\"")
    || normalized.includes("aria-roledescription='error'")
    || textContent.includes("syntax error in text")
    || textContent.includes("parse error")
    || textContent.includes("lexical error")
    || textContent.includes("mermaid version")
  );
}

const MermaidBlock = memo(function MermaidBlock({ chart, className }: MermaidBlockProps) {
  const reactId = useId();
  const chartId = useMemo(() => `mermaid-${reactId.replace(/[:]/g, "")}`, [reactId]);
  const normalizedChart = useMemo(() => String(chart || "").trim(), [chart]);
  const [svg, setSvg] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;

    if (!normalizedChart) {
      setSvg("");
      setError(null);
      return () => {
        disposed = true;
      };
    }

    const render = async () => {
      try {
        const theme = document.documentElement.classList.contains("dark") ? "dark" : "default";
        const mermaid = await ensureMermaid(theme);
        const { svg: renderedSvg } = await mermaid.render(chartId, normalizedChart);
        if (disposed) return;
        if (isMermaidErrorSvg(renderedSvg)) {
          setSvg("");
          setError("Mermaid 语法无效，已跳过渲染");
          return;
        }
        setSvg(renderedSvg);
        setError(null);
      } catch (renderError) {
        if (disposed) return;
        setSvg("");
        setError(
          isMermaidSyntaxError(renderError)
            ? "Mermaid 语法无效，已跳过渲染"
            : "Mermaid 图表渲染失败，已跳过渲染",
        );
      }
    };

    void render();

    return () => {
      disposed = true;
    };
  }, [chartId, normalizedChart]);

  if (!normalizedChart) return null;

  if (error) return null;

  if (!svg) {
    return (
      <div className={cn("mermaid-block mermaid-block-loading", className)}>
        正在渲染图表...
      </div>
    );
  }

  return (
    <div
      className={cn("mermaid-block", className)}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
});

export default MermaidBlock;
