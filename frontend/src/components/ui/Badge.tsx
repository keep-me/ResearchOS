/**
 * 徽章组件
 */
import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

type BadgeVariant = "default" | "success" | "warning" | "error" | "info";

interface BadgeProps {
  variant?: BadgeVariant;
  children: ReactNode;
  className?: string;
}

const variantStyles: Record<BadgeVariant, string> = {
  default: "border border-white/70 bg-white/78 text-ink-secondary shadow-[0_14px_28px_-24px_rgba(37,99,235,0.36)]",
  success: "border border-success/10 bg-success-light text-success",
  warning: "border border-warning/10 bg-warning-light text-warning",
  error: "border border-error/10 bg-error-light text-error",
  info: "border border-primary/10 bg-primary-light text-primary",
};

export function Badge({ variant = "default", children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium",
        variantStyles[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
