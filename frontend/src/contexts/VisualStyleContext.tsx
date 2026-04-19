import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  DEFAULT_VISUAL_STYLE,
  isVisualStyle,
  VISUAL_STYLE_STORAGE_KEY,
  type VisualStyle,
} from "@/lib/visualStyle";

interface VisualStyleContextValue {
  visualStyle: VisualStyle;
  setVisualStyle: (style: VisualStyle) => void;
}

const VisualStyleContext = createContext<VisualStyleContextValue | null>(null);

function readInitialVisualStyle(): VisualStyle {
  if (typeof window === "undefined") return DEFAULT_VISUAL_STYLE;
  const stored = window.localStorage.getItem(VISUAL_STYLE_STORAGE_KEY);
  return isVisualStyle(stored) ? stored : DEFAULT_VISUAL_STYLE;
}

export function VisualStyleProvider({ children }: { children: ReactNode }) {
  const [visualStyle, setVisualStyle] = useState<VisualStyle>(readInitialVisualStyle);

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.visualStyle = visualStyle;
    document.body.dataset.visualStyle = visualStyle;
    window.localStorage.setItem(VISUAL_STYLE_STORAGE_KEY, visualStyle);
  }, [visualStyle]);

  const value = useMemo(() => ({ visualStyle, setVisualStyle }), [visualStyle]);

  return (
    <VisualStyleContext.Provider value={value}>
      {children}
    </VisualStyleContext.Provider>
  );
}

export function useVisualStyle(): VisualStyleContextValue {
  const context = useContext(VisualStyleContext);
  if (!context) {
    throw new Error("useVisualStyle must be used within VisualStyleProvider");
  }
  return context;
}
