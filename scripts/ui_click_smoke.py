from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Error, Page, sync_playwright

BASE_URL = "http://localhost:4317"
ARTIFACT_DIR = Path("artifacts") / "ui-smoke"


def wait_settled(page: Page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Error:
        page.wait_for_timeout(1200)


def visible_text(page: Page) -> str:
    return page.locator("body").inner_text(timeout=10000)


def assert_no_crash(page: Page, label: str) -> None:
    text = visible_text(page)
    banned = [
        "页面遇到了错误",
        "Failed to fetch dynamically imported module",
        "网络连接失败，请检查后端服务是否启动",
    ]
    for item in banned:
        if item in text:
            raise AssertionError(f"{label}: found banned text: {item}")


def click_nav(page: Page, title: str, expected_path: str, heading: str) -> None:
    page.locator(f'a[title="{title}"]').first.click()
    page.wait_for_url(f"**{expected_path}", timeout=15000)
    wait_settled(page)
    page.get_by_role("heading", name=heading).first.wait_for(timeout=15000)
    assert_no_crash(page, title)


def first_visible(page: Page, selectors: list[str]):
    for selector in selectors:
        locator = page.locator(selector).first
        if locator.count() > 0:
            return locator
    raise AssertionError(f"selector not found: {selectors}")


def main() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    network_404s: list[str] = []
    results: dict[str, object] = {"steps": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on(
            "console",
            lambda msg: (
                console_errors.append(f"{msg.type}: {msg.text}")
                if msg.type in {"error", "warning"}
                else None
            ),
        )
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on(
            "response",
            lambda response: network_404s.append(response.url) if response.status == 404 else None,
        )

        page.wait_for_timeout(1200)
        page.goto(BASE_URL, wait_until="domcontentloaded")
        wait_settled(page)
        try:
            page.get_by_role("button", name="终端").first.wait_for(timeout=30000)
        except Exception as exc:
            page.screenshot(path=str(ARTIFACT_DIR / "assistant-load-failed.png"), full_page=True)
            raise AssertionError(
                json.dumps(
                    {
                        "stage": "assistant-load",
                        "error": str(exc),
                        "url": page.url,
                        "body": visible_text(page)[:3000],
                        "console_errors": console_errors,
                        "page_errors": page_errors,
                        "network_404s": network_404s,
                        "screenshot": str((ARTIFACT_DIR / "assistant-load-failed.png").resolve()),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            ) from exc
        assert_no_crash(page, "assistant-initial")
        results["steps"].append("assistant-loaded")

        collapse_button = first_visible(
            page, ['button[aria-label="收起侧栏"]', 'button[title="收起侧栏"]']
        )
        collapse_button.click()
        page.wait_for_timeout(400)
        first_visible(page, ['button[aria-label="展开侧栏"]', 'button[title="展开侧栏"]']).wait_for(
            timeout=10000
        )
        results["steps"].append("sidebar-collapsed")

        first_visible(page, ['button[aria-label="展开侧栏"]', 'button[title="展开侧栏"]']).click()
        page.wait_for_timeout(400)
        page.locator('a[title="论文收集"]').first.wait_for(timeout=10000)
        results["steps"].append("sidebar-expanded")

        click_nav(page, "论文收集", "/collect", "论文收集")
        results["steps"].append("collect-loaded")

        click_nav(page, "论文库", "/papers", "论文库")
        results["steps"].append("papers-loaded")

        click_nav(page, "ARIS", "/projects", "ARIS")
        results["steps"].append("projects-loaded")

        click_nav(page, "任务中心", "/tasks", "任务中心")
        results["steps"].append("tasks-loaded")

        click_nav(page, "设置", "/settings", "系统配置")
        results["steps"].append("settings-loaded")

        page.get_by_role("button", name="MCP 服务").first.click()
        wait_settled(page)
        page.get_by_text("MCP 运行状态").first.wait_for(timeout=10000)
        assert_no_crash(page, "settings-mcp")
        results["steps"].append("settings-mcp-opened")

        page.locator('a[title="研究助手"]').first.click()
        page.wait_for_url("**/assistant", timeout=15000)
        wait_settled(page)
        page.get_by_role("button", name="终端").first.wait_for(timeout=15000)
        assert_no_crash(page, "assistant-reload")
        results["steps"].append("assistant-reloaded")

        page.get_by_role("button", name="终端").first.click()
        page.get_by_text("Terminal").first.wait_for(timeout=10000)
        assert_no_crash(page, "assistant-terminal")
        results["steps"].append("assistant-terminal-opened")

        page.get_by_role("button", name="集成").first.click()
        page.get_by_text("MCP 服务").first.wait_for(timeout=10000)
        assert_no_crash(page, "assistant-mcp-modal")
        results["steps"].append("assistant-mcp-opened")

        page.screenshot(path=str(ARTIFACT_DIR / "final.png"), full_page=True)

        browser.close()

    results["console_errors"] = console_errors
    results["page_errors"] = page_errors
    results["network_404s"] = network_404s
    results["screenshot"] = str((ARTIFACT_DIR / "final.png").resolve())
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
