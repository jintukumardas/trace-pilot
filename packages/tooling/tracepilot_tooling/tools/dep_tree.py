"""``dep_tree`` — build a readable import / dependency map for a sub-tree.

Combines three signals:
  * Python ``import`` / ``from ... import`` statements (via the ``ast`` module).
  * JS/TS ``import`` / ``require`` statements (regex; no JS runtime needed).
  * Declared package dependencies (``requirements*.txt``, ``pyproject.toml``,
    ``package.json``).

Args:
    path (str, optional): sub-path to analyze (default: workspace root).
    depth (int, optional): max directory depth to walk (default 4).
    max_files (int, optional): cap on files scanned.

Returns a textual tree: declared dependencies, then per-file internal/external
imports. Read-only and sandbox-confined.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from tracepilot_shared.models import ToolName, ToolResult, ToolSpec

from ..base import Tool, ToolContext
from ..sandbox import safe_path

_SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    ".next",
    "__pycache__",
    ".venv",
    "venv",
    "vendor",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
_DEFAULT_DEPTH = 4
_DEFAULT_MAX_FILES = 400
_MAX_FILE_BYTES = 1_000_000

# JS/TS import + require detection.
_JS_IMPORT_RE = re.compile(
    r"""(?:import\s+(?:[^'"]*?\s+from\s+)?|export\s+[^'"]*?\s+from\s+|require\s*\(\s*)"""
    r"""['"]([^'"]+)['"]""",
    re.MULTILINE,
)


class DepTreeTool(Tool):
    name = ToolName.DEP_TREE
    spec = ToolSpec(
        name=ToolName.DEP_TREE,
        description=(
            "Build an import/dependency map (Python + JS/TS imports plus declared "
            "deps from requirements/pyproject/package.json). Returns a readable tree."
        ),
        args_schema={
            "path": {"type": "string", "required": False, "description": "sub-path to analyze"},
            "depth": {"type": "integer", "required": False, "default": _DEFAULT_DEPTH},
            "max_files": {"type": "integer", "required": False, "default": _DEFAULT_MAX_FILES},
        },
        destructive=False,
    )

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        t0 = self._now_ms()
        call_id = args.get("__id", "")
        sub = (args.get("path") or ".").strip() or "."
        depth = _coerce_int(args.get("depth"), _DEFAULT_DEPTH)
        depth = max(1, min(depth, 12))
        max_files = _coerce_int(args.get("max_files"), _DEFAULT_MAX_FILES)
        max_files = max(1, min(max_files, 5000))

        root = safe_path(ctx, sub)  # raises SandboxError on escape
        if not root.exists():
            return self._result(
                call_id, ok=False, error=f"path not found: {sub}", duration_ms=self._now_ms() - t0
            )

        ws_root = Path(ctx.workspace_root).resolve()
        warnings: list[str] = []

        declared = self._declared_deps(root if root.is_dir() else root.parent, ws_root, warnings)
        file_imports, scanned, truncated = self._scan_imports(root, ws_root, depth, max_files, warnings)

        output = self._render(declared, file_imports, warnings)
        clamped, was_clamped = _clamp(output, ctx.max_output_bytes)
        return self._result(
            call_id,
            ok=True,
            output=clamped,
            truncated=truncated or was_clamped,
            exit_code=0,
            duration_ms=self._now_ms() - t0,
            meta={
                "files_scanned": scanned,
                "declared_dep_sources": list(declared.keys()),
                "warnings": warnings,
            },
        )

    # -- declared deps ----------------------------------------------------- #
    def _declared_deps(self, start: Path, ws_root: Path, warnings: list[str]) -> dict[str, list[str]]:
        """Collect declared dependencies from manifests near ``start``."""
        found: dict[str, list[str]] = {}
        # Walk upward from start to the workspace root looking for manifests.
        seen_dirs: set[Path] = set()
        cur = start
        while True:
            if cur in seen_dirs:
                break
            seen_dirs.add(cur)
            for manifest, parser in (
                ("requirements.txt", self._parse_requirements),
                ("requirements-dev.txt", self._parse_requirements),
                ("pyproject.toml", self._parse_pyproject),
                ("package.json", self._parse_package_json),
            ):
                mpath = cur / manifest
                if mpath.exists() and mpath.is_file():
                    try:
                        rel = str(mpath.relative_to(ws_root))
                    except ValueError:
                        rel = str(mpath)
                    if rel in found:
                        continue
                    try:
                        deps = parser(mpath)
                        if deps:
                            found[rel] = deps
                    except Exception as exc:  # noqa: BLE001 - fail soft per manifest
                        warnings.append(f"failed to parse {rel}: {exc}")
            if cur == ws_root or cur.parent == cur:
                break
            try:
                cur.relative_to(ws_root)
            except ValueError:
                break
            cur = cur.parent
        return found

    @staticmethod
    def _parse_requirements(path: Path) -> list[str]:
        deps = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            name = re.split(r"[<>=!~;\[ ]", line, 1)[0].strip()
            if name:
                deps.append(name)
        return sorted(set(deps))

    @staticmethod
    def _parse_pyproject(path: Path) -> list[str]:
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover - py<3.11
            return []
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
        deps: list[str] = []
        project = data.get("project", {})
        for dep in project.get("dependencies", []) or []:
            name = re.split(r"[<>=!~;\[ ]", str(dep), 1)[0].strip()
            if name:
                deps.append(name)
        # Poetry-style
        poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
        for name in poetry:
            if name.lower() != "python":
                deps.append(name)
        return sorted(set(deps))

    @staticmethod
    def _parse_package_json(path: Path) -> list[str]:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        deps: list[str] = []
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            deps.extend((data.get(section) or {}).keys())
        return sorted(set(deps))

    # -- per-file imports -------------------------------------------------- #
    def _scan_imports(self, root, ws_root, depth, max_files, warnings):
        file_imports: dict[str, dict[str, list[str]]] = {}
        scanned = 0
        truncated = False
        root_depth = len(root.parts)

        files = [root] if root.is_file() else self._walk(root, root_depth, depth)
        for file in files:
            if scanned >= max_files:
                truncated = True
                break
            suffix = file.suffix.lower()
            if suffix not in {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
                continue
            try:
                if file.stat().st_size > _MAX_FILE_BYTES:
                    continue
                source = file.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                warnings.append(f"read failed {file.name}: {exc}")
                continue
            try:
                rel = str(file.relative_to(ws_root))
            except ValueError:
                rel = str(file)
            if suffix in {".py", ".pyi"}:
                internal, external = self._python_imports(source, warnings, rel)
            else:
                internal, external = self._js_imports(source)
            if internal or external:
                file_imports[rel] = {"internal": internal, "external": external}
            scanned += 1
        return file_imports, scanned, truncated

    def _walk(self, root: Path, root_depth: int, max_depth: int):
        import os

        for dirpath, dirnames, filenames in os.walk(root):
            dpath = Path(dirpath)
            cur_depth = len(dpath.parts) - root_depth
            if cur_depth >= max_depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fname in sorted(filenames):
                yield dpath / fname

    @staticmethod
    def _python_imports(source: str, warnings: list[str], rel: str):
        internal, external = set(), set()
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            warnings.append(f"syntax error in {rel}: {exc.msg} (line {exc.lineno})")
            return [], []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    external.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    internal.add("." * node.level + (node.module or ""))
                elif node.module:
                    external.add(node.module.split(".")[0])
        return sorted(internal), sorted(external)

    @staticmethod
    def _js_imports(source: str):
        internal, external = set(), set()
        for match in _JS_IMPORT_RE.finditer(source):
            spec = match.group(1)
            if spec.startswith("."):
                internal.add(spec)
            else:
                # Package name (handle scoped @scope/pkg/sub).
                parts = spec.split("/")
                pkg = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
                external.add(pkg)
        return sorted(internal), sorted(external)

    # -- rendering --------------------------------------------------------- #
    @staticmethod
    def _render(declared, file_imports, warnings) -> str:
        lines: list[str] = []
        lines.append("# Dependency map")
        lines.append("")
        if declared:
            lines.append("## Declared dependencies")
            for source, deps in declared.items():
                lines.append(f"  {source} ({len(deps)})")
                for dep in deps:
                    lines.append(f"    - {dep}")
            lines.append("")
        else:
            lines.append("## Declared dependencies: none found")
            lines.append("")

        lines.append(f"## File imports ({len(file_imports)} files)")
        if not file_imports:
            lines.append("  (no Python/JS/TS source files with imports found)")
        for rel, groups in sorted(file_imports.items()):
            lines.append(f"  {rel}")
            if groups["internal"]:
                lines.append(f"    internal: {', '.join(groups['internal'])}")
            if groups["external"]:
                lines.append(f"    external: {', '.join(groups['external'])}")
        if warnings:
            lines.append("")
            lines.append(f"## Warnings ({len(warnings)})")
            for w in warnings[:50]:
                lines.append(f"  - {w}")
        return "\n".join(lines)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(text: str, limit: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if limit > 0 and len(encoded) > limit:
        return encoded[:limit].decode("utf-8", errors="replace") + "\n... [truncated]", True
    return text, False
