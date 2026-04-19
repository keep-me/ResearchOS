import { useMemo } from "react";

import type { AgentPermissionPreset } from "@/contexts/AgentWorkbenchContext";
import { getPermissionPresetLabel } from "@/components/agent/agentPageShared";

export function useAgentRuntimeControls(permissionPreset: AgentPermissionPreset) {
  return useMemo(
    () => ({
      permissionLabel: getPermissionPresetLabel(permissionPreset),
    }),
    [permissionPreset],
  );
}

