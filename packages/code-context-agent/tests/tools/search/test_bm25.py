"""Tests for BM25 ranked text search."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from code_context_agent.tools.search.bm25 import BM25Index, _find_matching_lines, _tokenize

if TYPE_CHECKING:
    from pathlib import Path
from code_context_agent.tools.search.tools import _indexes, bm25_search


class TestBM25Index:
    def test_build_index_from_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("def hello_world(): pass")
        (tmp_path / "b.py").write_text("class FooBar: pass")

        index = BM25Index.from_files(["a.py", "b.py"], tmp_path)
        assert index.document_count == 2

    def test_search_returns_ranked_results(self, tmp_path: Path) -> None:
        (tmp_path / "auth.py").write_text(
            "def authenticate_user(username, password):\n"
            "    # authenticate the user credentials\n"
            "    return verify_password(password)\n",
        )
        (tmp_path / "utils.py").write_text("def format_string(s): return s.strip()")
        (tmp_path / "main.py").write_text("from auth import authenticate_user")

        index = BM25Index.from_files(["auth.py", "utils.py", "main.py"], tmp_path)
        results = index.search("authenticate user password")

        assert len(results) > 0
        # auth.py should rank highest — it contains all query terms
        assert results[0]["path"] == "auth.py"
        assert results[0]["score"] > 0

    def test_search_empty_query_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("some content here")
        index = BM25Index.from_files(["a.py"], tmp_path)

        results = index.search("")
        assert results == []

    def test_search_no_matches_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("def hello(): pass")
        index = BM25Index.from_files(["a.py"], tmp_path)

        results = index.search("zzzznonexistent xyzxyz")
        assert results == []

    def test_empty_index_search_returns_empty(self) -> None:
        index = BM25Index()
        assert index.search("anything") == []
        assert index.document_count == 0

    def test_skips_nonexistent_files(self, tmp_path: Path) -> None:
        (tmp_path / "exists.py").write_text("real content")
        index = BM25Index.from_files(["exists.py", "missing.py"], tmp_path)
        assert index.document_count == 1

    def test_skips_files_with_only_short_tokens(self, tmp_path: Path) -> None:
        # File with only single-character tokens after tokenization
        (tmp_path / "tiny.txt").write_text("a b c d e f")
        index = BM25Index.from_files(["tiny.txt"], tmp_path)
        assert index.document_count == 0


class TestTokenize:
    def test_splits_camel_case(self) -> None:
        tokens = _tokenize("getUserName")
        assert "get" in tokens
        assert "user" in tokens
        assert "name" in tokens

    def test_splits_snake_case(self) -> None:
        tokens = _tokenize("get_user_name")
        assert "get" in tokens
        assert "user" in tokens
        assert "name" in tokens

    def test_camel_and_snake_produce_same_tokens(self) -> None:
        camel_tokens = set(_tokenize("getUserName"))
        snake_tokens = set(_tokenize("get_user_name"))
        assert camel_tokens == snake_tokens

    def test_filters_single_char_tokens(self) -> None:
        tokens = _tokenize("a b cc dd")
        assert "a" not in tokens
        assert "b" not in tokens
        assert "cc" in tokens
        assert "dd" in tokens


class TestFindMatchingLines:
    def test_returns_context_with_line_numbers(self) -> None:
        content = "line one\ndef authenticate():\n  pass\n"
        results = _find_matching_lines(content, ["authenticate"])
        assert len(results) == 1
        assert results[0]["line"] == 2
        assert "authenticate" in results[0]["text"]
        assert "authenticate" in results[0]["matched_tokens"]

    def test_respects_max_lines(self) -> None:
        content = "auth line1\nauth line2\nauth line3\nauth line4\n"
        results = _find_matching_lines(content, ["auth"], max_lines=2)
        assert len(results) == 2

    def test_no_matches_returns_empty(self) -> None:
        content = "hello world\nfoo bar\n"
        results = _find_matching_lines(content, ["zzzznothere"])
        assert results == []


class TestBM25SearchTool:
    def test_returns_json(self, tmp_path: Path) -> None:
        (tmp_path / "hello.py").write_text("def greet(): print('hello world')")

        result_json = bm25_search(
            query="greet hello",
            repo_path=str(tmp_path),
            top_k=10,
            rebuild=True,
        )
        result = json.loads(result_json)

        assert result["status"] == "success"
        assert result["query"] == "greet hello"
        assert result["total_documents"] >= 1
        assert "results" in result
        assert "count" in result

    def test_caches_index(self, tmp_path: Path) -> None:
        (tmp_path / "data.py").write_text("important data here")
        cache_key = str(tmp_path.resolve())

        # Clear cache
        _indexes.pop(cache_key, None)

        bm25_search(query="data", repo_path=str(tmp_path), rebuild=True)
        assert cache_key in _indexes

        # Second call should reuse (no rebuild)
        first_index = _indexes[cache_key]
        bm25_search(query="data", repo_path=str(tmp_path), rebuild=False)
        assert _indexes[cache_key] is first_index

    def test_uses_manifest_when_present(self, tmp_path: Path) -> None:
        # Create .code-context/files.all.txt manifest
        code_ctx = tmp_path / ".code-context"
        code_ctx.mkdir()
        (tmp_path / "src.py").write_text("source code")
        (tmp_path / "other.py").write_text("other stuff")
        (code_ctx / "files.all.txt").write_text("src.py\n")

        cache_key = str(tmp_path.resolve())
        _indexes.pop(cache_key, None)

        result_json = bm25_search(query="source", repo_path=str(tmp_path), rebuild=True)
        result = json.loads(result_json)

        # Only src.py should be indexed (from manifest)
        assert result["total_documents"] == 1
