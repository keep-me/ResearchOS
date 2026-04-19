/**
 * 模态框组件
 * @author Bamzc
 */
import { cn } from "@/lib/utils";
import type { ReactNode } from "react";
import { X } from "lucide-react";
import { useEffect } from "react";

type MaxWidth = "sm" | "md" | "lg" | "xl";

export interface ModalProps {
  open?: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  className?: string;
  maxWidth?: MaxWidth;
  overlayClassName?: string;
}

const maxWidthStyles: Record<MaxWidth, string> = {
  sm: "max-w-sm",
  md: "max-w-md",
  lg: "max-w-2xl",
  xl: "max-w-4xl",
};

let activeModalCount = 0;

export function Modal({
  open = true,
  onClose,
  title,
  children,
  className,
  maxWidth = "md",
  overlayClassName,
}: ModalProps) {
  useEffect(() => {
    if (!open) {
      return undefined;
    }

    activeModalCount += 1;
    document.body.style.overflow = "hidden";
    return () => {
      activeModalCount = Math.max(0, activeModalCount - 1);
      if (activeModalCount === 0) {
        document.body.style.overflow = "";
      }
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto overscroll-contain">
      <div
        className={cn(
          "absolute inset-0 bg-black/45",
          overlayClassName,
        )}
        onClick={onClose}
      />
      <div className="relative z-10 flex min-h-full items-start justify-center px-2 py-3 sm:px-4 sm:py-6">
        <div
          className={cn(
            "my-auto w-full max-h-[calc(100dvh-1.5rem)] overflow-y-auto overscroll-contain rounded-xl border border-border bg-surface p-4 shadow-[0_24px_64px_-32px_rgba(0,0,0,0.55)] animate-glass-enter sm:max-h-[calc(100dvh-3rem)] sm:p-6",
            maxWidthStyles[maxWidth],
            className
          )}
        >
          <div className="mb-4 flex items-start justify-between gap-3 sm:mb-5 sm:items-center sm:gap-4">
            <h2 className="pr-2 text-base font-semibold text-ink sm:text-lg">{title}</h2>
            <button
              onClick={onClose}
              className="shrink-0 rounded-md p-2 text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
          {children}
        </div>
      </div>
    </div>
  );
}
