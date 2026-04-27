/**
 * 主布局组件
 */
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useEffect } from "react";
import Sidebar from "./Sidebar";
import { ConversationProvider } from "@/contexts/ConversationContext";
import { AssistantInstanceProvider } from "@/contexts/AssistantInstanceContext";
import { AgentWorkbenchProvider } from "@/contexts/AgentWorkbenchContext";
import { GlobalTaskProvider } from "@/contexts/GlobalTaskContext";
import { useDesktopRuntime } from "@/contexts/DesktopRuntimeContext";
import { resolveRouteMeta } from "@/components/shell/navigation";
import { cn } from "@/lib/utils";

function LayoutShell() {
  const { pathname } = useLocation();
  const { host, phase, isDesktop } = useDesktopRuntime();
  const meta = resolveRouteMeta(pathname);
  const isFullscreen = pathname.startsWith("/assistant");
  const isAssistantPage = pathname.startsWith("/assistant");
  const isWorkbenchWide = pathname.startsWith("/projects") || pathname.startsWith("/tasks") || pathname.startsWith("/settings");

  useEffect(() => {
    document.title = `${meta.title} · ResearchOS`;
  }, [meta.title]);

  return (
    <div
      className={cn("min-h-dvh bg-page text-ink", isDesktop && "desktop-shell")}
      data-runtime-host={host}
      data-runtime-phase={phase}
    >
      <div className="shell-backdrop" />
      <Sidebar />
      {isFullscreen ? (
        <main
          className={cn(
            "flex min-h-dvh flex-col overflow-x-hidden bg-page lg:ml-[var(--shell-sidebar-width)] lg:h-dvh",
            isAssistantPage ? "overflow-hidden" : "overflow-y-auto",
          )}
        >
          <Outlet />
        </main>
      ) : (
        <main className="min-h-dvh overflow-y-auto overflow-x-hidden bg-page pt-14 lg:ml-[var(--shell-sidebar-width)] lg:pt-0">
          <div className={cn(
            "mx-auto min-w-0 px-3 py-3 sm:px-4 sm:py-4 md:px-6 lg:px-8",
            isWorkbenchWide ? "max-w-[1720px]" : "max-w-6xl",
          )}>
            <div className={isWorkbenchWide
              ? "mx-auto min-w-0 max-w-[1680px]"
              : "mx-auto min-w-0 max-w-6xl"}
            >
            <Outlet />
            </div>
          </div>
        </main>
      )}
    </div>
  );
}

export default function Layout() {
  return (
    <AgentWorkbenchProvider>
      <AssistantInstanceProvider>
        <ConversationProvider>
          <GlobalTaskProvider>
            <LayoutShell />
          </GlobalTaskProvider>
        </ConversationProvider>
      </AssistantInstanceProvider>
    </AgentWorkbenchProvider>
  );
}
