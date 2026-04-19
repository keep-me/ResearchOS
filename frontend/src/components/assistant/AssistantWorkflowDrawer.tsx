import ResearchWorkflowLauncher, { type WorkflowLaunchResult } from "@/components/assistant/ResearchWorkflowLauncher";
import { Drawer } from "@/components/ui/Drawer";

interface AssistantWorkflowDrawerProps {
  open: boolean;
  onClose: () => void;
  initialProjectId?: string | null;
  workspacePath?: string | null;
  workspaceTitle?: string | null;
  workspaceServerId?: string | null;
  initialPaperIds?: string[];
  onLaunch?: (result: WorkflowLaunchResult) => void;
}

export default function AssistantWorkflowDrawer({
  open,
  onClose,
  initialProjectId,
  workspacePath,
  workspaceTitle,
  workspaceServerId,
  initialPaperIds,
  onLaunch,
}: AssistantWorkflowDrawerProps) {
  return (
    <Drawer open={open} onClose={onClose} title="研究流程" width="lg">
      <ResearchWorkflowLauncher
        initialProjectId={initialProjectId}
        workspacePath={workspacePath}
        workspaceTitle={workspaceTitle}
        workspaceServerId={workspaceServerId}
        initialPaperIds={initialPaperIds}
        compact
        surface="drawer"
        onLaunch={(result) => {
          onLaunch?.(result);
          onClose();
        }}
      />
    </Drawer>
  );
}
