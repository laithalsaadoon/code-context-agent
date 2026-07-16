"""BM25 ranked text search tool for the analysis agent."""

from __future__ import annotations

import json
from pathlib import Path

from strands import tool

from code_context_agent.tools.search.bm25 import BM25Index

# Module-level index cache
_indexes: dict[str, BM25Index] = {}


@tool
def bm25_search(query: str, repo_path: str, top_k: int = 20, rebuild: bool = False) -> str:
    """Ranked text search using BM25 algorithm.

    Unlike ripgrep (exact pattern matching), BM25 ranks results by relevance —
    terms appearing in fewer files score higher (TF-IDF-like).

    Use for: finding code related to a concept (e.g., "authentication flow"),
    semantic-like search without embeddings, ranked results when ripgrep
    returns too many matches.

    Args:
        query: Search query (natural language or code terms).
        repo_path: Absolute path to the repository.
        top_k: Maximum results to return.
        rebuild: Force rebuild the index (default: reuse cached).

    Returns:
        JSON with ranked results: [{path, score, matching_lines}]
    """
    repo = Path(repo_path).resolve()

    # Build or reuse cached index
    cache_key = str(repo)
    if rebuild or cache_key not in _indexes:
        # Get file list
        manifest_path = repo / ".code-context" / "files.all.txt"
        if manifest_path.exists():
            files = [line.strip() for line in manifest_path.read_text().splitlines() if line.strip()]
        else:
            # Fallback: glob for files, skipping known non-text paths
            files = [str(p.relative_to(repo)) for p in repo.rglob("*") if p.is_file() and not _should_skip(p)]
            files = files[:5000]  # Cap for safety

        _indexes[cache_key] = BM25Index.from_files(files, repo)

    index = _indexes[cache_key]
    results = index.search(query, top_k=top_k)

    return json.dumps(
        {
            "status": "success",
            "query": query,
            "total_documents": index.document_count,
            "results": results,
            "count": len(results),
        },
    )


def _should_skip(path: Path) -> bool:
    """Check if a file should be skipped for indexing."""
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".code-context", "dist", "build"}
    skip_exts = {".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin", ".jpg", ".png", ".gif", ".pdf", ".zip"}
    parts = path.parts
    if any(d in parts for d in skip_dirs):
        return True
    return path.suffix.lower() in skip_exts
