"""Tests for ``tracepilot_retrieval.chunking``.

Verifies code chunks carry sane symbol/line metadata and content hashes, that
markdown is split by heading sections, and that the chunker fails soft on empty
input. tree-sitter is an optional dependency; the assertions degrade to the
line-window fallback shape when no grammar is available.
"""

from __future__ import annotations

from pathlib import Path

from tracepilot_retrieval.chunking import (
    chunk_file,
    classify_chunk,
    detect_language,
)
from tracepilot_shared.models import ChunkType, CodeChunk

_CODE = '''\
"""Module docstring."""
import os

DEFAULT_TIMEOUT = 30


class Settings:
    """Holds config."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def as_dict(self) -> dict:
        return {"timeout": self.timeout}


def load_settings() -> Settings:
    """Load settings from env."""
    return Settings(int(os.environ.get("TIMEOUT", "30")))
'''

_MARKDOWN = """\
# Project Title

An introduction paragraph.

## Installation

Run the installer.

## Usage

Call the entrypoint.
"""


# --------------------------------------------------------------------------- #
# language detection / classification
# --------------------------------------------------------------------------- #
def test_detect_language():
    assert detect_language("config.py") == "python"
    assert detect_language("README.md") in {"markdown", "md"}
    assert detect_language("noext") is None


def test_classify_chunk_buckets():
    assert classify_chunk("python") == ChunkType.CODE
    assert classify_chunk(None) == ChunkType.UNKNOWN
    assert classify_chunk(detect_language("README.md")) == ChunkType.MARKDOWN


# --------------------------------------------------------------------------- #
# code chunking
# --------------------------------------------------------------------------- #
def test_code_chunking_metadata_and_hash():
    chunks = chunk_file(
        file_path="config.py",
        text=_CODE,
        repository_id="repo_1",
        repo_name="demo",
        branch="main",
        commit_hash="deadbeef",
    )
    assert chunks, "expected at least one chunk"
    assert all(isinstance(c, CodeChunk) for c in chunks)

    for c in chunks:
        md = c.metadata
        # Metadata is fully populated and self-consistent.
        assert md.repository_id == "repo_1"
        assert md.repo_name == "demo"
        assert md.branch == "main"
        assert md.file_path == "config.py"
        assert md.language == "python"
        assert md.chunk_type == ChunkType.CODE
        assert md.commit_hash == "deadbeef"
        # Lines are 1-based and ordered.
        assert md.start_line >= 1
        assert md.end_line >= md.start_line
        # Content hash + token estimate are populated.
        assert c.content_hash and len(c.content_hash) >= 8
        assert c.token_estimate >= 1
        # The chunk id is prefixed.
        assert c.id.startswith("chunk_")


def test_code_chunking_recovers_symbols():
    chunks = chunk_file(file_path="config.py", text=_CODE, repository_id="r", repo_name="demo")
    symbols = {c.metadata.symbol for c in chunks if c.metadata.symbol}
    # tree-sitter (if present) recovers class/function names. The fallback window
    # chunker yields None symbols; in that case we at least still got chunks.
    if symbols:
        assert "Settings" in symbols
        assert "load_settings" in symbols
    else:  # pragma: no cover - only when no python grammar is installed
        assert chunks


def test_content_hash_is_deterministic_and_text_sensitive():
    a = chunk_file(file_path="a.py", text=_CODE, repository_id="r", repo_name="d")
    b = chunk_file(file_path="a.py", text=_CODE, repository_id="r", repo_name="d")
    # Same text → same per-chunk content hashes (ids differ, hashes stable).
    assert [c.content_hash for c in a] == [c.content_hash for c in b]

    changed = chunk_file(
        file_path="a.py",
        text=_CODE.replace("DEFAULT_TIMEOUT = 30", "DEFAULT_TIMEOUT = 99"),
        repository_id="r",
        repo_name="d",
    )
    assert [c.content_hash for c in changed] != [c.content_hash for c in a]


# --------------------------------------------------------------------------- #
# markdown chunking
# --------------------------------------------------------------------------- #
def test_markdown_chunking_by_heading():
    chunks = chunk_file(file_path="README.md", text=_MARKDOWN, repository_id="r", repo_name="demo")
    assert len(chunks) >= 2
    assert all(c.metadata.chunk_type == ChunkType.MARKDOWN for c in chunks)
    headings = {c.metadata.symbol for c in chunks if c.metadata.symbol}
    # Heading text becomes the chunk symbol.
    assert {"Installation", "Usage"} <= headings or "Project Title" in headings
    # Line spans are ordered and 1-based.
    for c in chunks:
        assert 1 <= c.metadata.start_line <= c.metadata.end_line


# --------------------------------------------------------------------------- #
# fail-soft behavior
# --------------------------------------------------------------------------- #
def test_empty_file_yields_no_chunks():
    assert chunk_file(file_path="e.py", text="", repository_id="r", repo_name="d") == []
    assert chunk_file(file_path="e.py", text="   \n  \n", repository_id="r", repo_name="d") == []


def test_unknown_language_falls_back_to_windows():
    chunks = chunk_file(
        file_path="data.unknownext",
        text="line1\nline2\nline3\n",
        repository_id="r",
        repo_name="d",
    )
    assert chunks
    assert chunks[0].metadata.chunk_type == ChunkType.UNKNOWN
    assert chunks[0].metadata.symbol is None


def test_chunks_cover_whole_sample_repo(sample_repo: Path):
    total = 0
    for path in sorted(sample_repo.rglob("*.py")):
        rel = str(path.relative_to(sample_repo))
        chunks = chunk_file(
            file_path=rel, text=path.read_text(encoding="utf-8"), repository_id="repo_1", repo_name="demo"
        )
        total += len(chunks)
    assert total >= 3  # config.py + service.py + helpers.py produced chunks
