"""Per-file resolution results bound to observed export lookup dependencies."""

from reposcope.config import RepoScopeError
from reposcope.models import digest


class ResolutionCache:
    def __init__(self, store, parsed, by_full, metadata, references, modules):
        self.store = store
        self.parsed = parsed
        self.by_full = by_full
        self.metadata = metadata
        self.references = references
        self.modules = modules
        self.dependencies = {}
        self.fingerprints = {}

    def fingerprint(self, kind, target):
        key = (kind, target)
        if key in self.fingerprints:
            return self.fingerprints[key]
        direct = [
            (self.references[s.symbol_id], bool(self.metadata[s.symbol_id].get("decorated")))
            for s in self.by_full.get(target, [])
        ]
        value = {"direct": direct}
        if kind == "export":
            shadows = []
            for index, char in enumerate(target):
                if char != ".":
                    continue
                module = target[:index]
                root_name = target[index + 1 :].split(".")[0]
                for path in self.modules.get(module, []):
                    info = self.parsed[path]
                    shadows.append(
                        (
                            path,
                            root_name in info["bindings"].get("", []),
                            [
                                self.references[s.symbol_id]
                                for s in self.by_full.get(module + "." + root_name, [])
                                if s.path == path
                            ],
                            [item for item in info["imports"] if item["scope"] == "" and item["local"] == root_name],
                        )
                    )
            module, _, name = target.rpartition(".")
            exports = [
                (
                    path,
                    name in self.parsed[path]["bindings"].get("", []),
                    [item for item in self.parsed[path]["imports"] if item["scope"] == "" and item["local"] == name],
                )
                for path in self.modules.get(module, [])
            ]
            value.update(shadows=shadows, exports=exports)
        self.fingerprints[key] = digest(value)
        return self.fingerprints[key]

    def observe(self, kind, target):
        self.dependencies[(kind, target)] = self.fingerprint(kind, target)

    def load(self, key):
        try:
            row = self.store.get("resolution_cache", key)
        except RepoScopeError as exc:
            if exc.code == "not_found":
                return None
            raise
        checksum = row.pop("checksum", None)
        if checksum != digest(row):
            raise RepoScopeError("resolution_cache_invalid", "Resolution cache checksum mismatch")
        if any(self.fingerprint(kind, target) != expected for kind, target, expected in row["dependencies"]):
            return None
        return row

    def save(self, key, relations, unresolved):
        edges = []
        for relation in relations:
            row = relation.model_dump(exclude={"snapshot_id"})
            row["source_id"] = self.references[relation.source_id]
            row["target_id"] = self.references[relation.target_id]
            edges.append(row)
        unknowns = []
        for unknown in unresolved:
            row = dict(unknown)
            if "candidate_ids" in row:
                row["candidate_ids"] = [self.references[sid] for sid in row["candidate_ids"]]
            unknowns.append(row)
        row = {
            "dependencies": [[kind, target, value] for (kind, target), value in sorted(self.dependencies.items())],
            "relations": edges,
            "unresolved": unknowns,
        }
        self.store.put("resolution_cache", key, {**row, "checksum": digest(row)})
