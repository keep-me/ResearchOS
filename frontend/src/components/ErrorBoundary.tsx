/**
 * 全局错误边界 - 防止子组件崩溃导致白屏
 */
import { Component, type ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "@/lib/lucide";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

const RECOVERABLE_IMPORT_ERROR_KEY = "researchos.recoverableImportErrorReloaded";

function isRecoverableImportError(error: Error | null | undefined): boolean {
  const message = String(error?.message || "").toLowerCase();
  return (
    message.includes("failed to fetch dynamically imported module")
    || message.includes("importing a module script failed")
    || message.includes("outdated optimize dep")
    || message.includes("chunkloaderror")
  );
}

function requiresPageReload(error: Error | null | undefined): boolean {
  const message = String(error?.message || "");
  return isRecoverableImportError(error) || /must be used inside .*provider/i.test(message);
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);

    if (typeof window === "undefined" || !isRecoverableImportError(error)) {
      return;
    }

    const hasReloaded = window.sessionStorage.getItem(RECOVERABLE_IMPORT_ERROR_KEY) === "1";
    if (!hasReloaded) {
      window.sessionStorage.setItem(RECOVERABLE_IMPORT_ERROR_KEY, "1");
      window.setTimeout(() => {
        window.location.reload();
      }, 60);
    }
  }

  handleReset = () => {
    if (typeof window !== "undefined" && requiresPageReload(this.state.error)) {
      window.location.reload();
      return;
    }
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="flex min-h-[300px] flex-col items-center justify-center gap-4 p-8">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-red-50 dark:bg-red-900/20">
            <AlertTriangle className="h-7 w-7 text-red-500" />
          </div>
          <div className="text-center">
            <p className="text-sm font-medium text-ink">页面遇到了错误</p>
            <p className="mt-1 max-w-md text-xs text-ink-tertiary">
              {this.state.error?.message || "未知错误"}
            </p>
            {requiresPageReload(this.state.error) ? (
              <p className="mt-2 max-w-md text-xs text-ink-tertiary">
                该错误需要重新加载应用上下文，将通过刷新页面恢复。
              </p>
            ) : null}
          </div>
          <button
            onClick={this.handleReset}
            className="flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary/90 transition-colors"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            {requiresPageReload(this.state.error) ? "刷新页面" : "重试"}
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
