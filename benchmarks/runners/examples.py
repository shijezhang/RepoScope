"""Generate public deterministic-analysis examples; never fabricate test execution."""

import json
from pathlib import Path

from reposcope.analysis.impact import analyze
from reposcope.config import Settings
from reposcope.graph.store import Store
from reposcope.indexing.parser import build_snapshot
from reposcope.reports.render import export, validate_report

ROOT = Path(__file__).resolve().parents[2]


def main():
    rows = json.loads((ROOT / "benchmarks/results/index-consistency.json").read_text())["cases"]
    store = Store(Settings(home=ROOT / "artifacts/example-state", max_nodes=120))
    for case_id in ["click-01", "httpx-02", "fixture-04"]:
        row = next(r for r in rows if r["case_id"] == case_id)
        repo = {"repo_id": row["repo_id"], "path": str(ROOT / row["replay_repository"]), "name": row["repo_id"]}
        base, head = [build_snapshot(store, repo, row[side]) for side in ["base", "head"]]
        report = analyze(store, repo, base, head, "example-" + case_id)
        report["metadata"]["example_kind"] = "real deterministic analysis; tests not executed by product runner"
        validate_report(store, report)
        for format, extension in [("json", "json"), ("markdown", "md"), ("html", "html")]:
            content, _ = export(report, format)
            (ROOT / "docs/examples" / f"{case_id}.{extension}").write_text(content)
        print(case_id, len(report["impacts"]), report["completeness"])


if __name__ == "__main__":
    main()
