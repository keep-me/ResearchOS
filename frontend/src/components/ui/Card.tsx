/**
 * 卡片组件
 * @author Color2333
 */
import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

interface CardProps {
  children: ReactNode;
  className?: string;
  padding?: boolean;
}

export function Card({ children, className, padding = true }: CardProps) {
  return (
    <div
      className={cn(
        "theme-card theme-surface group rounded-xl border border-border bg-white shadow-sm transition-colors duration-150",
        padding && "p-6",
        className
      )}
    >
      {children}
    </div>
  );
}

interface CardHeaderProps {
  title: string;
  description?: string;
  action?: ReactNode;
}

export function CardHeader({ title, description, action }: CardHeaderProps) {
  return (
    <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 flex-1">
        <h3 className="text-base font-semibold text-ink">{title}</h3>
        {description ? <p className="mt-1 text-sm leading-6 text-ink-secondary">{description}</p> : null}
      </div>
      {action && <div className="min-w-0 sm:shrink-0">{action}</div>}
    </div>
  );
}
