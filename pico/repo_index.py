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
# The cache filename remains v2 for backwards compatibility with existing
# workspaces and review artifacts.  The payload schema is v3 and invalidates
# older records before loading them.
INDEX_SCHEMA_VERSION = "repo-index-v3"
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


@dataclass(frozen=True)
class CallRecord:
    """A bounded, explainable static call observation.

    ``resolved_symbol`` is intentionally optional: Python's dynamic dispatch
    means that an AST can observe ``obj.run()`` without proving the runtime
    type of ``obj``.  Consumers must inspect ``resolution`` and ``confidence``.
    """

    caller_symbol: str
    callee_text: str
    resolved_symbol: str = ""
    path: str = ""
    line: int = 1
    column: int = 0
    confidence: float = 0.0
    resolution: str = "unresolved"

    def to_dict(self):
        return {
            "caller_symbol": self.caller_symbol,
            "callee_text": self.callee_text,
            "resolved_symbol": self.resolved_symbol,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "confidence": self.confidence,
            "resolution": self.resolution,
        }

    @classmethod
    def from_dict(cls, value):
        return cls(
            caller_symbol=str(value.get("caller_symbol", "module")),
            callee_text=str(value.get("callee_text", "")),
            resolved_symbol=str(value.get("resolved_symbol", "")),
            path=str(value.get("path", "")),
            line=int(value.get("line", 1)),
            column=int(value.get("column", 0)),
            confidence=float(value.get("confidence", 0.0) or 0.0),
            resolution=str(value.get("resolution", "unresolved")),
        )


@dataclass
class FileRecord:
    path: str
    language: str
    fingerprint: str
    size: int
    symbols: tuple[SymbolRecord, ...] = ()
    calls: tuple[CallRecord, ...] = ()
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
            "calls": [call.to_dict() for call in self.calls],
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
            calls=tuple(CallRecord.from_dict(item) for item in value.get("calls", [])),
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


class _CallVisitor(ast.NodeVisitor):
    """Collect calls with the lexical function/method that made them."""

    def __init__(self):
        self.calls: list[CallRecord] = []
        self.scope: list[str] = []
        self.scope_kinds: list[str] = []

    @property
    def caller_symbol(self):
        return ".".join(self.scope) if self.scope else "module"

    @staticmethod
    def _callee_text(node):
        try:
            return ast.unparse(node)
        except (AttributeError, ValueError):
            if isinstance(node, ast.Name):
                return node.id
            if isinstance(node, ast.Attribute):
                return node.attr
            return "<dynamic>"

    def visit_ClassDef(self, node):
        self.scope.append(node.name)
        self.scope_kinds.append("class")
        self.generic_visit(node)
        self.scope.pop()
        self.scope_kinds.pop()

    def _visit_function(self, node):
        self.scope.append(node.name)
        self.scope_kinds.append("function")
        self.generic_visit(node)
        self.scope.pop()
        self.scope_kinds.pop()

    def visit_FunctionDef(self, node):
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node):
        self._visit_function(node)

    def visit_Call(self, node):
        self.calls.append(
            CallRecord(
                caller_symbol=self.caller_symbol,
                callee_text=self._callee_text(node.func),
                line=int(getattr(node, "lineno", 1)),
                column=int(getattr(node, "col_offset", 0)),
            )
        )
        self.generic_visit(node)


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
                calls=(),
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
                calls=(),
                diagnostics=(f"decode error: {exc}",),
            )
        except OSError as exc:
            return FileRecord(
                path=relative_path,
                language=language,
                fingerprint=f"read-error:{signature}",
                size=size,
                calls=(),
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
                calls=(),
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
                calls=(),
                diagnostics=(diagnostic,),
                source_lines=source_lines,
            )

        symbol_visitor = _SymbolVisitor()
        symbol_visitor.visit(tree)
        import_visitor = _ImportVisitor()
        import_visitor.visit(tree)
        call_visitor = _CallVisitor()
        call_visitor.visit(tree)
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
            calls=tuple(
                CallRecord(
                    caller_symbol=item.caller_symbol,
                    callee_text=item.callee_text,
                    path=relative_path,
                    line=item.line,
                    column=item.column,
                )
                for item in sorted(
                    call_visitor.calls,
                    key=lambda item: (item.line, item.column, item.callee_text),
                )
            ),
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

    @staticmethod
    def _symbol_key(path, qualified_name):
        return f"{path}:{qualified_name}"

    @staticmethod
    def _module_name(path):
        value = Path(path).with_suffix("")
        parts = list(value.parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    @classmethod
    def _symbol_entries(cls, records):
        entries = []
        for record in records:
            for symbol in record.symbols:
                entries.append(
                    {
                        "key": cls._symbol_key(record.path, symbol.qualified_name),
                        "path": record.path,
                        "module": cls._module_name(record.path),
                        "symbol": symbol,
                    }
                )
        return entries

    @classmethod
    def _resolve_call(cls, record, call, entries):
        """Resolve only cases for which the evidence is explainable."""

        callee = str(call.callee_text or "").strip()
        terminal = callee.rsplit(".", 1)[-1]
        name_matches = [item for item in entries if item["symbol"].name == terminal]
        qualified_matches = []
        if "." in callee:
            same_module_prefix = f"{cls._module_name(record.path)}."
            qualified_matches = [
                item
                for item in name_matches
                if item["symbol"].qualified_name == callee
                or item["module"] + "." + item["symbol"].qualified_name == callee
                or item["module"] + "." + item["symbol"].qualified_name == same_module_prefix + callee
            ]
        candidates = qualified_matches or name_matches
        if len(qualified_matches) == 1:
            item = qualified_matches[0]
            return CallRecord(
                caller_symbol=call.caller_symbol,
                callee_text=call.callee_text,
                resolved_symbol=item["key"],
                path=record.path,
                line=call.line,
                column=call.column,
                confidence=1.0,
                resolution="exact",
            )
        if len(candidates) == 1:
            item = candidates[0]
            return CallRecord(
                caller_symbol=call.caller_symbol,
                callee_text=call.callee_text,
                resolved_symbol=item["key"],
                path=record.path,
                line=call.line,
                column=call.column,
                confidence=0.75,
                resolution="unique_name",
            )
        return CallRecord(
            caller_symbol=call.caller_symbol,
            callee_text=call.callee_text,
            path=record.path,
            line=call.line,
            column=call.column,
            confidence=0.0,
            resolution="ambiguous" if len(candidates) > 1 else "unresolved",
        )

    @classmethod
    def _resolved_calls(cls, records):
        entries = cls._symbol_entries(records)
        return [
            cls._resolve_call(record, call, entries)
            for record in records
            for call in record.calls
        ]

    @staticmethod
    def _call_to_dict(call):
        return call.to_dict()

    @staticmethod
    def _definition_to_dict(entry):
        return {"path": entry["path"], **entry["symbol"].to_dict()}

    @staticmethod
    def _bounded_items(items, limit=50):
        items = list(items)
        return items[:limit], max(0, len(items) - limit)

    @staticmethod
    def _call_identity(call):
        return (
            call.path,
            call.line,
            call.column,
            call.caller_symbol,
            call.callee_text,
            call.resolved_symbol,
        )

    @classmethod
    def _walk_call_edges(cls, calls, entries, start_keys, direction, depth):
        """Walk only resolved edges, keeping the result deterministic and bounded."""

        entry_keys = {item["key"] for item in entries}
        frontier = set(start_keys) & entry_keys
        seen = set()
        collected = []
        for _ in range(depth):
            if not frontier:
                break
            if direction == "callers":
                matches = [call for call in calls if call.resolved_symbol in frontier]
                next_frontier = {
                    f"{call.path}:{call.caller_symbol}"
                    for call in matches
                    if call.caller_symbol != "module"
                }
            else:
                matches = [
                    call
                    for call in calls
                    if f"{call.path}:{call.caller_symbol}" in frontier
                ]
                next_frontier = {
                    call.resolved_symbol
                    for call in matches
                    if call.resolved_symbol
                }
            for call in sorted(
                matches,
                key=lambda item: (
                    item.path,
                    item.line,
                    item.column,
                    item.caller_symbol,
                    item.callee_text,
                ),
            ):
                identity = cls._call_identity(call)
                if identity not in seen:
                    seen.add(identity)
                    collected.append(call)
            frontier = next_frontier & entry_keys
        return collected

    def _target_entries(self, target, path, records):
        target = str(target or "").strip()
        entries = self._symbol_entries(records)
        scope_path = str(path or ".").replace("\\", "/").strip("/") or "."
        if scope_path != ".":
            entries = [
                item
                for item in entries
                if item["path"] == scope_path
                or item["path"].startswith(scope_path.rstrip("/") + "/")
            ]
        target_path = None
        try:
            candidate = Path(target)
            if not candidate.is_absolute():
                candidate = self.root / candidate
            if candidate.exists():
                target_path = self._relative(candidate)
        except (OSError, ValueError):
            target_path = None
        if target_path is not None:
            prefix = target_path.rstrip("/")
            return [item for item in entries if item["path"] == prefix or item["path"].startswith(prefix + "/")]
        terminal = target.rsplit(".", 1)[-1]
        return [
            item
            for item in entries
            if item["symbol"].name == terminal
            or item["symbol"].qualified_name == target
            or item["symbol"].qualified_name.endswith("." + target)
        ]

    def call_graph(self, symbol="", path=".", direction="both", depth=1):
        """Return a bounded, best-effort Python call graph.

        This is a navigation aid, not a compiler or language-server result.
        ``confidence`` is attached to every edge so callers can keep uncertain
        dynamic dispatch visible instead of silently treating it as fact.
        """

        direction = str(direction or "both").lower()
        if direction not in {"callers", "callees", "both"}:
            raise ValueError("direction must be callers, callees, or both")
        depth = int(depth)
        if depth < 1 or depth > 2:
            raise ValueError("depth must be in [1, 2]")
        records = self._records_for(".")
        target_entries = self._target_entries(symbol, path, records) if str(symbol).strip() else []
        target_keys = {item["key"] for item in target_entries}
        calls = self._resolved_calls(records)
        entries = self._symbol_entries(records)
        callers = self._walk_call_edges(calls, entries, target_keys, "callers", depth)
        target_callers = {
            item["path"] + ":" + item["symbol"].qualified_name
            for item in target_entries
        }
        callees = self._walk_call_edges(calls, entries, target_callers, "callees", depth)
        caller_items, caller_truncated = self._bounded_items(
            [self._call_to_dict(item) for item in callers]
        )
        callee_items, callee_truncated = self._bounded_items(
            [self._call_to_dict(item) for item in callees]
        )
        diagnostics = [
            f"{record.path}: {item}"
            for record in records
            for item in record.diagnostics
        ]
        unresolved = sum(1 for call in calls if call.resolution in {"unresolved", "ambiguous"})
        return {
            "schema_version": "repo-index-v3-call-graph-v1",
            "query": str(symbol),
            "path": str(path),
            "direction": direction,
            "depth": depth,
            "definitions": [self._definition_to_dict(item) for item in target_entries[:50]],
            "callers": caller_items if direction in {"callers", "both"} else [],
            "callees": callee_items if direction in {"callees", "both"} else [],
            "confidence": 0.75 if target_entries else 0.0,
            "unresolved_call_count": unresolved,
            "diagnostics": sorted(set(diagnostics + [
                "Python calls are resolved conservatively; dynamic dispatch may remain unresolved."
            ])),
            "truncated": {
                "callers": caller_truncated,
                "callees": callee_truncated,
            },
        }

    def analyze_impact(self, target, path=".", depth=1):
        """Find likely callers, importers and tests affected by a target."""

        depth = int(depth)
        if depth < 1 or depth > 2:
            raise ValueError("depth must be in [1, 2]")
        records = self._records_for(".")
        entries = self._target_entries(target, path, records)
        target_keys = {item["key"] for item in entries}
        target_paths = {item["path"] for item in entries}
        calls = self._resolved_calls(records)
        all_entries = self._symbol_entries(records)
        direct_callers = self._walk_call_edges(calls, all_entries, target_keys, "callers", 1)
        callers = self._walk_call_edges(calls, all_entries, target_keys, "callers", depth)
        target_callers = {
            item["path"] + ":" + item["symbol"].qualified_name
            for item in entries
        }
        direct_callees = self._walk_call_edges(calls, all_entries, target_callers, "callees", 1)
        callees = self._walk_call_edges(calls, all_entries, target_callers, "callees", depth)

        module_map = self._module_map(records)
        reverse_importers = []
        related_tests = []
        target_modules = {
            self._module_name(item["path"])
            for item in entries
        }
        target_names = {
            item["symbol"].name
            for item in entries
        }
        for record in records:
            internal_targets = []
            for imported in record.imports:
                resolved = self._resolve_import(record.path, imported, module_map)
                if resolved in target_paths or self._module_name(resolved or "") in target_modules:
                    internal_targets.append(resolved or imported)
            if internal_targets:
                reverse_importers.append(
                    {
                        "path": record.path,
                        "imports": sorted(set(internal_targets)),
                        "confidence": 0.9,
                        "reason": "internal_import",
                    }
                )

            if record.path.startswith("tests/") or Path(record.path).name.startswith("test_"):
                source = "\n".join(record.source_lines)
                name_hit = any(re.search(rf"\b{re.escape(name)}\b", source) for name in target_names)
                module_hit = any(module and module in source for module in target_modules)
                if name_hit or module_hit or internal_targets:
                    related_tests.append(
                        {
                            "path": record.path,
                            "confidence": 0.65 if name_hit else 0.8,
                            "reason": "symbol_or_module_reference",
                        }
                    )

        caller_items, caller_truncated = self._bounded_items([self._call_to_dict(item) for item in callers])
        callee_items, callee_truncated = self._bounded_items([self._call_to_dict(item) for item in callees])
        direct_caller_items, direct_caller_truncated = self._bounded_items(
            [self._call_to_dict(item) for item in direct_callers]
        )
        direct_callee_items, direct_callee_truncated = self._bounded_items(
            [self._call_to_dict(item) for item in direct_callees]
        )
        indirect_caller_items = [item for item in caller_items if item not in direct_caller_items]
        indirect_callee_items = [item for item in callee_items if item not in direct_callee_items]
        importer_items, importer_truncated = self._bounded_items(reverse_importers)
        test_items, test_truncated = self._bounded_items(related_tests)
        candidate_files = sorted(
            set(target_paths)
            | {item["path"] for item in caller_items}
            | {item["path"] for item in callee_items}
            | {item["path"] for item in importer_items}
            | {item["path"] for item in test_items}
        )
        candidate_files, candidate_truncated = self._bounded_items(candidate_files)
        diagnostics = [
            f"{record.path}: {item}"
            for record in records
            for item in record.diagnostics
        ]
        diagnostics.append(
            "Impact analysis is conservative navigation evidence; inspect source and run tests before editing."
        )
        if not entries:
            diagnostics.append("target was not resolved to an indexed symbol or file")
        confidences = [item["confidence"] for item in caller_items + callee_items]
        confidences.extend(item["confidence"] for item in importer_items + test_items)
        confidence = round(sum(confidences) / len(confidences), 2) if confidences else (0.0 if not entries else 0.5)
        return {
            "schema_version": "repo-index-v3-impact-v1",
            "target": str(target),
            "path": str(path),
            "depth": depth,
            "definitions": [self._definition_to_dict(item) for item in entries[:50]],
            "direct_callers": direct_caller_items,
            "direct_callees": direct_callee_items,
            "indirect_callers": indirect_caller_items,
            "indirect_callees": indirect_callee_items,
            "reverse_importers": importer_items,
            "related_tests": test_items,
            "candidate_files": candidate_files,
            "confidence": confidence,
            "unresolved_call_count": sum(1 for call in calls if call.resolution in {"unresolved", "ambiguous"}),
            "diagnostics": sorted(set(diagnostics)),
            "truncated": {
                "direct_callers": direct_caller_truncated,
                "direct_callees": direct_callee_truncated,
                "all_callers": caller_truncated,
                "all_callees": callee_truncated,
                "reverse_importers": importer_truncated,
                "related_tests": test_truncated,
                "candidate_files": candidate_truncated,
            },
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
