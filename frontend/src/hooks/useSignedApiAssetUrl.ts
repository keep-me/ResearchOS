import { useEffect, useMemo, useState } from "react";
import { resolveApiAssetUrl, resolveSignedApiAssetUrl } from "@/services/api";

export function useSignedApiAssetUrl(path: string | null | undefined): string {
  const rawPath = useMemo(() => String(path || "").trim(), [path]);
  const [resolvedUrl, setResolvedUrl] = useState(() => resolveApiAssetUrl(rawPath));

  useEffect(() => {
    let cancelled = false;
    setResolvedUrl(resolveApiAssetUrl(rawPath));
    if (!rawPath || !rawPath.startsWith("/")) {
      return () => {
        cancelled = true;
      };
    }
    resolveSignedApiAssetUrl(rawPath)
      .then((url) => {
        if (!cancelled) setResolvedUrl(url);
      })
      .catch(() => {
        if (!cancelled) setResolvedUrl(resolveApiAssetUrl(rawPath));
      });
    return () => {
      cancelled = true;
    };
  }, [rawPath]);

  return resolvedUrl;
}
