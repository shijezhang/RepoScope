import json
from pathlib import Path

import typer

from reposcope.config import RepoScopeError, Settings
from reposcope.graph.store import Store
from reposcope.indexing.parser import build_snapshot, semantic_hash
from reposcope.jobs.submission import submit_tests
from reposcope.models import TestRunInput, digest
from reposcope.reports.render import export, validate_report
from reposcope.repository.git import comparison, resolve, validate_repository
from reposcope.retrieval.search import Search

app = typer.Typer(help="RepoScope: fixed-commit Python change impact and regression evidence")


def store():
    return Store(Settings())


@app.command()
def register(path: Path, profile_id: str | None = None):
    s = store()
    root = validate_repository(str(path), s.settings)
    repo_id = digest(str(root))[:20]
    if profile_id is None:
        try:
            profile_id = s.get("repositories", repo_id).get("profile_id")
        except RepoScopeError as exc:
            if exc.code != "not_found":
                raise
    s.put("repositories", repo_id, {"repo_id": repo_id, "path": str(root), "name": root.name, "profile_id": profile_id})
    typer.echo(repo_id)


@app.command()
def index(repo_id: str, commit: str = "HEAD", full: bool = False):
    s = store()
    repo = s.get("repositories", repo_id)
    snap = build_snapshot(s, repo, resolve(Path(repo["path"]), commit), incremental=not full)
    typer.echo(
        json.dumps(
            {
                "snapshot_id": snap.snapshot_id,
                "status": snap.status,
                "stats": snap.stats,
                "semantic_hash": semantic_hash(snap),
            },
            indent=2,
        )
    )


@app.command()
def search(snapshot_id: str, query: str, limit: int = 20):
    typer.echo(json.dumps(Search(store().snapshot(snapshot_id)).query(query, limit), indent=2))


@app.command()
def callers(snapshot_id: str, symbol_id: str):
    snap = store().snapshot(snapshot_id)
    typer.echo(
        json.dumps(
            [e.model_dump() for e in snap.relations if e.target_id == symbol_id and e.relation_type == "CALLS"],
            indent=2,
        )
    )


@app.command("analyze")
def analyze_command(repo_id: str, base: str, head: str = "HEAD", mode: str = "direct", output: Path | None = None):
    s = store()
    repo = s.get("repositories", repo_id)
    if mode not in {"direct", "pr"}:
        raise typer.BadParameter("mode must be direct or pr")
    b, h = comparison(Path(repo["path"]), base, head, mode)
    jid = s.enqueue("analysis", {"repo_id": repo_id, "base": b, "head": h, "mode": mode, "question": ""})
    from reposcope.jobs.worker import Worker

    worker = Worker(s)
    while s.job(jid)["state"] not in {"completed", "failed", "cancelled"}:
        worker.once()
    job = s.job(jid)
    if not job["report"]:
        typer.echo(job["error"])
        raise typer.Exit(1)
    content, _ = export(job["report"], "json")
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content)
    typer.echo(content)


@app.command()
def worker():
    from reposcope.jobs.worker import Worker

    Worker(store()).run()


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    import uvicorn

    uvicorn.run("reposcope.api.app:app", host=host, port=port)


@app.command()
def verify(run_id: str):
    s = store()
    validate_report(s, s.job(run_id)["report"])
    typer.echo("Evidence checks passed")


@app.command()
def test(run_id: str, attempt: int = 1, reason: str | None = None):
    s = store()
    report = s.job(run_id)["report"]
    if not report:
        raise typer.BadParameter("Analysis report is not ready")
    jid = submit_tests(s, run_id, TestRunInput(plan_id=report["test_plan"]["plan_id"], attempt=attempt, reason=reason))
    typer.echo(json.dumps({"execution_id": jid, "message": "Run reposcope worker to execute"}))


@app.command()
def status(run_id: str):
    job = store().job(run_id)
    typer.echo(
        json.dumps(
            {"run_id": run_id, "status": job["state"], "error": job["error"], "report": job["report"]},
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command()
def cancel(run_id: str):
    s = store()
    s.cancel(run_id)
    from reposcope.jobs.submission import test_attempts

    for row in test_attempts(s, run_id):
        s.cancel(row["execution_id"])
    typer.echo("Cancellation requested; check task status for cleanup completion")


@app.command("export")
def export_command(run_id: str, output: Path, format: str = "json"):
    report = store().job(run_id)["report"]
    if not report:
        raise typer.BadParameter("Analysis report is not ready")
    content, _ = export(report, format)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content)
    typer.echo(str(output))


if __name__ == "__main__":
    app()
