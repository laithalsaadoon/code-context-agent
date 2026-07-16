"""BM25 full-text search index for codebase analysis."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from rank_bm25 import BM25Okapi

if TYPE_CHECKING:
    from pathlib import Path


class BM25Index:
    """BM25 full-text search index built from file contents."""

    def __init__(self) -> None:  # noqa: D107
        self._documents: list[dict[str, Any]] = []  # [{path, content, tokens}]
        self._bm25: BM25Okapi | None = None

    @classmethod
    def from_files(cls, file_paths: list[str], repo_path: Path) -> BM25Index:
        """Build index from file paths relative to repo root."""
        index = cls()
        for fp in file_paths:
            full = repo_path / fp
            if not full.is_file():
                continue
            try:
                content = full.read_text(errors="replace")
            except (OSError, UnicodeDecodeError):
                continue
            tokens = _tokenize(content)
            if tokens:
                index._documents.append({"path": fp, "content": content, "tokens": tokens})

        if index._documents:
            corpus = [doc["tokens"] for doc in index._documents]
            index._bm25 = BM25Okapi(corpus)
        return index

    def search(self, query: str, top_k: int = 20) -> list[dict[str, Any]]:
        """Search the index with a text query."""
        if not self._bm25 or not self._documents:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores = self._bm25.get_scores(query_tokens)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in ranked[:top_k]:
            if score <= 0:
                break
            doc = self._documents[idx]
            matching_lines = _find_matching_lines(doc["content"], query_tokens, max_lines=3)
            results.append(
                {
                    "path": doc["path"],
                    "score": round(float(score), 4),
                    "matching_lines": matching_lines,
                },
            )
        return results

    @property
    def document_count(self) -> int:
        """Return the number of indexed documents."""
        return len(self._documents)


def _tokenize(text: str) -> list[str]:
    """Simple tokenization: split on non-alphanumeric, lowercase, filter short tokens."""
    # Split camelCase and snake_case
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = text.replace("_", " ")
    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return [t for t in tokens if len(t) > 1]


def _find_matching_lines(content: str, query_tokens: list[str], max_lines: int = 3) -> list[dict[str, Any]]:
    """Find lines containing query tokens."""
    results = []
    query_set = set(query_tokens)
    for i, line in enumerate(content.splitlines(), 1):
        line_tokens = set(_tokenize(line))
        overlap = query_set & line_tokens
        if overlap:
            results.append({"line": i, "text": line.strip()[:200], "matched_tokens": sorted(overlap)})
            if len(results) >= max_lines:
                break
    return results
