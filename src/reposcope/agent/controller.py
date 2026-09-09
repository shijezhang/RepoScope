import json
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str


TOOLS = {"find_symbol": Lookup, "search_code": Lookup, "get_neighbors": Neighbors, "read_evidence": Evidence}


class Controller:
    def __init__(self, store, report, provider=None):
        self.store, self.report, self.provider = store, report, provider or Provider()
        self.allowed = {report["base"]["snapshot_id"], report["head"]["snapshot_id"]}

    def call(self, name, arguments):
        if name not in TOOLS:
            raise RepoScopeError("invalid_tool", "Tool is not registered")
        args = TOOLS[name].model_validate(arguments)
        if name == "read_evidence":
            value = self.store.get("evidence", args.evidence_id)
            if value["snapshot_id"] not in self.allowed:
                raise RepoScopeError("invalid_evidence", "Tool requested another snapshot")
            return value
        if args.snapshot_id not in self.allowed:
            raise RepoScopeError("invalid_snapshot", "Tool requested another snapshot")
        snap = self.store.snapshot(args.snapshot_id)
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
        schemas = {name: cls.model_json_schema() for name, cls in TOOLS.items()}
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
                context = {
                    "question": self.report["question"],
                    "base": self.report["base"],
                    "head": self.report["head"],
                    "limitations": self.report["limitations"],
                    "impact_summary": self.report["impacts"][:6],
                    "prior_results": results[-3:],
                }
                remaining = 12000 - sum(self.provider.usage[k] for k in ("input_tokens", "output_tokens"))
                # UTF-8 bytes are a conservative input-token upper bound; reserve output and system/schema space.
                if len(json.dumps(context).encode()) + len(json.dumps(schemas).encode()) + 2000 > remaining:
                    raise RepoScopeError("budget_exceeded", "Agent context exceeds remaining input budget")
                decision = self.provider.decide(context, schemas)
                if decision.tool == "finish":
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
            self.report["limitations"].append(str(exc))
        self.report["tool_calls"] = results
        self.report["metadata"]["agent"] = {
            "model": self.provider.model,
            "usage": self.provider.usage,
            "seconds": time.monotonic() - started,
            "prompt_version": "lookup-v1",
            "mode": "read-only gap investigation",
        }
        return self.report
