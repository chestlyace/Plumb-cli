"""Per-file parsing with tree-sitter.

Python, JS/TS and Dart get symbols and imports with language-specific code.
Languages in GENERIC_IMPORT_LANGUAGES get symbols and imports found by syntax
node types their grammars share. Other languages with a grammar get symbols
only. Anything else, or any parse failure, falls back.
"""

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import tree_sitter_language_pack as tslp
from tree_sitter import Node

from plumb.repomap.model import ParseLevel, Symbol

FULL_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".dart": "dart",
}

# Grammars whose import nodes are covered by the generic extractor.
GENERIC_IMPORT_LANGUAGES = frozenset(
    {
        "go",
        "java",
        "kotlin",
        "swift",
        "rust",
        "c",
        "cpp",
        "objc",
        "csharp",
        "ruby",
        "php",
        "scala",
        "lua",
    }
)
# Node types that hold one import in those grammars.
IMPORT_NODE_TYPES = frozenset(
    {
        "import_declaration",  # Go, Java, Swift, Scala
        "import_header",  # Kotlin
        "import_spec",  # Go, inside import_declaration
        "preproc_include",  # C, C++, Objective-C (#include, #import)
        "using_directive",  # C#
        "use_declaration",  # Rust
        "mod_item",  # Rust
        "namespace_use_declaration",  # PHP
    }
)
REQUIRE_NODE_TYPES = frozenset(
    {
        "require_expression",
        "require_once_expression",
        "include_expression",
        "include_once_expression",
    }
)
CALL_NODE_TYPES = frozenset({"call", "function_call", "call_expression"})
REQUIRE_NAMES = frozenset({"require", "require_relative", "load", "dofile"})
STRING_CONTENT_TYPES = frozenset(
    {"string_content", "interpreted_string_literal_content", "string_fragment"}
)
NAME_KEYWORDS = re.compile(
    r"^(import|using|use|mod|static|global|pub|from|function|const)\s+"
)


@dataclass
class RawImport:
    """An import as written; resolution to a repo file happens later."""

    module: str
    line: int
    # For Python `from x import a, b`: the imported names, which may be modules.
    names: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    language: str | None
    parsed: ParseLevel
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[RawImport] = field(default_factory=list)


def detect_language(rel_path: str) -> str | None:
    suffix = PurePosixPath(rel_path).suffix.lower()
    if suffix in FULL_LANGUAGES:
        return FULL_LANGUAGES[suffix]
    try:
        return tslp.detect_language_from_path(rel_path)
    except Exception:  # noqa: BLE001 - parsing must never crash the map
        return None


def _symbols(source: str, language: str) -> list[Symbol]:
    config = tslp.ProcessConfig(language, structure=True, imports=False, exports=False)
    result = tslp.process(source, config)
    symbols: list[Symbol] = []

    def visit(items: list[tslp.StructureItem], parent: str | None) -> None:
        for item in items:
            name = item.name or ""
            symbols.append(
                Symbol(
                    kind=str(item.kind).rsplit(".", 1)[-1].lower(),
                    name=name,
                    parent=parent,
                    start_line=item.span.start_line + 1,
                    end_line=item.span.end_line + 1,
                )
            )
            visit(item.children, name or parent)

    visit(result.structure, None)
    return symbols


def _text(node: Node | None, data: bytes) -> str:
    """Slice our own copy of the source. `Node.text` is not used: py-tree-sitter
    0.26 does not keep the parsed bytes alive, so it can read freed memory."""
    if node is None:
        return ""
    return data[node.start_byte : node.end_byte].decode("utf-8", "replace")


class _Lines:
    """1-based line numbers from byte offsets. `Node.start_point` is not used:
    reading it across many nodes crashes py-tree-sitter 0.26."""

    def __init__(self, data: bytes) -> None:
        self._newlines = [i for i, byte in enumerate(data) if byte == 0x0A]

    def of(self, node: Node) -> int:
        return bisect_right(self._newlines, node.start_byte - 1) + 1


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _python_imports(root: Node, data: bytes) -> list[RawImport]:
    imports: list[RawImport] = []
    lines = _Lines(data)
    for node in _walk(root):
        line = lines.of(node)
        if node.type == "import_statement":
            for name in node.children_by_field_name("name"):
                target = (
                    name.child_by_field_name("name")
                    if name.type == "aliased_import"
                    else name
                )
                imports.append(RawImport(module=_text(target, data), line=line))
        elif node.type == "import_from_statement":
            module = _text(node.child_by_field_name("module_name"), data)
            names = node.children_by_field_name("name")
            if not names:  # from x import *
                imports.append(RawImport(module=module, line=line))
            # One import per name, so each resolves to at most one file.
            for name in names:
                target = (
                    name.child_by_field_name("name")
                    if name.type == "aliased_import"
                    else name
                )
                imports.append(
                    RawImport(module=module, line=line, names=[_text(target, data)])
                )
    return imports


def _string_value(node: Node | None, data: bytes) -> str | None:
    if node is None or node.type != "string":
        return None
    return _text(node, data)[1:-1]


def _js_imports(root: Node, data: bytes) -> list[RawImport]:
    imports: list[RawImport] = []
    lines = _Lines(data)
    for node in _walk(root):
        line = lines.of(node)
        if node.type in ("import_statement", "export_statement"):
            value = _string_value(node.child_by_field_name("source"), data)
            if value:
                imports.append(RawImport(module=value, line=line))
        elif node.type == "call_expression":
            function = node.child_by_field_name("function")
            if function is not None and _text(function, data) in ("require", "import"):
                arguments = node.child_by_field_name("arguments")
                first = (
                    arguments.named_children[0]
                    if arguments and arguments.named_children
                    else None
                )
                value = _string_value(first, data)
                if value:
                    imports.append(RawImport(module=value, line=line))
    return imports


def _dart_imports(root: Node, data: bytes) -> list[RawImport]:
    """import, export and part directives all hold their path in a `uri` node."""
    imports: list[RawImport] = []
    lines = _Lines(data)
    for node in _walk(root):
        if node.type != "uri":
            continue
        literal = next((c for c in node.children if c.type == "string_literal"), None)
        if literal is None:
            continue
        value = _text(literal, data).strip("'\"")
        if value:
            imports.append(RawImport(module=value, line=lines.of(node)))
    return imports


def _quoted(node: Node, data: bytes) -> str | None:
    """The first string under `node`, kept in its quotes ("x" or <x>) so the
    resolver can tell a path from a dotted name."""
    for child in _walk(node):
        if child.type == "system_lib_string":
            return _text(child, data)
        if child.type in STRING_CONTENT_TYPES:
            return f'"{_text(child, data)}"'
    return None


def _strip_alias(name: str) -> str:
    name = re.sub(r"\s+as\s+\S+$", "", name.strip())
    return re.sub(r"(\.\*|\._|::\*)$", "", name)


def _dotted_names(text: str) -> list[str]:
    """`import static a.b.C;` -> [`a.b.C`]; `use a::{b, c as d};` -> [`a::b`, `a::c`]."""
    name = text.strip().rstrip(";").strip()
    while NAME_KEYWORDS.match(name):
        name = NAME_KEYWORDS.sub("", name, count=1)
    if "=" in name:  # C# `using X = A.B;`
        name = name.split("=", 1)[1].strip()
    if "{" not in name:
        name = _strip_alias(name)
        return [name.split()[0]] if name.split() else []
    # Grouped imports (Rust, PHP, Scala): expand each item under the prefix.
    prefix, _, rest = name.partition("{")
    separator = next((sep for sep in ("::", "\\", ".") if prefix.endswith(sep)), "")
    prefix = prefix.removesuffix(separator) if separator else prefix.strip()
    names: list[str] = []
    for item in rest.rsplit("}", 1)[0].replace("{", ",").replace("}", ",").split(","):
        item = _strip_alias(item)
        if not item or item == "self":
            if prefix:
                names.append(prefix)
            continue
        names.append(f"{prefix}{separator}{item}" if prefix else item)
    return list(dict.fromkeys(n.rstrip(":.\\") for n in names if n.rstrip(":.\\")))


def _generic_imports(root: Node, data: bytes) -> list[RawImport]:
    imports: list[RawImport] = []
    lines = _Lines(data)
    for node in _walk(root):
        module: str | None = None
        if node.type in IMPORT_NODE_TYPES:
            if node.type == "import_declaration" and any(
                c.type == "import_spec" for c in _walk(node)
            ):
                continue  # Go: each import_spec is handled on its own.
            quoted = _quoted(node, data)
            line = lines.of(node)
            for module in [quoted] if quoted else _dotted_names(_text(node, data)):
                imports.append(RawImport(module=module, line=line))
            continue
        elif node.type in REQUIRE_NODE_TYPES:
            module = _quoted(node, data)
        elif node.type in CALL_NODE_TYPES:
            function = (
                node.child_by_field_name("method")
                or node.child_by_field_name("name")
                or node.child_by_field_name("function")
            )
            if function is not None and _text(function, data) in REQUIRE_NAMES:
                arguments = node.child_by_field_name("arguments")
                module = _quoted(arguments, data) if arguments is not None else None
        if module:
            imports.append(RawImport(module=module, line=lines.of(node)))
    return imports


def parse_file(rel_path: str, source: str) -> ParseResult:
    """Never raises: any failure, including a failed grammar download, falls back."""
    language = detect_language(rel_path)
    if language is None:
        return ParseResult(language=None, parsed="fallback")
    try:
        symbols = _symbols(source, language)
        if (
            language not in FULL_LANGUAGES.values()
            and language not in GENERIC_IMPORT_LANGUAGES
        ):
            return ParseResult(language=language, parsed="symbols", symbols=symbols)
        data = source.encode("utf-8")
        tree = tslp.get_parser(language).parse(data)
        if language in GENERIC_IMPORT_LANGUAGES:
            raw = _generic_imports(tree.root_node, data)
        elif language == "python":
            raw = _python_imports(tree.root_node, data)
        elif language == "dart":
            raw = _dart_imports(tree.root_node, data)
        else:
            raw = _js_imports(tree.root_node, data)
        return ParseResult(
            language=language, parsed="full", symbols=symbols, imports=raw
        )
    except Exception:  # noqa: BLE001 - parsing must never crash the map
        return ParseResult(language=language, parsed="fallback")
