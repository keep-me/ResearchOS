import { useEffect, useMemo, useState } from "react";
import {
  canSignApiAssetUrl,
  getAuthToken,
  resolveApiAssetUrl,
  resolveSignedApiAssetUrl,
} from "@/services/api";

export function useSignedApiAssetUrl(path: string | null | undefined): string {
  const rawPath = useMemo(() => String(path || "").trim(), [path]);
  const shouldSign = useMemo(() => canSignApiAssetUrl(rawPath), [rawPath]);
  const [resolvedUrl, setResolvedUrl] = useState(() => (
    shouldSign && getAuthToken() ? "" : resolveApiAssetUrl(rawPath)
  ));

  useEffect(() => {
    let cancelled = false;
    if (!rawPath) {
      setResolvedUrl("");
      return () => {
        cancelled = true;
      };
    }
    if (!shouldSign || !getAuthToken()) {
      setResolvedUrl(resolveApiAssetUrl(rawPath));
      return () => {
        cancelled = true;
      };
    }
    setResolvedUrl("");
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
  }, [rawPath, shouldSign]);

  return resolvedUrl;
}
