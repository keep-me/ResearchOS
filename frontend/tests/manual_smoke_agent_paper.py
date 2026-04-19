from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


def main() -> int:
    base_url = "http://127.0.0.1:3002"
    target_paper_id = "43df8696-ee58-4a15-ada5-df5474f85032"
    report: dict[str, object] = {
        "assistant_loaded": False,
        "assistant_reply_received": False,
        "assistant_reply_preview": "",
        "paper_loaded": False,
        "paper_has_title": False,
        "paper_has_action_buttons": False,
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(120_000)

        console_errors: list[str] = []
        page.on(
            "console",
            lambda msg: console_errors.append(f"{msg.type}: {msg.text}")
            if msg.type in {"error", "warning"}
            else None,
        )

        page.goto(f"{base_url}/assistant", wait_until="domcontentloaded")
        page.wait_for_timeout(8_000)
        page.wait_for_selector("textarea")
        report["assistant_loaded"] = True

        textarea = page.locator("textarea").first
        textarea.fill("你好")
        textarea.press("Enter")

        try:
            assistant_block = page.locator("text=你好").locator("xpath=ancestor::div[contains(@class,'py-3')]").last
            assistant_block.wait_for(timeout=15_000)
        except PlaywrightTimeoutError:
            pass

        try:
            page.wait_for_timeout(20_000)
            content = page.content()
            if "网络连接失败" not in content and "后端暂未就绪" not in content:
                assistant_cards = page.locator("div").filter(has_text="You")
                if assistant_cards.count() >= 1:
                    report["assistant_reply_received"] = "你好" in content and ("内部推理" in content or "模型正在整理思路" in content or len(content) > 0)
            reply_candidates = page.locator("div.prose-custom, div.whitespace-pre-wrap")
            if reply_candidates.count() > 0:
                report["assistant_reply_preview"] = reply_candidates.last.inner_text()[:240]
        except PlaywrightTimeoutError:
            pass

        page.goto(f"{base_url}/papers/{target_paper_id}", wait_until="domcontentloaded")
        page.wait_for_timeout(4_000)
        content = page.content()
        report["paper_loaded"] = True
        report["paper_has_title"] = "Multi-View Feature Fusion and Visual Prompt for Remote Sensing Image Captioning" in content
        report["paper_has_action_buttons"] = all(
            label in content for label in ("粗读", "精读", "推理链", "三轮分析")
        )

        browser.close()

        report["console_errors"] = console_errors[-20:]

    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
