import { useCallback } from "react";

import type { ProjectWorkflowType } from "@/types";
import {
  WORKFLOW_LAUNCHER_PROJECT_KEY,
  WORKFLOW_LAUNCHER_WORKFLOW_KEY,
} from "@/components/agent/agentPageShared";

export function useAgentWorkflowDrawerPersistence() {
  const persistSelection = useCallback((projectId: string, workflowType: ProjectWorkflowType) => {
    if (typeof window === "undefined") return;
    localStorage.setItem(WORKFLOW_LAUNCHER_PROJECT_KEY, projectId);
    localStorage.setItem(WORKFLOW_LAUNCHER_WORKFLOW_KEY, workflowType);
  }, []);

  const readStoredProjectId = useCallback(() => {
    if (typeof window === "undefined") return "";
    return String(localStorage.getItem(WORKFLOW_LAUNCHER_PROJECT_KEY) || "").trim();
  }, []);

  return { persistSelection, readStoredProjectId };
}
