"""Coverage facts are useful only under their exact execution binding."""

from pathlib import PurePosixPath

from reposcope.config import RepoScopeError
from reposcope.models import digest

CONFIG_HASH = digest({"collector": "reposcope-context-v1", "branch": True, "config_file": False})


def binding(snapshot, environment_hash, test_suite_hash):
    return {
        "snapshot_id": snapshot.snapshot_id,
        "tree_hash": snapshot.tree_hash,
        "environment_hash": environment_hash,
        "test_suite_hash": test_suite_hash,
        "coverage_config_hash": CONFIG_HASH,
    }


def validity(evidence, expected):
    mismatches = [key for key, value in expected.items() if evidence.get(key) != value]
    return {"status": "stale" if mismatches else "valid", "mismatches": mismatches}


def import_coverage(store, raw, expected, execution_id):
    """Accept runner-produced contexts, preserving unattributed/shared fixture lines."""
    if raw.get("binding") != expected:
        raise RepoScopeError("coverage_binding_mismatch", "Coverage does not match frozen execution")
    records = []
    for row in raw.get("contexts", []):
        path = PurePosixPath(row["path"])
        if path.is_absolute() or ".." in path.parts:
            raise RepoScopeError("invalid_coverage", "Coverage path must be repository relative")
        phase = row.get("phase", "unattributed")
        if phase not in {"setup", "call", "teardown", "unattributed"}:
            raise RepoScopeError("invalid_coverage", "Invalid coverage phase")
        lines = sorted(set(row.get("lines", [])))
        if any(type(line) is not int or line < 1 for line in lines):
            raise RepoScopeError("invalid_coverage", "Invalid coverage line")
        record = {
            **expected,
            "execution_id": execution_id,
            "nodeid": row.get("nodeid"),
            "phase": phase,
            "path": str(path),
            "lines": lines,
            "provenance": "container-runtime",
            "status": "valid",
        }
        record["evidence_id"] = digest(record)
        records.append(record)
    for record in records:
        store.put("coverage", record["evidence_id"], record)
    return records
