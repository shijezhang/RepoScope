"""Build and verify the analysis-only application Compose deployment, then tear it down."""

import argparse
import hashlib
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from .case_locator import locate_case
except ImportError:
    from case_locator import locate_case

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-image", required=True)
    parser.add_argument("--python-image", required=True)
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", args.port))
    case = locate_case("fixture-01")
    source = ROOT / case["replay_repository"]
    project = "reposcope-acceptance-" + str(time.time_ns())
    directory = ROOT / "artifacts/compose-validation" / project
    state = directory / "state"
    state.mkdir(parents=True, exist_ok=False)
    environment = {
        **os.environ,
        "REPOSCOPE_APP_IMAGE": "reposcope-compose-acceptance:local",
        "REPOSCOPE_NODE_IMAGE": args.node_image,
        "REPOSCOPE_PYTHON_IMAGE": args.python_image,
        "REPOSCOPE_GIT_VERSION": "1:2.47.3-0+deb13u1",
        "REPOSCOPE_HTTP_PORT": str(args.port),
        "REPOSCOPE_STATE_MOUNT": str(state),
        "REPOSCOPE_REPOSITORY_DIR": str(source.parent),
    }
    compose = [
        "docker",
        "compose",
        "--env-file",
        "/dev/null",
        "--project-name",
        project,
        "--file",
        str(ROOT / "compose.yaml"),
    ]
    host_source_hashes = {
        str(path.relative_to(ROOT / "src")): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "src/reposcope").rglob("*.py"))
    }
    result = {
        "kind": "real-analysis-only-compose-acceptance",
        "project": project,
        "port": args.port,
        "started_at": time.time(),
        "node_image": args.node_image,
        "python_image": args.python_image,
        "git_package": environment["REPOSCOPE_GIT_VERSION"],
        "replay_manifest": case["replay_manifest"],
        "repository": case["replay_repository"],
        "base": case["base"],
        "head": case["head"],
        "lock_hashes": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ["uv.lock", "apps/web/package-lock.json"]
        },
        "status": "running",
        "target_tests_executed": False,
        "build_source_hashes": host_source_hashes,
        "build_source_manifest_hash": hashlib.sha256(
            json.dumps(host_source_hashes, sort_keys=True).encode()
        ).hexdigest(),
    }

    def run(command, *, timeout=900, log=None):
        if log:
            with (directory / log).open("w") as output:
                subprocess.run(
                    command,
                    cwd=ROOT,
                    env=environment,
                    check=True,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                )
            return ""
        return subprocess.check_output(command, cwd=ROOT, env=environment, text=True, timeout=timeout).strip()

    def request(path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{args.port}" + path, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                raw = response.read().decode()
                return json.loads(raw) if "application/json" in response.headers.get("Content-Type", "") else raw
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:2000]
            raise RuntimeError(f"{path}: HTTP {exc.code}: {detail}") from exc

    before = run(["git", "-C", str(source), "status", "--porcelain"])
    try:
        result["compose_version"] = run(["docker", "compose", "version", "--short"])
        result["buildx_version"] = run(["docker", "buildx", "version"])
        build_started = time.perf_counter()
        if not args.skip_build:
            print("Building application image from frozen Python/npm locks", flush=True)
            run([*compose, "build", "api"], log="build.log")
        result["build_wall_seconds"] = None if args.skip_build else time.perf_counter() - build_started
        result["build_mode"] = "existing-image" if args.skip_build else "compose-build-with-cache-available"
        result["image_id"] = run(
            ["docker", "image", "inspect", environment["REPOSCOPE_APP_IMAGE"], "--format", "{{.Id}}"]
        )
        print("Starting isolated API and analysis worker", flush=True)
        startup = time.perf_counter()
        run(
            [*compose, "up", "--detach", "--no-build", "--wait", "--wait-timeout", "90"], timeout=120, log="startup.log"
        )
        result["startup_wall_seconds"] = time.perf_counter() - startup
        result["health"] = request("/api/health")
        assert result["health"]["status"] == "ok"
        assert result["health"]["docker_available"] is False, "Analysis-only image unexpectedly exposes Docker CLI"
        page = request("/")
        assert 'id="root"' in page
        result["frontend_served"] = True
        result["git_version"] = run([*compose, "exec", "--no-TTY", "api", "git", "--version"])
        result["python_environment"] = run(
            [
                *compose,
                "exec",
                "--no-TTY",
                "api",
                "/app/.venv/bin/python",
                "-c",
                "import sys,importlib.metadata as m; print(sys.version); print('\\n'.join(sorted(d.metadata['Name']+'=='+d.version for d in m.distributions())))",
            ]
        )
        installed_hashes = json.loads(
            run(
                [
                    *compose,
                    "exec",
                    "--no-TTY",
                    "api",
                    "/app/.venv/bin/python",
                    "-c",
                    "import reposcope,pathlib,hashlib,json; root=pathlib.Path(reposcope.__file__).parent; print(json.dumps({str(p.relative_to(root.parent)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*.py')}))",
                ]
            )
        )
        result["installed_source_hashes"] = installed_hashes
        result["installed_source_matches_build_input"] = installed_hashes == host_source_hashes
        assert result["installed_source_matches_build_input"], (
            "Installed package source differs from the frozen build input"
        )
        repository = request("/api/repositories", {"path": "/repositories/repository", "name": "Compose fixture"})
        started = time.perf_counter()
        created = request(
            "/api/analyses",
            {
                "repo_id": repository["repo_id"],
                "base": case["base"],
                "head": case["head"],
                "mode": "direct",
                "agent": False,
            },
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            job = request("/api/analyses/" + created["run_id"])
            if job["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                break
            time.sleep(0.3)
        assert job["status"] == "completed", job
        report = job["report"]
        assert report["base"]["commit_sha"] == case["base"] and report["head"]["commit_sha"] == case["head"]
        assert report["impacts"] and report["claims"]
        assert not report["executions"], "Analysis-only acceptance must not execute target tests"
        result.update(
            analysis_wall_seconds=time.perf_counter() - started,
            analysis_id=created["run_id"],
            impact_count=len(report["impacts"]),
            claim_count=len(report["claims"]),
            completeness=report["completeness"],
            report=report,
        )
        containers = []
        for identifier in run([*compose, "ps", "--quiet"]).splitlines():
            inspected = json.loads(run(["docker", "inspect", identifier]))[0]
            mounts = [
                {key: mount.get(key) for key in ["Source", "Destination", "RW", "Type"]}
                for mount in inspected["Mounts"]
            ]
            assert set(mount["Destination"] for mount in mounts) == {"/state", "/repositories"}
            assert next(mount for mount in mounts if mount["Destination"] == "/repositories")["RW"] is False
            assert all("docker.sock" not in mount["Source"] for mount in mounts)
            containers.append(
                {
                    "service": inspected["Config"]["Labels"]["com.docker.compose.service"],
                    "state": inspected["State"]["Status"],
                    "mounts": mounts,
                    "image_id": inspected["Image"],
                }
            )
        assert {item["service"] for item in containers} == {"api", "worker"}
        result["containers"] = containers
        result["working_tree_unchanged"] = before == run(["git", "-C", str(source), "status", "--porcelain"])
        assert result["working_tree_unchanged"]
        result["status"] = "passed"
        print("Fixture analysis completed through the real API and worker", flush=True)
    except Exception as exc:
        result.update(status="failed", error=str(exc)[:4000])
        raise
    finally:
        try:
            run([*compose, "logs", "--no-color"], timeout=20, log="application.log")
            run([*compose, "down", "--volumes", "--remove-orphans"], timeout=60, log="shutdown.log")
            remaining = run(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--filter",
                    "label=com.docker.compose.project=" + project,
                    "--format",
                    "{{.Names}}",
                ]
            )
            result["cleanup"] = "completed" if not remaining else "unconfirmed"
            result["remaining_containers"] = remaining.splitlines()
        except Exception as exc:
            result.update(cleanup="unconfirmed", cleanup_error=str(exc)[:1000])
        result["artifact_directory"] = str(directory.relative_to(ROOT))
        result["finished_at"] = time.time()
        (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        (ROOT / "benchmarks/results/compose-validation.json").write_text(json.dumps(result, indent=2) + "\n")
    assert result["cleanup"] == "completed", result
    print(
        json.dumps(
            {
                key: result[key]
                for key in [
                    "status",
                    "image_id",
                    "build_wall_seconds",
                    "startup_wall_seconds",
                    "analysis_wall_seconds",
                    "impact_count",
                    "cleanup",
                    "artifact_directory",
                ]
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
