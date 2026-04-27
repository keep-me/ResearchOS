/**
 * Web-only runtime helpers.
 * Desktop shell integration is deprecated; keep these helpers as a stable
 * compatibility surface so the web frontend can build without Tauri packages.
 */

function desktopDeprecatedError(): Error {
  return new Error("ResearchOS 桌面端壳层已下线，请改用 Web 前端 + FastAPI 后端。");
}

/** 是否运行在 Tauri 桌面环境中 */
export function isTauri(): boolean {
  return !!(window as any).__TAURI_INTERNALS__;
}

/** 调用桌面命令：仅保留兼容接口，实际已不再支持。 */
async function invoke<T>(_cmd: string, _args?: Record<string, unknown>): Promise<T> {
  throw desktopDeprecatedError();
}

/** 监听桌面事件：web 模式下直接返回空取消函数。 */
export async function listen<T>(
  _event: string,
  _handler: (payload: T) => void,
): Promise<() => void> {
  if (!isTauri()) return () => {};
  throw desktopDeprecatedError();
}

/** 获取后端 API 端口 */
export async function getApiPort(): Promise<number | null> {
  if (!isTauri()) return null;
  return invoke<number | null>("get_api_port");
}

/** 获取最近一次后端启动错误 */
export async function getBackendError(): Promise<string | null> {
  if (!isTauri()) return null;
  return invoke<string | null>("get_backend_error");
}

/** 是否需要首次引导 */
export async function needsSetup(): Promise<boolean> {
  if (!isTauri()) return false;
  throw desktopDeprecatedError();
}

export interface LauncherConfig {
  data_dir: string;
  env_file: string;
}

/** 获取当前启动配置 */
export async function getLauncherConfig(): Promise<LauncherConfig | null> {
  if (!isTauri()) return null;
  throw desktopDeprecatedError();
}

/** 保存配置并启动后端 */
export async function saveConfigAndStart(
  _dataDir: string,
  _envFile: string,
): Promise<number> {
  throw desktopDeprecatedError();
}

/** 更新配置 */
export async function updateConfig(
  _dataDir: string,
  _envFile: string,
): Promise<void> {
  throw desktopDeprecatedError();
}

/** 打开文件夹选择对话框 */
export async function openFolderDialog(_title: string): Promise<string | null> {
  if (!isTauri()) return null;
  throw desktopDeprecatedError();
}

/** 打开文件选择对话框 */
export async function openFileDialog(
  _title: string,
  _filters?: { name: string; extensions: string[] }[],
): Promise<string | null> {
  if (!isTauri()) return null;
  throw desktopDeprecatedError();
}

/**
 * 全局 API 端口管理
 * Web 模式：优先 VITE_API_BASE；开发环境默认走 Vite 代理。
 */
let _resolvedPort: number | null = null;

export function resolveApiBase(): string {
  if (!isTauri()) {
    if (import.meta.env.VITE_API_BASE) return import.meta.env.VITE_API_BASE;
    if (import.meta.env.DEV) {
      if (import.meta.env.VITE_PROXY_TARGET) {
        return "/api";
      }
      return "http://localhost:8002";
    }
    return "/api";
  }

  if (_resolvedPort) {
    return `http://127.0.0.1:${_resolvedPort}`;
  }
  return import.meta.env.VITE_API_BASE || "http://localhost:8002";
}

export function setApiPort(port: number): void {
  _resolvedPort = port;
}

export function waitForBackend(): Promise<number> {
  if (!isTauri()) {
    return Promise.reject(desktopDeprecatedError());
  }
  if (_resolvedPort) {
    return Promise.resolve(_resolvedPort);
  }
  return Promise.reject(desktopDeprecatedError());
}
