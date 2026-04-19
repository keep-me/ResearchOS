import { lazy, Suspense } from "react";
import { Modal, Spinner } from "@/components/ui";
import type { ArtifactPreviewState } from "@/lib/workspaceArtifacts";

const Markdown = lazy(() => import("@/components/Markdown"));

interface ArtifactPreviewModalProps {
  preview: ArtifactPreviewState | null;
  onClose: () => void;
}

export default function ArtifactPreviewModal({
  preview,
  onClose,
}: ArtifactPreviewModalProps) {
  return (
    <Modal
      open={!!preview}
      onClose={onClose}
      title={preview?.title || "产物预览"}
      maxWidth="xl"
    >
      {preview ? (
        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-page px-4 py-3">
            <div className="text-xs font-medium text-ink">路径</div>
            <div className="mt-1 break-all text-xs text-ink-tertiary">{preview.path}</div>
            <div className="mt-2 text-[11px] text-ink-tertiary">
              运行环境: {preview.serverId === "local" ? "本地工作区" : preview.serverId}
              {preview.truncated ? " · 内容已截断" : ""}
            </div>
          </div>
          <div className="max-h-[65vh] overflow-auto rounded-xl border border-border bg-white/80 px-4 py-4">
            {preview.markdown ? (
              <Suspense fallback={<Spinner text="加载预览..." />}>
                <Markdown>{preview.content}</Markdown>
              </Suspense>
            ) : (
              <pre className="whitespace-pre-wrap break-words text-sm leading-6 text-ink-secondary">
                {preview.content}
              </pre>
            )}
          </div>
        </div>
      ) : null}
    </Modal>
  );
}
