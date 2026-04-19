from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from playwright.sync_api import Error, TimeoutError as PlaywrightTimeoutError, sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
FRONTEND_DIR = REPO_ROOT / "frontend"
ARTIFACT_DIR = REPO_ROOT / "artifacts" / "siglip-terminal-e2e"
API_PORT = int(os.environ.get("SIGLIP_E2E_API_PORT", "8011"))
FRONTEND_PORT = int(os.environ.get("SIGLIP_E2E_FRONTEND_PORT", "4319"))
API_BASE_URL = f"http://127.0.0.1:{API_PORT}"
BASE_URL = f"http://127.0.0.1:{FRONTEND_PORT}"
WORKSPACE_PATH = str(REPO_ROOT)
PAPER_ID = "8e03d7ff-6997-4f6c-97ff-6629c5ea9ebe"
PAPER_TITLE = "SigLIP 2"
TERMINAL_REQUEST_PATTERN = re.compile(r"/agent/workspace/terminal/session(?:\?|$)")
SESSION_REQUEST_PATTERN = re.compile(r"/session/[^/]+/message(?:\?|$)")
FIGURE_PATH_FRAGMENT = f"/papers/{PAPER_ID}/figures/"

FETCH_SPY_SCRIPT = """
(() => {
  if (window.__researchFetchSpyInstalled) return;
  window.__researchFetchSpyInstalled = true;
  window.__researchFetchLogs = [];
  const originalFetch = window.fetch.bind(window);

  window.fetch = async (...args) => {
    const [input, init] = args;
    const url = typeof input === "string"
      ? input
      : (input && typeof input.url === "string" ? input.url : String(input));
    const method = (init && init.method)
      || (input && typeof input.method === "string" ? input.method : "GET");
    let requestBody = null;
    if (init && typeof init.body === "string") {
      requestBody = init.body;
    } else if (init && init.body != null) {
      requestBody = String(init.body);
    }

    const entry = {
      url,
      method,
      requestBody,
      startedAt: Date.now(),
    };
    window.__researchFetchLogs.push(entry);

    try {
      const response = await originalFetch(...args);
      entry.status = response.status;
      entry.ok = response.ok;
      try {
        const cloned = response.clone();
        cloned.text().then((text) => {
          entry.responseText = text;
          entry.finishedAt = Date.now();
        }).catch((error) => {
          entry.responseTextError = String(error);
          entry.finishedAt = Date.now();
        });
      } catch (error) {
        entry.responseTextError = String(error);
        entry.finishedAt = Date.now();
      }
      return response;
    } catch (error) {
      entry.fetchError = String(error);
      entry.finishedAt = Date.now();
      throw error;
    }
  };
})();
"""


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def wait_http_ready(url: str, *, timeout_sec: int = 180) -> None:
    deadline = time.time() + timeout_sec
    last_error = "unknown error"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if 200 <= int(response.status) < 500:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def post_json(url: str, payload: dict[str, Any], *, timeout_sec: int = 900) -> str:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        return response.read().decode("utf-8", errors="replace")


def extract_text_deltas(sse_text: str) -> str:
    parts: list[str] = []
    for block in re.split(r"\r?\n\r?\n", sse_text):
        event_name: str | None = None
        data_lines: list[str] = []
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())
        if event_name != "text_delta" or not data_lines:
            continue
        payload = json.loads("\n".join(data_lines))
        parts.append(str(payload.get("content") or ""))
    return "".join(parts)


def build_storage_seed(conversation_id: str) -> str:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conversation = {
        "id": conversation_id,
        "title": "SigLIP 2 架构图",
        "createdAt": now,
        "updatedAt": now,
        "workspacePath": WORKSPACE_PATH,
        "effectiveWorkspacePath": WORKSPACE_PATH,
        "workspaceTitle": "ResearchOS",
        "workspaceServerId": None,
        "assistantSessionId": conversation_id,
        "assistantBackendId": "claw",
        "assistantBackendLabel": "Claw（Rust CLI 内核）",
        "assistantMode": "build",
        "assistantReasoningLevel": "low",
        "mountedPaperId": PAPER_ID,
        "mountedPaperTitle": PAPER_TITLE,
        "mountedPaperIds": [PAPER_ID],
        "mountedPaperTitles": [PAPER_TITLE],
    }
    metas = [conversation]
    seed = {
        "researchos_conversations_index": metas,
        f"researchos_conversations_{conversation_id}": conversation,
        "researchos_assistant_active_conversation": conversation_id,
        "researchos.agent.mode": "build",
        "researchos.agent.reasoningLevel": "low",
        "researchos.agent.backendId": "claw",
        "researchos.agent.activeSkillIds": [],
    }
    return f"""
(() => {{
  const seed = {json.dumps(seed, ensure_ascii=False)};
  try {{
    localStorage.clear();
    for (const [key, value] of Object.entries(seed)) {{
      localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
    }}
  }} catch (_error) {{
  }}
}})();
"""


def wait_settled(page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Error:
        page.wait_for_timeout(1500)


def wait_for_fetch_match(page, pattern: str, *, timeout_ms: int = 180000) -> list[dict[str, Any]]:
    page.wait_for_function(
        """(pattern) => {
          const logs = window.__researchFetchLogs || [];
          return logs.some((entry) =>
            new RegExp(pattern).test(String(entry.url || "")) &&
            (!!entry.finishedAt || !!entry.fetchError || !!entry.responseTextError)
          );
        }""",
        arg=pattern,
        timeout=timeout_ms,
    )
    return page.evaluate("window.__researchFetchLogs || []")


def start_process(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    backend_env = os.environ.copy()
    backend_env["PYTHONPATH"] = str(REPO_ROOT)
    frontend_env = os.environ.copy()
    frontend_env["VITE_PROXY_TARGET"] = API_BASE_URL

    backend_cmd = [
        "pwsh",
        "-NoLogo",
        "-Command",
        f"& '{PYTHON}' -m uvicorn apps.api.main:app --host 127.0.0.1 --port {API_PORT}",
    ]
    frontend_cmd = [
        "pwsh",
        "-NoLogo",
        "-Command",
        f"npx vite --host 127.0.0.1 --port {FRONTEND_PORT} --strictPort",
    ]

    backend_proc: subprocess.Popen[str] | None = None
    frontend_proc: subprocess.Popen[str] | None = None
    report: dict[str, Any] = {}

    try:
        backend_proc = start_process(backend_cmd, cwd=REPO_ROOT, env=backend_env)
        wait_http_ready(f"{API_BASE_URL}/health")

        api_session_id = f"siglip-api-{uuid.uuid4().hex[:8]}"
        api_payload = {
            "parts": [
                {
                    "type": "text",
                    "text": "我引用了 SigLIP 2 这篇论文，请分析一下其架构图，并引用原图，不要重复展示图表。",
                }
            ],
            "agent_backend_id": "claw",
            "mode": "build",
            "workspace_path": WORKSPACE_PATH,
            "workspace_server_id": None,
            "reasoning_level": "low",
            "mounted_paper_ids": [PAPER_ID],
            "mounted_primary_paper_id": PAPER_ID,
            "active_skill_ids": [],
            "noReply": False,
        }
        api_sse = post_json(f"{API_BASE_URL}/session/{api_session_id}/message", api_payload)
        api_text = extract_text_deltas(api_sse)
        _assert("当前会话里已挂载主论文" not in api_text, "API 仍然返回欢迎语而不是论文分析")
        _assert(FIGURE_PATH_FRAGMENT in api_text, "API 输出未包含原图 Markdown")
        report["api"] = {
            "session_id": api_session_id,
            "text_preview": api_text[:2000],
        }

        frontend_proc = start_process(frontend_cmd, cwd=FRONTEND_DIR, env=frontend_env)
        wait_http_ready(f"{BASE_URL}/assistant")

        conversation_id = f"siglip-browser-{uuid.uuid4().hex[:8]}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1600, "height": 1100})
            context.add_init_script(build_storage_seed(conversation_id))
            context.add_init_script(FETCH_SPY_SCRIPT)
            page = context.new_page()

            console_errors: list[str] = []
            page_errors: list[str] = []
            page.on(
                "console",
                lambda msg: console_errors.append(f"{msg.type}: {msg.text}")
                if msg.type in {"error", "warning"}
                else None,
            )
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))

            page.goto(f"{BASE_URL}/assistant/{conversation_id}", wait_until="domcontentloaded")
            wait_settled(page)
            page.locator("textarea").first.wait_for(timeout=30000)

            terminal_button = page.get_by_role("button", name="终端").first
            terminal_button.click()
            terminal_logs = wait_for_fetch_match(page, r"/agent/workspace/terminal/session(?:\\?|$)")
            page.wait_for_timeout(2500)
            terminal_logs = page.evaluate("window.__researchFetchLogs || []")
            terminal_requests = [
                entry
                for entry in terminal_logs
                if TERMINAL_REQUEST_PATTERN.search(str(entry.get("url") or ""))
                and str(entry.get("method") or "").upper() == "POST"
            ]
            _assert(len(terminal_requests) == 1, f"单次打开终端应只创建 1 个 session，实际 {len(terminal_requests)} 个")
            page.locator(".xterm").first.wait_for(timeout=30000)
            _assert(page.locator("text=终端未就绪").count() == 0, "终端面板仍显示“终端未就绪”")
            terminal_tab_1 = page.locator("text=终端 1").count()
            terminal_tab_2 = page.locator("text=终端 2").count()
            _assert(terminal_tab_1 >= 1, "终端标签页未显示“终端 1”")
            _assert(terminal_tab_2 == 0, "单次打开终端后仍出现多余的“终端 2”标签页")

            textarea = page.locator("textarea").first
            textarea.fill("我引用了 SigLIP 2 这篇论文，请分析一下其架构图，并引用原图，不要重复展示图表。")
            page.locator('button[aria-label="发送消息"]').first.click()

            session_logs = wait_for_fetch_match(page, r"/session/[^/]+/message(?:\\?|$)")
            request_entries = [
                entry
                for entry in session_logs
                if SESSION_REQUEST_PATTERN.search(str(entry.get("url") or ""))
                and str(entry.get("method") or "").upper() == "POST"
            ]
            _assert(request_entries, "未捕获到 assistant 会话请求")
            request_entry = request_entries[-1]
            request_json = json.loads(str(request_entry.get("requestBody") or "{}"))
            _assert(request_json.get("mounted_paper_ids") == [PAPER_ID], "前端请求未携带 mounted_paper_ids")

            page.wait_for_selector(f'img[src*="{FIGURE_PATH_FRAGMENT}"]', timeout=180000)
            figure_images = page.locator(f'img[src*="{FIGURE_PATH_FRAGMENT}"]').count()
            _assert(figure_images == 1, f"原图应只渲染 1 次，实际 {figure_images} 次")
            _assert(page.locator("text=关联图表 6 项").count() == 0, "页面仍出现重复图表摘要提示")

            screenshot_path = ARTIFACT_DIR / "assistant-siglip-terminal-flow.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            report["browser"] = {
                "conversation_id": conversation_id,
                "terminal_request_count": len(terminal_requests),
                "terminal_tab_1_count": terminal_tab_1,
                "terminal_tab_2_count": terminal_tab_2,
                "mounted_paper_ids": request_json.get("mounted_paper_ids"),
                "image_count": figure_images,
                "console_errors": console_errors,
                "page_errors": page_errors,
                "screenshot": str(screenshot_path),
            }

            context.close()
            browser.close()

        report_path = ARTIFACT_DIR / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        stop_process(frontend_proc)
        stop_process(backend_proc)


if __name__ == "__main__":
    main()
