"""Correctness counterexamples found during review; assertions describe required behavior."""

import copy
from pathlib import Path

import pytest

from reposcope.analysis.impact import analyze, changed_symbols, propagate
from reposcope.config import RepoScopeError
from reposcope.indexing.parser import build_snapshot
from reposcope.reports.render import validate_report
from reposcope.repository.git import changes


def calls(snapshot, caller):
    symbols = {s.symbol_id: s for s in snapshot.symbols}
    return [
        (symbols[r.target_id].qualname, r.resolution)
        for r in snapshot.relations
        if r.relation_type == "CALLS" and symbols[r.source_id].qualname == caller
    ]


def test_method_unqualified_name_skips_class_namespace(store, git_repo):
    repo, commit, _ = git_repo
    sha = commit(
        {
            "a.py": "def target():\n    pass\nclass C:\n    def target(self):\n        pass\n    def caller(self):\n        return target()\n"
        }
    )
    snap = build_snapshot(store, repo, sha)
    assert calls(snap, "C.caller") == [("target", "resolved")]


def test_lambda_parameter_shadows_global_target(store, git_repo):
    repo, commit, _ = git_repo
    sha = commit({"a.py": "def target():\n    pass\ndef caller():\n    return lambda target: target()\n"})
    snap = build_snapshot(store, repo, sha)
    assert ("target", "resolved") not in calls(snap, "caller")


def test_reexport_assignment_is_not_resolved_to_old_import(store, git_repo):
    repo, commit, _ = git_repo
    sha = commit(
        {
            "a.py": "def target():\n    pass\n",
            "pkg.py": "from a import target\ntarget = lambda: 42\n",
            "consumer.py": "from pkg import target\ndef caller():\n    return target()\n",
        }
    )
    snap = build_snapshot(store, repo, sha)
    assert ("target", "resolved") not in calls(snap, "caller")


def test_comprehension_binding_does_not_shadow_outer_call(store, git_repo):
    repo, commit, _ = git_repo
    sha = commit(
        {
            "a.py": "def target():\n    pass\ndef caller():\n    values = [target for target in []]\n    return target()\n"
        }
    )
    snap = build_snapshot(store, repo, sha)
    assert ("target", "resolved") in calls(snap, "caller")


def test_class_header_and_first_method_same_hunk_keeps_class_seed(store, git_repo):
    repo, commit, _ = git_repo
    before = "class A:\n    pass\nclass B:\n    pass\nclass C(A):\n    def first(self):\n        return 1\n    def sibling(self):\n        return 2\n"
    base = commit({"a.py": before})
    head = commit(
        {"a.py": before.replace("class C(A):\n    def first(self):", "class C(B):\n    def first(self, value=0):")}
    )
    snap = build_snapshot(store, repo, head)
    files, _ = changes(Path(repo["path"]), base, head)
    selected = changed_symbols(snap, files, "new")
    assert any(s.qualname == "C" and s.symbol_id in selected for s in snap.symbols)


def test_validator_rejects_existing_but_unrelated_edge(store, git_repo):
    repo, commit, _ = git_repo
    source = "def target():\n    return 1\ndef caller():\n    return target()\ndef other():\n    return 7\ndef unrelated():\n    return other()\n"
    b = commit({"a.py": source})
    h = commit({"a.py": source.replace("return 1", "return 2")})
    base, head = [build_snapshot(store, repo, sha) for sha in [b, h]]
    report = analyze(store, repo, base, head, "review")
    assert validate_report(store, report)
    forged = copy.deepcopy(report)
    impact = next(i for i in forged["impacts"] if i["side"] == "head" and i["symbol"]["qualname"] == "caller")
    other_id = next(s.symbol_id for s in head.symbols if s.qualname == "other")
    edge = next(r for r in head.relations if r.relation_type == "CALLS" and r.target_id == other_id)
    impact["path"] = [edge.model_dump()]
    with pytest.raises(RepoScopeError):
        validate_report(store, forged)


def test_containment_expansion_path_is_contiguous(store, git_repo):
    repo, commit, _ = git_repo
    sha = commit(
        {
            "a.py": "FLAG = 1\ndef target():\n    return FLAG\n",
            "b.py": "from a import target\ndef caller():\n    return target()\n",
        }
    )
    snap = build_snapshot(store, repo, sha)
    seed = next(s.symbol_id for s in snap.symbols if s.path == "a.py" and s.kind == "Module")
    hits, _ = propagate(snap, {seed: "module_initialization"}, 8, 1500)
    hit = next(i for i in hits if i["symbol"]["qualname"] == "caller")
    edges = hit["path"]
    # Each ordinary dependency edge runs toward the changed seed; containment is
    # an expansion step and needs explicit direction, rather than a false CALLS-like path.
    assert all(
        a.get("traversal_target_id", a["target_id"]) == b.get("traversal_source_id", b["source_id"])
        for a, b in zip(edges, edges[1:])
    )


def test_imported_definition_reassigned_in_source_is_not_resolved(store, git_repo):
    repo, commit, _ = git_repo
    sha = commit(
        {
            "a.py": "def target():\n    pass\ntarget = lambda: 42\n",
            "consumer.py": "from a import target\ndef caller():\n    return target()\n",
        }
    )
    snap = build_snapshot(store, repo, sha)
    assert ("target", "resolved") not in calls(snap, "caller")


def test_repeated_definition_calls_and_containment_use_source_owner(store, git_repo):
    repo, commit, _ = git_repo
    sha = commit(
        {
            "a.py": "def first():\n    pass\ndef second():\n    pass\ndef caller():\n    return first()\ndef caller():\n    return second()\nclass C:\n    def old(self):\n        return first()\nclass C:\n    def new(self):\n        return second()\n"
        }
    )
    snap = build_snapshot(store, repo, sha)
    symbols = {s.symbol_id: s for s in snap.symbols}
    owners = sorted((s for s in snap.symbols if s.qualname == "caller"), key=lambda s: s.start)
    for owner, expected in zip(owners, ["first", "second"]):
        targets = [
            symbols[r.target_id].qualname
            for r in snap.relations
            if r.relation_type == "CALLS" and r.source_id == owner.symbol_id
        ]
        assert targets == [expected]
    for relation in snap.relations:
        if relation.relation_type == "CONTAINS":
            source, target = symbols[relation.source_id], symbols[relation.target_id]
            assert source.start <= target.start <= target.end <= source.end
