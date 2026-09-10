"""Export frozen source-only review inputs; no predicted labels or gold promotion."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from benchmarks.runners.case_locator import ROOT, locate_case


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("Use a new output directory; existing review work is never overwritten")
    definitions = json.loads((ROOT / "benchmarks/cases/development.json").read_text())
    rows, patches = [], {}
    for definition in definitions:
        case = locate_case(definition["case_id"])
        repository = ROOT / case["replay_repository"]
        patch = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--binary",
                case["base"],
                case["head"],
            ],
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
        if not patch:
            raise ValueError(f"Empty change for {case['case_id']}")
        filename = case["case_id"] + ".diff"
        patches[filename] = patch
        rows.append(
            {
                "case_id": case["case_id"],
                "repo_id": case["repo_id"],
                "base_sha": case["base"],
                "head_sha": case["head"],
                "origin_group": case["repo_id"] + ":" + case["base"],
                "split": "development",
                "annotation_status": "unreviewed",
                "replay_repository": case["replay_repository"],
                "replay_manifest": case["replay_manifest"],
                "diff_file": filename,
                "diff_sha256": hashlib.sha256(patch).hexdigest(),
                "reviewer": None,
                "reviewed_at": None,
                "gold_impacts": None,
                "test_pool": None,
                "regression_failures": None,
                "config_hash": None,
                "evidence": [],
                "limitations": [],
            }
        )
    output.mkdir(parents=True)
    for name, patch in patches.items():
        (output / name).write_bytes(patch)
    (output / "annotations.json").write_text(json.dumps(rows, indent=2) + "\n")
    manifest = {
        "cases": len(rows),
        "annotation_status": "unreviewed",
        "labels_generated": False,
        "inputs": "fixed Git diffs; no system predictions or mutation hypotheses included",
        "split_policy": "same repo/base grouped conservatively; all current cases remain development",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({**manifest, "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()
