/**
 * 统一 Markdown 渲染组件（含 LaTeX 支持）
 * @author Color2333
 */
import { Children, isValidElement, memo, useMemo, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import MermaidBlock from "@/components/MermaidBlock";
import { resolveApiAssetUrl } from "@/services/api";
import "katex/dist/katex.min.css";

interface Props {
  children: string;
  className?: string;
  autoMath?: boolean;
}

const LATEX_COMMAND_RE = /\\(?:frac|mathbf|mathrm|mathcal|mathbb|operatorname|text|left|right|sum|prod|alpha|beta|gamma|delta|epsilon|lambda|theta|mu|sigma|tau|phi|psi|omega|quad|qquad|cdot|times|leq|geq|neq|approx|infty|begin|end)\b/;
const LATEX_SYMBOL_RE = /(?:[A-Za-z][A-Za-z0-9]*_(?:\{[^}]+\}|[A-Za-z0-9]+))|(?:[A-Za-z][A-Za-z0-9]*\^(?:\{[^}]+\}|[A-Za-z0-9]+))/;
const EQUATION_OPERATOR_RE = /(?:=|\\approx|\\leq|\\geq|\\neq|\\to|\\mapsto|\\cdot|\\times|\\sum|\\prod|\\max|\\min|\\argmax|\\argmin)/;
const DISPLAY_ENV_RE = /(^|\n)(\\begin\{(?:equation|align|aligned|gather|multline)\*?\}[\s\S]*?\\end\{(?:equation|align|aligned|gather|multline)\*?\})(?=\n|$)/g;
const DISPLAY_ENV_DOUBLE_ESCAPED_RE = /(^|\n)(\\\\begin\{(?:equation|align|aligned|gather|multline)\*?\}[\s\S]*?\\\\end\{(?:equation|align|aligned|gather|multline)\*?\})(?=\n|$)/g;

function normalizeMathExpression(expr: string): string {
  return String(expr || "")
    .trim()
    .replace(/\\\\([A-Za-z])/g, "\\$1")
    .replace(/\\\\([()[\]{}])/g, "\\$1");
}

function normalizeMathDelimiters(markdown: string): string {
  return markdown
    .replace(/\\\\\[\s*([\s\S]*?)\s*\\\\\]/g, (_match, expr: string) => `\n$$\n${normalizeMathExpression(expr)}\n$$\n`)
    .replace(/\\\\\(\s*([\s\S]*?)\s*\\\\\)/g, (_match, expr: string) => `$${normalizeMathExpression(expr)}$`)
    .replace(/\\\[\s*([\s\S]*?)\s*\\\]/g, (_match, expr: string) => `\n$$\n${normalizeMathExpression(expr)}\n$$\n`)
    .replace(/\\\(\s*([\s\S]*?)\s*\\\)/g, (_match, expr: string) => `$${normalizeMathExpression(expr)}$`)
    .replace(DISPLAY_ENV_DOUBLE_ESCAPED_RE, (_match, prefix: string, expr: string) => `${prefix}$$\n${normalizeMathExpression(expr)}\n$$`)
    .replace(DISPLAY_ENV_RE, (_match, prefix: string, expr: string) => `${prefix}$$\n${normalizeMathExpression(expr)}\n$$`);
}

function normalizeMathDelimitersPreservingCode(markdown: string): string {
  return markdown
    .split(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`)/g)
    .map((segment) => {
      if (segment.startsWith("```") || segment.startsWith("~~~") || segment.startsWith("`")) {
        return segment;
      }
      return normalizeMathDelimiters(segment);
    })
    .join("");
}

function looksLikeStandaloneMathLine(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed) return false;
  if (trimmed.includes("$")) return false;
  if (/^(?:#{1,6}\s|[-*+]\s|>\s|\d+[.)]\s)/.test(trimmed)) return false;
  if (trimmed.startsWith("|") || trimmed.endsWith("|")) return false;
  if (/^<[^>]+>/.test(trimmed)) return false;
  if (trimmed.length < 8 || trimmed.length > 240) return false;

  const chineseCount = (trimmed.match(/[\u4e00-\u9fff]/g) || []).length;
  const mathSignals = LATEX_COMMAND_RE.test(trimmed) || LATEX_SYMBOL_RE.test(trimmed) || /[{}_^]/.test(trimmed);
  const operatorSignals = EQUATION_OPERATOR_RE.test(trimmed);
  if (!mathSignals || !operatorSignals) return false;
  if (chineseCount > 10 && chineseCount > trimmed.length * 0.18) return false;
  return true;
}

function autoWrapStandaloneMath(markdown: string): string {
  const segments = markdown.split(/(```[\s\S]*?```|~~~[\s\S]*?~~~)/g);
  return segments
    .map((segment) => {
      if (segment.startsWith("```") || segment.startsWith("~~~")) {
        return segment;
      }
      const normalized = normalizeMathDelimiters(segment);
      let insideDisplayMath = false;
      return normalized
        .split("\n")
        .map((line) => {
          const trimmed = line.trim();
          if (trimmed === "$$") {
            insideDisplayMath = !insideDisplayMath;
            return line;
          }
          if (insideDisplayMath) {
            return line;
          }
          if (!looksLikeStandaloneMathLine(line)) return line;
          return `$$\n${trimmed}\n$$`;
        })
        .join("\n");
    })
    .join("");
}

function getCodeLanguage(className?: string): string {
  const match = (className || "").match(/language-([\w-]+)/i);
  return (match?.[1] || "").toLowerCase();
}

function extractText(node: ReactNode): string {
  return Children.toArray(node)
    .map((child) => {
      if (typeof child === "string" || typeof child === "number") {
        return String(child);
      }
      if (isValidElement<{ children?: ReactNode }>(child)) {
        return extractText(child.props.children);
      }
      return "";
    })
    .join("");
}

/**
 * 带 GFM + LaTeX 的 Markdown 渲染
 */
const Markdown = memo(function Markdown({ children, className, autoMath = false }: Props) {
  const content = useMemo(
    () => {
      const raw = String(children || "");
      return autoMath ? autoWrapStandaloneMath(raw) : normalizeMathDelimitersPreservingCode(raw);
    },
    [autoMath, children],
  );

  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          pre({ children: preChildren, ...props }) {
            const firstChild = Children.toArray(preChildren)[0];
            if (isValidElement<{ className?: string; children?: ReactNode }>(firstChild)) {
              const language = getCodeLanguage(firstChild.props.className);
              if (language === "mermaid") {
                return <MermaidBlock chart={extractText(firstChild.props.children).trim()} />;
              }
            }
            return <pre {...props}>{preChildren}</pre>;
          },
          img({ src, alt, ...props }) {
            const resolvedSrc = resolveApiAssetUrl(String(src || ""));
            return <img {...props} src={resolvedSrc || undefined} alt={alt || ""} loading="lazy" />;
          },
          a({ href, children: linkChildren, ...props }) {
            const resolvedHref = resolveApiAssetUrl(String(href || ""));
            const isExternal = /^https?:\/\//i.test(resolvedHref);
            return (
              <a
                {...props}
                href={resolvedHref || undefined}
                target={isExternal ? "_blank" : undefined}
                rel={isExternal ? "noreferrer" : undefined}
              >
                {linkChildren}
              </a>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});

export default Markdown;
