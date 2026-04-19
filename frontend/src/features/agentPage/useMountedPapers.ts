import { useMemo } from "react";

export interface MountedPaperSummaryItem {
  id: string;
  title: string;
  primary: boolean;
}

export function useMountedPapers(params: {
  mountedPaperIds: string[];
  mountedPaperTitleMap: Map<string, string> | Record<string, string>;
  mountedPrimaryPaperId?: string | null;
}): MountedPaperSummaryItem[] {
  return useMemo(
    () => {
      const titleFor = (id: string) => (
        params.mountedPaperTitleMap instanceof Map
          ? params.mountedPaperTitleMap.get(id)
          : params.mountedPaperTitleMap[id]
      );
      return params.mountedPaperIds.map((id) => ({
        id,
        title: titleFor(id) || id,
        primary: id === params.mountedPrimaryPaperId,
      }));
    },
    [params.mountedPaperIds, params.mountedPaperTitleMap, params.mountedPrimaryPaperId],
  );
}
