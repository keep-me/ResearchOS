import { useEffect, useMemo, useState } from "react";
import {
  canSignApiAssetUrl,
  getAuthToken,
  resolveApiAssetUrl,
  resolveSignedApiAssetUrl,
} from "@/services/api";

export type SignedApiAssetUrlState = {
  url: string;
  loading: boolean;
  error: string | null;
};

export function useSignedApiAssetUrlState(path: string | null | undefined): SignedApiAssetUrlState {
  const rawPath = useMemo(() => String(path || "").trim(), [path]);
  const shouldSign = useMemo(() => canSignApiAssetUrl(rawPath), [rawPath]);
  const [state, setState] = useState<SignedApiAssetUrlState>(() => {
    if (!rawPath) return { url: "", loading: false, error: null };
    if (shouldSign && getAuthToken()) return { url: "", loading: true, error: null };
    return { url: resolveApiAssetUrl(rawPath), loading: false, error: null };
  });

  useEffect(() => {
    let cancelled = false;
    if (!rawPath) {
      setState({ url: "", loading: false, error: null });
      return () => {
        cancelled = true;
      };
    }
    if (!shouldSign || !getAuthToken()) {
      setState({ url: resolveApiAssetUrl(rawPath), loading: false, error: null });
      return () => {
        cancelled = true;
      };
    }
    setState({ url: "", loading: true, error: null });
    resolveSignedApiAssetUrl(rawPath)
      .then((url) => {
        if (!cancelled) setState({ url, loading: false, error: null });
      })
      .catch((error) => {
        if (!cancelled) {
          const message = error instanceof Error ? error.message : "资源签名失败";
          setState({ url: "", loading: false, error: message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [rawPath, shouldSign]);

  return state;
}

export function useSignedApiAssetUrl(path: string | null | undefined): string {
  return useSignedApiAssetUrlState(path).url;
}
