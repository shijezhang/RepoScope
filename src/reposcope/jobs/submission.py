"""Shared API/CLI test submission with explicit, bounded retry attempts."""

import json

from reposcope.config import RepoScopeError
from reposcope.models import TestRunInput, digest

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def test_attempts(store, run_id):
    with store.connect() as db:
        rows = db.execute(
            "SELECT id,state,payload FROM jobs WHERE kind='test' AND json_extract(payload,'$.run_id')=? ORDER BY created",
            (run_id,),
        ).fetchall()
    return [
        {"execution_id": row["id"], "status": row["state"], "attempt": json.loads(row["payload"]).get("attempt", 1)}
        for row in rows
    ]


def submit_tests(store, run_id, request: TestRunInput):
    job = store.job(run_id)
    if job["state"] != "completed" or not job["report"]:
        raise RepoScopeError("snapshot_not_ready", "Wait for completed analysis")
    if request.plan_id != job["report"]["test_plan"]["plan_id"]:
        raise RepoScopeError("invalid_plan", "Test plan does not belong to this report")
    payload = {"run_id": run_id, "plan_id": request.plan_id}
    key = ["test", run_id, request.plan_id]
    if request.attempt > 1:
        if not request.reason or not request.reason.strip():
            raise RepoScopeError("retry_reason_required", "An explicit retry reason is required")
        prior = next((row for row in test_attempts(store, run_id) if row["attempt"] == request.attempt - 1), None)
        if not prior or prior["status"] not in TERMINAL:
            raise RepoScopeError(
                "previous_attempt_not_finished", "Previous attempt must reach a terminal state before retry"
            )
        payload.update(attempt=request.attempt, reason=request.reason)
        key.append(request.attempt)
    # Keep first-attempt identities compatible with existing persisted reports.
    idempotency_key = "test:" + digest(key[1:])
    return store.enqueue("test", payload, key=idempotency_key)
