---
name: research-os-web-research
description: Handle generic web research with website/project/news lookup before falling back to paper tools.
---

# ResearchOS Web Research

## When to use

Use this skill for generic web research tasks such as:

- looking up a project, product, company, lab, or GitHub repo
- searching the web for introductions, docs, or news
- comparing information from websites

Typical requests include:

- "去网上找一找 ..."
- "搜一下官网"
- "查一下这个项目"

## Preferred tool

Use `search_web` first.

## Guardrails

- Do not start with `search_papers` or `search_arxiv` unless the request explicitly asks for papers or literature.
- Do not auto-run skim/deep/figure/reasoning tools for a generic web query.
- If the user later pivots from web research to papers, then switch to the paper workflow tools.
