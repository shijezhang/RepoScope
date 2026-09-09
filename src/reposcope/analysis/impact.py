import time
from collections import defaultdict, deque
from pathlib import Path

import networkx as nx

from reposcope.models import digest
from reposcope.repository.git import changes

PROPAGATES = {"CALLS", "IMPORTS", "INHERITS"}


def graph(snapshot):
    g = nx.MultiDiGraph()
    g.add_nodes_from(s.symbol_id for s in snapshot.symbols)
    for edge in snapshot.relations:
        g.add_edge(edge.source_id, edge.target_id, **edge.model_dump())
    return g


def changed_symbols(snapshot, files, side):
    selected = {}
    for change in files:
        path = change["old_path"] if side == "old" else change["path"]
        symbols = [s for s in snapshot.symbols if s.path == path]
        if change["status"] in {"A", "D", "R"}:
            for symbol in symbols:
                selected[symbol.symbol_id] = (
                    "added" if change["status"] == "A" else "deleted" if change["status"] == "D" else "renamed"
                )
            continue
        for hunk in change["hunks"]:
            start, count = hunk[f"{side}_start"], hunk[f"{side}_count"]
            if count == 0:
                continue  # The opposite side provides the changed seed; do not select an unrelated neighbor.
            end = start + count - 1
            # Assign each changed line to its deepest owner. Taking leaves of
            # the whole hunk loses a class header when its first method changes
            # in the same hunk.
            for line in range(start, end + 1):
                owners = [s for s in symbols if s.start <= line <= s.end and s.kind != "Module"]
                if owners:
                    depth = max(s.qualname.count(".") for s in owners)
                    for symbol in owners:
                        if symbol.qualname.count(".") == depth:
                            selected[symbol.symbol_id] = "signature_or_body"
                else:
                    for symbol in symbols:
                        if symbol.kind == "Module":
                            selected[symbol.symbol_id] = "module_initialization"
    return selected


def propagate(snapshot, seeds, max_depth, max_nodes):
    g = graph(snapshot)
    by_id = {s.symbol_id: s for s in snapshot.symbols}
    queue = deque((sid, []) for sid in sorted(seeds))
    visited, results, truncated = set(), [], False
    while queue:
        sid, path = queue.popleft()
        if sid in visited:
            continue
        if len(visited) >= max_nodes:
            truncated = True
            break
        visited.add(sid)
        results.append(
            {
                "symbol": by_id[sid].model_dump(),
                "distance": len(path),
                "path": path,
                "change_type": seeds.get(sid, "potential_caller"),
                "resolution": "candidate" if any(e["resolution"] != "resolved" for e in path) else "resolved",
            }
        )
        predecessors = [d for _, _, d in g.in_edges(sid, data=True) if d["relation_type"] in PROPAGATES]
        # A changed module can invalidate every member; explicit containment traversal only here.
        if by_id[sid].kind in {"Module", "Class"}:
            for _, target, data in g.out_edges(sid, data=True):
                if data["relation_type"] == "CONTAINS" and target not in visited:
                    if len(path) < max_depth:
                        step = {**data, "traversal_source_id": target, "traversal_target_id": sid}
                        queue.append((target, [step] + path))
                    else:
                        truncated = True
        for data in sorted(predecessors, key=lambda e: (e["source_id"], e["line"])):
            if data["source_id"] in visited:
                continue
            if len(path) >= max_depth:
                truncated = True
            else:
                step = {**data, "traversal_source_id": data["source_id"], "traversal_target_id": sid}
                queue.append((data["source_id"], [step] + path))
    return results, truncated


def mappings(base, head, files):
    renamed = {c["old_path"]: c["path"] for c in files if c["status"] == "R"}
    lookup = defaultdict(list)
    for s in head.symbols:
        lookup[(s.path, s.qualname, s.kind)].append(s)
    result = []
    for old in base.symbols:
        matches = lookup[(renamed.get(old.path, old.path), old.qualname, old.kind)]
        if len(matches) == 1:
            result.append(
                {
                    "base_id": old.symbol_id,
                    "head_id": matches[0].symbol_id,
                    "method": "path_qualname_kind",
                    "exact_content": old.content_hash == matches[0].content_hash,
                }
            )
    return result


def analyze(store, repo, base, head, run_id, mode="direct", question=""):
    started = time.perf_counter()
    files, patch_hash = changes(Path(repo["path"]), base.commit_sha, head.commit_sha)
    impacts, limitations = [], []
    for snapshot, side in [(base, "old"), (head, "new")]:
        seeds = changed_symbols(snapshot, files, side)
        hits, truncated = propagate(snapshot, seeds, store.settings.max_depth, store.settings.max_nodes)
        by_id = {s.symbol_id: s for s in snapshot.symbols}
        for hit in hits:
            hit["evidence_id"] = store.evidence(snapshot, by_id[hit["symbol"]["symbol_id"]])
            hit["side"] = "base" if side == "old" else "head"
        impacts.extend(hits)
        if truncated:
            limitations.append(f"{side}: graph traversal truncated by node/depth budget")
        if snapshot.diagnostics:
            limitations.append(f"{side}: {len(snapshot.diagnostics)} files failed parsing; analysis is partial")
        impacted_paths = {h["symbol"]["path"] for h in hits}
        unknown = [u for u in snapshot.unresolved if u["path"] in impacted_paths]
        if unknown:
            limitations.append(f"{side}: {len(unknown)} unresolved/candidate call sites in affected files")
    limitations.extend(
        [
            "Tests have not been collected or executed for this plan",
            "No valid version-bound coverage imported; test selection must fall back conservatively",
        ]
    )
    config_changes = [
        c["path"] for c in files if Path(c["path"]).name == "conftest.py" or not c["path"].endswith(".py")
    ]
    reasons = ["Coverage unavailable: collect and run the full registered test pool"]
    if config_changes:
        reasons.append("Configuration, documentation or shared fixture changed: " + ", ".join(config_changes))
    claims = [
        {
            "claim_id": digest([run_id, i["symbol"]["symbol_id"]]),
            "text": f"{i['symbol']['path']}:{i['symbol']['qualname'] or '<module>'} has potential structural impact",
            "status": "inferred",
            "evidence_ids": [i["evidence_id"]],
            "limitations": ["Graph reachability does not prove behavioral change"],
        }
        for i in impacts
    ]
    plan = {
        "plan_id": digest([base.snapshot_id, head.snapshot_id, patch_hash, "all-v1"]),
        "nodeids": [],
        "status": "collection_required",
        "strategy": "all",
        "reasons": reasons,
        "snapshot_id": head.snapshot_id,
        "uncovered": [i["symbol"]["symbol_id"] for i in impacts],
    }
    return {
        "run_id": run_id,
        "revision": 1,
        "repo_id": repo["repo_id"],
        "mode": mode,
        "question": question,
        "base": {"snapshot_id": base.snapshot_id, "commit_sha": base.commit_sha},
        "head": {"snapshot_id": head.snapshot_id, "commit_sha": head.commit_sha},
        "changes": files,
        "patch_hash": patch_hash,
        "symbol_mappings": mappings(base, head, files),
        "impacts": sorted(impacts, key=lambda i: (i["distance"], i["symbol"]["path"], i["symbol"]["start"], i["side"])),
        "claims": claims,
        "limitations": list(dict.fromkeys(limitations)),
        "test_plan": plan,
        "executions": [],
        "completeness": "partial",
        "tool_calls": [],
        "metadata": {
            "parser": base.parser_version,
            "analysis_seconds": time.perf_counter() - started,
            "base_index": base.stats,
            "head_index": head.stats,
            "model": None,
        },
    }
