"""Language-aware chunking.

Strategy per file:
  * code (parser available)  -> split on top-level function/class definition nodes
                                 via tree-sitter, with a sliding line-window fallback
                                 for the gaps and for files we can't parse.
  * markdown                 -> split by heading sections.
  * generic text / config    -> sliding line-window.

Each chunk becomes a ``CodeChunk`` carrying ``ChunkMetadata`` (symbol, lines,
language, chunk_type) and ``content_hash = sha1(text)``. tree-sitter is imported
lazily so a missing grammar never crashes import or the whole ingestion run.
"""

from __future__ import annotations

import hashlib
import os
import re

from tracepilot_shared.ids import CHUNK, new_id
from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import ChunkMetadata, ChunkType, CodeChunk

from .constants import (
    CHARS_PER_TOKEN,
    CONFIG_LANGS,
    CONTAINER_NODE_TYPES,
    DOC_LANGS,
    LANG_BY_EXT,
    MARKDOWN_LANGS,
    TREE_SITTER_LANGS,
    WINDOW_LINES,
    WINDOW_OVERLAP,
    definition_types_for,
)

log = get_logger("retrieval.chunking")

# Special filenames whose language can't be inferred from an extension.
_SPECIAL_FILENAMES = {
    "dockerfile": "dockerfile",
    "makefile": "makefile",
    "rakefile": "ruby",
    "gemfile": "ruby",
    "cmakelists.txt": "cmake",
    "go.mod": "go",
    "requirements.txt": "text",
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# Cache parsers across calls so we don't reload grammars per file.
_parser_cache: dict[str, object] = {}
_parser_unavailable: set[str] = set()


# --------------------------------------------------------------------------- #
# Language detection + classification
# --------------------------------------------------------------------------- #
def detect_language(file_path: str) -> str | None:
    """Map a path to a logical language name, or ``None`` if unknown."""
    base = os.path.basename(file_path).lower()
    if base in _SPECIAL_FILENAMES:
        return _SPECIAL_FILENAMES[base]
    # Dotfiles like ``.gitignore`` have no extension; treat as text/ini-ish.
    _, ext = os.path.splitext(base)
    ext = ext.lstrip(".").lower()
    if not ext:
        return None
    return LANG_BY_EXT.get(ext)


def classify_chunk(language: str | None) -> ChunkType:
    """Bucket a language into a ``ChunkType`` for filtering + prompting."""
    if language is None:
        return ChunkType.UNKNOWN
    if language in MARKDOWN_LANGS:
        return ChunkType.MARKDOWN
    if language in DOC_LANGS:
        return ChunkType.DOC
    if language in CONFIG_LANGS:
        return ChunkType.CONFIG
    return ChunkType.CODE


def _content_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()


def _token_estimate(text: str) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN))


# --------------------------------------------------------------------------- #
# tree-sitter helpers (lazy, fail-soft)
# --------------------------------------------------------------------------- #
def _get_parser(language: str):
    """Return a cached tree-sitter parser for ``language`` or ``None``."""
    if language in _parser_unavailable:
        return None
    if language in _parser_cache:
        return _parser_cache[language]
    try:
        from tree_sitter_language_pack import get_parser  # type: ignore

        parser = get_parser(language)  # may raise for unsupported langs
        _parser_cache[language] = parser
        return parser
    except Exception as exc:  # pragma: no cover - grammar missing / optional dep
        log.debug("no tree-sitter parser for %s: %s", language, exc)
        _parser_unavailable.add(language)
        return None


def _node_symbol(node, source_bytes: bytes) -> str | None:
    """Best-effort symbol name for a definition node (the ``name`` child, if any)."""
    try:
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            return source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8", "ignore")
    except Exception:
        pass
    # Fallback: scan named children for an identifier-ish node.
    try:
        for child in node.named_children:
            if "identifier" in child.type or child.type in {"name", "type_identifier"}:
                return source_bytes[child.start_byte : child.end_byte].decode("utf-8", "ignore")
    except Exception:
        pass
    return None


def _collect_definitions(
    root, source_bytes: bytes, def_types: frozenset[str]
) -> list[tuple[int, int, str | None]]:
    """Walk the tree collecting (start_line, end_line, symbol) for definition nodes.

    We keep only the *outermost* definitions to avoid duplicating nested methods
    inside their class chunk; nested defs are still covered by their parent's span.
    Container nodes (module/program roots) are never treated as definitions.
    """
    spans: list[tuple[int, int, str | None]] = []
    stack = [(root, False)]
    while stack:
        node, inside_def = stack.pop()
        node_type = node.type
        is_def = node_type in def_types and node_type not in CONTAINER_NODE_TYPES
        if is_def and not inside_def:
            start = node.start_point[0] + 1  # tree-sitter is 0-based
            end = node.end_point[0] + 1
            spans.append((start, end, _node_symbol(node, source_bytes)))
        # Descend only while we have not yet entered a definition, so we keep the
        # outermost defs and let their chunk cover nested members.
        if not (inside_def or is_def):
            for child in node.children:
                stack.append((child, False))
    spans.sort(key=lambda s: s[0])
    return spans


# --------------------------------------------------------------------------- #
# Sliding line-window chunker (shared fallback)
# --------------------------------------------------------------------------- #
def _window_chunks(
    lines: list[str],
    start_offset: int,
    end_offset: int,
    window: int = WINDOW_LINES,
    overlap: int = WINDOW_OVERLAP,
) -> list[tuple[int, int, str]]:
    """Yield (start_line, end_line, text) windows over ``lines[start_offset:end_offset]``.

    Line numbers in the result are 1-based file line numbers.
    """
    out: list[tuple[int, int, str]] = []
    step = max(1, window - overlap)
    i = start_offset
    n = end_offset
    while i < n:
        j = min(i + window, n)
        text = "\n".join(lines[i:j])
        if text.strip():
            out.append((i + 1, j, text))
        if j >= n:
            break
        i += step
    return out


# --------------------------------------------------------------------------- #
# Markdown chunker (by heading sections)
# --------------------------------------------------------------------------- #
def _markdown_chunks(text: str) -> list[tuple[int, int, str | None, str]]:
    """Split markdown into (start_line, end_line, heading, text) sections."""
    lines = text.splitlines()
    sections: list[tuple[int, int, str | None, str]] = []
    cur_start = 0
    cur_heading: str | None = None
    buf: list[str] = []

    def flush(end_idx: int) -> None:
        if not buf:
            return
        body = "\n".join(buf).strip()
        if body:
            sections.append((cur_start + 1, end_idx, cur_heading, "\n".join(buf)))

    for idx, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m and buf:
            # Close the previous section before starting a new heading.
            flush(idx)
            buf = []
            cur_start = idx
            cur_heading = m.group(2).strip()
        elif m and not buf:
            cur_start = idx
            cur_heading = m.group(2).strip()
            buf.append(line)
            continue
        buf.append(line)
    flush(len(lines))

    # Very long sections get window-split so a single huge README doesn't overflow.
    final: list[tuple[int, int, str | None, str]] = []
    for start, end, heading, body in sections:
        body_lines = body.splitlines()
        if len(body_lines) <= WINDOW_LINES * 2:
            final.append((start, end, heading, body))
            continue
        for ws, we, wtext in _window_chunks(body_lines, 0, len(body_lines)):
            final.append((start + ws - 1, start + we - 1, heading, wtext))
    if not final and text.strip():
        final.append((1, max(1, len(lines)), None, text))
    return final


# --------------------------------------------------------------------------- #
# Code chunker
# --------------------------------------------------------------------------- #
def _code_chunks(text: str, language: str) -> list[tuple[int, int, str | None, str]]:
    """Split code into (start_line, end_line, symbol, text) chunks via tree-sitter.

    Falls back to a sliding window if no parser is available or parsing yields
    nothing useful.
    """
    lines = text.splitlines()
    if not lines:
        return []

    parser = _get_parser(language) if language in TREE_SITTER_LANGS else None
    if parser is None:
        return [(s, e, None, t) for s, e, t in _window_chunks(lines, 0, len(lines))]

    try:
        source_bytes = text.encode("utf-8", "ignore")
        tree = parser.parse(source_bytes)
        spans = _collect_definitions(tree.root_node, source_bytes, definition_types_for(language))
    except Exception as exc:  # pragma: no cover - parser hiccup
        log.debug("tree-sitter parse failed for %s: %s", language, exc)
        return [(s, e, None, t) for s, e, t in _window_chunks(lines, 0, len(lines))]

    if not spans:
        return [(s, e, None, t) for s, e, t in _window_chunks(lines, 0, len(lines))]

    chunks: list[tuple[int, int, str | None, str]] = []
    prev_end = 0  # 0-based exclusive index of last consumed line

    for start_line, end_line, symbol in spans:
        s_idx = start_line - 1
        e_idx = end_line  # exclusive
        # Capture inter-definition gap (module-level code, imports) as its own chunk.
        if s_idx > prev_end:
            for ws, we, wtext in _window_chunks(lines, prev_end, s_idx):
                chunks.append((ws, we, None, wtext))
        body_lines = lines[s_idx:e_idx]
        if len(body_lines) > WINDOW_LINES * 3:
            # Huge function/class: window it but keep the symbol on each piece.
            for ws, we, wtext in _window_chunks(body_lines, 0, len(body_lines)):
                chunks.append((start_line + ws - 1, start_line + we - 1, symbol, wtext))
        else:
            chunk_text = "\n".join(body_lines)
            if chunk_text.strip():
                chunks.append((start_line, end_line, symbol, chunk_text))
        prev_end = max(prev_end, e_idx)

    # Trailing module-level code after the last definition.
    if prev_end < len(lines):
        for ws, we, wtext in _window_chunks(lines, prev_end, len(lines)):
            chunks.append((ws, we, None, wtext))

    return chunks


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #
def chunk_file(
    *,
    file_path: str,
    text: str,
    repository_id: str,
    repo_name: str,
    branch: str = "main",
    commit_hash: str | None = None,
) -> list[CodeChunk]:
    """Chunk one file's text into ``CodeChunk`` objects with full metadata.

    ``file_path`` should be the repo-relative path (used for citations + filtering).
    Returns an empty list for empty/whitespace-only files. Never raises.
    """
    if not text or not text.strip():
        return []

    language = detect_language(file_path)
    chunk_type = classify_chunk(language)

    try:
        if chunk_type == ChunkType.MARKDOWN:
            raw = [(s, e, heading, body) for (s, e, heading, body) in _markdown_chunks(text)]
        elif chunk_type == ChunkType.CODE and language:
            raw = _code_chunks(text, language)
        else:
            # config / doc / unknown -> generic windows
            lines = text.splitlines()
            raw = [(s, e, None, t) for s, e, t in _window_chunks(lines, 0, len(lines))]
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("chunking failed for %s, using whole-file fallback: %s", file_path, exc)
        raw = [(1, len(text.splitlines()) or 1, None, text)]

    chunks: list[CodeChunk] = []
    for start_line, end_line, symbol, chunk_text in raw:
        chunk_text = chunk_text.strip("\n")
        if not chunk_text.strip():
            continue
        metadata = ChunkMetadata(
            repository_id=repository_id,
            repo_name=repo_name,
            branch=branch,
            file_path=file_path,
            language=language,
            chunk_type=chunk_type,
            symbol=symbol,
            start_line=max(1, int(start_line)),
            end_line=max(int(start_line), int(end_line)),
            commit_hash=commit_hash,
        )
        chunks.append(
            CodeChunk(
                id=new_id(CHUNK),
                text=chunk_text,
                metadata=metadata,
                content_hash=_content_hash(chunk_text),
                token_estimate=_token_estimate(chunk_text),
            )
        )
    return chunks
