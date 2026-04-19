import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

type DrawerWidth = "md" | "lg" | "xl";

export interface DrawerProps {
  open?: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  className?: string;
  width?: DrawerWidth;
}

const widthStyles: Record<DrawerWidth, string> = {
  md: "max-w-[560px]",
  lg: "max-w-[680px]",
  xl: "max-w-[820px]",
};

export function Drawer({
  open = true,
  onClose,
  title,
  children,
  className,
  width = "lg",
}: DrawerProps) {
  useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50">
      <button
        type="button"
        aria-label="关闭抽屉"
        className="absolute inset-0 bg-black/40"
        onClick={onClose}
      />
      <div className="absolute inset-y-0 right-0 flex max-w-full pl-2 sm:pl-6">
        <div
          className={cn(
            "flex h-full w-screen flex-col border-l border-border bg-sidebar shadow-sm",
            widthStyles[width],
            className,
          )}
        >
          <div className="flex items-start justify-between gap-3 border-b border-border bg-white px-4 py-3 sm:items-center sm:gap-4 sm:px-5 sm:py-4">
            <h2 className="pr-2 text-base font-semibold text-ink">{title}</h2>
            <button
              type="button"
              aria-label="关闭抽屉"
              onClick={onClose}
              className="inline-flex h-9 w-9 items-center justify-center rounded-md text-ink-tertiary transition-colors duration-150 hover:bg-hover hover:text-ink"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-3 sm:px-4 sm:py-4 md:px-5 md:py-5">
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}
