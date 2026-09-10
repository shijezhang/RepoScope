"""Dependency invalidation counterexamples and snapshot identity rebinding."""

import pytest

from reposcope.config import RepoScopeError
from reposcope.indexing.parser import build_snapshot, semantic_hash

BASE = {
    "target.py": "def work():\n    return 1\n",
    "facade.py": "from target import work\n",
    "consumer.py": "from facade import work\ndef caller():\n    return work()\n",
    "unrelated.py": "def independent():\n    return 0\n",
}


@pytest.mark.parametrize(
    "change",
    [
        {"target.py": "def work():\n    return 2\n"},
        {"target.py": None},
        {"target.py": "def renamed():\n    return 1\n"},
        {"target.py": "@decorate\ndef work():\n    return 1\n"},
        {"target.py": "def work():\n    return 1\nwork = lambda: 2\n"},
        {"facade.py": "from target import work\nwork = lambda: 2\n"},
        {"facade.py": "from consumer import caller as work\n"},
        {"facade.py": "from facade import work\n"},
        {"target.py": "def work():\n    return 1\ndef work():\n    return 2\n"},
    ],
)
def test_changed_exports_match_full_resolution(store, git_repo, change):
    repo, commit, _ = git_repo
    base = build_snapshot(store, repo, commit(BASE), incremental=False)
    sha = commit(change)
    incremental = build_snapshot(store, repo, sha)
    assert incremental.stats["reused_resolution_files"] >= 1
    if change == {"target.py": "def work():\n    return 2\n"}:
        # Body changes do not alter exports; callers need only new snapshot IDs.
        assert incremental.stats["reused_resolution_files"] == 3
    full = build_snapshot(store, repo, sha, incremental=False)
    assert semantic_hash(incremental) == semantic_hash(full)
    old_ids = {s.symbol_id for s in base.symbols}
    assert not any(r.source_id in old_ids or r.target_id in old_ids for r in incremental.relations)
    assert not any(sid in old_ids for row in incremental.unresolved for sid in row.get("candidate_ids", []))


def test_previously_missing_target_becoming_available_invalidates_consumer(store, git_repo):
    repo, commit, _ = git_repo
    build_snapshot(store, repo, commit({k: v for k, v in BASE.items() if k != "target.py"}))
    sha = commit({"target.py": BASE["target.py"]})
    incremental = build_snapshot(store, repo, sha)
    assert incremental.stats["resolved_files"] == 3
    assert incremental.stats["reused_resolution_files"] == 1
    full = build_snapshot(store, repo, sha, incremental=False)
    assert semantic_hash(incremental) == semantic_hash(full)


def test_import_parent_fallback_dependency_tracks_added_module(store, git_repo):
    repo, commit, _ = git_repo
    build_snapshot(store, repo, commit({"consumer.py": "from missing import name\n", "unrelated.py": "value = 1\n"}))
    sha = commit({"missing.py": "value = 2\n"})
    incremental = build_snapshot(store, repo, sha)
    assert incremental.stats["reused_resolution_files"] == 1
    assert semantic_hash(incremental) == semantic_hash(build_snapshot(store, repo, sha, incremental=False))


def test_package_prefix_shadow_invalidates_nested_export(store, git_repo):
    repo, commit, _ = git_repo
    build_snapshot(
        store,
        repo,
        commit(
            {
                "pkg/__init__.py": "",
                "pkg/sub.py": "def work():\n    return 1\n",
                "consumer.py": "from pkg.sub import work\ndef caller():\n    return work()\n",
            }
        ),
    )
    sha = commit({"pkg/__init__.py": "sub = object()\n"})
    incremental = build_snapshot(store, repo, sha)
    assert semantic_hash(incremental) == semantic_hash(build_snapshot(store, repo, sha, incremental=False))
    assert any(row.get("reason") == "import target unavailable" for row in incremental.unresolved)


def test_corrupt_resolution_cache_is_rejected(store, git_repo):
    repo, commit, _ = git_repo
    sha = commit(BASE)
    before = build_snapshot(store, repo, sha)
    with store.connect() as db:
        db.execute("UPDATE resolution_cache SET data='{}'")
    with pytest.raises(RepoScopeError) as error:
        build_snapshot(store, repo, sha)
    assert error.value.code == "resolution_cache_invalid"
    assert semantic_hash(store.snapshot(before.snapshot_id)) == semantic_hash(before)
