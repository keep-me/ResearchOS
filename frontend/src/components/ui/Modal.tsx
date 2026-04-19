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
      <div className="relative z-10 flex min-h-full items-start justify-center px-4 py-6 sm:px-6 sm:py-8">
        <div
          className={cn(
            "my-auto w-full max-h-[calc(100dvh-3rem)] overflow-y-auto overscroll-contain rounded-xl border border-border bg-surface p-6 shadow-[0_24px_64px_-32px_rgba(0,0,0,0.55)] animate-glass-enter sm:max-h-[calc(100dvh-4rem)]",
            maxWidthStyles[maxWidth],
            className
          )}
        >
          <div className="mb-5 flex items-center justify-between gap-4">
            <h2 className="text-lg font-semibold text-ink">{title}</h2>
            <button
              onClick={onClose}
              className="rounded-md p-2 text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-ink active:bg-active"
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
