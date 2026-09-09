import html
import json

from reposcope.config import RepoScopeError
from reposcope.models import digest


def validate_report(store, report):
    from reposcope.analysis.impact import PROPAGATES, changed_symbols

    allowed = {report["base"]["snapshot_id"], report["head"]["snapshot_id"]}
    snapshots = {sid: store.snapshot(sid) for sid in allowed}

    def evidence(eid):
        e = store.get("evidence", eid)
        if e["snapshot_id"] not in allowed:
            raise RepoScopeError("invalid_evidence", "Cross-snapshot reference")
        snap = snapshots[e["snapshot_id"]]
        if e["path"] not in snap.files or not (
            1 <= e["start"] <= e["end"] <= max(1, len(snap.files[e["path"]].splitlines()))
        ):
            raise RepoScopeError("invalid_evidence", "Source range mismatch")
        source = "\n".join(snap.files[e["path"]].splitlines()[e["start"] - 1 : e["end"]])
        if digest(source) != e["content_hash"] or source != e["source"]:
            raise RepoScopeError("invalid_evidence", "Source content hash mismatch")
        return e

    for claim in report["claims"]:
        if claim["status"] != "unknown" and not claim["evidence_ids"]:
            raise RepoScopeError("invalid_evidence", "Claim has no evidence")
        for eid in claim["evidence_ids"]:
            evidence(eid)
    seed_sets = {
        side: changed_symbols(snapshots[report[side]["snapshot_id"]], report["changes"], git_side)
        for side, git_side in [("base", "old"), ("head", "new")]
    }
    for impact in report["impacts"]:
        side = impact.get("side")
        if side not in seed_sets or impact["symbol"]["snapshot_id"] != report[side]["snapshot_id"]:
            raise RepoScopeError("invalid_evidence", "Impact snapshot/side mismatch")
        snap = snapshots[impact["symbol"]["snapshot_id"]]
        symbols = {s.symbol_id: s for s in snap.symbols}
        sid = impact["symbol"]["symbol_id"]
        if sid not in symbols or symbols[sid].model_dump() != impact["symbol"]:
            raise RepoScopeError("invalid_evidence", "Impact symbol mismatch")
        e = evidence(impact["evidence_id"])
        symbol = symbols[sid]
        if any(e[k] != getattr(symbol, k) for k in ["snapshot_id", "path", "start", "end", "content_hash"]):
            raise RepoScopeError("invalid_evidence", "Impact evidence does not match its symbol")
        relations = {digest(r.model_dump()) for r in snap.relations}
        current = sid
        for step in impact["path"]:
            raw = {
                key: value for key, value in step.items() if key not in {"traversal_source_id", "traversal_target_id"}
            }
            if digest(raw) not in relations:
                raise RepoScopeError("invalid_evidence", "Impact path contains an unknown edge")
            source = step.get("traversal_source_id", step["source_id"])
            target = step.get("traversal_target_id", step["target_id"])
            kind = step["relation_type"]
            expected = (
                (step["target_id"], step["source_id"]) if kind == "CONTAINS" else (step["source_id"], step["target_id"])
            )
            if kind not in PROPAGATES | {"CONTAINS"} or (source, target) != expected or source != current:
                raise RepoScopeError("invalid_evidence", "Impact path is disconnected or has invalid direction")
            current = target
        if current not in seed_sets[side] or impact["distance"] != len(impact["path"]):
            raise RepoScopeError("invalid_evidence", "Impact path does not end at a changed seed")
    return True


def markdown(report):
    lines = [
        "# RepoScope analysis",
        "",
        f"Run: `{report['run_id']}` · Revision: {report['revision']} · {report['completeness']}",
        f"Base: `{report['base']['commit_sha']}`",
        f"Head: `{report['head']['commit_sha']}`",
        "",
        "## Potential impact",
        "",
    ]
    for item in report["impacts"]:
        s = item["symbol"]
        lines.append(
            f"- [{item['side']}] `{s['path']}:{s['start']}` `{s['qualname'] or '<module>'}` · distance {item['distance']} · evidence `{item['evidence_id']}`"
        )
    lines += ["", "## Test plan", "", f"Status: {report['test_plan']['status']}"]
    lines += [f"- {r}" for r in report["test_plan"]["reasons"]]
    lines += [
        "",
        "## Execution",
        "",
        "```json",
        json.dumps(report["executions"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Limitations",
        "",
    ]
    lines += [f"- {item}" for item in report["limitations"]]
    return "\n".join(lines)


def export(report, format):
    if format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2), "application/json"
    if format == "markdown":
        return markdown(report), "text/markdown"
    if format == "html":
        return (
            '<!doctype html><html lang="en"><meta charset="utf-8"><title>RepoScope report</title><style>body{max-width:1100px;margin:40px auto;font:15px/1.6 system-ui;background:#f6f8fc;color:#18304e}pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:28px;background:white;border:1px solid #dce3ed;border-radius:12px}</style><pre>'
            + html.escape(markdown(report))
            + "</pre></html>",
            "text/html",
        )
    raise RepoScopeError("invalid_format", "Use json, markdown or html")
