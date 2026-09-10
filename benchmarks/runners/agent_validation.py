"""Two-case real-provider Agent vs deterministic validation development probe.

Credentials are read by Provider through REPOSCOPE_LLM_CONFIG, never serialized.
Each arm owns a separate state store, so coverage cannot leak between arms.
"""

import argparse
import json
import os
import re
import time
import uuid
from pathlib import Path

from reposcope.config import RepoScopeError, Settings
from reposcope.graph.store import Store
from reposcope.jobs.worker import Worker
from reposcope.models import digest
from reposcope.repository.git import read_tree

try:
    from .case_locator import locate_case
except ImportError:
    from case_locator import locate_case

ROOT = Path(__file__).resolve().parents[2]
BUDGET = {
    "job_seconds": 180,
    "agent_seconds": 60,
    "model_decisions": 6,
    "tool_calls": 12,
    "tokens": 12000,
    "base_head_validations": 1,
    "test_seconds": 120,
}
QUESTION = (
    "Review the supplied Python change and use the authorized base/head test comparison when it would "
    "resolve a meaningful uncertainty. State whether existing tests detect a change and preserve "
    "unresolved dynamic behavior. Do not equate passing tests with proof of safety."
)


def validate_resume_scope(frozen, preview, scope_seal, root=ROOT, provider=None):
    """Read-only checks; constructing Provider reads config but sends no request."""
    from reposcope.agent.controller import TOOLS
    from reposcope.llm.provider import Provider

    def require(condition):
        if not condition:
            raise RepoScopeError("review_scope_changed", "Reviewed Agent scope changed; stop and review again")

    require(digest(preview) == scope_seal["preview_sha256"])
    require(frozen["question"] == preview["question"] and frozen["budget"] == preview["budget"] == BUDGET)
    require(digest(frozen["profile"]) == frozen["profile_hash"])
    require(
        preview["tool_schemas"] == {**{name: model.model_json_schema() for name, model in TOOLS.items()}, "finish": {}}
    )
    expected = {case["case_id"]: case for case in preview["cases"]}
    require(set(expected) == {"fixture-01", "fixture-04"})
    require({case["case_id"] for case in frozen["cases"]} == set(expected) and len(frozen["cases"]) == 2)
    for case in frozen["cases"]:
        path = (Path(root) / case["replay_repository"]).resolve()
        require(path.is_relative_to((Path(root) / "artifacts/benchmark-replay").resolve()))
        reviewed = {snap["side"]: snap for snap in expected[case["case_id"]]["snapshots"]}
        require(set(reviewed) == {"base", "head"})
        for side in ["base", "head"]:
            snap = reviewed[side]
            require(case[side] == snap["commit_sha"] and bool(re.fullmatch(r"[0-9a-f]{40}", case[side])))
            require(set(snap["files"]) == {"api.py", "calc.py", "test_calc.py"})
            files = read_tree(path, case[side], Settings())
            require(digest(files) == digest(snap["files"]))
    current = provider if provider is not None else Provider()
    require(current.base_url.rstrip("/") == preview["destination"].rstrip("/") and current.model == preview["model"])
    # The actual Worker constructs Provider later. Recheck the same expectations
    # in that constructor, so config edits after preflight cannot switch hosts.
    return {
        "destination": preview["destination"].rstrip("/"),
        "model": preview["model"],
        "preview_sha256": scope_seal["preview_sha256"],
        "source_hashes_verified": True,
    }


def summarize(case, arm, job, wall, measurement):
    report = job.get("report") or {}
    agent = report.get("metadata", {}).get("agent", {})
    validations = [row for row in report.get("executions", []) if row.get("kind") == "comparison"]
    comparisons = []
    for validation in validations:
        sides = {}
        for side in ("base", "head"):
            run = validation.get(side, {})
            sides[side] = {
                key: run.get(key)
                for key in ["status", "results", "environment_hash", "comparison_hash", "cleanup_status"]
            }
        comparisons.append({**sides, "comparison": validation.get("comparison")})
    return {
        "case_id": case["case_id"],
        "arm": arm,
        "run_id": job["id"],
        "job_status": job["state"],
        "base_sha": case["base"],
        "head_sha": case["head"],
        "budget": BUDGET,
        "wall_seconds": wall,
        "time_measurement": measurement,
        "model": agent.get("model"),
        "usage": agent.get("usage", {"requests": 0, "input_tokens": 0, "output_tokens": 0}),
        "agent_metadata": agent,
        "tool_calls": report.get("tool_calls", []),
        "validation_count": len(validations),
        "comparisons": comparisons,
        "test_status": report.get("test_plan", {}).get("status"),
        "selected_nodeids": report.get("test_plan", {}).get("nodeids", []),
        "limitations": report.get("limitations", []),
        "job_error": json.loads(job["error"]) if job.get("error") else None,
        "annotation_status": "unreviewed",
        "quality_metrics": None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", help="Existing run-<hex> checkpoint; finished work is never resubmitted")
    parser.add_argument(
        "--fixed-only", action="store_true", help="Run Docker deterministic arms without loading provider configuration"
    )
    parser.add_argument("--scope-seal-from", help="Reuse an approved checkpoint scope for a NEW bounded experiment")
    parser.add_argument("--output", help="Result JSON filename under benchmarks/results; defaults to this run ID")
    args = parser.parse_args()
    if args.scope_seal_from and (args.resume or not re.fullmatch(r"run-[0-9a-f]{32}", args.scope_seal_from)):
        parser.error("Use a valid prior scope checkpoint only for a new experiment")
    if args.output and (
        Path(args.output).name != args.output or not args.output.endswith(".json") or args.output.startswith(".")
    ):
        parser.error("Output must be a JSON filename")
    run_id = args.resume or "run-" + uuid.uuid4().hex
    if not re.fullmatch(r"run-[0-9a-f]{32}", run_id):
        parser.error("Invalid run checkpoint")
    root = ROOT / "artifacts/agent-validation-state" / run_id
    checkpoint = root / "checkpoint.json"
    if args.resume:
        frozen = json.loads(checkpoint.read_text())
    else:
        root.mkdir(parents=True, exist_ok=False)
        profile = json.loads((ROOT / "artifacts/state/profiles/fixture.json").read_text())
        frozen = {
            "run_id": run_id,
            "cases": [locate_case(case) for case in ["fixture-01", "fixture-04"]],
            "profile": profile,
            "profile_hash": digest(profile),
            "budget": BUDGET,
            "question": QUESTION,
        }
        checkpoint.write_text(json.dumps(frozen, indent=2) + "\n")
    if args.scope_seal_from:
        old_seal = ROOT / "artifacts/agent-validation-state" / args.scope_seal_from / "review-scope.json"
        (root / "review-scope.json").write_text(old_seal.read_text())
        frozen["scope_inherited_from"] = args.scope_seal_from
        checkpoint.write_text(json.dumps(frozen, indent=2) + "\n")
    if not args.fixed_only:
        preview = json.loads((ROOT / "docs/examples/agent-data-preview.json").read_text())
        seal = json.loads((root / "review-scope.json").read_text())
        guard = validate_resume_scope(frozen, preview, seal)
        os.environ["REPOSCOPE_LLM_EXPECTED_BASE_URL"] = guard["destination"]
        os.environ["REPOSCOPE_LLM_EXPECTED_MODEL"] = guard["model"]
    result = {
        "run_id": run_id,
        "state": "running",
        "scope": "Two unreviewed development fixtures; not a gold evaluation or causal quality claim",
        "budget": frozen["budget"],
        "profile": frozen["profile"],
        "profile_hash": frozen["profile_hash"],
        "scope_inherited_from": frozen.get("scope_inherited_from"),
        "scope_verification_before_run": guard if not args.fixed_only else None,
        "question": frozen["question"],
        "arms": [],
        "case_comparisons": [],
        "fairness": "Same base/head, profile, task, and maximum budget; independent Store and no preloaded coverage per arm",
    }
    output = ROOT / "benchmarks/results" / (args.output or f"agent-{run_id}.json")

    def save():
        data = json.dumps(result, indent=2) + "\n"
        (root / "result.json").write_text(data)
        temporary = output.with_suffix(".tmp")
        temporary.write_text(data)
        temporary.replace(output)

    save()
    for case in frozen["cases"]:
        for arm in ["fixed"] if args.fixed_only else ["fixed", "agent"]:
            state = root / case["case_id"] / arm
            store = Store(Settings(home=state, job_seconds=180, test_seconds=120))
            (state / "profiles").mkdir(exist_ok=True)
            (state / "profiles/fixture.json").write_text(json.dumps(frozen["profile"], indent=2) + "\n")
            repo = {
                "repo_id": case["case_id"],
                "name": case["case_id"],
                "path": str(ROOT / case["replay_repository"]),
                "profile_id": "fixture",
            }
            store.put("repositories", repo["repo_id"], repo)
            payload = {
                "repo_id": repo["repo_id"],
                "base": case["base"],
                "head": case["head"],
                "mode": "direct",
                "question": frozen["question"],
                "agent": arm == "agent",
                "allow_tests": True,
            }
            jid = store.enqueue("analysis", payload, key="same-budget-development-probe")
            job = store.job(jid)
            elapsed_path = state / "elapsed.json"
            if job["state"] == "queued":
                if arm == "agent":
                    validate_resume_scope(frozen, preview, seal)
                print(
                    json.dumps({"case_id": case["case_id"], "arm": arm, "run_id": jid, "state": "starting"}), flush=True
                )
                started = time.perf_counter()
                Worker(store).once()
                elapsed = {"seconds": time.perf_counter() - started, "measurement": "worker wall clock"}
                elapsed_path.write_text(json.dumps(elapsed))
            elif job["state"] not in {"completed", "failed", "cancelled", "interrupted"}:
                # A previous external interruption must not silently submit more
                # model calls or validation work. Preserve the existing task.
                raise RuntimeError("Prior worker is active or interrupted before checkpoint; inspect its existing task")
            job = store.job(jid)
            elapsed = (
                json.loads(elapsed_path.read_text())
                if elapsed_path.exists()
                else {"seconds": job["updated"] - job["created"], "measurement": "persistent job lifecycle"}
            )
            row = summarize(case, arm, job, elapsed["seconds"], elapsed["measurement"])
            result["arms"].append(row)
            save()
            print(
                json.dumps(
                    {
                        "case_id": case["case_id"],
                        "arm": arm,
                        "job_status": row["job_status"],
                        "wall_seconds": row["wall_seconds"],
                        "test_status": row["test_status"],
                        "requests": row["usage"]["requests"],
                        "tools": [t["tool"] for t in row["tool_calls"]],
                    }
                ),
                flush=True,
            )
        pair = [row for row in result["arms"] if row["case_id"] == case["case_id"]]
        if len(pair) == 2:
            fixed, agent = pair
            result["case_comparisons"].append(
                {
                    "case_id": case["case_id"],
                    "both_executed_validation": bool(fixed["comparisons"] and agent["comparisons"]),
                    "same_selected_test_pool": fixed["selected_nodeids"] == agent["selected_nodeids"],
                    "agent_extra_wall_seconds": agent["wall_seconds"] - fixed["wall_seconds"],
                    "agent_model_requests": agent["usage"]["requests"],
                    "outcome": "No formal benefit conclusion; inspect preserved execution findings, errors and limitations",
                }
            )
        save()
    result["state"] = "fixed-completed-agent-awaiting-authorization" if args.fixed_only else "completed"
    save()
    print(json.dumps({"checkpoint": str(checkpoint.relative_to(ROOT)), "output": str(output.relative_to(ROOT))}))


if __name__ == "__main__":
    main()
