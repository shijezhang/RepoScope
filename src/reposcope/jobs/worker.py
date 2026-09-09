import json
import threading
import time
import uuid
from pathlib import Path

from reposcope.agent.controller import Controller
from reposcope.analysis.impact import analyze
from reposcope.analysis.selection import select_tests
from reposcope.config import RepoScopeError
from reposcope.coverage import binding, validity
from reposcope.indexing.parser import build_snapshot
from reposcope.reports.render import validate_report
from reposcope.repository.git import comparison, resolve

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def compare_results(base, head, base_catalog):
    comparable = (
        bool(base.get("comparison_hash"))
        and base.get("comparison_hash") == head.get("comparison_hash")
        and bool(base.get("environment_hash"))
        and base.get("environment_hash") == head.get("environment_hash")
    )
    old = {row["nodeid"]: row["status"] for row in base.get("results", [])}
    rows = []
    for row in head.get("results", []):
        nodeid, status = row["nodeid"], row["status"]
        before = old.get(nodeid, "not_run")
        finding = "inconclusive"
        if status in {"failed", "error"}:
            if base_catalog is not None and nodeid not in base_catalog:
                finding = "new_test_failure"
            elif comparable and before == "passed":
                finding = "suspected_regression" if status == "failed" else "head_execution_error"
            elif comparable and before in {"failed", "error"}:
                finding = "existing_failure"
            else:
                finding = "head_validation_failure"
        elif status == "passed":
            finding = "passed_both" if comparable and before == "passed" else "head_passed"
        rows.append({"nodeid": nodeid, "base_status": before, "head_status": status, "finding": finding})
    return {
        "comparable": comparable,
        "status": "suspected_regression"
        if any(r["finding"] == "suspected_regression" for r in rows)
        else "inconclusive",
        "tests": rows,
        "reason": "Single execution cannot exclude flakiness; base/head comparison requires matching test assets, configuration and environment",
    }


class Worker:
    def __init__(self, store):
        self.store = store
        self.owner = uuid.uuid4().hex

    def once(self):
        jid = self.store.claim(self.owner)
        if not jid:
            self.recover_interrupted()
            return False
        stop = threading.Event()
        deadline = time.monotonic() + self.store.settings.job_seconds

        def pulse():
            while not stop.wait(5):
                self.store.heartbeat(jid, self.owner)

        def stop_reason():
            job = self.store.job(jid)
            if job["owner"] != self.owner:
                return "interrupted"
            if job["cancel"]:
                return "cancelled"
            if time.monotonic() > deadline:
                return "budget_exceeded"
            return None

        def cancelled():
            return stop_reason() is not None

        thread = threading.Thread(target=pulse, daemon=True)
        thread.start()
        try:
            self.recover_interrupted()
            job = self.store.job(jid)
            p = job["payload"]
            if job["kind"] == "test":
                result = self.tests(jid, p, cancelled, stop_reason)
            else:
                repo = self.store.get("repositories", p["repo_id"])
                root = Path(repo["path"])
                if job["kind"] == "snapshot":
                    snap = build_snapshot(self.store, repo, resolve(root, p["commit"]), cancelled=cancelled)
                    result = {"snapshot_id": snap.snapshot_id, "status": snap.status, "stats": snap.stats}
                else:
                    b, h = comparison(root, p["base"], p["head"], p.get("mode", "direct"))
                    base = build_snapshot(self.store, repo, b, cancelled=cancelled)
                    if cancelled():
                        raise RepoScopeError("cancelled", "Cancelled during preparation")
                    head = build_snapshot(self.store, repo, h, cancelled=cancelled)
                    self.store.update(jid, "analyzing", "Comparing both immutable snapshots", owner=self.owner)
                    result = analyze(self.store, repo, base, head, jid, p.get("mode", "direct"), p.get("question", ""))
                    self.store.update(jid, "analyzing", "Deterministic report saved", result=result, owner=self.owner)

                    def execute_validation():
                        self.tests(
                            jid, {"run_id": jid, "plan_id": result["test_plan"]["plan_id"]}, cancelled, stop_reason
                        )
                        self.store.update(jid, "analyzing", "Validation feedback saved", owner=self.owner)

                    if p.get("agent") and not cancelled():
                        self.store.update(jid, "retrieving", "Looking up evidence gaps", owner=self.owner)
                        result = Controller(
                            self.store, result, test_executor=execute_validation if p.get("allow_tests") else None
                        ).run(cancelled)
                    elif p.get("allow_tests") and not cancelled():
                        execute_validation()
                        result = self.store.job(jid)["report"]
                    validate_report(self.store, result)
            reason = stop_reason()
            if reason:
                result.update(status=reason, completeness="partial")
            self.store.update(
                jid,
                "cancelled"
                if reason == "cancelled"
                else "interrupted"
                if reason == "interrupted"
                else "failed"
                if reason
                else "completed",
                result=result,
                owner=self.owner,
                error=json.dumps({"code": reason, "message": "Execution stopped: " + reason, "retryable": False})
                if reason
                else None,
            )
        except Exception as exc:
            reason = stop_reason()
            code = reason or (exc.code if isinstance(exc, RepoScopeError) else "internal_error")
            self.store.update(
                jid,
                "cancelled" if code == "cancelled" else "interrupted" if code == "interrupted" else "failed",
                error=json.dumps({"code": code, "message": str(exc)[:2000], "retryable": False}),
                owner=self.owner,
            )
        finally:
            stop.set()
            thread.join(timeout=1)
        return True

    def recover_interrupted(self):
        from reposcope.execution.runner import TestRunner

        runner = TestRunner(self.store)
        with self.store.connect() as db:
            pending = [row[0] for row in db.execute("SELECT job_id FROM recovery WHERE state='pending'")]
        for jid in pending:
            results = []
            for suffix in ("-collect-head", "-collect-base", "-head", "-base"):
                try:
                    results.append(runner.recover(jid + suffix))
                except RepoScopeError as exc:
                    if exc.code != "not_found":
                        raise
            needs_attention = any(result.get("cleanup_status") == "unconfirmed" for result in results)
            with self.store.connect() as db:
                db.execute(
                    "UPDATE recovery SET state=?,data=? WHERE job_id=?",
                    ("needs_attention" if needs_attention else "completed", json.dumps(results), jid),
                )
                self.store._event(
                    db,
                    jid,
                    "interrupted",
                    "Container cleanup unconfirmed; administrator attention required"
                    if needs_attention
                    else "Known execution identities reconciled; no tests resubmitted",
                )

    def select_plan(self, report, base, head, collection, base_collection):
        """Base call-phase coverage is a recommendation clue, never head verification."""
        pool = collection["nodeids"]
        reasons = [r for r in report["test_plan"].get("reasons", []) if not r.startswith("Coverage unavailable")]
        if base.diagnostics or head.diagnostics or base.unresolved or head.unresolved:
            reasons.append("Parser diagnostics or unresolved calls require full test pool")
        if any("truncated" in text for text in report.get("limitations", [])):
            reasons.append("Truncated impact graph requires full test pool")
        if base_collection.get("status") != "completed" or base_collection.get("environment_hash") != collection.get(
            "environment_hash"
        ):
            reasons.append("Base collection or matching environment unavailable")
        base_symbols = {symbol.symbol_id: symbol for symbol in base.symbols}
        mappings = {row["head_id"]: row["base_id"] for row in report.get("symbol_mappings", [])}
        targets = {}
        for impact in report.get("impacts", []):
            symbol = impact["symbol"]
            sid = symbol["symbol_id"] if impact["side"] == "base" else mappings.get(symbol["symbol_id"])
            if sid not in base_symbols:
                reasons.append("New or unmapped impact lacks base coverage")
                continue
            if symbol["kind"] == "Module" and impact.get("change_type") != "potential_caller":
                reasons.append("Module initialization change requires full test pool")
            targets[sid] = max(targets.get(sid, 0), 1 / (1 + impact.get("distance", 0)))
        expected = binding(base, base_collection.get("environment_hash"), base_collection.get("test_suite_hash"))
        records = [
            row
            for row in self.store.list("coverage")
            if row.get("status") == "valid"
            and validity(row, expected)["status"] == "valid"
            and row.get("phase") == "call"
            and row.get("nodeid") in pool
            and row.get("nodeid") in base_collection.get("nodeids", [])
        ]
        covered, evidence_ids = {}, []
        for row in records:
            hits = [
                sid
                for sid in targets
                if base_symbols[sid].path == row["path"]
                and any(base_symbols[sid].start <= line <= base_symbols[sid].end for line in row["lines"])
            ]
            if hits:
                covered.setdefault(row["nodeid"], set()).update(hits)
                evidence_ids.append(row["evidence_id"])
        if not targets:
            reasons.append("No sufficiently mapped impact targets")
        selection = select_tests(
            pool, targets, covered, budget=self.store.settings.test_seconds, fallback_reasons=reasons
        )
        if selection["uncovered"] and selection["strategy"] != "all":
            selection = select_tests(
                pool,
                targets,
                covered,
                fallback_reasons=["Base coverage leaves impact targets uncovered; full fallback"],
            )
        changed_tests = {
            node for node in pool if any(node.split("::")[0] == change["path"] for change in report.get("changes", []))
        }
        selection["nodeids"] = sorted(
            set(selection["nodeids"]) | changed_tests | (set(pool) - set(base_collection.get("nodeids", [])))
        )
        selection.update(
            coverage_source_snapshot=base.snapshot_id,
            coverage_evidence_ids=sorted(set(evidence_ids)),
            coverage_use="base recommendation only; head behavior remains unverified until execution",
        )
        return selection

    def tests(self, jid, payload, cancelled, stop_reason=None):
        from reposcope.execution.runner import TestRunner

        run_id = payload["run_id"]
        original = self.store.job(run_id)
        report = original["report"]
        if not report or report["test_plan"]["plan_id"] != payload["plan_id"]:
            raise RepoScopeError("invalid_plan", "Test plan is no longer current")
        repo = self.store.get("repositories", report["repo_id"])
        runner = TestRunner(self.store)
        base, head = (self.store.snapshot(report[s]["snapshot_id"]) for s in ["base", "head"])
        self.store.update(jid, "testing", "Collecting tests in the registered isolated environment", owner=self.owner)
        collection = runner.collect(head, repo, jid + "-collect-head", cancelled)
        pending_limit = "Tests collected; selected tests have not completed execution"
        if collection["status"] == "completed":
            report["limitations"] = [
                text
                for text in report["limitations"]
                if text != "Tests have not been collected or executed for this plan"
                and not text.startswith("Test collection unavailable or failed:")
                and text != pending_limit
            ]
            report["limitations"].append(pending_limit)
        report["executions"].append({"execution_id": jid, "kind": "collection", **collection})
        if collection["status"] not in {"completed", "collected", "passed"}:
            report["limitations"].append("Test collection unavailable or failed: " + collection["status"])
            report["test_plan"]["status"] = collection["status"]
        elif not cancelled():
            base_collection = runner.collect(base, repo, jid + "-collect-base", cancelled)
            report["executions"].append({"kind": "collection", **base_collection})
            selection = self.select_plan(report, base, head, collection, base_collection)
            nodes = selection["nodeids"]
            report["test_plan"].update(selection, status="collected")
            if selection.get("coverage_evidence_ids"):
                report["limitations"] = [
                    text
                    for text in report["limitations"]
                    if text != "No valid version-bound coverage imported; test selection must fall back conservatively"
                ]
                report["limitations"].append(
                    "Base coverage informs recommendations; head coverage is limited to actually executed tests"
                )
            common = (
                sorted(set(nodes).intersection(base_collection.get("nodeids", [])))
                if base_collection.get("status") == "completed"
                else []
            )
            head_result = (
                runner.run(head, repo, nodes, jid + "-head", cancelled)
                if nodes and not cancelled()
                else {"status": "not_run", "results": []}
            )
            base_result = (
                runner.run(base, repo, common, jid + "-base", cancelled)
                if common and not cancelled()
                else {"status": "not_run", "results": []}
            )
            comparison_result = compare_results(
                base_result,
                head_result,
                set(base_collection.get("nodeids", [])) if base_collection.get("status") == "completed" else None,
            )
            report["executions"].append(
                {
                    "execution_id": jid,
                    "kind": "comparison",
                    "base": base_result,
                    "head": head_result,
                    "comparison": comparison_result,
                }
            )
            report["test_plan"]["status"] = head_result["status"]
            if head_result["status"] == "completed":
                report["limitations"] = [text for text in report["limitations"] if text != pending_limit]
            report["limitations"].append(
                "Selected tests passing does not prove all behavior safe; base/head failures need flakiness review"
            )
        if cancelled():
            reason = (
                stop_reason() if stop_reason else "cancelled" if self.store.job(jid)["cancel"] else "budget_exceeded"
            )
            report["test_plan"]["status"] = reason
            report["limitations"].append("Test work stopped: " + reason)
        if self.store.job(jid)["owner"] not in {None, self.owner}:
            raise RepoScopeError("interrupted", "Worker no longer owns task")
        if self.store.job(jid)["state"] == "interrupted":
            raise RepoScopeError("interrupted", "Task lease expired")
        report["revision"] += 1
        validate_report(self.store, report)
        self.store.update(run_id, original["state"], "Test evidence appended as new report revision", result=report)
        return {"execution_id": jid, "analysis_id": run_id, "status": report["test_plan"]["status"]}

    def run(self):
        while True:
            if not self.once():
                time.sleep(0.5)
