import fs from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

const AUTH_TOKEN = "smoke-test-token";
const API_BASE = (process.env.PLAYWRIGHT_API_BASE || "http://127.0.0.1:8000").replace(/\/+$/, "");
const BACKEND_FS_MODE = (
  process.env.PLAYWRIGHT_BACKEND_FS
  || (/:8002(?:\/|$)/.test(API_BASE) ? "container" : "host")
).toLowerCase() === "container" ? "container" : "host";
const ACP_PYTHON = process.env.PLAYWRIGHT_PYTHON || "python";
const BACKEND_REPO_ROOT = resolveBackendPath(path.resolve(process.cwd(), ".."), "/app");
const MOCK_ACP_SERVER = resolveBackendPath(path.resolve(process.cwd(), "../scripts/mock_acp_server.py"), "/app/scripts/mock_acp_server.py");
const MOCK_ACP_PERMISSION_SERVER = resolveBackendPath(
  path.resolve(process.cwd(), "../scripts/mock_acp_permission_server.py"),
  "/app/scripts/mock_acp_permission_server.py",
);
const LIST_UPLOADS_COMMAND = `${ACP_PYTHON} -c "import os; print('\\\\n'.join(sorted(os.listdir('uploads'))))"`;

type RouteExpectation = {
  path: string;
  title: string | RegExp;
  probe?: string | RegExp;
  expectedUrl?: RegExp;
};

const ASSISTANT_ROUTE_PROBE = /发起研究对话|新对话|对话历史|默认对话|暂无历史|历史/;
const ASSISTANT_SETTINGS_PROBE = /设置作用域|当前模式|当前后端|Skills/;

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function apiRoutePattern(pathname: string) {
  const normalized = pathname.startsWith("/") ? pathname : `/${pathname}`;
  return new RegExp(`(?:^https?:\\/\\/[^/]+)?(?:\\/api)?${escapeRegExp(normalized)}(?:\\?.*)?$`);
}

const ROUTES: RouteExpectation[] = [
  { path: "/assistant", title: "研究助手", probe: ASSISTANT_ROUTE_PROBE },
  {
    path: "/workbench",
    title: "项目工作区",
    probe: /项目工作区|项目列表|还没有项目/,
    expectedUrl: /\/projects(?:\?.*)?$/,
  },
  { path: "/collect", title: "论文收集", probe: /自动订阅|PDF|文件夹|检索/ },
  { path: "/papers", title: "全部论文", probe: /全部论文|论文库/ },
  { path: "/projects", title: "项目工作区", probe: /项目工作区|项目列表|还没有项目/ },
  {
    path: "/topics",
    title: "论文收集",
    probe: /自动订阅|PDF|文件夹|检索/,
    expectedUrl: /\/collect(?:\?.*)?$/,
  },
  { path: "/graph", title: "研究洞察", probe: "全局概览" },
  { path: "/wiki", title: "专题综述" },
  { path: "/brief", title: "研究日报" },
  { path: "/tasks", title: /任务后台|Background Jobs/, probe: /运行中|已完成|失败|暂无任务记录/ },
  {
    path: "/pipelines",
    title: /任务后台|Background Jobs/,
    probe: /运行中|已完成|失败|暂无任务记录/,
    expectedUrl: /\/tasks(?:\?.*)?$/,
  },
  {
    path: "/operations",
    title: "系统配置",
    probe: /模型与嵌入|MCP 服务|工作区与 SSH 服务器/,
    expectedUrl: /\/settings(?:\?.*)?$/,
  },
  { path: "/settings", title: "系统配置", probe: /模型与嵌入|MCP 服务|工作区与 SSH 服务器/ },
  { path: "/writing", title: "写作助手" },
  {
    path: "/email-settings",
    title: "系统配置",
    probe: /模型与嵌入|MCP 服务|工作区与 SSH 服务器/,
    expectedUrl: /\/settings(?:\?.*)?$/,
  },
];

function authHeaders() {
  return {
    Authorization: `Bearer ${AUTH_TOKEN}`,
  };
}

function apiUrl(pathname: string) {
  const normalized = pathname.startsWith("/") ? pathname : `/${pathname}`;
  return `${API_BASE}${normalized.replace(/^\/api/, "")}`;
}

function resolveBackendPath(hostPath: string, containerPath: string) {
  return BACKEND_FS_MODE === "container" ? containerPath : hostPath;
}

function makeWorkspacePath(workspaceId: string) {
  return BACKEND_FS_MODE === "container"
    ? path.posix.join("/app/data/playwright-smoke", workspaceId)
    : path.resolve(process.cwd(), "../tmp", workspaceId);
}

function removeHostWorkspace(workspacePath: string) {
  if (BACKEND_FS_MODE !== "host") return;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      fs.rmSync(workspacePath, { recursive: true, force: true });
      return;
    } catch (error) {
      if (attempt === 4) {
        return;
      }
      const code = typeof error === "object" && error && "code" in error ? String((error as { code?: unknown }).code || "") : "";
      if (code && code !== "EBUSY" && code !== "EPERM") {
        return;
      }
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 150);
    }
  }
}

async function createLocalAssistantWorkspace(
  request: APIRequestContext,
  workspaceLabel: string,
) {
  const workspaceId = `assistant-smoke-${Date.now()}-${randomUUID().slice(0, 8)}`;
  const workspacePath = makeWorkspacePath(workspaceId);
  removeHostWorkspace(workspacePath);
  const response = await request.put(apiUrl("/api/agent/workspace/file"), {
    headers: authHeaders(),
    data: {
      path: workspacePath,
      server_id: "local",
      relative_path: "notes/smoke.md",
      content: `# ${workspaceLabel}\nfrontend smoke\n`,
      create_dirs: true,
      overwrite: true,
    },
  });
  expect(response.ok()).toBeTruthy();
  return {
    workspacePath,
    effectiveWorkspacePath: workspacePath,
    workspaceTitle: workspaceLabel,
    workspaceServerId: "local",
    workspaceServerLabel: "本地工作区",
  };
}

async function createRemoteAssistantWorkspace(
  request: APIRequestContext,
  workspaceLabel: string,
  server: {
    id: string;
    label: string;
    workspace_root?: string;
  },
) {
  const workspaceRoot = String(server.workspace_root || "").trim() || "/tmp";
  const folderName = `playwright-smoke-${Date.now()}-${randomUUID().slice(0, 8)}`;
  const workspacePath = path.posix.join(workspaceRoot, folderName);
  const response = await request.put(apiUrl("/api/agent/workspace/file"), {
    headers: authHeaders(),
    data: {
      path: workspaceRoot,
      server_id: server.id,
      relative_path: `${folderName}/notes/smoke.md`,
      content: `# ${workspaceLabel}\nfrontend smoke\n`,
      create_dirs: true,
      overwrite: true,
    },
  });
  expect(response.ok()).toBeTruthy();
  return {
    workspacePath,
    effectiveWorkspacePath: workspacePath,
    workspaceTitle: workspaceLabel,
    workspaceServerId: server.id,
    workspaceServerLabel: server.label,
  };
}

async function getDefaultProjectsRoot(request: APIRequestContext) {
  const response = await request.get(apiUrl("/api/settings/workspace-roots"), {
    headers: authHeaders(),
  });
  expect(response.ok()).toBeTruthy();
  const payload = await response.json();
  return String(payload.default_projects_root || "").trim();
}

async function setDefaultProjectsRoot(
  request: APIRequestContext,
  targetPath: string | null,
) {
  const response = await request.put(apiUrl("/api/settings/workspace-roots/default"), {
    headers: authHeaders(),
    data: {
      path: targetPath || null,
    },
  });
  expect(response.ok()).toBeTruthy();
}

function isApiRequest(url: string) {
  return url.includes("/api/") || url.startsWith(API_BASE);
}

async function enableAppSession(page: Page) {
  await page.addInitScript((token) => {
    localStorage.setItem("auth_token", token);
    localStorage.setItem("theme", "light");
  }, AUTH_TOKEN);
}

async function seedAssistantConversation(
  page: Page,
  payload: {
    id: string;
    title: string;
    workspacePath?: string | null;
    workspaceTitle?: string | null;
    workspaceServerId?: string | null;
    workspaceServerLabel?: string | null;
    effectiveWorkspacePath?: string | null;
    assistantBackendId?: string | null;
    assistantBackendLabel?: string | null;
  },
) {
  await page.addInitScript((conversation) => {
    const storageKey = "researchos_conversations";
    const indexKey = `${storageKey}_index`;
    const currentIndex = JSON.parse(localStorage.getItem(indexKey) || "[]");
    const nextMeta = {
      id: conversation.id,
      title: conversation.title,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      workspacePath: conversation.workspacePath || null,
      workspaceTitle: conversation.workspaceTitle || null,
      workspaceServerId: conversation.workspaceServerId || null,
      workspaceServerLabel: conversation.workspaceServerLabel || null,
      effectiveWorkspacePath: conversation.effectiveWorkspacePath || conversation.workspacePath || null,
      assistantSessionId: null,
      assistantSessionDirectory: conversation.workspacePath || null,
      assistantContextKey: null,
      assistantBackendId: conversation.assistantBackendId || null,
      assistantBackendLabel: conversation.assistantBackendLabel || conversation.assistantBackendId || null,
      mountedPaperId: null,
      mountedPaperTitle: null,
      mountedPaperIds: null,
      mountedPaperTitles: null,
    };
    const nextIndex = [nextMeta, ...currentIndex.filter((item: { id: string }) => item.id !== conversation.id)];
    localStorage.setItem(indexKey, JSON.stringify(nextIndex));
    localStorage.setItem(
      `${storageKey}_${conversation.id}`,
      JSON.stringify({
        ...nextMeta,
        messages: [],
      }),
    );
    localStorage.setItem("researchos_active_conversation", conversation.id);
  }, payload);
}

async function seedAssistantWorkbench(
  page: Page,
  payload?: {
    backendId?: string;
    permissionPreset?: "confirm" | "full_access";
    agentMode?: "build" | "plan";
    reasoningLevel?: "default" | "low" | "medium" | "high" | "xhigh";
  },
) {
  await page.addInitScript((settings) => {
    localStorage.setItem("researchos.agent.backendId", settings.backendId || "claw");
    localStorage.setItem("researchos.agent.permissionPreset", settings.permissionPreset || "confirm");
    localStorage.setItem("researchos.agent.mode", settings.agentMode || "build");
    localStorage.setItem("researchos.agent.reasoningLevel", settings.reasoningLevel || "default");
  }, payload || {});
}

async function configureExecPolicy(
  request: APIRequestContext,
  preset: "confirm" | "full_access",
) {
  const body = preset === "full_access"
    ? {
      workspace_access: "read_write",
      command_execution: "full",
      approval_mode: "off",
    }
    : {
      workspace_access: "read_write",
      command_execution: "full",
      approval_mode: "on_request",
    };
  const response = await request.put(apiUrl("/api/settings/assistant-exec-policy"), {
    headers: authHeaders(),
    data: body,
  });
  expect(response.ok()).toBeTruthy();
}

async function getExecPolicy(request: APIRequestContext) {
  const response = await request.get(apiUrl("/api/settings/assistant-exec-policy"), {
    headers: authHeaders(),
  });
  expect(response.ok()).toBeTruthy();
  return await response.json() as Record<string, unknown>;
}

async function restoreExecPolicy(
  request: APIRequestContext,
  policy: Record<string, unknown>,
) {
  const response = await request.put(apiUrl("/api/settings/assistant-exec-policy"), {
    headers: authHeaders(),
    data: policy,
  });
  expect(response.ok()).toBeTruthy();
}

async function withMockAcpPermissionServer(
  request: APIRequestContext,
  callback: () => Promise<void>,
) {
  const serverName = `playwright-acp-permission-${Date.now()}-${randomUUID().slice(0, 6)}`;
  let previousConfig: Record<string, unknown> | undefined;
  try {
    const configResponse = await request.get(apiUrl("/api/acp/config"), {
      headers: authHeaders(),
    });
    expect(configResponse.ok()).toBeTruthy();
    previousConfig = await configResponse.json();

    const nextConfig = {
      version: 1,
      default_server: serverName,
      servers: {
        ...((previousConfig?.servers as Record<string, unknown>) || {}),
        [serverName]: {
          name: serverName,
          label: "Playwright ACP Permission",
          transport: "stdio",
          command: ACP_PYTHON,
          args: [MOCK_ACP_PERMISSION_SERVER],
          cwd: BACKEND_REPO_ROOT,
          env: {},
          enabled: true,
          timeout_sec: 60,
        },
      },
    };

    const updateResponse = await request.put(apiUrl("/api/acp/config"), {
      headers: authHeaders(),
      data: nextConfig,
    });
    expect(updateResponse.ok()).toBeTruthy();

    const connectResponse = await request.post(apiUrl(`/api/acp/servers/${encodeURIComponent(serverName)}/connect`), {
      headers: authHeaders(),
      data: {},
    });
    expect(connectResponse.ok()).toBeTruthy();

    await callback();
  } finally {
    await configureExecPolicy(request, "confirm");
    if (previousConfig) {
      await request.put(apiUrl("/api/acp/config"), {
        headers: authHeaders(),
        data: previousConfig,
      });
    }
  }
}

async function getWorkspaceServers(request: APIRequestContext) {
  const response = await request.get(apiUrl("/api/agent/workspace/servers"), {
    headers: authHeaders(),
  });
  expect(response.ok()).toBeTruthy();
  const payload = await response.json();
  return payload.items as Array<Record<string, unknown>>;
}

function watchClientProblems(page: Page) {
  const pageErrors: string[] = [];
  const requestFailures: string[] = [];
  const apiFailures: string[] = [];

  page.on("pageerror", (error) => {
    pageErrors.push(error.message);
  });

  page.on("requestfailed", (request) => {
    const url = request.url();
    if (!isApiRequest(url)) return;
    const failureText = request.failure()?.errorText || "request failed";
    if (failureText.includes("net::ERR_ABORTED")) return;
    requestFailures.push(
      `${request.method()} ${url} :: ${failureText}`,
    );
  });

  page.on("response", (response) => {
    const url = response.url();
    if (!isApiRequest(url)) return;
    if (response.status() < 400) return;
    apiFailures.push(`${response.request().method()} ${response.status()} ${url}`);
  });

  return {
    async assertClean() {
      await page.waitForTimeout(400);
      expect(pageErrors).toEqual([]);
      expect(requestFailures).toEqual([]);
      expect(apiFailures).toEqual([]);
    },
  };
}

async function gotoRoute(page: Page, route: RouteExpectation) {
  await page.goto(route.path, { waitUntil: "domcontentloaded" });
  if (route.expectedUrl) {
    await expect(page).toHaveURL(route.expectedUrl);
  }
  await expect(page.locator("body")).toContainText(route.title);
  if (route.probe instanceof RegExp) {
    await expect(page.locator("body")).toContainText(route.probe);
  } else if (route.probe) {
    await expect(page.locator("body")).toContainText(route.probe);
  }
}

async function expectWorkingScroll(page: Page) {
  const candidate = await page.evaluate(() => {
    const elements = [document.scrollingElement, ...Array.from(document.querySelectorAll("main, [role='dialog'], aside, section, div"))]
      .filter((value): value is HTMLElement => value instanceof HTMLElement);

    let best: HTMLElement | null = null;
    let delta = 0;
    for (const el of elements) {
      const style = getComputedStyle(el);
      const canScroll = el === document.scrollingElement || /(auto|scroll)/.test(style.overflowY);
      const nextDelta = el.scrollHeight - el.clientHeight;
      if (!canScroll || nextDelta <= delta || nextDelta < 48) continue;
      best = el;
      delta = nextDelta;
    }

    if (!best) return null;
    best.setAttribute("data-scroll-probe", "true");
    return {
      tag: best.tagName,
      delta,
    };
  });

  expect(candidate).not.toBeNull();

  const scrolled = await page.evaluate(() => {
    const el = document.querySelector("[data-scroll-probe='true']") as HTMLElement | null;
    if (!el) return { before: -1, after: -1 };
    const before = el.scrollTop;
    el.scrollTop = Math.min(el.scrollHeight, before + 240);
    const after = el.scrollTop;
    el.removeAttribute("data-scroll-probe");
    return { before, after };
  });

  expect(scrolled.after).toBeGreaterThan(scrolled.before);
}

async function getFirstPaperId(request: APIRequestContext) {
  const response = await request.get(apiUrl("/api/papers/latest?page=1&page_size=1"), {
    headers: authHeaders(),
  });
  expect(response.ok()).toBeTruthy();
  const data = await response.json();
  return data.items?.[0]?.id as string | undefined;
}

function assistantComposer(page: Page) {
  return page.locator("textarea").first();
}

async function sendAssistantMessage(page: Page, message: string) {
  await assistantComposer(page).click();
  await assistantComposer(page).fill(message);
  await page.getByLabel("发送消息").click();
}

async function getStoredAssistantSessionId(page: Page, conversationId: string) {
  return page.evaluate((targetConversationId) => {
    const raw = localStorage.getItem(`researchos_conversations_${targetConversationId}`);
    if (!raw) return "";
    try {
      const parsed = JSON.parse(raw) as { assistantSessionId?: string | null };
      return String(parsed.assistantSessionId || "").trim();
    } catch {
      return "";
    }
  }, conversationId);
}

test.beforeEach(async ({ page }) => {
  await enableAppSession(page);
});

test("major routes render without runtime or api errors", async ({ page, request }) => {
  test.slow();
  const problems = watchClientProblems(page);

  for (const route of ROUTES) {
    await gotoRoute(page, route);
  }

  const paperId = await getFirstPaperId(request);
  if (paperId) {
    await gotoRoute(page, {
      path: `/papers/${paperId}`,
      title: /阅读 PDF|下载 PDF|获取 PDF|论文详情/,
    });
  }

  await problems.assertClean();
});

test("settings dialog opens and switches ResearchClaw-style sections", async ({ page }) => {
  const problems = watchClientProblems(page);

  await gotoRoute(page, { path: "/settings", title: "系统配置", probe: /模型与嵌入|MCP 服务|工作区与 SSH 服务器/ });
  await expect(page.getByRole("heading", { name: "系统配置" })).toBeVisible();

  await page.getByRole("button", { name: /Skills/ }).click();
  await expect(page.locator("body")).toContainText(ASSISTANT_SETTINGS_PROBE);

  await page.getByRole("button", { name: "工作区与 SSH 服务器" }).click();
  await expect(page.locator("body")).toContainText(/工作区根目录|默认项目根目录|SSH 服务器/);

  await page.getByRole("button", { name: "模型与嵌入" }).click();
  await expect(page.locator("body")).toContainText(/当前生效|当前未激活任何 LLM 配置/);
  await expect(page.locator("body")).toContainText(/新建 LLM 配置|模板直接开始|当前模板建议|OpenAI-compatible|Anthropic-compatible/);

  await page.getByRole("button", { name: "MCP 服务" }).click();
  await expect(page.locator("body")).toContainText(/MCP 运行状态|服务列表/);
  await expect(page.getByRole("button", { name: "通知与报告" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "系统维护" })).toHaveCount(0);

  await problems.assertClean();
});

test("editing an llm config can switch provider presets", async ({ page, request }) => {
  const problems = watchClientProblems(page);
  const configName = `Playwright Provider ${Date.now()}`;
  let createdConfigId: string | undefined;

  try {
    const createResponse = await request.post(apiUrl("/api/settings/llm-providers"), {
      headers: authHeaders(),
      data: {
        name: configName,
        provider: "custom",
        api_key: "sk-playwright-temp",
        api_base_url: "https://example.com/v1",
        model_skim: "test-mini",
        model_deep: "test-plus",
        model_vision: "test-vision",
        model_embedding: "test-embedding",
        model_fallback: "test-mini",
      },
    });
    expect(createResponse.ok()).toBeTruthy();
    const createPayload = await createResponse.json();
    createdConfigId = createPayload.id as string;

    await gotoRoute(page, { path: "/settings", title: "系统配置", probe: /模型与嵌入|MCP 服务|工作区与 SSH 服务器/ });
    await expect(page.getByRole("heading", { name: "系统配置" })).toBeVisible();
    await page.getByRole("button", { name: "模型与嵌入" }).click();

    const configRow = page.getByTestId(`llm-config-card-${createdConfigId}`);
    await expect(configRow).toBeVisible();
    await page.getByTestId(`llm-config-edit-${createdConfigId}`).click();

    const editPanel = page.getByTestId("llm-config-edit-panel");
    await expect(editPanel).toBeVisible();

    const openAiCard = editPanel.getByTestId("provider-card-openai");
    await openAiCard.click();

    await expect(openAiCard).toContainText(/OpenAI|已选中/);
    await expect(editPanel.locator('input[value="https://api.openai.com/v1"]')).toBeVisible();
    await expect(editPanel.locator('input[value="gpt-5.4"]')).toBeVisible();

    await problems.assertClean();
  } finally {
    if (createdConfigId) {
      await request.delete(apiUrl(`/api/settings/llm-providers/${createdConfigId}`), {
        headers: authHeaders(),
      });
    }
  }
});

test("graph workspace and operations redirect work", async ({ page }) => {
  const problems = watchClientProblems(page);

  await gotoRoute(page, { path: "/graph", title: "研究洞察", probe: "全局概览" });

  await page.getByRole("button", { name: "领域洞察" }).click();
  await expect(page.getByPlaceholder("输入关键词: transformer, reinforcement learning...")).toBeVisible();
  await expect(page.locator("body")).toContainText(/快速探索 · 当前论文库关键词|分析|领域洞察/);

  await page.getByRole("button", { name: "全局概览" }).click();
  await expect(page.locator("body")).toContainText("全局引用网络");

  await gotoRoute(page, {
    path: "/operations",
    title: "系统配置",
    probe: /模型与嵌入|MCP 服务|工作区与 SSH 服务器/,
    expectedUrl: /\/settings(?:\?.*)?$/,
  });
  await page.getByRole("button", { name: "MCP 服务" }).click();
  await expect(page.locator("body")).toContainText(/MCP 运行状态|服务列表/);
  await expect(page.getByRole("button", { name: "通知与报告" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "系统维护" })).toHaveCount(0);

  await problems.assertClean();
});

test("mobile settings layout keeps a working scroll container", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });

  await gotoRoute(page, { path: "/settings", title: "系统配置", probe: /模型与嵌入|MCP 服务|工作区与 SSH 服务器/ });
  await page.getByRole("button", { name: "工作区与 SSH 服务器" }).click();
  await page.waitForTimeout(250);
  await expectWorkingScroll(page);
});

test("assistant page exposes compact controls and workspace side panel", async ({ page, request }) => {
  const problems = watchClientProblems(page);
  const workspaceId = `assistant-toolbar-${Date.now()}-${randomUUID().slice(0, 8)}`;
  const workspacePath = makeWorkspacePath(workspaceId);
  const conversationId = `assistant-toolbar-conv-${Date.now()}`;
  const previousPolicy = await getExecPolicy(request);

  try {
    await configureExecPolicy(request, "full_access");
    removeHostWorkspace(workspacePath);
    const writeResponse = await request.put(apiUrl("/api/agent/workspace/file"), {
      headers: authHeaders(),
      data: {
        path: workspacePath,
        server_id: "local",
        relative_path: "notes/hello.md",
        content: "# hello\nworkspace panel smoke\n",
        create_dirs: true,
        overwrite: true,
      },
    });
    expect(writeResponse.ok()).toBeTruthy();
    const gitInitResponse = await request.post(apiUrl("/api/agent/workspace/git/init"), {
      headers: authHeaders(),
      data: {
        path: workspacePath,
        server_id: "local",
      },
    });
    expect(gitInitResponse.ok()).toBeTruthy();
    await seedAssistantConversation(page, {
      id: conversationId,
      title: "Toolbar Workspace Smoke",
      workspacePath,
      effectiveWorkspacePath: workspacePath,
      workspaceTitle: "Toolbar Workspace Smoke",
      workspaceServerId: "local",
      workspaceServerLabel: "本地工作区",
    });

    await gotoRoute(page, { path: "/assistant", title: "研究助手", probe: ASSISTANT_ROUTE_PROBE });
    await expect(page.locator("body")).toContainText(/模型|推理|权限|模式|目标|MCP/);
    await expect(page.getByTestId("assistant-target-select")).toHaveValue("local");

    await page.getByTestId("assistant-workspace-toggle").click();
    await expect(page.getByTestId("assistant-workspace-panel")).toBeVisible();
    await expect(page.locator("body")).toContainText(/工作区侧栏|文件|终端|Git/);
    await expect(page.locator("body")).toContainText(/notes|hello\.md/);

    await page.getByRole("button", { name: "终端" }).click();
    await expect(page.locator("body")).toContainText(/Terminal|终端 1/);
    await expect(page.locator("body")).toContainText(/已连接|ready/);

    await problems.assertClean();
  } finally {
    await restoreExecPolicy(request, previousPolicy);
    removeHostWorkspace(workspacePath);
  }
});

test("assistant route keeps active conversation in sync with the instance store", async ({ page }) => {
  const problems = watchClientProblems(page);
  const conversationA = `assistant-route-a-${Date.now()}`;
  const conversationB = `assistant-route-b-${Date.now()}`;

  await seedAssistantConversation(page, {
    id: conversationA,
    title: "Assistant Route Sync A",
  });
  await seedAssistantConversation(page, {
    id: conversationB,
    title: "Assistant Route Sync B",
  });

  await page.goto(`/assistant/${conversationA}`, { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(new RegExp(`/assistant/${conversationA}(?:\\?.*)?$`));
  await expect(page.locator("body")).toContainText("Assistant Route Sync A");
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("researchos_active_conversation")))
    .toBe(conversationA);

  await page.goto(`/assistant/${conversationB}`, { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(new RegExp(`/assistant/${conversationB}(?:\\?.*)?$`));
  await expect(page.locator("body")).toContainText("Assistant Route Sync B");
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("researchos_active_conversation")))
    .toBe(conversationB);

  await page.goto("/assistant", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(new RegExp(`/assistant/${conversationB}(?:\\?.*)?$`));

  await problems.assertClean();
});

test("assistant new conversation button creates a routed conversation shell", async ({ page }) => {
  const problems = watchClientProblems(page);

  await page.goto("/assistant", { waitUntil: "domcontentloaded" });
  await expect(page.locator("body")).toContainText("研究助手");

  await page.getByRole("button", { name: /新对话|发起研究对话/ }).click();

  await expect.poll(() =>
    page.evaluate(() => localStorage.getItem("researchos_active_conversation") || ""),
  ).not.toBe("");
  const activeConversationId = await page.evaluate(() => localStorage.getItem("researchos_active_conversation") || "");

  await expect(page).toHaveURL(new RegExp(`/assistant/${activeConversationId}(?:\\?.*)?$`));
  await expect.poll(() =>
    page.evaluate((conversationId) => {
      const raw = localStorage.getItem(`researchos_conversations_${conversationId}`);
      if (!raw) return null;
      try {
        const parsed = JSON.parse(raw) as { id?: string | null; title?: string | null };
        return {
          id: String(parsed.id || ""),
          title: String(parsed.title || ""),
        };
      } catch {
        return null;
      }
    }, activeConversationId),
  ).toEqual({
    id: activeConversationId,
    title: "新对话",
  });

  await problems.assertClean();
});

test("assistant mode selection is forwarded to session create and prompt requests", async ({ page, request }) => {
  const problems = watchClientProblems(page);
  const conversationId = `assistant-mode-${Date.now()}`;
  const sessionId = conversationId;
  const now = Date.now();
  const createdBodies: Array<Record<string, unknown>> = [];
  const promptBodies: Array<Record<string, unknown>> = [];
  const workspace = await createLocalAssistantWorkspace(request, "Mode Forward Smoke");

  const baseState = {
    session: {
      id: sessionId,
      title: "Mode Forward Smoke",
      directory: workspace.workspacePath,
      time: {
        created: now,
        updated: now,
      },
    },
    messages: [],
    permissions: [],
    status: { type: "idle" },
  };

  await page.route(apiRoutePattern(`/session/${sessionId}/message`), async (route) => {
    promptBodies.push(route.request().postDataJSON() as Record<string, unknown>);
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: 'event: text_delta\ndata: {"content":"ok"}\n\nevent: done\ndata: {}\n\n',
    });
  });

  await page.route(apiRoutePattern(`/session/${sessionId}/permissions`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "[]",
    });
  });

  await page.route(apiRoutePattern(`/session/${sessionId}/state`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(baseState),
    });
  });

  await page.route(apiRoutePattern("/session"), async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    const body = route.request().postDataJSON() as Record<string, unknown>;
    if (String(body.id || "").trim() !== sessionId) {
      await route.fallback();
      return;
    }
    createdBodies.push(body);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(baseState.session),
    });
  });

  await seedAssistantWorkbench(page, {
    backendId: "claw",
    agentMode: "plan",
    reasoningLevel: "medium",
  });
  await seedAssistantConversation(page, {
    id: conversationId,
    title: "Mode Forward Smoke",
    ...workspace,
  });

  await page.goto(`/assistant/${conversationId}`, { waitUntil: "domcontentloaded" });
  await expect(page.locator("body")).toContainText("Mode Forward Smoke");
  await expect(page.locator("body")).toContainText("Plan");
  await expect.poll(() => createdBodies.length).toBeGreaterThan(0);

  expect(createdBodies[0]).toMatchObject({
    id: sessionId,
    mode: "plan",
  });

  await sendAssistantMessage(page, "first request in plan mode");
  await expect.poll(() => promptBodies.length).toBe(1);
  expect(promptBodies[0]).toMatchObject({
    mode: "plan",
    reasoning_level: "medium",
    agent_backend_id: "native",
  });

  const modeSelect = page.locator("label").filter({ hasText: "模式" }).locator("select");
  await modeSelect.selectOption("build");
  await expect(modeSelect).toHaveValue("build");
  await expect.poll(() => page.evaluate((targetConversationId) => {
    const raw = localStorage.getItem(`researchos_conversations_${targetConversationId}`);
    if (!raw) return "";
    try {
      return String((JSON.parse(raw) as { assistantMode?: string | null }).assistantMode || "");
    } catch {
      return "";
    }
  }, conversationId)).toBe("build");

  await sendAssistantMessage(page, "second request in build mode");
  await expect.poll(() => promptBodies.length).toBe(2);
  expect(promptBodies[1]).toMatchObject({
    mode: "build",
    reasoning_level: "medium",
    agent_backend_id: "native",
  });

  await problems.assertClean();
});

test("assistant switching workspace-bound conversations updates the active workspace and survives refresh", async ({ page, request }) => {
  const problems = watchClientProblems(page);
  const workspaceIdA = `assistant-workspace-a-${Date.now()}-${randomUUID().slice(0, 6)}`;
  const workspaceIdB = `assistant-workspace-b-${Date.now()}-${randomUUID().slice(0, 6)}`;
  const workspacePathA = makeWorkspacePath(workspaceIdA);
  const workspacePathB = makeWorkspacePath(workspaceIdB);
  const conversationA = `assistant-workspace-route-a-${Date.now()}`;
  const conversationB = `assistant-workspace-route-b-${Date.now()}`;

  removeHostWorkspace(workspacePathA);
  removeHostWorkspace(workspacePathB);

  await request.put(apiUrl("/api/agent/workspace/file"), {
    headers: authHeaders(),
    data: {
      path: workspacePathA,
      server_id: "local",
      relative_path: "notes/a.md",
      content: "# workspace-a\nalpha\n",
      create_dirs: true,
      overwrite: true,
    },
  });
  await request.put(apiUrl("/api/agent/workspace/file"), {
    headers: authHeaders(),
    data: {
      path: workspacePathB,
      server_id: "local",
      relative_path: "notes/b.md",
      content: "# workspace-b\nbeta\n",
      create_dirs: true,
      overwrite: true,
    },
  });

  await seedAssistantConversation(page, {
    id: conversationA,
    title: "Workspace Switch Smoke A",
    workspacePath: workspacePathA,
    effectiveWorkspacePath: workspacePathA,
    workspaceTitle: "Workspace Switch Smoke A",
    workspaceServerId: "local",
    workspaceServerLabel: "本地工作区",
  });
  await seedAssistantConversation(page, {
    id: conversationB,
    title: "Workspace Switch Smoke B",
    workspacePath: workspacePathB,
    effectiveWorkspacePath: workspacePathB,
    workspaceTitle: "Workspace Switch Smoke B",
    workspaceServerId: "local",
    workspaceServerLabel: "本地工作区",
  });

  await page.goto(`/assistant/${conversationA}`, { waitUntil: "domcontentloaded" });
  await expect(page.locator("body")).toContainText("Workspace Switch Smoke A");
  await expect(page.getByTestId("assistant-target-select")).toHaveValue("local");
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("researchos_active_conversation")))
    .toBe(conversationA);

  await page.getByTestId("assistant-workspace-toggle").click();
  await expect(page.getByTestId("assistant-workspace-panel")).toBeVisible();
  await expect(page.locator("body")).toContainText(/notes|a\.md/);

  await page.locator("aside").getByRole("button", { name: /Workspace Switch Smoke B.*刚刚/ }).click();
  await expect(page).toHaveURL(new RegExp(`/assistant/${conversationB}(?:\\?.*)?$`));
  await expect(page.locator("body")).toContainText("Workspace Switch Smoke B");
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("researchos_active_conversation")))
    .toBe(conversationB);
  await expect(page.locator("body")).toContainText(/notes|b\.md/);

  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(new RegExp(`/assistant/${conversationB}(?:\\?.*)?$`));
  await expect(page.locator("body")).toContainText("Workspace Switch Smoke B");
  const workspacePanel = page.getByTestId("assistant-workspace-panel");
  if (!(await workspacePanel.isVisible())) {
    await page.getByTestId("assistant-workspace-toggle").click();
  }
  await expect(workspacePanel).toBeVisible();
  await expect(page.locator("body")).toContainText(/notes|b\.md/);

  await problems.assertClean();
});

test("assistant custom ACP confirm flow survives refresh and session switching", async ({ page, request }) => {
  const problems = watchClientProblems(page);
  const conversationA = `assistant-acp-confirm-a-${Date.now()}`;
  const conversationB = `assistant-acp-confirm-b-${Date.now()}`;
  const servers = await getWorkspaceServers(request);
  const remoteServer = servers.find((item) => item.kind === "ssh" && item.id && item.label) as
    | {
        id: string;
        label: string;
        workspace_root?: string;
      }
    | undefined;

  test.skip(!remoteServer, "No SSH workspace server is configured for ACP smoke.");

  const workspaceA = await createRemoteAssistantWorkspace(request, "ACP Confirm Smoke A", remoteServer);
  const workspaceB = await createRemoteAssistantWorkspace(request, "ACP Confirm Smoke B", remoteServer);

  await withMockAcpPermissionServer(request, async () => {
    await configureExecPolicy(request, "confirm");
    await seedAssistantWorkbench(page, {
      backendId: "custom_acp",
      permissionPreset: "confirm",
      agentMode: "build",
      reasoningLevel: "medium",
    });
    await seedAssistantConversation(page, {
      id: conversationA,
      title: "ACP Confirm Smoke A",
      ...workspaceA,
      assistantBackendId: "custom_acp",
      assistantBackendLabel: "custom_acp",
    });
    await seedAssistantConversation(page, {
      id: conversationB,
      title: "ACP Confirm Smoke B",
      ...workspaceB,
      assistantBackendId: "custom_acp",
      assistantBackendLabel: "custom_acp",
    });

    await page.goto(`/assistant/${conversationA}`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).toContainText("ACP Confirm Smoke A");
    await sendAssistantMessage(page, "please continue the ACP confirm smoke");
    await expect(page.locator("body")).toContainText("Permission required");
    await expect(page.locator("body")).toContainText("需要你的确认");
    await page.getByRole("button", { name: "确认执行" }).click();
    await expect(page.locator("body")).toContainText("Permission outcome: allow_once");

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).toContainText("Permission outcome: allow_once");
    await expect(page).toHaveURL(new RegExp(`/assistant/${conversationA}(?:\\?.*)?$`));

    await page.goto(`/assistant/${conversationB}`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).toContainText("ACP Confirm Smoke B");
    await page.goto(`/assistant/${conversationA}`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).toContainText("Permission outcome: allow_once");
  });

  await problems.assertClean();
});

test("assistant custom ACP reject and full access auto-allow flows work", async ({ page, request }) => {
  const problems = watchClientProblems(page);
  const conversationId = `assistant-acp-policy-${Date.now()}`;
  const servers = await getWorkspaceServers(request);
  const remoteServer = servers.find((item) => item.kind === "ssh" && item.id && item.label) as
    | {
        id: string;
        label: string;
        workspace_root?: string;
      }
    | undefined;

  test.skip(!remoteServer, "No SSH workspace server is configured for ACP smoke.");

  const workspace = await createRemoteAssistantWorkspace(request, "ACP Policy Smoke", remoteServer);

  await withMockAcpPermissionServer(request, async () => {
    await configureExecPolicy(request, "confirm");
    await seedAssistantWorkbench(page, {
      backendId: "custom_acp",
      permissionPreset: "confirm",
      agentMode: "build",
      reasoningLevel: "medium",
    });
    await seedAssistantConversation(page, {
      id: conversationId,
      title: "ACP Policy Smoke",
      ...workspace,
      assistantBackendId: "custom_acp",
      assistantBackendLabel: "custom_acp",
    });

    await page.goto(`/assistant/${conversationId}`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).toContainText("ACP Policy Smoke");

    await sendAssistantMessage(page, "please pause for policy reject smoke");
    await expect(page.locator("body")).toContainText("Permission required");
    await page.getByRole("button", { name: "跳过" }).click();
    await expect(page.locator("body")).toContainText("Permission outcome: reject_once");

    await page.getByRole("button", { name: "权限 需确认" }).click();
    await expect(page.locator("body")).toContainText("自动确认");

    await sendAssistantMessage(page, "please pause for policy auto allow smoke");
    await expect(page.locator("body")).toContainText("Permission outcome: allow_always");
    await expect(page.getByRole("button", { name: "确认执行" })).toHaveCount(0);
  });

  await problems.assertClean();
});

test("assistant ui reflects an external abort for a paused custom ACP prompt", async ({ page, request }) => {
  const problems = watchClientProblems(page);
  const conversationId = `assistant-acp-abort-${Date.now()}`;
  const servers = await getWorkspaceServers(request);
  const remoteServer = servers.find((item) => item.kind === "ssh" && item.id && item.label) as
    | {
        id: string;
        label: string;
        workspace_root?: string;
      }
    | undefined;

  test.skip(!remoteServer, "No SSH workspace server is configured for ACP smoke.");

  const workspace = await createRemoteAssistantWorkspace(request, "ACP Abort Smoke", remoteServer);

  await withMockAcpPermissionServer(request, async () => {
    await configureExecPolicy(request, "confirm");
    await seedAssistantWorkbench(page, {
      backendId: "custom_acp",
      permissionPreset: "confirm",
      agentMode: "build",
      reasoningLevel: "medium",
    });
    await seedAssistantConversation(page, {
      id: conversationId,
      title: "ACP Abort Smoke",
      ...workspace,
      assistantBackendId: "custom_acp",
      assistantBackendLabel: "custom_acp",
    });

    await page.goto(`/assistant/${conversationId}`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).toContainText("ACP Abort Smoke");

    await sendAssistantMessage(page, "please pause so I can abort this ACP run");
    await expect(page.locator("body")).toContainText("Permission required");

    await expect.poll(() => getStoredAssistantSessionId(page, conversationId)).not.toBe("");
    const sessionId = await getStoredAssistantSessionId(page, conversationId);
    const abortResponse = await request.post(apiUrl(`/api/session/${sessionId}/abort`), {
      headers: authHeaders(),
    });
    expect(abortResponse.ok()).toBeTruthy();

    await expect(page.locator("body")).toContainText("会话已中止");
    await expect(page.getByRole("button", { name: "确认执行" })).toHaveCount(0);
    const historyResponse = await request.get(apiUrl(`/api/session/${sessionId}/message`), {
      headers: authHeaders(),
    });
    expect(historyResponse.ok()).toBeTruthy();
    const history = await historyResponse.json();
    const assistant = [...history].reverse().find((item: { info?: { role?: string; finish?: string } }) =>
      item?.info?.role === "assistant");
    expect(assistant?.info?.finish).toBe("aborted");

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).toContainText("会话已中止");
  });

  await problems.assertClean();
});

test("assistant question cards submit structured answers through the session permission api", async ({ page, request }) => {
  const problems = watchClientProblems(page);
  const conversationId = `assistant-question-${Date.now()}`;
  const sessionId = conversationId;
  const actionId = `question-${Date.now()}`;
  const now = Date.now();
  let answered = false;
  let submittedBody: Record<string, unknown> | null = null;
  const workspace = await createLocalAssistantWorkspace(request, "Question Flow Smoke");

  const statePayload = () => ({
    session: {
      id: sessionId,
      title: "Question Flow Smoke",
      directory: workspace.workspacePath,
      time: {
        created: now,
        updated: now,
      },
    },
    messages: [],
    permissions: answered
      ? []
      : [
          {
            id: actionId,
            session_id: sessionId,
            permission: "question",
            title: "需要你补充信息",
            description: "请先说明接下来更想让智能体处理哪一部分。",
            metadata: {
              title: "需要你补充信息",
              description: "请先说明接下来更想让智能体处理哪一部分。",
              questions: [
                {
                  header: "范围",
                  question: "这轮应该优先看哪部分？",
                  options: [
                    {
                      label: "阅读图表",
                      description: "先看论文里的图表和实验结果。",
                    },
                    {
                      label: "整理方法",
                      description: "先梳理论文的方法和推理链路。",
                    },
                  ],
                  multiple: true,
                  custom: true,
                },
              ],
            },
          },
        ],
    status: { type: "idle" },
  });

  await page.route(apiRoutePattern(`/session/${sessionId}/permissions/${actionId}`), async (route) => {
    submittedBody = route.request().postDataJSON() as Record<string, unknown>;
    answered = true;
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: "event: done\ndata: {}\n\n",
    });
  });

  await page.route(apiRoutePattern(`/session/${sessionId}/permissions`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(answered ? [] : statePayload().permissions),
    });
  });

  await page.route(apiRoutePattern(`/session/${sessionId}/state`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(statePayload()),
    });
  });

  await page.route(apiRoutePattern("/session"), async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    const body = route.request().postDataJSON() as Record<string, unknown>;
    if (String(body.id || "").trim() !== sessionId) {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(statePayload().session),
    });
  });

  await seedAssistantConversation(page, {
    id: conversationId,
    title: "Question Flow Smoke",
    ...workspace,
  });

  await page.goto(`/assistant/${conversationId}`, { waitUntil: "domcontentloaded" });
  await expect(page.locator("body")).toContainText("Question Flow Smoke");
  await expect(page.locator("body")).toContainText("需要你补充信息");
  await expect(page.locator("body")).toContainText("这轮应该优先看哪部分？");

  await page.getByRole("button", { name: /阅读图表/ }).click();
  await page.getByPlaceholder("或填写你自己的回答").fill("补充一下图 3 和消融实验");
  const submitAnswersButton = page.getByRole("button", { name: "提交回答" });
  await expect(submitAnswersButton).toBeEnabled();
  await submitAnswersButton.click();

  await expect.poll(() => submittedBody).not.toBeNull();
  expect(submittedBody).toEqual({
    response: "answer",
    message: null,
    answers: [["阅读图表", "补充一下图 3 和消融实验"]],
  });

  await expect(page.getByRole("button", { name: "提交回答" })).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("需要你补充信息");

  await problems.assertClean();
});

test("assistant settings surface ACP registry and mock ACP can be tested", async ({ page, request }) => {
  const problems = watchClientProblems(page);
  let previousConfig: Record<string, unknown> | undefined;

  try {
    const configResponse = await request.get(apiUrl("/api/acp/config"), {
      headers: authHeaders(),
    });
    expect(configResponse.ok()).toBeTruthy();
    previousConfig = await configResponse.json();

    const nextConfig = {
      version: 1,
      default_server: "playwright-mock-acp",
      servers: {
        ...((previousConfig?.servers as Record<string, unknown>) || {}),
        "playwright-mock-acp": {
          name: "playwright-mock-acp",
          label: "Playwright Mock ACP",
          transport: "stdio",
          command: ACP_PYTHON,
          args: [MOCK_ACP_SERVER],
          cwd: BACKEND_REPO_ROOT,
          env: {},
          enabled: true,
          timeout_sec: 60,
        },
      },
    };

    const updateResponse = await request.put(apiUrl("/api/acp/config"), {
      headers: authHeaders(),
      data: nextConfig,
    });
    expect(updateResponse.ok()).toBeTruthy();

    const connectResponse = await request.post(apiUrl("/api/acp/servers/playwright-mock-acp/connect"), {
      headers: authHeaders(),
      data: {},
    });
    expect(connectResponse.ok()).toBeTruthy();

    const testResponse = await request.post(apiUrl("/api/acp/servers/playwright-mock-acp/test"), {
      headers: authHeaders(),
      data: {
        prompt: "please echo playwright-acp-ok",
        workspace_path: BACKEND_REPO_ROOT,
        timeout_sec: 60,
      },
    });
    expect(testResponse.ok()).toBeTruthy();
    const testPayload = await testResponse.json();
    expect(String(testPayload.item?.content || "")).toContain("MOCK_ACP_OK");
    expect(String(testPayload.item?.content || "")).toContain("playwright-acp-ok");

    await gotoRoute(page, { path: "/settings", title: "系统配置", probe: /模型与嵌入|MCP 服务|工作区与 SSH 服务器/ });
    await page.getByRole("button", { name: /Skills/ }).click();
    await expect(page.locator("body")).toContainText(ASSISTANT_SETTINGS_PROBE);

    await problems.assertClean();
  } finally {
    if (previousConfig) {
      await request.put(apiUrl("/api/acp/config"), {
        headers: authHeaders(),
        data: previousConfig,
      });
    }
  }
});

test("assistant shell can bind a real ssh target in the compact toolbar", async ({ page, request }) => {
  const problems = watchClientProblems(page);
  const servers = await getWorkspaceServers(request);
  const remoteServer = servers.find((item) => item.kind === "ssh" && item.id && item.label) as
    | {
        id: string;
        label: string;
        workspace_root?: string;
      }
    | undefined;

  test.skip(!remoteServer, "No SSH workspace server is configured in this environment.");

  const conversationId = `remote-smoke-${Date.now()}`;
  const remoteWorkspace = String(remoteServer.workspace_root || "").trim() || "/tmp";
  await seedAssistantConversation(page, {
    id: conversationId,
    title: "Remote SSH Smoke",
    workspacePath: remoteWorkspace,
    effectiveWorkspacePath: remoteWorkspace,
    workspaceTitle: `SSH · ${remoteServer.label}`,
    workspaceServerId: remoteServer.id,
    workspaceServerLabel: remoteServer.label,
  });

  await gotoRoute(page, { path: "/assistant", title: "研究助手", probe: /Session Shell|研究助手|助手后端/ });
  await expect(page.locator("body")).toContainText(remoteServer.label);
  await expect(page.getByTestId("assistant-target-select")).toHaveValue(remoteServer.id);
  await expect(page.locator("body")).toContainText(/权限|模式|目标|MCP|SSH/);
  await expect(page.getByTestId("assistant-ssh-button")).toBeVisible();

  await problems.assertClean();
});

test("papers tasks and pipelines support safe view interactions", async ({ page, request }) => {
  const problems = watchClientProblems(page);

  await gotoRoute(page, { path: "/papers", title: "全部论文", probe: /全部论文|论文库/ });
  await page.getByRole("button", { name: "网格视图" }).click();
  await page.getByRole("button", { name: "列表视图" }).click();

  await gotoRoute(page, { path: "/tasks", title: /任务后台|Background Jobs/, probe: /运行中|已完成|失败|暂无任务记录/ });
  await page.getByRole("button", { name: /已完成/ }).first().click();
  await expect(page.locator("body")).toContainText(/已完成|暂无任务记录/);

  await gotoRoute(page, {
    path: "/pipelines",
    title: /任务后台|Background Jobs/,
    probe: /运行中|已完成|失败|暂无任务记录/,
    expectedUrl: /\/tasks(?:\?.*)?$/,
  });
  await page.getByRole("button", { name: /已完成/ }).first().click();
  await expect(page.locator("body")).toContainText(/已完成|暂无任务记录/);

  const paperId = await getFirstPaperId(request);
  if (paperId) {
    await page.goto(`/papers/${paperId}`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).toContainText(/阅读 PDF|下载 PDF|获取 PDF|粗读|精读/);
  }

  await problems.assertClean();
});

test("projects workspace supports creating a project through the ui and shows the desktop workbench layout", async ({ page, request }) => {
  test.slow();
  const problems = watchClientProblems(page);
  const projectName = `UI Smoke Project ${Date.now()}`;
  const projectRoot = path.resolve(process.cwd(), "../tmp", `ui-project-root-${Date.now()}-${randomUUID().slice(0, 8)}`);
  const projectDirName = `ui-project-${Date.now()}-${randomUUID().slice(0, 8)}`;
  let createdProjectId: string | undefined;
  let previousDefaultProjectsRoot: string | null = null;

  try {
    fs.mkdirSync(projectRoot, { recursive: true });
    previousDefaultProjectsRoot = await getDefaultProjectsRoot(request);
    await setDefaultProjectsRoot(request, projectRoot);

    await gotoRoute(page, { path: "/projects", title: "项目工作区", probe: /项目工作区|项目工作台/ });
    await page.getByTestId("projects-list-panel").getByRole("button", { name: "新建", exact: true }).click();
    await expect(page.locator("body")).toContainText("新建项目");
    await page.getByPlaceholder("输入显示名称").fill(projectName);
    await page.getByPlaceholder("输入项目说明").fill("playwright ui create smoke");
    await page.getByPlaceholder("输入目录名称").fill(projectDirName);
    await page.getByRole("button", { name: "保存" }).click();

    await expect(page.getByTestId("projects-workbench")).toContainText(projectName);
    await expect(page.getByTestId("project-companion-card")).toBeVisible();
    await expect(page.getByTestId("projects-workbench")).toContainText(/目标|运行|论文/);
    await expect(page.getByTestId("projects-workbench")).toContainText(/这个项目还没有运行记录|运行列表/);
    await expect(page.getByTestId("project-companion-card").getByRole("button", { name: "打开助手" })).toBeVisible();

    const projectsResponse = await request.get(apiUrl("/api/projects"), {
      headers: authHeaders(),
    });
    expect(projectsResponse.ok()).toBeTruthy();
    const projectsPayload = await projectsResponse.json();
    createdProjectId = (projectsPayload.items as Array<{ id: string; name: string }>).find((item) => item.name === projectName)?.id;
    expect(createdProjectId).toBeTruthy();

    await problems.assertClean();
  } finally {
    if (createdProjectId) {
      try {
        await request.delete(apiUrl(`/api/projects/${createdProjectId}`), {
          headers: authHeaders(),
        });
      } catch {}
    }
    if (previousDefaultProjectsRoot !== null) {
      try {
        await setDefaultProjectsRoot(request, previousDefaultProjectsRoot || null);
      } catch {}
    }
    fs.rmSync(projectRoot, { recursive: true, force: true });
  }
});

test("restored workspace api works through the frontend proxy", async ({ request }) => {
  test.slow();
  const workspaceId = `playwright-workspace-smoke-${Date.now()}-${randomUUID().slice(0, 8)}`;
  const workspacePath = makeWorkspacePath(workspaceId);
  const remoteServerId = `playwright-remote-${randomUUID().slice(0, 8)}`;
  const branchName = `feature/${workspaceId}`;
  const previousPolicy = await getExecPolicy(request);

  removeHostWorkspace(workspacePath);

  try {
    await configureExecPolicy(request, "full_access");
    const serversResponse = await request.get(apiUrl("/api/agent/workspace/servers"), {
      headers: authHeaders(),
    });
    expect(serversResponse.ok()).toBeTruthy();
    const serversPayload = await serversResponse.json();
    expect(serversPayload.items.some((item: { id: string }) => item.id === "local")).toBeTruthy();

    const createServerResponse = await request.post(apiUrl("/api/agent/workspace/servers"), {
      headers: authHeaders(),
      data: {
        id: remoteServerId,
        label: remoteServerId,
        host: "127.0.0.1",
        port: 65535,
        username: "smoke",
        password: "invalid-password",
        workspace_root: "/tmp/researchos-smoke",
        enabled: true,
      },
    });
    expect(createServerResponse.ok()).toBeTruthy();
    const createdServer = await createServerResponse.json();
    expect(createdServer.item.id).toBe(remoteServerId);
    expect(createdServer.item.kind).toBe("ssh");
    expect(createdServer.item.available).toBeTruthy();
    expect(createdServer.item.auth_mode).toBe("password");
    expect(createdServer.item.workspace_root).toBe("/tmp/researchos-smoke");

    const probeResponse = await request.post(apiUrl("/api/agent/workspace/ssh/probe"), {
      headers: authHeaders(),
      data: {
        host: "127.0.0.1",
        port: 65535,
        username: "smoke",
        password: "invalid-password",
        workspace_root: "/tmp/researchos-smoke",
      },
    });
    expect(probeResponse.ok()).toBeTruthy();
    const probePayload = await probeResponse.json();
    expect(probePayload.success).toBeFalsy();
    expect(String(probePayload.message || "")).not.toBe("");

    const writeResponse = await request.put(apiUrl("/api/agent/workspace/file"), {
      headers: authHeaders(),
      data: {
        path: workspacePath,
        server_id: "local",
        relative_path: "docs/note.md",
        content: "# smoke\nalpha\n",
        create_dirs: true,
        overwrite: true,
      },
    });
    expect(writeResponse.ok()).toBeTruthy();
    const writePayload = await writeResponse.json();
    expect(writePayload.relative_path).toBe("docs/note.md");

    const readResponse = await request.get(
      apiUrl(
        `/api/agent/workspace/file?path=${encodeURIComponent(workspacePath)}&relative_path=${encodeURIComponent("docs/note.md")}&server_id=local`,
      ),
      {
        headers: authHeaders(),
      },
    );
    expect(readResponse.ok()).toBeTruthy();
    const readPayload = await readResponse.json();
    expect(readPayload.content).toContain("alpha");

    const gitInitResponse = await request.post(apiUrl("/api/agent/workspace/git/init"), {
      headers: authHeaders(),
      data: {
        path: workspacePath,
        server_id: "local",
      },
    });
    expect(gitInitResponse.ok()).toBeTruthy();
    const gitInitPayload = await gitInitResponse.json();
    expect(gitInitPayload.ok).toBeTruthy();
    expect(gitInitPayload.git.is_repo).toBeTruthy();

    const commitResponse = await request.post(apiUrl("/api/agent/workspace/terminal/run"), {
      headers: authHeaders(),
      data: {
        path: workspacePath,
        server_id: "local",
        timeout_sec: 120,
        command: [
          "git config user.email 'smoke@example.com'",
          "git config user.name 'Playwright Smoke'",
          "git add .",
          "git commit -m 'smoke init'",
        ].join("; "),
      },
    });
    expect(commitResponse.ok()).toBeTruthy();
    const commitPayload = await commitResponse.json();
    expect(commitPayload.success).toBeTruthy();

    const secondWriteResponse = await request.put(apiUrl("/api/agent/workspace/file"), {
      headers: authHeaders(),
      data: {
        path: workspacePath,
        server_id: "local",
        relative_path: "docs/note.md",
        content: "# smoke\nalpha\nbeta\n",
        create_dirs: true,
        overwrite: true,
      },
    });
    expect(secondWriteResponse.ok()).toBeTruthy();

    const overviewResponse = await request.get(
      apiUrl(
        `/api/agent/workspace/overview?path=${encodeURIComponent(workspacePath)}&depth=3&max_entries=200&server_id=local`,
      ),
      {
        headers: authHeaders(),
      },
    );
    expect(overviewResponse.ok()).toBeTruthy();
    const overviewPayload = await overviewResponse.json();
    expect(overviewPayload.exists).toBeTruthy();
    expect(overviewPayload.files).toContain("docs/note.md");
    expect(overviewPayload.git.is_repo).toBeTruthy();

    const diffResponse = await request.get(
      apiUrl(
        `/api/agent/workspace/git/diff?path=${encodeURIComponent(workspacePath)}&file_path=${encodeURIComponent("docs/note.md")}&server_id=local`,
      ),
      {
        headers: authHeaders(),
      },
    );
    expect(diffResponse.ok()).toBeTruthy();
    const diffPayload = await diffResponse.json();
    expect(diffPayload.git.is_repo).toBeTruthy();
    expect(diffPayload.diff).toContain("+beta");

    const branchResponse = await request.post(apiUrl("/api/agent/workspace/git/branch"), {
      headers: authHeaders(),
      data: {
        path: workspacePath,
        server_id: "local",
        branch_name: branchName,
        checkout: true,
      },
    });
    expect(branchResponse.ok()).toBeTruthy();
    const branchPayload = await branchResponse.json();
    expect(branchPayload.ok).toBeTruthy();
    expect(branchPayload.branch).toBe(branchName);
    expect(branchPayload.git.branch).toBe(branchName);

    const uploadResponse = await request.post(apiUrl("/api/agent/workspace/upload"), {
      headers: authHeaders(),
      multipart: {
        path: workspacePath,
        server_id: "local",
        relative_path: "uploads/sample.txt",
        file: {
          name: "sample.txt",
          mimeType: "text/plain",
          buffer: Buffer.from("upload smoke\n", "utf-8"),
        },
      },
    });
    expect(uploadResponse.ok()).toBeTruthy();
    const uploadPayload = await uploadResponse.json();
    expect(uploadPayload.relative_path).toBe("uploads/sample.txt");
    expect(uploadPayload.size_bytes).toBeGreaterThan(0);

    const listCommandResponse = await request.post(apiUrl("/api/agent/workspace/terminal/run"), {
      headers: authHeaders(),
      data: {
        path: workspacePath,
        server_id: "local",
        timeout_sec: 60,
        command: LIST_UPLOADS_COMMAND,
      },
    });
    expect(listCommandResponse.ok()).toBeTruthy();
    const listCommandPayload = await listCommandResponse.json();
    expect(listCommandPayload.success).toBeTruthy();
    expect(listCommandPayload.stdout).toContain("sample.txt");
  } finally {
    await restoreExecPolicy(request, previousPolicy);
    await request.delete(apiUrl(`/api/agent/workspace/servers/${encodeURIComponent(remoteServerId)}`), {
      headers: authHeaders(),
    });
    removeHostWorkspace(workspacePath);
  }
});
