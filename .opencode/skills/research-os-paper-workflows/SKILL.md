---
name: research-os-paper-workflows
description: Use ResearchOS paper-library tools for paper search, import, skim, deep read, reasoning, figure extraction, and task follow-up.
---

# ResearchOS Paper Workflows

## When to use

Use this skill when the user is clearly asking about papers, literature review, arXiv import, library lookup, skim/deep reading, reasoning chains, figure extraction, or task follow-up for a paper workflow.

Do not use this skill for generic web lookup such as project introductions, product news, GitHub pages, or "go search the web".

## Preferred flow

1. If the user is asking "what papers exist" or "find related papers", start with:
   - `search_papers`
   - `search_arxiv`
   - `get_paper_detail`

2. If the user wants to add arXiv papers into the library, use:
   - `ingest_arxiv`

3. Only trigger heavy analysis when the user explicitly asks for it:
   - `skim_paper`
   - `deep_read_paper`
   - `reasoning_analysis`
   - `embed_paper`
   - `analyze_figures`

4. If a heavy task was started, tell the user the `task_id` or current state and, when needed, follow up with:
   - `get_workspace_task_status`
   - `task`

## Guardrails

- Do not auto-run skim, deep read, reasoning, embedding, or figure analysis just because you found a paper.
- If multiple candidate papers match, first show candidates and ask or infer the correct paper conservatively.
- Prefer the local ResearchOS library first when the user says "my papers", "library", "already imported", or "论文库".
- Prefer arXiv search when the user says "找论文", "搜 arXiv", or needs external paper discovery.
