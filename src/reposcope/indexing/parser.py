"""Finite, conservative AST resolver. Parsing never imports target code."""

import ast
import time
from collections import defaultdict
from pathlib import PurePosixPath

from reposcope.config import RepoScopeError
from reposcope.models import Relation, Snapshot, Symbol, digest
from reposcope.repository.git import git, read_tree

VERSION = "ast312-resolver-v3"


def module_name(path):
    p = PurePosixPath(path).with_suffix("")
    parts = list(p.parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def parse_file(path, source):
    tree = ast.parse(source, filename=path, feature_version=(3, 12))
    definitions, imports, calls, bases, bindings = [], [], [], [], defaultdict(set)
    module = module_name(path)

    class Visitor(ast.NodeVisitor):
        scope = ""
        kind = "Module"

        def definition(self, node, kind):
            parent, parent_kind = self.scope, self.kind
            qual = f"{parent}.{node.name}" if parent else node.name
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            signature = (
                ast.unparse(node.args)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                else ", ".join(ast.unparse(b) for b in node.bases)
            )
            definitions.append(
                {
                    "qualname": qual,
                    "kind": kind,
                    "start": start,
                    "end": node.end_lineno,
                    "signature": signature,
                    "parent": parent,
                    "decorated": bool(node.decorator_list),
                }
            )
            if kind == "Class":
                bases.extend({"scope": qual, "line": node.lineno, "expr": ast.unparse(b)} for b in node.bases)
            # Decorators/defaults execute in the outer scope.
            for dec in node.decorator_list:
                self.visit(dec)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for default in node.args.defaults + [x for x in node.args.kw_defaults if x is not None]:
                    self.visit(default)
                bindings[qual].update(a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs)
                if node.args.vararg:
                    bindings[qual].add(node.args.vararg.arg)
                if node.args.kwarg:
                    bindings[qual].add(node.args.kwarg.arg)
            self.scope, self.kind = qual, kind
            for statement in node.body:
                self.visit(statement)
            self.scope, self.kind = parent, parent_kind

        def visit_FunctionDef(self, node):
            kind = "Method" if self.kind == "Class" else "Function"
            if node.name.startswith("test_") and (
                PurePosixPath(path).name.startswith("test_") or PurePosixPath(path).name.endswith("_test.py")
            ):
                kind = "TestCase"
            self.definition(node, kind)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            self.definition(node, "Class")

        def visit_Import(self, node):
            for alias in node.names:
                imports.append(
                    {
                        "scope": self.scope,
                        "local": alias.asname or alias.name.split(".")[0],
                        "target": alias.name if alias.asname else alias.name.split(".")[0],
                        "line": node.lineno,
                    }
                )

        def visit_ImportFrom(self, node):
            package = module.split(".") if path.endswith("/__init__.py") else module.split(".")[:-1]
            if node.level:
                package = package[: len(package) - node.level + 1]
                target = ".".join(package + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            for alias in node.names:
                imports.append(
                    {
                        "scope": self.scope,
                        "local": alias.asname or alias.name,
                        "target": f"{target}.{alias.name}".strip("."),
                        "line": node.lineno,
                    }
                )

        def visit_Lambda(self, node):
            # Lambda bodies have their own parameter/binding scope. Until they
            # receive symbol identities, retain calls as explicit unknown sites.
            for default in node.args.defaults + [x for x in node.args.kw_defaults if x is not None]:
                self.visit(default)
            parent = self.scope
            self.scope = f"{parent}.<lambda:{node.lineno}>"
            self.visit(node.body)
            self.scope = parent

        def comprehension(self, node):
            # Python 3 comprehension targets do not leak into the containing
            # function. The first iterable is evaluated in the outer scope.
            self.visit(node.generators[0].iter)
            parent = self.scope
            self.scope = f"{parent}.<comprehension:{node.lineno}>"
            for index, generator in enumerate(node.generators):
                if index:
                    self.visit(generator.iter)
                self.visit(generator.target)
                for condition in generator.ifs:
                    self.visit(condition)
            if isinstance(node, ast.DictComp):
                self.visit(node.key)
                self.visit(node.value)
            else:
                self.visit(node.elt)
            self.scope = parent

        visit_ListComp = comprehension
        visit_SetComp = comprehension
        visit_DictComp = comprehension
        visit_GeneratorExp = comprehension

        def visit_Name(self, node):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                bindings[self.scope].add(node.id)

        def visit_Call(self, node):
            calls.append({"scope": self.scope, "expr": ast.unparse(node.func), "line": node.lineno})
            self.generic_visit(node)

    Visitor().visit(tree)
    return {
        "definitions": definitions,
        "imports": imports,
        "calls": calls,
        "bases": bases,
        "bindings": {k: sorted(v) for k, v in bindings.items()},
    }


def build_snapshot(store, repo, sha, incremental=True, cancelled=lambda: False):
    started = time.perf_counter()
    root = __import__("pathlib").Path(repo["path"])
    files = read_tree(root, sha, store.settings, cancelled)
    tree_hash = git(root, "rev-parse", sha + "^{tree}")
    sid = digest([repo["repo_id"], sha, VERSION])
    symbols, relations, unresolved, diagnostics = [], [], [], []
    parsed, reused = {}, 0
    by_full, by_local = defaultdict(list), defaultdict(list)
    metadata = {}
    for path, source in sorted(files.items()):
        if cancelled():
            raise RepoScopeError("cancelled", "Snapshot parsing interrupted")
        if not path.endswith(".py"):
            continue
        cache_id = digest([path, source, VERSION])
        info = None
        if incremental:
            try:
                info = store.get("ast_cache", cache_id)
                reused += 1
            except Exception as exc:
                if not isinstance(exc, RepoScopeError) or exc.code != "not_found":
                    raise
        if info is None:
            try:
                info = parse_file(path, source)
                store.put("ast_cache", cache_id, info)
            except (SyntaxError, RecursionError) as exc:
                diagnostics.append({"path": path, "code": "unsupported_syntax", "message": str(exc)})
                continue
        parsed[path] = info
        module = module_name(path)
        defs = [
            {
                "qualname": "",
                "kind": "Module",
                "start": 1,
                "end": max(1, len(source.splitlines())),
                "signature": "",
                "parent": None,
            }
        ] + info["definitions"]
        duplicate = defaultdict(int)
        for d in defs:
            identity = (d["qualname"], d["kind"])
            ordinal = duplicate[identity]
            duplicate[identity] += 1
            sym = Symbol(
                symbol_id=digest([sid, path, *identity, ordinal]),
                snapshot_id=sid,
                path=path,
                module=module,
                qualname=d["qualname"],
                kind=d["kind"],
                start=d["start"],
                end=d["end"],
                signature=d["signature"],
                content_hash=digest("\n".join(source.splitlines()[d["start"] - 1 : d["end"]])),
            )
            symbols.append(sym)
            by_full[".".join(x for x in [module, d["qualname"]] if x)].append(sym)
            by_local[(path, d["qualname"])].append(sym)
            metadata[sym.symbol_id] = d
    edges = set()

    def edge(source, target, kind, line, resolution="resolved"):
        key = (source.symbol_id, target.symbol_id, kind, line, resolution)
        if key not in edges:
            edges.add(key)
            relations.append(
                Relation(
                    source_id=source.symbol_id,
                    target_id=target.symbol_id,
                    relation_type=kind,
                    snapshot_id=sid,
                    line=line,
                    resolution=resolution,
                )
            )

    for sym in symbols:
        parent = metadata[sym.symbol_id]["parent"]
        if parent is not None:
            for p in by_local[(sym.path, parent)]:
                if p.start <= sym.start and sym.end <= p.end:
                    edge(p, sym, "CONTAINS", sym.start)

    def resolve(path, scope, expr):
        info = parsed[path]
        if "<" in scope:
            return [], "anonymous expression scope not resolved"
        parts = expr.split(".")
        if not all(p.isidentifier() for p in parts):
            return [], "dynamic expression"
        # self/cls dispatch remains candidate, since subclasses and patching are not type-proven.
        if parts[0] in {"self", "cls"} and len(parts) == 2:
            owner = scope.rsplit(".", 1)[0] if "." in scope else ""
            return by_local[(path, f"{owner}.{parts[1]}")], "dynamic method dispatch"
        scopes = []
        current = scope
        while current:
            scopes.append(current)
            current = current.rsplit(".", 1)[0] if "." in current else ""
        scopes.append("")
        class_scopes = {d["qualname"] for d in info["definitions"] if d["kind"] == "Class"}
        for lexical in scopes:
            # A method/nested class does not close over the surrounding class
            # namespace; only evaluating the class body itself can use it.
            if lexical in class_scopes and lexical != scope:
                continue
            if parts[0] in info["bindings"].get(lexical, []):
                return [], "local binding shadows symbol"
            aliases = [i for i in info["imports"] if i["scope"] == lexical and i["local"] == parts[0]]
            if aliases:
                if by_local[(path, ".".join([lexical, parts[0]]).strip("."))]:
                    return [], "import and definition binding conflict"
                candidates = []
                for alias in aliases:
                    target = ".".join([alias["target"], *parts[1:]])
                    # Follow imports through the exporting module's bindings,
                    # including direct definitions that were later reassigned.
                    candidates.extend(export_target(target, set()))
                return candidates, "import target unavailable" if not candidates else ""
            target = ".".join([lexical, expr]).strip(".")
            candidates = by_local[(path, target)]
            if candidates:
                return candidates, ""
        return [], "external or unresolved name"

    def export_target(target, visited):
        if target in visited:
            return []
        visited.add(target)
        # A recorded definition is not a live export after assignment. Check
        # the root binding in its owning module before accepting direct hits.
        for export_path, export_info in parsed.items():
            prefix = module_name(export_path) + "."
            if target.startswith(prefix):
                root_name = target[len(prefix) :].split(".")[0]
                if root_name in export_info["bindings"].get("", []):
                    return []
        if by_full[target]:
            return by_full[target]
        module, _, name = target.rpartition(".")
        found = []
        for path, info in parsed.items():
            if module_name(path) == module:
                if name in info["bindings"].get("", []):
                    continue  # Explicit assignment invalidates an import re-export.
                for item in info["imports"]:
                    if item["scope"] == "" and item["local"] == name:
                        found.extend(export_target(item["target"], visited))
        return found

    for path, info in parsed.items():
        if cancelled():
            raise RepoScopeError("cancelled", "Symbol resolution interrupted")
        for item in info["imports"]:
            sources = [s for s in by_local[(path, item["scope"])] if s.start <= item["line"] <= s.end]
            targets = export_target(item["target"], set())
            if not targets:
                parent = item["target"].rsplit(".", 1)[0]
                targets = by_full[parent]
            for source in sources:
                for target in targets:
                    edge(source, target, "IMPORTS", item["line"], "resolved" if len(targets) == 1 else "candidate")
            if item["local"] == "*":
                unresolved.append({**item, "path": path, "reason": "star import"})
        for kind, items in [("CALLS", info["calls"]), ("INHERITS", info["bases"])]:
            for item in items:
                scope = item["scope"]
                lookup_scope = (
                    scope.rsplit(".", 1)[0]
                    if kind == "INHERITS" and "." in scope
                    else ("" if kind == "INHERITS" else scope)
                )
                targets, reason = resolve(path, lookup_scope, item["expr"])
                sources = [s for s in by_local[(path, scope)] if s.start <= item["line"] <= s.end]
                if not targets or reason:
                    unresolved.append(
                        {**item, "path": path, "reason": reason, "candidate_ids": [s.symbol_id for s in targets]}
                    )
                for source in sources:
                    for target in targets:
                        level = (
                            "candidate"
                            if reason or len(targets) > 1 or metadata[target.symbol_id].get("decorated")
                            else "resolved"
                        )
                        edge(source, target, kind, item["line"], level)
    snap = Snapshot(
        snapshot_id=sid,
        repo_id=repo["repo_id"],
        commit_sha=sha,
        tree_hash=tree_hash,
        manifest_hash=digest(files),
        parser_version=VERSION,
        files=files,
        symbols=symbols,
        relations=relations,
        unresolved=unresolved,
        diagnostics=diagnostics,
        stats={
            "seconds": time.perf_counter() - started,
            "python_files": len(parsed),
            "reused_files": reused,
            "parsed_files": len(parsed) - reused,
            "mode": "parse-incremental" if incremental else "full",
            "resolution": "all references re-resolved",
            "retrieval": "sparse-ready; dense optional",
        },
    )
    # Partial parse is retained for diagnostics but never published as a ready default snapshot.
    if diagnostics:
        snap.status = "partial"
    store.put("snapshots", sid, snap)
    return snap


def semantic_hash(snapshot):
    return digest(
        {
            "symbols": sorted([s.model_dump() for s in snapshot.symbols], key=lambda s: s["symbol_id"]),
            "relations": sorted([r.model_dump() for r in snapshot.relations], key=lambda r: digest(r)),
            "files": snapshot.files,
            "unresolved": snapshot.unresolved,
            "diagnostics": snapshot.diagnostics,
        }
    )
