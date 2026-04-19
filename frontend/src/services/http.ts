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
  return sessionStorage.getItem("auth_token") || localStorage.getItem("auth_token");
}

export function resolveApiAssetUrl(path: string, options?: { includeAuthToken?: boolean }): string {
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
  const url = `${base}${normalizedPath}`;
  const includeAuthToken = options?.includeAuthToken !== false;
  const token = includeAuthToken ? getAuthToken() : null;
  if (!token || url.includes("token=")) {
    return url;
  }
  return `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
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
