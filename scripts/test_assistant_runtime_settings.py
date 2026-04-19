from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from playwright.sync_api import Error, TimeoutError as PlaywrightTimeoutError, sync_playwright


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:4317").rstrip("/")
ARTIFACT_DIR = Path("artifacts") / "assistant-runtime-settings"
REQUEST_PATTERN = re.compile(r"/session/[^/]+/message(?:\?|$)")
BACKEND_STORAGE_KEY = "researchos.agent.backendId"
MODE_STORAGE_KEY = "researchos.agent.mode"
REASONING_STORAGE_KEY = "researchos.agent.reasoningLevel"
ACTIVE_SKILLS_STORAGE_KEY = "researchos.agent.activeSkillIds"


def wait_settled(page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Error:
        page.wait_for_timeout(1500)


def build_storage_seed(seed: dict[str, Any]) -> str:
    return f"""
(() => {{
  const seed = {json.dumps(seed, ensure_ascii=False)};
  try {{
    localStorage.clear();
    for (const [key, value] of Object.entries(seed)) {{
      if (value === null || value === undefined) continue;
      localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
    }}
  }} catch (_error) {{
  }}
}})();
"""


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
      entry.headers = {};
      try {
        response.headers.forEach((value, key) => {
          entry.headers[key] = value;
        });
      } catch (_error) {
      }
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


def extract_done_payload(sse_text: str | None) -> dict[str, Any] | None:
    if not sse_text:
      return None

    blocks = re.split(r"\r?\n\r?\n", sse_text)
    done_payload: dict[str, Any] | None = None
    for block in blocks:
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
        if event_name != "done" or not data_lines:
            continue
        try:
            done_payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            done_payload = {"raw": "\n".join(data_lines)}
    return done_payload


def last_session_request(fetch_logs: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [
        entry
        for entry in fetch_logs
        if REQUEST_PATTERN.search(str(entry.get("url") or ""))
    ]
    if not matches:
        raise AssertionError("未捕获到 /session/{id}/message 请求")
    return matches[-1]


def read_select_snapshot(page) -> list[dict[str, Any]]:
    return page.locator("select").evaluate_all(
        """(nodes) => nodes.map((node) => {
          const options = Array.from(node.options || []).map((option) => ({
            value: option.value,
            text: (option.textContent || "").trim(),
          }));
          const label = node.closest("label");
          return {
            value: node.value,
            disabled: !!node.disabled,
            labelText: label ? (label.innerText || "").trim() : "",
            optionCount: options.length,
            options: options.slice(0, 12),
          };
        })"""
    )


def find_setting_labels(selects: list[dict[str, Any]]) -> dict[str, bool]:
    labels = [(item.get("labelText") or "").strip() for item in selects]
    return {
        "has_reasoning_select": any(label.startswith("推理") for label in labels),
        "has_mode_select": any(label.startswith("模式") for label in labels),
        "has_target_select": any(label.startswith("目标") for label in labels),
        "has_backend_select": any(
            label.startswith("后端") or label.startswith("backend")
            for label in (label.lower() for label in labels)
        ),
    }


def wait_for_session_request_completion(page) -> dict[str, Any]:
    page.wait_for_function(
        """() => {
          const logs = window.__researchFetchLogs || [];
          return logs.some((entry) =>
            /\\/session\\/[^/]+\\/message(?:\\?|$)/.test(String(entry.url || "")) &&
            (!!entry.finishedAt || !!entry.fetchError || !!entry.responseTextError)
          );
        }""",
        timeout=180000,
    )
    fetch_logs = page.evaluate("window.__researchFetchLogs || []")
    return last_session_request(fetch_logs)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_results(results: list[dict[str, Any]]) -> None:
    result_map = {str(item.get("name") or ""): item for item in results}

    for name, result in result_map.items():
        flags = result.get("visible_setting_flags") or {}
        request_json = result.get("request_json") or {}
        done_payload = result.get("done_payload") or {}
        _assert(flags.get("has_backend_select") is False, f"{name}: 不应再展示 backend 选择器")
        _assert(request_json.get("agent_backend_id") == "claw", f"{name}: 请求中的 agent_backend_id 必须固定为 claw")
        _assert(done_payload.get("agent_backend_id") == "claw", f"{name}: done 事件中的 agent_backend_id 必须固定为 claw")

    default_result = result_map["default_claw"]
    default_request = default_result.get("request_json") or {}
    _assert(default_request.get("mode") == "plan", "default_claw: 模式切换后请求应传 mode=plan")
    _assert(default_request.get("reasoning_level") == "high", "default_claw: 推理切换后请求应传 reasoning_level=high")
    _assert(default_request.get("workspace_server_id") is None, "default_claw: 默认目标应保持本地后端")

    legacy_result = result_map["legacy_storage_forced_to_claw"]
    legacy_request = legacy_result.get("request_json") or {}
    _assert(
        legacy_request.get("mode") == "build",
        "legacy_storage_forced_to_claw: 旧 localStorage 注入后模式仍应以当前选择为准",
    )
    _assert(
        legacy_request.get("reasoning_level") == "low",
        "legacy_storage_forced_to_claw: 旧 localStorage 注入后推理仍应以当前选择为准",
    )

    target_result = result_map["switch_target_xdu"]
    target_request = target_result.get("request_json") or {}
    target_done = target_result.get("done_payload") or {}
    _assert(
        target_request.get("workspace_server_id") == "xdu",
        "switch_target_xdu: 切换目标后请求应传 workspace_server_id=xdu",
    )
    target_execution_mode = str(target_done.get("execution_mode") or "").strip().lower()
    target_done_server_id = str(target_done.get("workspace_server_id") or "").strip() or None
    target_fallback_reason = str(target_done.get("fallback_reason") or "").strip() or None
    _assert(
        (
            target_execution_mode == "ssh"
            and target_done_server_id == "xdu"
        )
        or (
            target_execution_mode == "local"
            and target_fallback_reason is not None
        ),
        (
            "switch_target_xdu: done 事件必须体现远端接管结果，"
            "要么真正走 ssh，要么带上明确 fallback_reason，不能静默退回本地"
        ),
    )


def run_scenario(
    playwright,
    name: str,
    storage_seed: dict[str, Any],
    mode_value: str,
    reasoning_value: str,
    target_value: str | None = None,
) -> dict[str, Any]:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1600, "height": 1100})
    context.add_init_script(build_storage_seed(storage_seed))
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

    page.goto(f"{BASE_URL}/assistant", wait_until="domcontentloaded")
    wait_settled(page)
    page.locator("textarea").first.wait_for(timeout=30000)
    page.locator('button[aria-label="发送消息"]').first.wait_for(timeout=30000)

    select_snapshot_before = read_select_snapshot(page)
    labels_before = find_setting_labels(select_snapshot_before)

    reasoning_select = page.locator('label:has-text("推理") select').first
    mode_select = page.locator('label:has-text("模式") select').first
    target_select = page.locator('[data-testid="assistant-target-select"]').first
    reasoning_select.select_option(reasoning_value)
    mode_select.select_option(mode_value)
    if target_value:
        target_select.select_option(target_value)
    page.wait_for_timeout(700)

    message = f"请只回复 ok。scenario={name}"
    textarea = page.locator("textarea").first
    textarea.fill(message)
    page.locator('button[aria-label="发送消息"]').first.click()

    request_entry = wait_for_session_request_completion(page)
    request_body = request_entry.get("requestBody")
    request_json = json.loads(request_body) if isinstance(request_body, str) and request_body else None
    response_text = request_entry.get("responseText")
    done_payload = extract_done_payload(response_text)

    screenshot_path = ARTIFACT_DIR / f"{name}.png"
    page.screenshot(path=str(screenshot_path), full_page=True)

    result = {
        "name": name,
        "storage_seed": storage_seed,
        "visible_selects": select_snapshot_before,
        "visible_setting_flags": labels_before,
        "request_url": request_entry.get("url"),
        "request_status": request_entry.get("status"),
        "request_json": request_json,
        "done_payload": done_payload,
        "response_text_length": len(response_text or ""),
        "response_text_preview": (response_text or "")[:2000],
        "console_errors": console_errors,
        "page_errors": page_errors,
        "screenshot": str(screenshot_path.resolve()),
        "final_url": page.url,
    }

    context.close()
    browser.close()
    return result


def main() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    scenarios = [
        {
            "name": "default_claw",
            "storage_seed": {},
            "mode": "plan",
            "reasoning": "high",
        },
        {
            "name": "legacy_storage_forced_to_claw",
            "storage_seed": {
                BACKEND_STORAGE_KEY: "researchos_native",
                MODE_STORAGE_KEY: "build",
                REASONING_STORAGE_KEY: "default",
                ACTIVE_SKILLS_STORAGE_KEY: [],
            },
            "mode": "build",
            "reasoning": "low",
        },
        {
            "name": "switch_target_xdu",
            "storage_seed": {},
            "mode": "build",
            "reasoning": "low",
            "target": "xdu",
        },
    ]

    with sync_playwright() as playwright:
        results = [
            run_scenario(
                playwright,
                scenario["name"],
                scenario["storage_seed"],
                scenario["mode"],
                scenario["reasoning"],
                scenario.get("target"),
            )
            for scenario in scenarios
        ]

    validate_results(results)

    summary = {
        "base_url": BASE_URL,
        "results": results,
    }
    report_path = ARTIFACT_DIR / "report.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"REPORT_PATH={report_path.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except PlaywrightTimeoutError as exc:
        raise SystemExit(f"Playwright timeout: {exc}") from exc
