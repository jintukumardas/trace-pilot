"""Static lookup tables for ingestion: excludes, extensions, languages, limits.

Kept in one place so chunking + ingestion agree on what is code vs. doc vs. config
and which files to walk at all.
"""

from __future__ import annotations

# Directories never walked during ingestion (build artifacts, vendored deps, VCS).
EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "dist",
        "build",
        ".next",
        "__pycache__",
        ".venv",
        "venv",
        "vendor",
        "target",
        ".mypy_cache",
        ".ruff_cache",
        "coverage",
        ".pytest_cache",
        ".tox",
        ".idea",
        ".gradle",
        "out",
        ".cache",
    }
)

# File extensions we refuse to index (binaries, media, archives, compiled artifacts).
EXCLUDE_EXT: frozenset[str] = frozenset(
    {
        # images / media
        "png",
        "jpg",
        "jpeg",
        "gif",
        "bmp",
        "ico",
        "webp",
        "svg",
        "tiff",
        "mp3",
        "mp4",
        "wav",
        "avi",
        "mov",
        "mkv",
        "flac",
        "ogg",
        "webm",
        # archives / packages
        "zip",
        "tar",
        "gz",
        "tgz",
        "bz2",
        "xz",
        "7z",
        "rar",
        "jar",
        "war",
        # compiled / binary artifacts
        "pyc",
        "pyo",
        "so",
        "o",
        "a",
        "dll",
        "dylib",
        "exe",
        "bin",
        "class",
        "wasm",
        "node",
        "obj",
        "lib",
        "pdb",
        # documents / fonts
        "pdf",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
        "ttf",
        "otf",
        "woff",
        "woff2",
        "eot",
        # data blobs / db
        "db",
        "sqlite",
        "sqlite3",
        "parquet",
        "feather",
        "npy",
        "npz",
        "pkl",
        "h5",
        "hdf5",
        "onnx",
        "pt",
        "pth",
        # misc
        "ds_store",
        "map",
        "min.js",
        "min.css",
    }
)

# Specific filenames we skip wholesale (lockfiles + noise). Compared case-insensitively.
EXCLUDE_FILES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pdm.lock",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "go.sum",
        "uv.lock",
        ".ds_store",
        "thumbs.db",
    }
)

# Lockfile suffix patterns (e.g. anything ending in ``.lock``).
EXCLUDE_NAME_SUFFIXES: tuple[str, ...] = (".lock", ".lockb", ".min.js", ".min.css")

# Anything larger than this is skipped (generated/minified blobs blow up the index).
MAX_FILE_BYTES: int = 1_000_000  # 1 MB

# Extension -> language name. Drives parser selection and the ``languages`` histogram.
LANG_BY_EXT: dict[str, str] = {
    # python
    "py": "python",
    "pyi": "python",
    "pyw": "python",
    # javascript / typescript
    "js": "javascript",
    "jsx": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "ts": "typescript",
    "tsx": "tsx",
    # web
    "html": "html",
    "htm": "html",
    "vue": "vue",
    "svelte": "svelte",
    "css": "css",
    "scss": "scss",
    "sass": "scss",
    "less": "css",
    # jvm
    "java": "java",
    "kt": "kotlin",
    "kts": "kotlin",
    "scala": "scala",
    "groovy": "groovy",
    # systems
    "c": "c",
    "h": "c",
    "cpp": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "hpp": "cpp",
    "hh": "cpp",
    "rs": "rust",
    "go": "go",
    "zig": "zig",
    # other languages
    "rb": "ruby",
    "php": "php",
    "cs": "csharp",
    "swift": "swift",
    "m": "objc",
    "mm": "objc",
    "lua": "lua",
    "pl": "perl",
    "pm": "perl",
    "r": "r",
    "jl": "julia",
    "dart": "dart",
    "ex": "elixir",
    "exs": "elixir",
    "erl": "erlang",
    "hs": "haskell",
    "clj": "clojure",
    "sh": "bash",
    "bash": "bash",
    "zsh": "bash",
    "fish": "bash",
    "ps1": "powershell",
    "sql": "sql",
    # markup / docs
    "md": "markdown",
    "mdx": "markdown",
    "markdown": "markdown",
    "rst": "rst",
    "txt": "text",
    "adoc": "asciidoc",
    # config / data
    "json": "json",
    "jsonc": "json",
    "json5": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "toml": "toml",
    "ini": "ini",
    "cfg": "ini",
    "conf": "ini",
    "env": "ini",
    "properties": "ini",
    "xml": "xml",
    "proto": "proto",
    "graphql": "graphql",
    "gql": "graphql",
    "tf": "hcl",
    "hcl": "hcl",
    "dockerfile": "dockerfile",
    "makefile": "makefile",
}

# Languages we have a tree-sitter parser available for (via tree-sitter-language-pack).
# Anything not listed here falls back to the sliding line-window chunker.
TREE_SITTER_LANGS: frozenset[str] = frozenset(
    {
        "python",
        "javascript",
        "typescript",
        "tsx",
        "java",
        "kotlin",
        "scala",
        "c",
        "cpp",
        "rust",
        "go",
        "ruby",
        "php",
        "csharp",
        "swift",
        "lua",
        "bash",
        "html",
        "css",
        "scss",
        "json",
        "yaml",
        "toml",
        "sql",
        "haskell",
        "elixir",
        "erlang",
        "r",
        "julia",
        "dart",
        "clojure",
    }
)

# Container node types that are NEVER chunk boundaries on their own (root/namespace
# wrappers). Critical: some grammars name their root ``module`` (Python) which would
# otherwise collide with Ruby's ``module`` definition node and swallow the whole file.
CONTAINER_NODE_TYPES: frozenset[str] = frozenset(
    {"module", "program", "source_file", "translation_unit", "compilation_unit", "document"}
)

# Per-language tree-sitter node types that mark the start of a semantic code unit.
# We split source into function/class scoped chunks on these. Scoping per language
# avoids cross-grammar name collisions (e.g. ``module``/``class``/``method``).
_DEFS_C_FAMILY = {
    "function_definition",
    "class_specifier",
    "struct_specifier",
    "namespace_definition",
    "template_declaration",
    "enum_specifier",
}
_DEFS_JS = {
    "function_declaration",
    "function",
    "method_definition",
    "class_declaration",
    "generator_function_declaration",
    "arrow_function",
    "lexical_declaration",
}
_DEFS_TS = _DEFS_JS | {
    "interface_declaration",
    "enum_declaration",
    "type_alias_declaration",
    "abstract_class_declaration",
    "internal_module",
}

DEFINITION_NODE_TYPES_BY_LANG: dict[str, frozenset[str]] = {
    "python": frozenset({"function_definition", "class_definition", "decorated_definition"}),
    "javascript": frozenset(_DEFS_JS),
    "typescript": frozenset(_DEFS_TS),
    "tsx": frozenset(_DEFS_TS),
    "java": frozenset(
        {
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "method_declaration",
            "constructor_declaration",
            "record_declaration",
        }
    ),
    "kotlin": frozenset({"class_declaration", "function_declaration", "object_declaration"}),
    "scala": frozenset({"function_definition", "class_definition", "object_definition", "trait_definition"}),
    "go": frozenset({"function_declaration", "method_declaration", "type_declaration"}),
    "rust": frozenset({"function_item", "impl_item", "struct_item", "enum_item", "trait_item", "mod_item"}),
    "c": frozenset(_DEFS_C_FAMILY),
    "cpp": frozenset(_DEFS_C_FAMILY),
    "csharp": frozenset(
        {
            "class_declaration",
            "interface_declaration",
            "struct_declaration",
            "method_declaration",
            "constructor_declaration",
            "enum_declaration",
        }
    ),
    "ruby": frozenset({"method", "class", "module", "singleton_method"}),
    "php": frozenset(
        {
            "function_definition",
            "class_declaration",
            "method_declaration",
            "interface_declaration",
            "trait_declaration",
        }
    ),
    "swift": frozenset({"function_declaration", "class_declaration", "protocol_declaration"}),
    "lua": frozenset({"function_declaration", "function_definition"}),
    "elixir": frozenset({"call"}),  # def/defmodule appear as `call` nodes in elixir grammar
    "haskell": frozenset({"function", "signature", "data_type"}),
}

# Fallback set for languages without a specific entry above.
DEFINITION_NODE_TYPES: frozenset[str] = frozenset(
    {
        "function_definition",
        "function_declaration",
        "method_definition",
        "class_definition",
        "class_declaration",
        "decorated_definition",
        "method_declaration",
        "constructor_declaration",
        "interface_declaration",
        "enum_declaration",
        "type_declaration",
        "function_item",
        "impl_item",
        "struct_item",
        "enum_item",
        "trait_item",
    }
)


def definition_types_for(language: str) -> frozenset[str]:
    """Return the definition node-type set for a language (specific or fallback)."""
    return DEFINITION_NODE_TYPES_BY_LANG.get(language, DEFINITION_NODE_TYPES)


# Config-ish languages classified as ChunkType.CONFIG.
CONFIG_LANGS: frozenset[str] = frozenset(
    {"json", "yaml", "toml", "ini", "xml", "hcl", "dockerfile", "makefile", "proto", "graphql"}
)

# Doc-ish languages classified as ChunkType.MARKDOWN / DOC.
MARKDOWN_LANGS: frozenset[str] = frozenset({"markdown"})
DOC_LANGS: frozenset[str] = frozenset({"rst", "text", "asciidoc"})

# Sliding-window chunker tuning (line based, applied to code we can't parse semantically).
WINDOW_LINES: int = 60
WINDOW_OVERLAP: int = 12

# Rough chars-per-token used to estimate token budgets without a tokenizer.
CHARS_PER_TOKEN: float = 4.0
