"""Small, deterministic and persistent repository index.

Python uses the standard-library AST.  Other common repository languages use
conservative structural extractors: they are navigation hints, not compiler or
language-server facts.  The source tree remains authoritative and fingerprints
invalidate stale cache entries.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .workspace import IGNORED_PATH_NAMES


MAX_INDEX_FILE_BYTES = 2 * 1024 * 1024
INDEX_SCHEMA_VERSION = "repo-index-v2"
LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
}


@dataclass(frozen=True)
class SymbolRecord:
    name: str
    qualified_name: str
    kind: str
    line_start: int
    line_end: int
    column: int
    parent: str = ""

    def to_dict(self):
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "kind": self.kind,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "column": self.column,
            "parent": self.parent,
        }

    @classmethod
    def from_dict(cls, value):
        return cls(
            name=str(value.get("name", "")),
            qualified_name=str(value.get("qualified_name", value.get("name", ""))),
            kind=str(value.get("kind", "symbol")),
            line_start=int(value.get("line_start", 1)),
            line_end=int(value.get("line_end", value.get("line_start", 1))),
            column=int(value.get("column", 0)),
            parent=str(value.get("parent", "")),
        )


@dataclass
class FileRecord:
    path: str
    language: str
    fingerprint: str
    size: int
    symbols: tuple[SymbolRecord, ...] = ()
    imports: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    source_lines: tuple[str, ...] = ()
    tree: ast.AST | None = field(default=None, repr=False, compare=False)

    def to_dict(self):
        return {
            "path": self.path,
            "language": self.language,
            "size": self.size,
            "symbols": [symbol.to_dict() for symbol in self.symbols],
            "imports": list(self.imports),
            "diagnostics": list(self.diagnostics),
        }

    def to_cache_dict(self):
        value = self.to_dict()
        value.update(
            {
                "fingerprint": self.fingerprint,
                "source_lines": list(self.source_lines),
            }
        )
        return value

    @classmethod
    def from_cache_dict(cls, value):
        return cls(
            path=str(value["path"]),
            language=str(value.get("language", "unknown")),
            fingerprint=str(value.get("fingerprint", "")),
            size=int(value.get("size", 0)),
            symbols=tuple(SymbolRecord.from_dict(item) for item in value.get("symbols", [])),
            imports=tuple(str(item) for item in value.get("imports", [])),
            diagnostics=tuple(str(item) for item in value.get("diagnostics", [])),
            source_lines=tuple(str(item) for item in value.get("source_lines", [])),
        )


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self):
        self.symbols: list[SymbolRecord] = []
        self.scope: list[str] = []
        self.scope_kinds: list[str] = []

    def _add(self, node, name, kind):
        qualified_name = ".".join([*self.scope, name])
        self.symbols.append(
            SymbolRecord(
                name=name,
                qualified_name=qualified_name,
                kind=kind,
                line_start=int(getattr(node, "lineno", 1)),
                line_end=int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
                column=int(getattr(node, "col_offset", 0)),
                parent=".".join(self.scope),
            )
        )

    def visit_ClassDef(self, node):
        self._add(node, node.name, "class")
        self.scope.append(node.name)
        self.scope_kinds.append("class")
        self.generic_visit(node)
        self.scope.pop()
        self.scope_kinds.pop()

    def _visit_function(self, node, kind):
        if self.scope_kinds and self.scope_kinds[-1] == "class":
            kind = "method" if kind == "function" else "async_method"
        self._add(node, node.name, kind)
        self.scope.append(node.name)
        self.scope_kinds.append("function")
        self.generic_visit(node)
        self.scope.pop()
        self.scope_kinds.pop()

    def visit_FunctionDef(self, node):
        self._visit_function(node, "function")

    def visit_AsyncFunctionDef(self, node):
        self._visit_function(node, "async_function")


class _ImportVisitor(ast.NodeVisitor):
    def __init__(self):
        self.imports: set[str] = set()

    def visit_Import(self, node):
        self.imports.update(alias.name for alias in node.names)

    def visit_ImportFrom(self, node):
        prefix = "." * int(getattr(node, "level", 0))
        module = node.module or ""
        self.imports.add(prefix + module)


class RepoIndex:
    """Multi-language index with per-file reuse and an atomic disk cache."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.cache_path = self.root / ".pico" / "index" / "repo-index-v2.json"
        self._records: dict[str, FileRecord] = {}
        self._signatures: dict[str, tuple[int, int]] = {}
        self._refresh_count = 0
        self._load_cache()

    def _load_cache(self):
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != INDEX_SCHEMA_VERSION:
                return
            if Path(payload.get("root", "")).resolve() != self.root:
                return
            self._records = {
                item["path"]: FileRecord.from_cache_dict(item)
                for item in payload.get("records", [])
                if isinstance(item, dict) and item.get("path")
            }
            self._signatures = {
                key: (int(value[0]), int(value[1]))
                for key, value in payload.get("signatures", {}).items()
                if isinstance(value, list) and len(value) == 2
            }
        except (OSError, ValueError, TypeError, KeyError):
            self._records = {}
            self._signatures = {}

    def _save_cache(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".tmp")
        payload = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "root": str(self.root),
            "signatures": {key: list(value) for key, value in sorted(self._signatures.items())},
            "records": [self._records[key].to_cache_dict() for key in sorted(self._records)],
        }
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temporary.replace(self.cache_path)

    def _assert_inside(self, path):
        path = Path(path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path escapes workspace: {path}") from exc
        return path

    def _relative(self, path):
        return self._assert_inside(path).relative_to(self.root).as_posix()

    @staticmethod
    def _is_ignored(path, root):
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            return True
        return any(part in IGNORED_PATH_NAMES for part in parts)

    def _candidate_files(self, target):
        target = self._assert_inside(target)
        if target.is_file():
            return [target] if target.suffix.lower() in LANGUAGE_BY_SUFFIX else []
        if not target.is_dir():
            raise ValueError(f"path is not a file or directory: {target}")
        return sorted(
            (
                path for path in target.rglob("*")
                if path.is_file()
                and path.suffix.lower() in LANGUAGE_BY_SUFFIX
                and not self._is_ignored(path, self.root)
            ),
            key=lambda path: path.relative_to(self.root).as_posix(),
        )

    @staticmethod
    def _signature(path):
        stat = path.stat()
        return int(stat.st_mtime_ns), int(stat.st_size)

    def _parse_file(self, path, relative_path, signature):
        size = signature[1]
        language = LANGUAGE_BY_SUFFIX.get(path.suffix.lower(), "unknown")
        if size > MAX_INDEX_FILE_BYTES:
            return FileRecord(
                path=relative_path,
                language=language,
                fingerprint=f"size:{size}",
                size=size,
                diagnostics=(f"file exceeds index limit ({MAX_INDEX_FILE_BYTES} bytes)",),
            )

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            return FileRecord(
                path=relative_path,
                language=language,
                fingerprint=f"decode-error:{signature}",
                size=size,
                diagnostics=(f"decode error: {exc}",),
            )
        except OSError as exc:
            return FileRecord(
                path=relative_path,
                language=language,
                fingerprint=f"read-error:{signature}",
                size=size,
                diagnostics=(f"read error: {exc}",),
            )

        source_lines = tuple(text.splitlines())
        fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if language != "python":
            symbols, imports = self._extract_structural_facts(text, language)
            return FileRecord(
                path=relative_path,
                language=language,
                fingerprint=fingerprint,
                size=size,
                symbols=tuple(symbols),
                imports=tuple(sorted(imports)),
                diagnostics=("heuristic structural index; verify with compiler or language server",),
                source_lines=source_lines,
            )
        try:
            tree = ast.parse(text, filename=relative_path)
        except SyntaxError as exc:
            location = f"line {exc.lineno}" if exc.lineno else "unknown line"
            diagnostic = f"syntax error at {location}: {exc.msg}"
            return FileRecord(
                path=relative_path,
                language=language,
                fingerprint=fingerprint,
                size=size,
                diagnostics=(diagnostic,),
                source_lines=source_lines,
            )

        symbol_visitor = _SymbolVisitor()
        symbol_visitor.visit(tree)
        import_visitor = _ImportVisitor()
        import_visitor.visit(tree)
        symbols = tuple(
            sorted(
                symbol_visitor.symbols,
                key=lambda item: (item.line_start, item.column, item.qualified_name),
            )
        )
        return FileRecord(
            path=relative_path,
            language=language,
            fingerprint=fingerprint,
            size=size,
            symbols=symbols,
            imports=tuple(sorted(item for item in import_visitor.imports if item)),
            source_lines=source_lines,
            tree=tree,
        )

    @staticmethod
    def _extract_structural_facts(text, language):
        patterns = {
            "java": [
                ("class", r"\b(?:class|interface|enum|record)\s+([A-Za-z_$][\w$]*)"),
                ("method", r"^\s*(?:public|protected|private|static|final|abstract|synchronized|native|\s)+[\w<>,.?\[\]]+\s+([A-Za-z_$][\w$]*)\s*\("),
            ],
            "javascript": [
                ("class", r"\bclass\s+([A-Za-z_$][\w$]*)"),
                ("function", r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\("),
                ("function", r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"),
            ],
            "typescript": [
                ("class", r"\b(?:class|interface|enum|type)\s+([A-Za-z_$][\w$]*)"),
                ("function", r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\("),
                ("function", r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?\([^)]*\)\s*=>"),
            ],
            "go": [
                ("type", r"^\s*type\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b"),
                ("function", r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\("),
            ],
            "rust": [
                ("type", r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_]\w*)"),
                ("function", r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)\s*\("),
            ],
        }
        imports = set()
        symbols = []
        import_patterns = {
            "java": r"^\s*import\s+(?:static\s+)?([^;]+)",
            "javascript": r"^\s*import\s+.*?\sfrom\s+['\"]([^'\"]+)['\"]|^\s*(?:const|let|var).*?require\(['\"]([^'\"]+)['\"]\)",
            "typescript": r"^\s*import\s+.*?\sfrom\s+['\"]([^'\"]+)['\"]|^\s*import\s+['\"]([^'\"]+)['\"]",
            "go": r"^\s*import\s+(?:\w+\s+)?\"([^\"]+)\"",
            "rust": r"^\s*(?:pub\s+)?use\s+([^;]+)",
        }
        for line_number, line in enumerate(text.splitlines(), start=1):
            for kind, pattern in patterns.get(language, []):
                match = re.search(pattern, line)
                if match:
                    name = match.group(1)
                    symbols.append(SymbolRecord(name, name, kind, line_number, line_number, match.start(1)))
            import_match = re.search(import_patterns.get(language, r"$^"), line)
            if import_match:
                imports.add(next((group for group in import_match.groups() if group), import_match.group(0)).strip())
        symbols.sort(key=lambda item: (item.line_start, item.column, item.name))
        return symbols, imports

    def refresh(self, path="."):
        target = self._assert_inside(path if Path(path).is_absolute() else self.root / path)
        candidates = self._candidate_files(target)
        target_is_file = target.is_file()
        seen: set[str] = set()
        reused = 0
        diagnostics = []
        for file_path in candidates:
            relative_path = file_path.relative_to(self.root).as_posix()
            seen.add(relative_path)
            try:
                signature = self._signature(file_path)
            except OSError as exc:
                diagnostics.append(f"{relative_path}: {exc}")
                continue
            if self._signatures.get(relative_path) == signature and relative_path in self._records:
                reused += 1
                record = self._records[relative_path]
            else:
                record = self._parse_file(file_path, relative_path, signature)
                self._records[relative_path] = record
                self._signatures[relative_path] = signature
            diagnostics.extend(f"{relative_path}: {item}" for item in record.diagnostics)

        if not target_is_file:
            prefix = target.relative_to(self.root).as_posix()
            for relative_path in list(self._records):
                in_scope = prefix == "." or relative_path == prefix or relative_path.startswith(prefix.rstrip("/") + "/")
                if in_scope and relative_path not in seen:
                    self._records.pop(relative_path, None)
                    self._signatures.pop(relative_path, None)

        self._refresh_count += 1
        self._save_cache()
        return {
            "path": "." if target == self.root else target.relative_to(self.root).as_posix(),
            "files_indexed": len(candidates),
            "files_reused": reused,
            "diagnostics": sorted(set(diagnostics)),
            "refresh_count": self._refresh_count,
        }

    def _records_for(self, path):
        target = self._assert_inside(path if Path(path).is_absolute() else self.root / path)
        self.refresh(target)
        if target.is_file():
            relative = target.relative_to(self.root).as_posix()
            record = self._records.get(relative)
            if record is None:
                raise ValueError("file type is not supported by Repo Index")
            return [record]
        prefix = target.relative_to(self.root).as_posix()
        return [
            self._records[key]
            for key in sorted(self._records)
            if prefix == "." or key == prefix or key.startswith(prefix.rstrip("/") + "/")
        ]

    @staticmethod
    def _scope_for_line(record, line):
        candidates = [
            symbol
            for symbol in record.symbols
            if symbol.line_start <= line <= symbol.line_end
        ]
        if not candidates:
            return ""
        return max(candidates, key=lambda item: (item.line_start, len(item.qualified_name))).qualified_name

    def file_outline(self, path):
        records = self._records_for(path)
        if len(records) != 1:
            return {
                "path": str(path),
                "files": [record.to_dict() for record in records],
                "count": len(records),
            }
        return records[0].to_dict()

    def find_symbol(self, name, path="."):
        query = str(name).strip()
        target = query.rsplit(".", 1)[-1]
        results = []
        diagnostics = []
        for record in self._records_for(path):
            diagnostics.extend(f"{record.path}: {item}" for item in record.diagnostics)
            for symbol in record.symbols:
                if symbol.name == target or symbol.qualified_name == query or symbol.qualified_name.endswith("." + query):
                    results.append({"path": record.path, **symbol.to_dict()})
        return {"query": query, "results": results, "count": len(results), "diagnostics": sorted(set(diagnostics))}

    def find_references(self, name, path="."):
        query = str(name).strip()
        target = query.rsplit(".", 1)[-1]
        results = []
        diagnostics = []
        for record in self._records_for(path):
            diagnostics.extend(f"{record.path}: {item}" for item in record.diagnostics)
            for symbol in record.symbols:
                if symbol.name == target or symbol.qualified_name == query or symbol.qualified_name.endswith("." + query):
                    results.append(
                        {
                            "path": record.path,
                            "line": symbol.line_start,
                            "column": symbol.column,
                            "kind": "definition",
                            "scope": symbol.parent,
                            "text": record.source_lines[symbol.line_start - 1].strip()
                            if 0 < symbol.line_start <= len(record.source_lines)
                            else "",
                        }
                    )
            if record.tree is None:
                token_pattern = re.compile(rf"\b{re.escape(target)}\b")
                definition_lines = {symbol.line_start for symbol in record.symbols if symbol.name == target}
                for line_number, text in enumerate(record.source_lines, start=1):
                    for match in token_pattern.finditer(text):
                        if line_number in definition_lines:
                            continue
                        results.append(
                            {
                                "path": record.path,
                                "line": line_number,
                                "column": match.start(),
                                "kind": "token",
                                "scope": self._scope_for_line(record, line_number),
                                "text": text.strip(),
                            }
                        )
                continue
            for node in ast.walk(record.tree):
                line = int(getattr(node, "lineno", 0) or 0)
                column = int(getattr(node, "col_offset", 0) or 0)
                if isinstance(node, ast.Name) and node.id == target:
                    kind = "name"
                elif isinstance(node, ast.Attribute) and node.attr == target:
                    kind = "attribute"
                else:
                    continue
                results.append(
                    {
                        "path": record.path,
                        "line": line,
                        "column": column,
                        "kind": kind,
                        "scope": self._scope_for_line(record, line),
                        "text": record.source_lines[line - 1].strip()
                        if 0 < line <= len(record.source_lines)
                        else "",
                    }
                )
        results.sort(key=lambda item: (item["path"], item["line"], item["column"], item["kind"]))
        return {
            "query": query,
            "results": results,
            "count": len(results),
            "diagnostics": sorted(set(diagnostics)),
        }

    @staticmethod
    def _module_map(records):
        module_map = {}
        for record in records:
            path = Path(record.path)
            module = ".".join(path.with_suffix("").parts)
            module_map[module] = record.path
            if path.name == "__init__.py":
                module_map[".".join(path.parent.parts)] = record.path
        return module_map

    @staticmethod
    def _resolve_import(record_path, imported, module_map):
        imported = str(imported)
        if not imported.startswith("."):
            return module_map.get(imported)
        level = len(imported) - len(imported.lstrip("."))
        remainder = imported[level:]
        package_parts = list(Path(record_path).parent.parts)
        trim = max(level - 1, 0)
        if trim:
            package_parts = package_parts[:-trim] if trim <= len(package_parts) else []
        module_parts = [*package_parts]
        if remainder:
            module_parts.extend(remainder.split("."))
        module = ".".join(module_parts)
        return module_map.get(module)

    def dependency_graph(self, path="."):
        records = self._records_for(path)
        module_map = self._module_map(self._records.values())
        files = []
        edges = []
        for record in records:
            internal = []
            for imported in record.imports:
                target = self._resolve_import(record.path, imported, module_map)
                if target is not None:
                    internal.append(target)
                    edges.append({"from": record.path, "to": target, "import": imported})
            files.append(
                {
                    "path": record.path,
                    "imports": list(record.imports),
                    "internal_imports": sorted(set(internal)),
                    "diagnostics": list(record.diagnostics),
                }
            )
        return {
            "path": str(path),
            "files": files,
            "edges": sorted(edges, key=lambda item: (item["from"], item["to"], item["import"])),
            "count": len(files),
        }

    def changed_files(self):
        try:
            result = subprocess.run(
                ["git", "status", "--short", "--untracked-files=all"],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"available": False, "reason": str(exc), "files": []}
        if result.returncode != 0:
            reason = (result.stderr or "not a git repository").strip()
            return {"available": False, "reason": reason, "files": []}
        files = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            status = line[:2]
            path = line[3:].strip() if len(line) > 3 else ""
            files.append({"status": status, "path": path})
        return {"available": True, "files": files, "count": len(files)}


def render_index_result(value):
    """Render bounded, valid JSON so ToolGateway clipping cannot corrupt it."""

    payload = dict(value) if isinstance(value, dict) else {"value": value}
    list_keys = [key for key, item in payload.items() if isinstance(item, list)]
    for max_items in (50, 20, 10, 5, 0):
        candidate = dict(payload)
        truncated = {}
        for key in list_keys:
            items = payload[key]
            if len(items) > max_items:
                candidate[key] = items[:max_items]
                truncated[key] = len(items) - max_items
        if truncated:
            candidate["truncated"] = truncated
        rendered = json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True)
        if len(rendered) <= 3600:
            return rendered
    summary = {
        "truncated": True,
        "keys": sorted(payload),
        "message": "Index result exceeded the tool output budget; narrow the path or query.",
    }
    return json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
