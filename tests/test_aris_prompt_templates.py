from __future__ import annotations

from pathlib import Path

from packages.ai.project.amadeus_compat import workflow_runner_preamble
from packages.ai.project.aris_skill_templates import (
    clear_aris_skill_template_cache,
    load_aris_skill_template,
)
from packages.domain.enums import ProjectWorkflowType


def test_aris_skill_template_loader_and_compat_preamble(monkeypatch, tmp_path: Path):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "research-lit"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: research-lit",
                'description: "Template description"',
                "argument-hint: [topic]",
                "allowed-tools: Bash(*), Read, WebSearch",
                "---",
                "",
                "# Research Literature Review",
                "",
                "Use this exact ARIS template body.",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("RESEARCHOS_ARIS_REFERENCE_ROOT", str(skills_root))
    clear_aris_skill_template_cache()

    template = load_aris_skill_template("research-lit")
    assert template is not None
    assert template["frontmatter"]["name"] == "research-lit"
    assert "Use this exact ARIS template body." in template["body"]

    preamble = workflow_runner_preamble(ProjectWorkflowType.literature_review)
    assert "Reference skill: /research-lit" in preamble
    assert "Allowed tools: Bash(*), Read, WebSearch" in preamble
    assert "Use this exact ARIS template body." in preamble
