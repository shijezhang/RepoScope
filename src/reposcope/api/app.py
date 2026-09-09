import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from reposcope.config import RepoScopeError, Settings
from reposcope.graph.store import Store
from reposcope.jobs.submission import submit_tests, test_attempts
from reposcope.jobs.worker import TERMINAL
from reposcope.models import AnalysisInput, RegisterInput, TestRunInput, digest
from reposcope.reports.render import export
from reposcope.repository.git import comparison, resolve, validate_repository


class SnapshotInput(BaseModel):
    commit: str


def create_app(settings=None):
    settings = settings or Settings()
    store = Store(settings)
    app = FastAPI(title="RepoScope", version="0.2.0")
    app.state.store = store

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        # Local-only service: reject cross-origin browser mutations (including simple POSTs).
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            if origin and origin not in {
                f"http://{host}",
                f"https://{host}",
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            }:
                return JSONResponse(
                    status_code=403,
                    content={
                        "code": "origin_not_allowed",
                        "message": "Cross-origin request rejected",
                        "retryable": False,
                    },
                )
        return await call_next(request)

    @app.exception_handler(RepoScopeError)
    async def domain_error(request, exc):
        status = (
            404
            if exc.code == "not_found"
            else 409
            if exc.code in {"idempotency_conflict", "snapshot_not_ready", "invalid_plan"}
            else 400
        )
        return JSONResponse(
            status_code=status, content={"code": exc.code, "message": exc.message, "retryable": exc.retryable}
        )

    @app.get("/api/health")
    def health():
        import shutil

        return {
            "status": "ok",
            "version": "0.2.0",
            "docker_available": bool(shutil.which("docker")),
            "worker": "separate process",
            "model_configured": bool(os.getenv("REPOSCOPE_LLM_MODEL") and os.getenv("REPOSCOPE_LLM_API_KEY")),
        }

    @app.get("/api/repositories")
    def repositories():
        return store.list("repositories")

    @app.post("/api/repositories", status_code=201)
    def register(body: RegisterInput):
        root = validate_repository(body.path, settings)
        repo_id = digest(str(root))[:20]
        profile_id = body.profile_id
        if "profile_id" not in body.model_fields_set:
            try:
                profile_id = store.get("repositories", repo_id).get("profile_id")
            except RepoScopeError as exc:
                if exc.code != "not_found":
                    raise
        repo = {"repo_id": repo_id, "path": str(root), "name": body.name or root.name, "profile_id": profile_id}
        store.put("repositories", repo_id, repo)
        return repo

    @app.post("/api/repositories/{repo_id}/snapshots", status_code=202)
    def snapshot(repo_id: str, body: SnapshotInput):
        repo = store.get("repositories", repo_id)
        sha = resolve(Path(repo["path"]), body.commit)
        jid = store.enqueue("snapshot", {"repo_id": repo_id, "commit": sha}, key="snapshot:" + digest([repo_id, sha]))
        return {"job_id": jid}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return public_job(store.job(job_id))

    @app.post("/api/analyses", status_code=202)
    def create_analysis(body: AnalysisInput, idempotency_key: str | None = Header(None)):
        repo = store.get("repositories", body.repo_id)
        b, h = comparison(Path(repo["path"]), body.base, body.head, body.mode)
        payload = body.model_dump()
        payload.update(base=b, head=h)
        jid = store.enqueue("analysis", payload, "analysis:" + idempotency_key if idempotency_key else None)
        return {"run_id": jid}

    def public_job(job):
        if job["error"]:
            try:
                job["error"] = json.loads(job["error"])
            except ValueError:
                pass
        value = {
            key: job[key]
            for key in ["run_id", "status", "payload", "report", "error", "created", "updated", "kind", "cancel"]
        }
        value["test_attempts"] = test_attempts(store, job["run_id"]) if job["kind"] == "analysis" else []
        return value

    @app.get("/api/analyses")
    def analyses():
        return [public_job(job) for job in store.jobs()]

    @app.get("/api/analyses/{run_id}")
    def analysis(run_id: str):
        return public_job(store.job(run_id))

    @app.get("/api/analyses/{run_id}/events")
    async def events(
        run_id: str, request: Request, after: int = Query(0, ge=0), last_event_id: str | None = Header(None)
    ):
        store.job(run_id)
        try:
            cursor = int(last_event_id) if last_event_id else after
        except ValueError as exc:
            raise RepoScopeError("invalid_cursor", "SSE cursor must be an integer") from exc

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                rows = store.events(run_id, cursor)
                for event in rows:
                    cursor = event["event_id"]
                    yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
                if store.job(run_id)["state"] in TERMINAL:
                    break
                if not rows:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    @app.post("/api/analyses/{run_id}/cancel")
    def cancel(run_id: str):
        store.cancel(run_id)
        with store.connect() as db:
            children = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM jobs WHERE kind='test' AND json_extract(payload,'$.run_id')=?", (run_id,)
                )
            ]
        for jid in children:
            store.cancel(jid)
        return {"run_id": run_id, "status": store.job(run_id)["state"], "cancel_requested": True}

    @app.post("/api/analyses/{run_id}/test-runs", status_code=202)
    def test_run(run_id: str, body: TestRunInput):
        jid = submit_tests(store, run_id, body)
        return {"execution_id": jid, "status": store.job(jid)["state"]}

    @app.get("/api/evidence/{evidence_id}")
    def evidence(evidence_id: str):
        return store.get("evidence", evidence_id)

    @app.get("/api/analyses/{run_id}/export")
    def report_export(run_id: str, format: str = "json", revision: int | None = None):
        report = store.job(run_id)["report"]
        if revision is not None:
            with store.connect() as db:
                row = db.execute(
                    "SELECT data FROM revisions WHERE run_id=? AND revision=?", (run_id, revision)
                ).fetchone()
            if not row:
                raise RepoScopeError("not_found", "Unknown report revision")
            report = json.loads(row[0])
        if not report:
            raise RepoScopeError("snapshot_not_ready", "Report is not ready")
        content, media = export(report, format)
        extension = {"markdown": "md", "json": "json", "html": "html"}[format]
        return Response(
            content,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="reposcope-{run_id}.{extension}"'},
        )

    web = Path(__file__).resolve().parents[3] / "apps" / "web" / "dist"
    if web.exists():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app


app = create_app()
