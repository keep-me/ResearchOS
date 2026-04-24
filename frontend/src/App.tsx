/**
 * ResearchOS - 主应用路由（懒加载）
 * @author Bamzc
 */
import { lazy, Suspense, useEffect, useState, useCallback } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Layout from "@/components/Layout";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { ToastProvider } from "@/contexts/ToastContext";
import ToastContainer from "@/components/Toast";
import { Loader2, FileQuestion, Sparkles } from "@/lib/lucide";

/* 其余页面全部懒加载，按需拆 chunk */
const Agent = lazy(() => import("@/pages/Agent"));
const Collect = lazy(() => import("@/pages/Collect"));
const DashboardHome = lazy(() => import("@/pages/DashboardHome"));
const Papers = lazy(() => import("@/pages/Papers"));
const PaperDetail = lazy(() => import("@/pages/PaperDetail"));
const Projects = lazy(() => import("@/pages/Projects"));
const GraphExplorer = lazy(() => import("@/pages/GraphExplorer"));
const Wiki = lazy(() => import("@/pages/Wiki"));
const DailyBrief = lazy(() => import("@/pages/DailyBrief"));
const Tasks = lazy(() => import("@/pages/Tasks"));
const Writing = lazy(() => import("@/pages/Writing"));
const SettingsPage = lazy(() => import("@/pages/SettingsPage"));

import LoginPage from "@/pages/Login";
import { authApi, clearAuth } from "@/services/api";

function PageFallback() {
  return (
    <div className="flex h-full items-center justify-center">
      <Loader2 className="h-6 w-6 animate-spin text-ink-tertiary" />
    </div>
  );
}

function BackendWaitingScreen({ message }: { message: string }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-page px-4 py-10">
      <div className="flex w-full max-w-xl flex-col items-center rounded-xl border border-border bg-white px-8 py-12 text-center shadow-sm">
        <div className="inline-flex h-14 w-14 items-center justify-center rounded-lg bg-primary-light">
          <Sparkles className="h-8 w-8 text-primary" />
        </div>
        <Loader2 className="mt-6 h-6 w-6 animate-spin text-primary" />
        <h1 className="mt-5 text-2xl font-semibold text-ink">正在连接 ResearchOS 后端</h1>
        <p className="mt-3 text-sm leading-6 text-ink-secondary">{message}</p>
      </div>
    </div>
  );
}

function NotFound() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
      <FileQuestion className="h-16 w-16 text-ink-tertiary" />
      <h1 className="text-2xl font-semibold text-ink">404 - 页面不存在</h1>
      <p className="text-sm text-ink-secondary">你访问的页面不存在或已被移除</p>
      <a href="/" className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white transition-colors duration-150 hover:bg-primary-hover">
        返回主页
      </a>
    </div>
  );
}

/**
 * 首屏加载完成后，利用浏览器空闲时间预取重型 chunk
 * markdown(168KB) + katex(259KB) 在 Agent 首条 AI 回复时需要
 */
function PrefetchChunks() {
  useEffect(() => {
    const prefetch = () => {
      import("@/components/Markdown");
    };
    if ("requestIdleCallback" in window) {
      const id = requestIdleCallback(prefetch);
      return () => cancelIdleCallback(id);
    }
    const timer = setTimeout(prefetch, 3000);
    return () => clearTimeout(timer);
  }, []);
  return null;
}
export default function App() {
  const [isAuthed, setIsAuthed] = useState(false);
  const [authReady, setAuthReady] = useState(false);
  const [authStatusMessage, setAuthStatusMessage] = useState("正在检查后端连接...");

  useEffect(() => {
    document.documentElement.classList.remove("dark");
    document.documentElement.style.colorScheme = "light";
    localStorage.setItem("theme", "light");
  }, []);

  useEffect(() => {
    if (isAuthed) {
      setAuthReady(true);
      setAuthStatusMessage("");
      return;
    }

    let cancelled = false;
    let timer: number | null = null;

    async function bootstrapAuth() {
      try {
        setAuthStatusMessage("正在连接后端服务...");
        const status = await authApi.status();
        if (cancelled) return;
        setAuthStatusMessage("");
        if (!status.auth_enabled) {
          setIsAuthed(true);
          setAuthReady(true);
          return;
        }
        clearAuth();
        setIsAuthed(false);
        setAuthReady(true);
      } catch (error) {
        if (!cancelled) {
          setAuthReady(false);
          setAuthStatusMessage(error instanceof Error ? error.message : "后端暂未就绪，正在重试连接...");
        }
      } finally {
        if (!cancelled) {
          timer = window.setTimeout(() => {
            void bootstrapAuth();
          }, 3000);
        }
      }
    }

    setAuthReady(false);
    void bootstrapAuth();
    return () => {
      cancelled = true;
      if (timer !== null) {
        window.clearTimeout(timer);
      }
    };
  }, [isAuthed]);

  const handleLoginSuccess = useCallback(() => {
    setIsAuthed(true);
    setAuthReady(true);
  }, []);

  const handleLogout = useCallback(() => {
    clearAuth();
    setIsAuthed(false);
    setAuthReady(false);
  }, []);

  if (!authReady) {
    return (
      <ErrorBoundary>
        <BackendWaitingScreen message={authStatusMessage} />
      </ErrorBoundary>
    );
  }

  // 未认证时显示登录页
  if (!isAuthed) {
    return (
      <ErrorBoundary>
        <LoginPage onLoginSuccess={handleLoginSuccess} />
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary>
    <ToastProvider>
    <BrowserRouter>
      <ToastContainer />
      <PrefetchChunks />
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Suspense fallback={<PageFallback />}><DashboardHome /></Suspense>} />
          <Route path="/dashboard" element={<Suspense fallback={<PageFallback />}><DashboardHome /></Suspense>} />
          <Route path="/assistant" element={<Suspense fallback={<PageFallback />}><Agent /></Suspense>} />
          <Route path="/assistant/:conversationId" element={<Suspense fallback={<PageFallback />}><Agent /></Suspense>} />
          <Route path="/workbench" element={<Navigate to="/projects" replace />} />
          <Route path="/assistant-runtime" element={<Navigate to="/assistant" replace />} />
          <Route path="/assistant-legacy" element={<Navigate to="/assistant" replace />} />
          <Route path="/openclaw" element={<Navigate to="/assistant" replace />} />
          <Route path="/collect" element={<Suspense fallback={<PageFallback />}><Collect /></Suspense>} />
          <Route path="/papers" element={<Suspense fallback={<PageFallback />}><Papers /></Suspense>} />
          <Route path="/papers/:id" element={<Suspense fallback={<PageFallback />}><PaperDetail /></Suspense>} />
          <Route path="/projects" element={<Suspense fallback={<PageFallback />}><Projects /></Suspense>} />
          <Route path="/projects/:projectId" element={<Suspense fallback={<PageFallback />}><Projects /></Suspense>} />
          <Route path="/topics" element={<Navigate to="/collect" replace />} />
          <Route path="/chat" element={<Navigate to="/assistant" replace />} />
          <Route path="/graph" element={<Suspense fallback={<PageFallback />}><GraphExplorer /></Suspense>} />
          <Route path="/wiki" element={<Suspense fallback={<PageFallback />}><Wiki /></Suspense>} />
          <Route path="/brief" element={<Suspense fallback={<PageFallback />}><DailyBrief /></Suspense>} />
          <Route path="/my-day" element={<Navigate to="/brief" replace />} />
          <Route path="/pipelines" element={<Navigate to="/tasks" replace />} />
          <Route path="/tasks" element={<Suspense fallback={<PageFallback />}><Tasks /></Suspense>} />
          <Route path="/operations" element={<Navigate to="/settings" replace />} />
          <Route path="/settings" element={<Suspense fallback={<PageFallback />}><SettingsPage /></Suspense>} />
          <Route path="/email-settings" element={<Navigate to="/settings" replace />} />
          <Route path="/writing" element={<Suspense fallback={<PageFallback />}><Writing /></Suspense>} />

          {/* 常见拼写重定向 */}
          <Route path="/briefs" element={<Navigate to="/brief" replace />} />
          <Route path="/agent" element={<Navigate to="/assistant" replace />} />

          {/* 404 */}
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
    </ToastProvider>
    </ErrorBoundary>
  );
}
