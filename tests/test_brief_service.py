from packages.ai.research.brief_service import _render_markdown_fragment, _repair_legacy_daily_brief_html


def test_render_markdown_fragment_renders_lists_and_bold():
    markdown = (
        "1. **今日焦点**：多模态模型开始进入应用阶段\n"
        "2. **技术亮点**：支持 [官方文档](https://example.com)\n\n"
        "结论段落。"
    )

    rendered = _render_markdown_fragment(markdown)

    assert "<ol>" in rendered
    assert "<strong>今日焦点</strong>" in rendered
    assert "**今日焦点**" not in rendered
    assert '<a href="https://example.com"' in rendered
    assert "<p>结论段落。</p>" in rendered


def test_render_markdown_fragment_escapes_raw_html():
    rendered = _render_markdown_fragment("<script>alert(1)</script>\n\n**安全输出**")

    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<p><strong>安全输出</strong></p>" in rendered


def test_repair_legacy_daily_brief_html_upgrades_plain_markdown_block():
    legacy_html = """
    <html>
    <head><style>.ai-insight { color: #15803d; }</style></head>
    <body>
      <div class="ai-insight">
        <div class="ai-insight-title">核心发现</div>
        <p style="margin: 6px 0; font-size: 13px; line-height: 1.6;">1. **今日焦点**：多模态推理</p>
      </div>
    </body>
    </html>
    """

    repaired = _repair_legacy_daily_brief_html(legacy_html)

    assert '<div class="ai-summary-content"><ol><li><strong>今日焦点</strong>：多模态推理</li></ol></div>' in repaired
    assert "**今日焦点**" not in repaired
    assert ".ai-summary-content p" in repaired
