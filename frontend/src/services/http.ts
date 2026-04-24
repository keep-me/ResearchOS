import { resolveApiBase } from "@/lib/tauri";

export function getApiBase(): string {
  return resolveApiBase();
}

function isAbsoluteOrSpecialUrl(value: string): boolean {
  return /^(?:[a-z][a-z0-9+.-]*:)?\/\//i.test(value)
    || /^[a-z][a-z0-9+.-]*:/i.test(value)
    || value.startsWith("data:")
    || value.startsWith("blob:")
    || value.startsWith("#");
}

export function getAuthToken(): string | null {
  return sessionStorage.getItem("auth_token");
}

export function resolveApiAssetUrl(path: string, _options?: { includeAuthToken?: boolean }): string {
  const raw = String(path || "").trim();
  if (!raw || isAbsoluteOrSpecialUrl(raw) || !raw.startsWith("/")) {
    return raw;
  }
  const normalizedPath = raw.startsWith("/api/") ? raw.slice(4) : raw;
  if (
    !normalizedPath.startsWith("/papers/")
    && !normalizedPath.startsWith("/agent/")
    && !normalizedPath.startsWith("/session/")
  ) {
    return raw;
  }
  const base = getApiBase().replace(/\/+$/, "");
  return `${base}${normalizedPath}`;
}

type PathTokenCacheEntry = {
  token: string;
  expiresAt: number;
};

const pathTokenCache = new Map<string, PathTokenCacheEntry>();

function normalizeApiPath(path: string): string {
  const raw = String(path || "").trim();
  if (!raw) return raw;
  if (isAbsoluteOrSpecialUrl(raw) && /^https?:\/\//i.test(raw)) {
    try {
      const baseHref = typeof window === "undefined" ? "http://localhost" : window.location.href;
      const candidate = new URL(raw, baseHref);
      const apiBase = new URL(getApiBase(), baseHref);
      const apiBasePath = apiBase.pathname.replace(/\/+$/, "");
      if (candidate.origin === apiBase.origin && candidate.pathname.startsWith(`${apiBasePath}/`)) {
        return candidate.pathname.slice(apiBasePath.length) + candidate.search;
      }
    } catch {
      return raw;
    }
  }
  if (!raw.startsWith("/") || isAbsoluteOrSpecialUrl(raw)) return raw;
  return raw.startsWith("/api/") ? raw.slice(4) : raw;
}

export async function getPathAccessToken(path: string): Promise<string | null> {
  const normalizedPath = normalizeApiPath(path);
  const authToken = getAuthToken();
  if (!authToken || !normalizedPath.startsWith("/")) return null;

  const cached = pathTokenCache.get(normalizedPath);
  const now = Date.now();
  if (cached && cached.expiresAt > now + 10_000) {
    return cached.token;
  }

  const resp = await fetch(`${getApiBase().replace(/\/+$/, "")}/auth/path-token`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${authToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ path: normalizedPath }),
  });
  if (!resp.ok) {
    if (resp.status === 401) clearAuth();
    return null;
  }
  const payload = await resp.json() as { access_token?: string; expires_in?: number };
  const token = String(payload.access_token || "").trim();
  if (!token) return null;
  const expiresIn = Number(payload.expires_in || 90);
  pathTokenCache.set(normalizedPath, {
    token,
    expiresAt: now + Math.max(15, expiresIn) * 1000,
  });
  return token;
}

export async function resolveSignedApiAssetUrl(path: string): Promise<string> {
  const raw = String(path || "").trim();
  const normalizedPath = normalizeApiPath(raw);
  if (!normalizedPath || !normalizedPath.startsWith("/")) return raw;
  const baseUrl = resolveApiAssetUrl(normalizedPath, { includeAuthToken: false });
  const token = await getPathAccessToken(normalizedPath);
  if (!token || baseUrl.includes("token=")) return baseUrl;
  return `${baseUrl}${baseUrl.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
}

export function buildWebSocketUrl(path: string, params?: Record<string, string>): string {
  const baseInput = getApiBase();
  const baseUrl = baseInput.startsWith("http")
    ? new URL(baseInput)
    : new URL(baseInput, window.location.href);
  baseUrl.protocol = baseUrl.protocol === "https:" ? "wss:" : "ws:";
  const normalizedBasePath = baseUrl.pathname.replace(/\/+$/, "");
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  baseUrl.pathname = `${normalizedBasePath}${normalizedPath}`;
  baseUrl.search = "";
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value) {
      baseUrl.searchParams.set(key, value);
    }
  });
  return baseUrl.toString();
}

export class ApiHttpError extends Error {
  readonly status: number;
  readonly statusText: string;

  constructor(message: string, status: number, statusText: string) {
    super(message);
    this.name = "ApiHttpError";
    this.status = status;
    this.statusText = statusText;
  }
}

export function isAuthenticated(): boolean {
  return !!getAuthToken();
}

export function clearAuth(): void {
  sessionStorage.removeItem("auth_token");
  localStorage.removeItem("auth_token");
  pathTokenCache.clear();
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${getApiBase().replace(/\/+$/, "")}${path}`;
  let resp: Response;
  try {
    const headers: Record<string, string> = {
      ...(getAuthToken() ? { "Authorization": `Bearer ${getAuthToken()}` } : {}),
      ...((options.headers as Record<string, string>) || {}),
    };
    if (!(options.body instanceof FormData) && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    resp = await fetch(url, {
      headers,
      ...options,
    });
  } catch {
    throw new Error("网络连接失败，请检查后端服务是否启动");
  }
  if (!resp.ok) {
    let msg = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      msg = body.message || body.detail || body.error || msg;
    } catch {
      const text = await resp.text().catch(() => "");
      if (text) msg = text;
    }
    if (resp.status === 401) {
      clearAuth();
    }
    throw new ApiHttpError(msg, resp.status, resp.statusText);
  }
  if (resp.status === 204) {
    return undefined as T;
  }
  return resp.json();
}

export function get<T>(path: string, opts?: { signal?: AbortSignal }) {
  return request<T>(path, { signal: opts?.signal });
}

export function post<T>(path: string, body?: unknown, opts?: { signal?: AbortSignal }) {
  return request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}), signal: opts?.signal });
}

export function postForm<T>(path: string, body: FormData, opts?: { signal?: AbortSignal }) {
  return request<T>(path, { method: "POST", body, signal: opts?.signal });
}

export function patch<T>(path: string, body?: unknown, opts?: { signal?: AbortSignal }) {
  return request<T>(path, { method: "PATCH", body: JSON.stringify(body ?? {}), signal: opts?.signal });
}

export function put<T>(path: string, body?: unknown, opts?: { signal?: AbortSignal }) {
  return request<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}), signal: opts?.signal });
}

export function del<T>(path: string, opts?: { signal?: AbortSignal }) {
  return request<T>(path, { method: "DELETE", signal: opts?.signal });
}

export async function fetchSSE(url: string, init?: RequestInit): Promise<Response> {
  const authHeaders: Record<string, string> = {};
  const token = getAuthToken();
  if (token) {
    authHeaders["Authorization"] = `Bearer ${token}`;
  }
  const resp = await fetch(url, {
    ...init,
    headers: {
      ...authHeaders,
      ...((init?.headers as Record<string, string>) || {}),
    },
  });
  if (!resp.ok) {
    if (resp.status === 401) {
      clearAuth();
    }
    const text = await resp.text().catch(() => "");
    throw new Error(`请求失败 (${resp.status}): ${text || resp.statusText}`);
  }
  return resp;
}
