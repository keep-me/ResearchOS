import { useCallback, useState } from "react";

import {
  AGENT_WORKSPACE_PANEL_WIDTH_KEY,
  AGENT_WORKSPACE_PANEL_MAX_WIDTH,
  AGENT_WORKSPACE_PANEL_MIN_WIDTH,
  readAgentWorkspacePanelWidth,
} from "@/components/agent/agentPageShared";

export function useAgentWorkspacePanel() {
  const [width, setWidthState] = useState(readAgentWorkspacePanelWidth);

  const setWidth = useCallback((value: number) => {
    const bounded = Math.min(AGENT_WORKSPACE_PANEL_MAX_WIDTH, Math.max(AGENT_WORKSPACE_PANEL_MIN_WIDTH, value));
    setWidthState(bounded);
    window.localStorage.setItem(AGENT_WORKSPACE_PANEL_WIDTH_KEY, String(bounded));
  }, []);

  return { width, setWidth };
}

