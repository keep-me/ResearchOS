/**
 * 空状态组件
 * @author Color2333
 */
import { cn } from "@/lib/utils";
import type { ReactNode } from "react";
import { Inbox } from "@/lib/lucide";

interface EmptyProps {
  icon?: ReactNode;
  title?: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export function Empty({
  icon,
  title = "暂无数据",
  action,
  className,
}: EmptyProps) {
  return (
    <div className={cn("flex flex-col items-center justify-center py-16 text-center", className)}>
      <div className="mb-4 text-ink-tertiary">
        {icon || <Inbox className="h-12 w-12" />}
      </div>
      <h3 className="text-sm font-medium text-ink-secondary">{title}</h3>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
