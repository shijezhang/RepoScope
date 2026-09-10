import json
import time
from collections import deque
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from reposcope.agent.context import pack_context
from reposcope.config import RepoScopeError
from reposcope.llm.provider import Provider
from reposcope.models import digest
from reposcope.retrieval.search import Search


class Lookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=30)


class Neighbors(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str
    symbol_id: str
    direction: Literal["incoming", "outgoing"] = "incoming"
    limit: int = Field(default=20, ge=1, le=50)


class Paths(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str
    source_id: str
    target_id: str
    max_depth: int = Field(default=6, ge=1, le=8)
    max_nodes: int = Field(default=200, ge=1, le=500)
    limit: int = Field(default=3, ge=1, le=5)


class Candidates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str


class VerifyRequest(Candidates):
    plan_id: str


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str


TOOLS = {
    "find_symbol": Lookup,
    "search_code": Lookup,
    "get_neighbors": Neighbors,
    "read_evidence": Evidence,
    "find_paths": Paths,
    "get_test_candidates": Candidates,
    "get_test_result": Candidates,
    "run_tests": VerifyRequest,
}


class Controller:
    def __init__(self, store, report, provider=None, test_executor=None):
        self.store, self.report, self.provider = store, report, provider or Provider()
        self.allowed = {report["base"]["snapshot_id"], report["head"]["snapshot_id"]}
        self.test_executor = test_executor
        self.test_submitted = False

    def call(self, name, arguments):
        if name not in TOOLS:
            raise RepoScopeError("invalid_tool", "Tool is not registered")
        args = TOOLS[name].model_validate(arguments)
        if name in {"run_tests", "get_test_result"}:
            if args.run_id != self.report["run_id"]:
                raise RepoScopeError("invalid_run", "Tool requested another analysis")
            if name == "run_tests":
                if self.test_executor is None:
                    raise RepoScopeError(
                        "test_execution_not_authorized", "This analysis permits read-only investigation"
                    )
                if args.plan_id != self.report["test_plan"]["plan_id"]:
                    raise RepoScopeError("invalid_plan", "Tool requested another test plan")
                if self.test_submitted:
                    raise RepoScopeError("budget_exceeded", "One base/head validation is allowed per analysis")
                self.test_submitted = True
                try:
                    self.test_executor()
                finally:
                    self.report = self.store.job(self.report["run_id"])["report"] or self.report
            comparisons = [row for row in self.report["executions"] if row.get("kind") == "comparison"]
            latest = comparisons[-1].get("comparison", {}) if comparisons else {}
            findings = [
                row for row in latest.get("tests", []) if row.get("finding") not in {"passed_both", "head_passed"}
            ]
            return {
                "status": self.report["test_plan"]["status"],
                "selected_count": len(self.report["test_plan"]["nodeids"]),
                "comparable": latest.get("comparable", False),
                "findings": findings[:10],
                "findings_truncated": len(findings) > 10,
                "total_test_results": len(latest.get("tests", [])),
            }
        if name == "get_test_candidates":
            if args.run_id != self.report["run_id"]:
                raise RepoScopeError("invalid_run", "Tool requested another analysis")
            plan = dict(self.report["test_plan"])
            for field, maximum in (("nodeids", 20), ("uncovered", 6), ("coverage_evidence_ids", 6)):
                values = plan.get(field, [])
                plan[field] = values[:maximum]
                plan[field + "_total"] = len(values)
                plan[field + "_truncated"] = len(values) > maximum
            plan["collection_required"] = plan["status"] == "collection_required"
            return plan
        if name == "read_evidence":
            value = self.store.get("evidence", args.evidence_id)
            if value["snapshot_id"] not in self.allowed:
                raise RepoScopeError("invalid_evidence", "Tool requested another snapshot")
            return value
        if args.snapshot_id not in self.allowed:
            raise RepoScopeError("invalid_snapshot", "Tool requested another snapshot")
        snap = self.store.snapshot(args.snapshot_id)
        if name == "find_paths":
            ids = {symbol.symbol_id for symbol in snap.symbols}
            if args.source_id not in ids or args.target_id not in ids:
                raise RepoScopeError("invalid_symbol", "Path endpoints must belong to this snapshot")
            outgoing = {}
            for edge in snap.relations:
                if edge.relation_type in {"CALLS", "IMPORTS", "INHERITS"}:
                    outgoing.setdefault(edge.source_id, []).append(edge.model_dump())
            queue = deque([(args.source_id, [], {args.source_id})])
            found, expanded, truncated = [], 0, False
            while queue and len(found) < args.limit:
                if expanded >= args.max_nodes:
                    truncated = True
                    break
                current, path, seen = queue.popleft()
                expanded += 1
                if current == args.target_id:
                    found.append(path)
                    continue
                neighbors = outgoing.get(current, [])
                if len(path) >= args.max_depth:
                    truncated |= bool(neighbors)
                    continue
                for edge in neighbors:
                    target = edge["target_id"]
                    if target not in seen:
                        queue.append((target, path + [edge], seen | {target}))
            return {"paths": found, "truncated": truncated or bool(queue), "expanded": expanded}
        if name == "get_neighbors":
            if not any(s.symbol_id == args.symbol_id for s in snap.symbols):
                raise RepoScopeError("invalid_symbol", "Unknown snapshot symbol")
            key = "target_id" if args.direction == "incoming" else "source_id"
            all_edges = [e.model_dump() for e in snap.relations if getattr(e, key) == args.symbol_id]
            return {"relations": all_edges[: args.limit], "truncated": len(all_edges) > args.limit}
        hits = Search(snap).query(args.query, args.limit)
        if name == "find_symbol":
            hits = [h for h in hits if args.query in {h["symbol"]["path"], h["symbol"]["qualname"]}]
        by_id = {s.symbol_id: s for s in snap.symbols}
        for hit in hits:
            hit["evidence_id"] = self.store.evidence(snap, by_id[hit["symbol"]["symbol_id"]])
        return hits

    def run(self, cancelled=lambda: False):
        started, results = time.monotonic(), []
        context_packing = []
        completion_reason = "decision_limit"
        decision_after_test_feedback = False
        schemas = {
            name: cls.model_json_schema()
            for name, cls in TOOLS.items()
            if name != "run_tests" or self.test_executor is not None
        }
        schemas["finish"] = {}
        run_id = self.report["run_id"]
        with self.store.connect() as db:
            prior = [
                json.loads(row[0])
                for row in db.execute("SELECT data FROM tool_calls WHERE run_id=? ORDER BY rowid", (run_id,))
            ]
        seen = {row["id"] for row in prior}
        results.extend(prior)
        try:
            for _ in range(max(0, 6 - len(prior))):
                if cancelled():
                    raise RepoScopeError("cancelled", "Agent cancelled")
                if (
                    time.monotonic() - started > 60
                    or len(results) >= 12
                    or sum(self.provider.usage[k] for k in ("input_tokens", "output_tokens")) >= 12000
                ):
                    raise RepoScopeError("budget_exceeded", "Agent lookup budget exhausted")
                remaining = 12000 - sum(self.provider.usage[k] for k in ("input_tokens", "output_tokens"))
                available_schemas = {
                    name: schema for name, schema in schemas.items() if name != "run_tests" or not self.test_submitted
                }
                context, packing = pack_context(
                    self.report,
                    results,
                    available_schemas,
                    remaining,
                    self.test_executor is not None and not self.test_submitted,
                )
                packing["available_tools"] = sorted(available_schemas)
                packing["included_result_tools"] = [row["tool"] for row in context["prior_results"]]
                context_packing.append(packing)
                decision = self.provider.decide(context, available_schemas)
                decision_after_test_feedback |= any(
                    row["tool"] in {"run_tests", "get_test_result"} and row["result"].get("total_test_results", 0) > 0
                    for row in context["prior_results"]
                )
                if decision.tool == "finish":
                    completion_reason = "model_finished"
                    break
                key = digest([run_id, decision.tool, decision.arguments])
                if key in seen:
                    raise RepoScopeError("no_new_evidence", "Repeated lookup stopped")
                seen.add(key)
                result = self.call(decision.tool, decision.arguments)
                row = {
                    "id": key,
                    "tool": decision.tool,
                    "arguments": decision.arguments,
                    "summary": decision.summary,
                    "result": result,
                }
                with self.store.connect() as db:
                    db.execute("INSERT OR IGNORE INTO tool_calls VALUES(?,?,?)", (key, run_id, json.dumps(row)))
                results.append(row)
            else:
                self.report["limitations"].append("Agent decision rounds exhausted")
        except (RepoScopeError, ValueError) as exc:
            completion_reason = exc.code if isinstance(exc, RepoScopeError) else "invalid_tool_arguments"
            self.report["limitations"].append(str(exc))
        self.report["tool_calls"] = results
        self.report["metadata"]["agent"] = {
            "model": self.provider.model,
            "usage": self.provider.usage,
            "provider_diagnostics": getattr(self.provider, "diagnostics", []),
            "seconds": time.monotonic() - started,
            "prompt_version": "lookup-v4-explicit-contracts",
            "completion_reason": completion_reason,
            "decision_after_test_feedback": decision_after_test_feedback,
            "request_options": getattr(self.provider, "request_options", {}),
            "context_packing": context_packing,
            "mode": "bounded investigation and validation"
            if self.test_executor is not None
            else "read-only gap investigation",
            "test_validation_submitted": self.test_submitted,
        }
        return self.report
