import { createContext, useContext, type ReactNode } from "react";
import { isTauri } from "@/lib/tauri";

export type DesktopRuntimeHost = "desktop" | "web";
export type DesktopRuntimePhase = "checking" | "setup" | "waiting" | "ready";

export interface DesktopRuntimeValue {
  host: DesktopRuntimeHost;
  isDesktop: boolean;
  phase: DesktopRuntimePhase;
  ready: boolean;
  backendPort: number | null;
  backendError: string | null;
}

const DEFAULT_VALUE: DesktopRuntimeValue = {
  host: isTauri() ? "desktop" : "web",
  isDesktop: isTauri(),
  phase: isTauri() ? "checking" : "ready",
  ready: !isTauri(),
  backendPort: null,
  backendError: null,
};

const Context = createContext<DesktopRuntimeValue>(DEFAULT_VALUE);

export function DesktopRuntimeProvider({
  value,
  children,
}: {
  value: DesktopRuntimeValue;
  children: ReactNode;
}) {
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useDesktopRuntime(): DesktopRuntimeValue {
  return useContext(Context);
}
