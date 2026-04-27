/**
 * Tab 切换组件（支持 label 为 ReactNode，可带状态指示器）
 */
import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

interface Tab {
  id: string;
  label: ReactNode;
}

interface TabsProps {
  tabs: Tab[];
  active: string;
  onChange: (id: string) => void;
  className?: string;
}

export function Tabs({ tabs, active, onChange, className }: TabsProps) {
  return (
    <div className={cn("overflow-x-auto pb-1 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden", className)}>
      <div className="flex min-w-max gap-1 rounded-lg border border-border bg-page p-1">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => onChange(tab.id)}
            className={cn(
              "inline-flex min-h-10 items-center justify-center gap-1.5 whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium transition-colors duration-150 sm:px-4",
              active === tab.id
                ? "border border-border bg-white text-ink"
                : "border border-transparent text-ink-secondary hover:bg-hover hover:text-ink active:bg-active"
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>
    </div>
  );
}
