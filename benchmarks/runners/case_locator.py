"""Resolve the newest available replay without mutating historical repositories."""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def locate_case(case_id, root=ROOT):
    root = Path(root).resolve()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", case_id):
        raise ValueError("Invalid replay case identifier")
    results = root / "benchmarks/results"
    candidates = [results / f"{case_id}-index.json", results / "index-consistency.json"]
    candidates = sorted(
        (path for path in candidates if path.is_file()),
        key=lambda path: (path.stat().st_mtime_ns, path.name == f"{case_id}-index.json"),
        reverse=True,
    )
    errors = []
    for manifest in candidates:
        try:
            document = json.loads(manifest.read_text())
            case = next(row for row in document["cases"] if row["case_id"] == case_id)
            source = (root / case["replay_repository"]).resolve()
            if not source.is_dir() or not source.is_relative_to(root / "artifacts/benchmark-replay"):
                raise ValueError("Replay repository is missing or outside the owned replay directory")
            for side in ("base", "head"):
                sha = case[side]
                if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
                    raise ValueError(f"Invalid frozen {side} commit")
                result = subprocess.run(
                    ["git", "-C", str(source), "cat-file", "-e", sha + "^{commit}"], capture_output=True, timeout=15
                )
                if result.returncode:
                    raise ValueError(f"Frozen {side} commit is unavailable")
            return {
                **case,
                "replay_repository": str(source.relative_to(root)),
                "replay_manifest": str(manifest.relative_to(root)),
                "ignored_unavailable_manifests": errors,
            }
        except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as exc:
            errors.append({"manifest": str(manifest.relative_to(root)), "reason": str(exc) or "Case missing"})
    raise ValueError(
        f"No available replay for {case_id}; run benchmarks/runners/replay.py --case {case_id}. Checked: {errors}"
    )
