"""Resolve raw imports to files in the repo. Unresolved absolute imports are external."""

import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass

JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")


@dataclass
class Resolution:
    targets: list[str]
    external: str | None = None


class PythonResolver:
    def __init__(self, files: Iterable[str]) -> None:
        self.files = {f for f in files if f.endswith((".py", ".pyi"))}

    def _module_file(self, base: str, dotted: str) -> str | None:
        stem = posixpath.join(base, dotted.replace(".", "/")) if dotted else base
        for candidate in (f"{stem}.py", f"{stem}.pyi", f"{stem}/__init__.py"):
            candidate = posixpath.normpath(candidate)
            if candidate in self.files:
                return candidate
        return None

    def _absolute(self, importer: str, dotted: str) -> str | None:
        """Match by path suffix so any source layout (src/, monorepo) works.

        When several files match, the one sharing the longest folder prefix
        with the importer wins, then the shortest path.
        """
        tail = dotted.replace(".", "/")
        endings = (f"{tail}.py", f"{tail}.pyi", f"{tail}/__init__.py")
        matches = [
            f for f in self.files if any(f == e or f.endswith("/" + e) for e in endings)
        ]
        importer_dirs = posixpath.dirname(importer).split("/")

        def shared(path: str) -> int:
            count = 0
            for a, b in zip(importer_dirs, posixpath.dirname(path).split("/")):
                if a != b or not a:
                    break
                count += 1
            return count

        return (
            min(matches, key=lambda f: (-shared(f), f.count("/"), f))
            if matches
            else None
        )

    def resolve(self, importer: str, module: str, names: list[str]) -> Resolution:
        if module.startswith("."):
            level = len(module) - len(module.lstrip("."))
            rest = module[level:]
            base = posixpath.dirname(importer)
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            submodules = [
                f
                for name in names
                if (f := self._module_file(base, f"{rest}.{name}".strip(".")))
            ]
            if submodules:
                return Resolution(targets=submodules)
            target = self._module_file(base, rest)
            return Resolution(targets=[target] if target else [])
        submodules = [
            f for name in names if (f := self._absolute(importer, f"{module}.{name}"))
        ]
        if submodules:
            return Resolution(targets=submodules)
        target = self._absolute(importer, module)
        if target:
            return Resolution(targets=[target])
        return Resolution(targets=[], external=module.split(".")[0])


class JsResolver:
    def __init__(self, files: Iterable[str]) -> None:
        self.files = set(files)

    def _find(self, stem: str) -> str | None:
        candidates = [stem]
        root, ext = posixpath.splitext(stem)
        if ext in (".js", ".jsx", ".mjs", ".cjs"):
            # TypeScript ESM imports name the compiled .js file.
            candidates += [root + e for e in JS_EXTENSIONS]
        candidates += [stem + e for e in JS_EXTENSIONS]
        candidates += [f"{stem}/index{e}" for e in JS_EXTENSIONS]
        return next((c for c in candidates if c in self.files), None)

    def resolve(self, importer: str, module: str) -> Resolution:
        if module.startswith(("./", "../")) or module in (".", ".."):
            stem = posixpath.normpath(
                posixpath.join(posixpath.dirname(importer), module)
            )
            target = self._find(stem)
            return Resolution(targets=[target] if target else [])
        parts = module.split("/")
        package = "/".join(parts[:2]) if module.startswith("@") else parts[0]
        return Resolution(targets=[], external=package)


class DartResolver:
    """Relative paths, and package:<name>/ for packages defined in the repo."""

    def __init__(self, files: Iterable[str], packages: dict[str, str]) -> None:
        self.files = set(files)
        # Package name -> folder holding its pubspec.yaml.
        self.packages = packages

    def resolve(self, importer: str, module: str) -> Resolution:
        if module.startswith("dart:"):
            return Resolution(targets=[], external=module)
        if module.startswith("package:"):
            name, _, path = module.removeprefix("package:").partition("/")
            if name not in self.packages:
                return Resolution(targets=[], external=name)
            target = posixpath.normpath(
                posixpath.join(self.packages[name], "lib", path)
            )
        else:
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(importer), module)
            )
        return Resolution(targets=[target] if target in self.files else [])


# Languages that import each other's files.
LANGUAGE_FAMILIES: tuple[frozenset[str], ...] = (
    frozenset({"c", "cpp", "objc"}),
    frozenset({"java", "kotlin", "scala"}),
)
NAME_PREFIXES_TO_DROP = frozenset({"crate", "self", "super", "global"})
NAME_SEPARATORS = re.compile(r"::|\.|\\|/")


def _family(language: str | None) -> frozenset[str]:
    for family in LANGUAGE_FAMILIES:
        if language in family:
            return family
    return frozenset({language}) if language else frozenset()


def _shared_dirs(a: str, b: str) -> int:
    count = 0
    for x, y in zip(posixpath.dirname(a).split("/"), posixpath.dirname(b).split("/")):
        if x != y or not x:
            break
        count += 1
    return count


class GenericResolver:
    """Resolves imports found by the generic extractor.

    A quoted module ("x/y.h", <x.h>, "lib/db") is a path: tried next to the
    importer, then from the repo root, then by path ending, dropping leading
    segments while at least two remain. Anything else is a
    dotted or :: name: its segments are matched against file paths (without
    extension) and folders, keeping at least two segments, and then with up
    to two trailing segments dropped, since names often end in a class or
    function. Matches stay within the importer's language family. When
    several match, the one nearest the importer wins, then the shortest.
    """

    def __init__(self, languages: dict[str, str | None]) -> None:
        self.languages = languages
        self.files = set(languages)
        self.by_stem_tail: dict[str, list[tuple[str, str]]] = {}
        self.by_dir_tail: dict[str, list[str]] = {}
        self.dir_files: dict[str, list[str]] = {}
        for rel in sorted(languages):
            self.dir_files.setdefault(posixpath.dirname(rel), []).append(rel)
        dirs = set(self.dir_files)
        for rel in languages:
            stem = posixpath.splitext(rel)[0]
            self.by_stem_tail.setdefault(posixpath.basename(stem), []).append(
                (stem, rel)
            )
        for folder in dirs:
            if folder:
                self.by_dir_tail.setdefault(posixpath.basename(folder), []).append(
                    folder
                )

    def _nearest(self, importer: str, matches: list[str]) -> str | None:
        if not matches:
            return None
        return min(matches, key=lambda f: (-_shared_dirs(importer, f), f.count("/"), f))

    def _in_family(self, family: frozenset[str] | None, rel: str) -> bool:
        """`None` means any language."""
        return family is None or self.languages.get(rel) in family

    def _dir_files(self, folder: str, family: frozenset[str] | None) -> list[str]:
        return [r for r in self.dir_files.get(folder, []) if self._in_family(family, r)]

    def _match_tail(
        self, importer: str, tail: str, family: frozenset[str] | None
    ) -> list[str]:
        """Files whose path (with or without extension) ends with `tail`, else
        the files of the nearest folder ending with `tail` (two or more segments)."""
        last = posixpath.basename(tail)
        files = [
            rel
            for stem, rel in self.by_stem_tail.get(posixpath.splitext(last)[0], [])
            if self._in_family(family, rel)
            and any(p == tail or p.endswith("/" + tail) for p in (stem, rel))
        ]
        best = self._nearest(importer, files)
        if best:
            return [best]
        if "/" not in tail:
            # A one-segment name only matches a file, or Rust's <name>/mod.rs:
            # matching any folder of that name is too loose.
            return self._match_tail(importer, f"{tail}/mod", family) if family else []
        folders = [
            d
            for d in self.by_dir_tail.get(last, [])
            if (d == tail or d.endswith("/" + tail)) and self._dir_files(d, family)
        ]
        if not folders:
            return []
        # A folder's files are compared to the importer as if the folder were a file in it.
        folder = min(
            folders,
            key=lambda d: (-_shared_dirs(importer, d + "/_"), d.count("/"), d),
        )
        return self._dir_files(folder, family)

    def _path(self, importer: str, module: str) -> Resolution:
        path = module[1:-1]
        system = module.startswith("<")
        variants = (
            [path] if "/" in path or "." not in path else [path, path.replace(".", "/")]
        )
        for variant in variants:
            if not system:
                for base in (posixpath.dirname(importer), ""):
                    exact = posixpath.normpath(posixpath.join(base, variant))
                    if exact in self.files:
                        return Resolution(targets=[exact])
            segments = [p for p in variant.split("/") if p not in ("", ".", "..")]
            # Drop leading segments (e.g. a Go module host), keeping at least two.
            for start in range(len(segments) - min(2, len(segments)) + 1):
                tail = "/".join(segments[start:])
                targets = self._match_tail(importer, tail, None)
                if targets:
                    return Resolution(targets=targets)
        parts = [p for p in path.split("/") if p]
        if not parts:
            return Resolution(targets=[])
        external = (
            "/".join(parts[:3]) if "." in parts[0] and len(parts) > 1 else parts[0]
        )
        return Resolution(targets=[], external=external)

    def _name(self, importer: str, module: str) -> Resolution:
        segments = [s for s in NAME_SEPARATORS.split(module) if s]
        while segments and segments[0] in NAME_PREFIXES_TO_DROP:
            segments = segments[1:]
        if not segments:
            return Resolution(targets=[])
        family = _family(self.languages.get(importer))
        for end in range(len(segments), max(len(segments) - 3, 0), -1):
            keep = min(2, end)
            for start in range(end - keep + 1):
                targets = self._match_tail(
                    importer, "/".join(segments[start:end]), family
                )
                if targets:
                    return Resolution(targets=targets)
        return Resolution(targets=[], external=segments[0])

    def resolve(self, importer: str, module: str) -> Resolution:
        if module[:1] in ('"', "<"):
            return self._path(importer, module)
        return self._name(importer, module)
