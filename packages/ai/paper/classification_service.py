"""
Automatic paper classification by topic keywords + citation graph signals.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from packages.domain.schemas import PaperAutoClassifyReq
from packages.storage.db import session_scope
from packages.storage.models import Citation, Paper, PaperTopic, TopicSubscription
from packages.storage.repositories import PaperRepository, TopicRepository


_TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._+\-]{1,}")
_PHRASE_RE = re.compile(r'"([^"]+)"')
_QUERY_TOKEN_RE = re.compile(
    r"\(|\)|ANDNOT|AND|OR|(?:[a-zA-Z]+:)?\"[^\"]+\"|(?:[a-zA-Z]+:)?[^\s()]+",
    re.IGNORECASE,
)
_STOPWORDS = {
    "and",
    "or",
    "not",
    "the",
    "for",
    "with",
    "from",
    "into",
    "that",
    "this",
    "are",
    "all",
    "abs",
    "ti",
    "au",
    "cat",
    "co",
    "jr",
    "rn",
    "id",
    "model",
    "models",
    "language",
    "vision",
    "image",
    "images",
    "text",
    "learning",
    "machine",
    "deep",
    "large",
    "foundation",
    "based",
    "using",
    "with",
    "without",
    "towards",
    "via",
    "agent",
    "agents",
    "system",
    "systems",
    "framework",
    "approach",
    "approaches",
    "method",
    "methods",
    "analysis",
    "study",
    "paper",
    "task",
    "tasks",
    "dataset",
    "datasets",
    "benchmark",
    "benchmarks",
    "general",
    "vision-language",
    "visionlanguage",
    "llm",
    "llms",
    "vlm",
    "vlms",
}


@dataclass
class TopicFeature:
    topic_id: str
    name: str
    name_lc: str
    query_lc: str
    tokens: set[str]
    phrases: tuple[str, ...]


@dataclass
class QueryMatchResult:
    matched: bool
    score: float


def _to_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_text(text: str) -> str:
    return re.sub(r"[\-_\/]+", " ", (text or "").lower())


def _tokenize(text: str) -> set[str]:
    out: set[str] = set()
    for token in _TOKEN_RE.findall(_normalize_text(text)):
        if len(token) < 2 or token in _STOPWORDS:
            continue
        out.add(token)
    return out


def _extract_phrases(text: str) -> tuple[str, ...]:
    phrases: list[str] = []
    seen: set[str] = set()
    for raw in _PHRASE_RE.findall(text or ""):
        phrase = " ".join(_normalize_text(raw).split())
        if len(phrase.split()) < 2 or phrase in seen:
            continue
        seen.add(phrase)
        phrases.append(phrase)
    return tuple(phrases)


def _query_uses_expression_syntax(query: str) -> bool:
    query_lc = (query or "").lower()
    markers = (" and ", " or ", " andnot ", "all:", "ti:", "abs:", "cat:", "(", ")", '"')
    return any(marker in query_lc for marker in markers)


def _tokenize_query(query: str) -> list[str]:
    return [token for token in _QUERY_TOKEN_RE.findall(query or "") if token.strip()]


def _to_rpn(tokens: list[str]) -> list[str]:
    precedence = {"OR": 1, "AND": 2, "ANDNOT": 3}
    output: list[str] = []
    ops: list[str] = []
    for token in tokens:
        upper = token.upper()
        if upper in precedence:
            while ops and ops[-1] != "(" and precedence.get(ops[-1], 0) >= precedence[upper]:
                output.append(ops.pop())
            ops.append(upper)
        elif token == "(":
            ops.append(token)
        elif token == ")":
            while ops and ops[-1] != "(":
                output.append(ops.pop())
            if ops and ops[-1] == "(":
                ops.pop()
        else:
            output.append(token)
    while ops:
        output.append(ops.pop())
    return output


def _split_field_term(token: str) -> tuple[str, str]:
    if ":" in token:
        field, value = token.split(":", 1)
        field_lc = field.lower()
        if field_lc in {"all", "ti", "abs", "cat", "au"}:
            return field_lc, value
    return "all", token


def _match_query_term(
    token: str,
    *,
    full_text_norm: str,
    title_text_norm: str,
    abstract_text_norm: str,
    categories: list[str],
    authors: list[str],
) -> QueryMatchResult:
    field, raw_value = _split_field_term(token)
    value = raw_value.strip().strip('"')
    normalized = " ".join(_normalize_text(value).split())
    if not normalized:
        return QueryMatchResult(matched=False, score=0.0)

    if field == "cat":
        matched = any(
            normalized == cat.lower() or cat.lower().startswith(normalized)
            for cat in categories
        )
    elif field == "ti":
        matched = normalized in title_text_norm
    elif field == "abs":
        matched = normalized in abstract_text_norm
    elif field == "au":
        matched = any(normalized in _normalize_text(author) for author in authors)
    else:
        matched = normalized in full_text_norm

    term_tokens = _tokenize(normalized)
    score = max(1.0, min(3.0, len(term_tokens) * 0.9 or len(normalized.split()) * 0.9))
    return QueryMatchResult(matched=matched, score=score if matched else 0.0)


def _match_query_expression(
    query: str,
    *,
    full_text_norm: str,
    title_text_norm: str,
    abstract_text_norm: str,
    categories: list[str],
    authors: list[str],
) -> QueryMatchResult:
    if not _query_uses_expression_syntax(query):
        return QueryMatchResult(matched=False, score=0.0)

    tokens = _tokenize_query(query)
    if not tokens:
        return QueryMatchResult(matched=False, score=0.0)

    stack: list[QueryMatchResult] = []
    for token in _to_rpn(tokens):
        upper = token.upper()
        if upper in {"AND", "OR", "ANDNOT"}:
            if len(stack) < 2:
                return QueryMatchResult(matched=False, score=0.0)
            right = stack.pop()
            left = stack.pop()
            if upper == "AND":
                stack.append(
                    QueryMatchResult(
                        matched=left.matched and right.matched,
                        score=(left.score + right.score) if (left.matched and right.matched) else 0.0,
                    )
                )
            elif upper == "OR":
                matched = left.matched or right.matched
                score = 0.0
                if left.matched:
                    score += left.score
                if right.matched:
                    score += right.score
                stack.append(QueryMatchResult(matched=matched, score=score))
            else:
                stack.append(
                    QueryMatchResult(
                        matched=left.matched and not right.matched,
                        score=left.score if (left.matched and not right.matched) else 0.0,
                    )
                )
            continue

        stack.append(
            _match_query_term(
                token,
                full_text_norm=full_text_norm,
                title_text_norm=title_text_norm,
                abstract_text_norm=abstract_text_norm,
                categories=categories,
                authors=authors,
            )
        )

    return stack[-1] if len(stack) == 1 else QueryMatchResult(matched=False, score=0.0)


class PaperClassificationService:
    """Classify papers into existing topics."""

    def auto_classify(self, req: PaperAutoClassifyReq) -> dict:
        with session_scope() as session:
            paper_repo = PaperRepository(session)
            topic_repo = TopicRepository(session)
            # Folders are manual buckets, not scheduled subscriptions. Their `enabled`
            # flag should not block auto-classification after imports.
            topics = topic_repo.list_topics(enabled_only=False, kind="folder")
            if not topics:
                return {
                    "requested": 0,
                    "scanned_papers": 0,
                    "classified_papers": 0,
                    "linked_topics": 0,
                    "dry_run": req.dry_run,
                    "items": [],
                    "message": "no topics configured",
                }

            topic_features = self._build_topic_features(topics)
            topic_name_map = {t.id: t.name for t in topics}

            target_papers = self._load_target_papers(
                session=session,
                paper_repo=paper_repo,
                req=req,
            )
            if not target_papers:
                return {
                    "requested": len(req.paper_ids),
                    "scanned_papers": 0,
                    "classified_papers": 0,
                    "linked_topics": 0,
                    "dry_run": req.dry_run,
                    "items": [],
                }

            target_ids = [str(p.id) for p in target_papers]
            existing_topic_map = self._load_existing_topic_links(session, target_ids)
            graph_votes = (
                self._build_graph_votes(session, target_ids) if req.use_graph else {}
            )

            items: list[dict[str, Any]] = []
            classified_papers = 0
            linked_topics = 0

            for paper in target_papers:
                pid = str(paper.id)
                if req.only_unclassified and existing_topic_map.get(pid):
                    continue

                text_scores = self._score_by_keywords(paper, topic_features)
                graph_scores = graph_votes.get(pid, {})

                total_scores: dict[str, float] = defaultdict(float)
                for tid, score in text_scores.items():
                    total_scores[tid] += score
                for tid, score in graph_scores.items():
                    total_scores[tid] += score

                ranked = sorted(total_scores.items(), key=lambda x: x[1], reverse=True)
                if not ranked:
                    continue

                selected: list[dict[str, Any]] = []
                for topic_id, total_score in ranked:
                    if total_score < req.min_score:
                        break
                    if topic_id in existing_topic_map.get(pid, set()):
                        continue

                    entry = {
                        "topic_id": topic_id,
                        "topic_name": topic_name_map.get(topic_id, topic_id),
                        "score": round(total_score, 3),
                        "keyword_score": round(text_scores.get(topic_id, 0.0), 3),
                        "graph_score": round(graph_scores.get(topic_id, 0.0), 3),
                    }
                    selected.append(entry)
                    if len(selected) >= req.max_topics_per_paper:
                        break

                if not selected:
                    continue

                if not req.dry_run:
                    for topic in selected:
                        paper_repo.link_to_topic(pid, topic["topic_id"])

                linked_topics += len(selected)
                classified_papers += 1
                items.append(
                    {
                        "paper_id": pid,
                        "title": paper.title,
                        "matched_topics": selected,
                    }
                )

            return {
                "requested": len(req.paper_ids) if req.paper_ids else len(target_papers),
                "scanned_papers": len(target_papers),
                "classified_papers": classified_papers,
                "linked_topics": linked_topics,
                "dry_run": req.dry_run,
                "items": items,
            }

    @staticmethod
    def _build_topic_features(topics: list[Any]) -> dict[str, TopicFeature]:
        out: dict[str, TopicFeature] = {}
        for topic in topics:
            name = (topic.name or "").strip()
            query = (topic.query or "").strip()
            phrases = _extract_phrases(f"{name} {query}")
            tokens = _tokenize(f"{name} {query}")
            out[topic.id] = TopicFeature(
                topic_id=topic.id,
                name=name,
                name_lc=name.lower(),
                query_lc=query.lower(),
                tokens=tokens,
                phrases=phrases,
            )
        return out

    @staticmethod
    def _load_target_papers(
        *,
        session,
        paper_repo: PaperRepository,
        req: PaperAutoClassifyReq,
    ) -> list[Paper]:
        if req.paper_ids:
            return paper_repo.list_by_ids([str(x) for x in req.paper_ids[: req.max_papers]])

        if req.only_unclassified:
            subq = (
                select(PaperTopic.paper_id)
                .join(TopicSubscription, TopicSubscription.id == PaperTopic.topic_id)
                .where(TopicSubscription.kind == "folder")
                .distinct()
            )
            q = (
                select(Paper)
                .where(Paper.id.notin_(subq))
                .order_by(Paper.created_at.desc())
                .limit(req.max_papers)
            )
            return list(session.execute(q).scalars())

        q = select(Paper).order_by(Paper.created_at.desc()).limit(req.max_papers)
        return list(session.execute(q).scalars())

    @staticmethod
    def _load_existing_topic_links(session, paper_ids: list[str]) -> dict[str, set[str]]:
        result: dict[str, set[str]] = defaultdict(set)
        if not paper_ids:
            return result
        rows = session.execute(
            select(PaperTopic.paper_id, PaperTopic.topic_id)
            .join(TopicSubscription, TopicSubscription.id == PaperTopic.topic_id)
            .where(
                PaperTopic.paper_id.in_(paper_ids),
                TopicSubscription.kind == "folder",
            )
        ).all()
        for paper_id, topic_id in rows:
            result[str(paper_id)].add(str(topic_id))
        return result

    @staticmethod
    def _build_graph_votes(session, target_ids: list[str]) -> dict[str, dict[str, float]]:
        if not target_ids:
            return {}

        target_set = set(target_ids)
        edges = session.execute(
            select(Citation.source_paper_id, Citation.target_paper_id).where(
                Citation.source_paper_id.in_(target_ids) | Citation.target_paper_id.in_(target_ids)
            )
        ).all()

        neighbors: dict[str, set[str]] = defaultdict(set)
        neighbor_ids: set[str] = set()
        for source_id, target_id in edges:
            source_id = str(source_id)
            target_id = str(target_id)
            if source_id in target_set and target_id != source_id:
                neighbors[source_id].add(target_id)
                neighbor_ids.add(target_id)
            if target_id in target_set and source_id != target_id:
                neighbors[target_id].add(source_id)
                neighbor_ids.add(source_id)

        if not neighbor_ids:
            return {}

        neighbor_topic_map: dict[str, set[str]] = defaultdict(set)
        rows = session.execute(
            select(PaperTopic.paper_id, PaperTopic.topic_id)
            .join(TopicSubscription, TopicSubscription.id == PaperTopic.topic_id)
            .where(
                PaperTopic.paper_id.in_(neighbor_ids),
                TopicSubscription.kind == "folder",
            )
        ).all()
        for paper_id, topic_id in rows:
            neighbor_topic_map[str(paper_id)].add(str(topic_id))

        votes: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for paper_id, nids in neighbors.items():
            for nid in nids:
                for topic_id in neighbor_topic_map.get(nid, set()):
                    votes[paper_id][topic_id] += 1.5

        return {pid: dict(v) for pid, v in votes.items()}

    @staticmethod
    def _score_by_keywords(
        paper: Paper,
        topic_features: dict[str, TopicFeature],
    ) -> dict[str, float]:
        meta = paper.metadata_json or {}
        title = str(paper.title or "")
        abstract = str(paper.abstract or "")
        title_zh = str(meta.get("title_zh", "") or "")
        abstract_zh = str(meta.get("abstract_zh", "") or "")
        keywords = _to_str_list(meta.get("keywords"))
        categories = _to_str_list(meta.get("categories"))
        authors = _to_str_list(meta.get("authors"))

        full_text = " ".join(
            [
                title,
                abstract,
                title_zh,
                abstract_zh,
                " ".join(keywords),
                " ".join(categories),
                " ".join(authors),
                str(paper.arxiv_id or ""),
            ]
        )
        full_text_norm = _normalize_text(full_text)
        title_text_norm = _normalize_text(title)
        abstract_text_norm = _normalize_text(abstract)
        all_tokens = _tokenize(full_text_norm)
        title_tokens = _tokenize(title_text_norm)

        scores: dict[str, float] = defaultdict(float)
        for topic_id, topic in topic_features.items():
            expr_match = _match_query_expression(
                topic.query_lc,
                full_text_norm=full_text_norm,
                title_text_norm=title_text_norm,
                abstract_text_norm=abstract_text_norm,
                categories=categories,
                authors=authors,
            )
            if _query_uses_expression_syntax(topic.query_lc):
                if not expr_match.matched:
                    continue
                scores[topic_id] += expr_match.score

            phrase_hits = 0
            for phrase in topic.phrases:
                if phrase in full_text_norm:
                    scores[topic_id] += 2.6
                    phrase_hits += 1
                    if phrase in title_text_norm:
                        scores[topic_id] += 0.8

            overlap = all_tokens & topic.tokens
            if phrase_hits > 0:
                if overlap:
                    scores[topic_id] += min(2.4, len(overlap) * 0.45)
            elif len(overlap) >= 2:
                scores[topic_id] += min(3.0, len(overlap) * 0.75)

            title_overlap = title_tokens & topic.tokens
            if phrase_hits > 0:
                if title_overlap:
                    scores[topic_id] += min(1.8, len(title_overlap) * 0.4)
            elif len(title_overlap) >= 2:
                scores[topic_id] += min(2.2, len(title_overlap) * 0.65)

            normalized_name = " ".join(_normalize_text(topic.name_lc).split())
            if normalized_name and len(normalized_name.split()) >= 2 and normalized_name in full_text_norm:
                scores[topic_id] += 2.0

            for cat in categories:
                cat_lc = cat.lower()
                if cat_lc and (cat_lc in topic.name_lc or cat_lc in topic.query_lc):
                    scores[topic_id] += 1.2
                    break
                prefix = cat_lc.split(".", 1)[0]
                if prefix and prefix in topic.tokens and prefix not in _STOPWORDS:
                    scores[topic_id] += 0.5
                    break

        return dict(scores)
