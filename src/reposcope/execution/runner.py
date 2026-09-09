"""Fixed-command Docker execution of complete, immutable Git trees."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath

from reposcope.config import RepoScopeError
from reposcope.coverage import binding, import_coverage
from reposcope.models import digest


def parse_results(raw, requested, terminal=None):
    results = []
    phases = raw.get("phases", [])
    for nodeid in dict.fromkeys(requested or raw.get("nodeids", [])):
        reports = [r for r in phases if r["nodeid"] == nodeid]
        status = "not_run"
        if reports:
            statuses = [r["status"] for r in reports]
            status = next(
                (s for s in ("error", "failed", "xpass", "xfail", "skipped") if s in statuses),
                "passed" if any(r["phase"] == "call" for r in reports) else "not_run",
            )
        if terminal and status == "not_run":
            status = terminal
        results.append(
            {
                "nodeid": nodeid,
                "status": status,
                "phases": reports,
                "duration": sum(r.get("duration", 0) for r in reports),
            }
        )
    return results


class TestRunner:
    __test__ = False

    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY, request_hash TEXT, data TEXT)")

    def _save(self, execution_id, value):
        with self.store.connect() as db:
            db.execute("UPDATE executions SET data=? WHERE id=?", (json.dumps(value), execution_id))

    def get(self, execution_id):
        with self.store.connect() as db:
            row = db.execute("SELECT data FROM executions WHERE id=?", (execution_id,)).fetchone()
        if not row:
            raise RepoScopeError("not_found", "Unknown execution")
        return json.loads(row[0])

    def recover(self, execution_id):
        """Only call after the owning worker lease is known to have expired."""
        result = self.get(execution_id)
        if result["status"] in {"running", "preparing"} or result.get("cleanup_status") == "unconfirmed":
            docker = shutil.which("docker")
            cleaned = self._remove(docker, result["container"]) if docker else False
            result["cleanup_status"] = "completed" if cleaned else "unconfirmed"
            checkout = result.get("temporary_directory")
            if cleaned and checkout:
                directory = Path(checkout)
                if (
                    directory.parent.resolve()
                    in {Path(tempfile.gettempdir()).resolve(), (self.store.settings.home / "execution-tmp").resolve()}
                    and directory.name.startswith("reposcope-test-")
                    and not directory.is_symlink()
                ):
                    shutil.rmtree(directory, ignore_errors=True)
                    result.pop("temporary_directory", None)
            result.update(
                status="interrupted",
                error="Worker lease lost; container cleanup requested; execution is not resubmitted",
            )
            self._save(execution_id, result)
        return result

    def profile(self, repo):
        identifier = repo.get("profile_id")
        if not identifier or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier):
            raise RepoScopeError("test_environment_unavailable", "Repository has no valid server-managed test profile")
        path = self.store.settings.home / "profiles" / (identifier + ".json")
        try:
            profile = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise RepoScopeError("test_environment_unavailable", "Test profile is unavailable") from exc
        allowed = {
            "image",
            "python",
            "timeout",
            "memory",
            "cpus",
            "pids_limit",
            "test_paths",
            "pytest_plugins",
            "max_checkout_file_bytes",
        }
        if set(profile) - allowed or not re.fullmatch(r"(?:[^\s]+@)?sha256:[a-f0-9]{64}", profile.get("image", "")):
            raise RepoScopeError(
                "test_environment_unavailable", "Profile requires immutable image digest and supported fields"
            )
        if profile.get("python", "python") not in {"python", "python3", "/usr/local/bin/python"}:
            raise RepoScopeError("test_environment_unavailable", "Unsupported interpreter")
        plugins = profile.get("pytest_plugins", [])
        if (
            not isinstance(plugins, list)
            or len(plugins) > 16
            or any(
                not isinstance(plugin, str)
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", plugin)
                or plugin == "reposcope_pytest"
                for plugin in plugins
            )
        ):
            raise RepoScopeError(
                "test_environment_unavailable", "Plugins must be explicit Python module names in the server profile"
            )
        paths = profile.get("test_paths", ["tests"])
        if (
            not isinstance(paths, list)
            or not paths
            or any(
                not isinstance(p, str)
                or p.startswith("-")
                or PurePosixPath(p).is_absolute()
                or ".." in PurePosixPath(p).parts
                for p in paths
            )
        ):
            raise RepoScopeError("test_environment_unavailable", "Invalid test paths")
        try:
            if (
                not 0 < float(profile.get("timeout", 120)) <= 3600
                or not 0 < float(profile.get("cpus", 1)) <= 16
                or not 16 <= int(profile.get("pids_limit", 128)) <= 4096
                or not 1_000
                <= int(profile.get("max_checkout_file_bytes", self.store.settings.max_file_bytes))
                <= 20_000_000
            ):
                raise ValueError()
            if not re.fullmatch(r"[1-9][0-9]*[mg]", profile.get("memory", "512m")):
                raise ValueError()
        except (ValueError, TypeError):
            raise RepoScopeError("test_environment_unavailable", "Invalid resource limits")
        return profile

    def export_tree(self, snapshot, repo, destination, max_file_bytes=None):
        """Git object reads include assets/configs, independent of the parser file manifest."""
        root = repo.get("path") or repo.get("source")

        def git(*args):
            return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, timeout=30).stdout

        actual = git("rev-parse", "--verify", snapshot.commit_sha + "^{tree}").decode().strip()
        if actual != snapshot.tree_hash:
            raise RepoScopeError("snapshot_mismatch", "Commit tree changed or does not match snapshot")
        entries = git("ls-tree", "-rz", "--full-tree", snapshot.commit_sha).split(b"\0")
        count, total, manifest = 0, 0, {}
        for entry in entries:
            if not entry:
                continue
            metadata, filename = entry.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            path = PurePosixPath(os.fsdecode(filename))
            if mode not in {"100644", "100755"} or kind != "blob" or path.is_absolute() or ".." in path.parts:
                raise RepoScopeError(
                    "unsupported_checkout_entry", "Symlinks and submodules are not executable snapshot inputs"
                )
            count += 1
            size = int(git("cat-file", "-s", oid))
            total += size
            if (
                count > self.store.settings.max_files
                or size > (max_file_bytes if max_file_bytes is not None else self.store.settings.max_file_bytes)
                or total > self.store.settings.max_total_bytes
            ):
                raise RepoScopeError("budget_exceeded", "Execution checkout exceeds file budget")
            target = destination / str(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(git("cat-file", "blob", oid))
            target.chmod(0o755 if mode == "100755" else 0o644)
            manifest[str(path)] = oid
        # Conservatively includes all tracked files: fixtures and helper modules cannot be omitted.
        self._export_manifest = manifest
        return digest(manifest)

    def collect(self, snapshot, repo, execution_id, cancelled=lambda: False):
        return self._execute(snapshot, repo, None, execution_id, cancelled, True)

    def run(self, snapshot, repo, nodeids, execution_id, cancelled=lambda: False):
        return self._execute(snapshot, repo, nodeids, execution_id, cancelled, False)

    def _execute(self, snapshot, repo, nodeids, execution_id, cancelled, collect):
        started_at, started_clock = time.time(), time.perf_counter()
        profile_error = None
        try:
            profile = self.profile(repo)
        except RepoScopeError as exc:
            profile, profile_error = None, exc
        request_hash = digest([snapshot.snapshot_id, repo.get("profile_id"), profile, nodeids, collect])
        container = "reposcope-" + digest(execution_id)[:32]
        result = {
            "execution_id": execution_id,
            "started_at": started_at,
            "snapshot_id": snapshot.snapshot_id,
            "status": "preparing",
            "mode": "collect" if collect else "run",
            "nodeids": nodeids or [],
            "results": [],
            "container": container,
        }
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT request_hash,data FROM executions WHERE id=?", (execution_id,)).fetchone()
            if previous:
                if previous[0] != request_hash:
                    raise RepoScopeError("idempotency_conflict", "Execution identity reused with different inputs")
                return json.loads(previous[1])
            db.execute("INSERT INTO executions VALUES(?,?,?)", (execution_id, request_hash, json.dumps(result)))
        temp = None
        process = None
        submitted = False
        try:
            if cancelled():
                result["status"] = "cancelled"
                return result
            if profile_error:
                raise profile_error
            result["environment_hash"] = digest(profile)
            docker = shutil.which("docker")
            if not docker:
                raise RepoScopeError("test_environment_unavailable", "Docker is required; host execution is disabled")
            check = subprocess.run([docker, "image", "inspect", profile["image"]], capture_output=True, timeout=15)
            if check.returncode:
                raise RepoScopeError("test_environment_unavailable", "Docker daemon or prepared image is unavailable")
            if nodeids is not None and (
                not isinstance(nodeids, list)
                or any(
                    not isinstance(n, str)
                    or not n
                    or n.startswith("-")
                    or PurePosixPath(n.split("::")[0]).is_absolute()
                    or ".." in PurePosixPath(n.split("::")[0]).parts
                    for n in nodeids
                )
            ):
                raise RepoScopeError("invalid_nodeid", "Invalid repository-relative pytest nodeid")
            temp_root = self.store.settings.home / "execution-tmp"
            temp_root.mkdir(parents=True, exist_ok=True)
            temp = tempfile.mkdtemp(prefix="reposcope-test-", dir=temp_root)
            directory = Path(temp)
            result["temporary_directory"] = str(directory)
            self._save(execution_id, result)
            work, output, control = (directory / name for name in ("work", "output", "control"))
            for path in (work, output, control):
                path.mkdir()
            suite_hash = self.export_tree(snapshot, repo, work, max_file_bytes=profile.get("max_checkout_file_bytes"))
            expected = binding(snapshot, digest(profile), suite_hash)
            result.update(expected)
            manifest = getattr(self, "_export_manifest", None)
            if manifest is not None:
                paths = profile.get("test_paths", ["tests"])
                result["comparison_hash"] = digest(
                    {
                        p: oid
                        for p, oid in manifest.items()
                        if not p.endswith(".py")
                        or Path(p).name == "conftest.py"
                        or Path(p).name.startswith("test_")
                        or Path(p).name.endswith("_test.py")
                        or any(p == root or p.startswith(root.rstrip("/") + "/") for root in paths)
                    }
                )
            if not collect and nodeids is not None:
                with self.store.connect() as db:
                    catalogs = [json.loads(row[0]) for row in db.execute("SELECT data FROM executions")]
                catalog = next(
                    (
                        c
                        for c in catalogs
                        if c.get("mode") == "collect"
                        and c.get("status") == "completed"
                        and all(c.get(k) == v for k, v in expected.items())
                    ),
                    None,
                )
                if catalog is None or not set(nodeids).issubset(catalog.get("nodeids", [])):
                    raise RepoScopeError(
                        "invalid_nodeid",
                        "Tests must belong to a successful collection in the same snapshot and environment",
                    )
            shutil.copyfile(Path(__file__).with_name("pytest_plugin.py"), control / "reposcope_pytest.py")
            command = [
                docker,
                "run",
                "--rm",
                "--pull",
                "never",
                "--name",
                container,
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                str(profile.get("memory", "512m")),
                "--cpus",
                str(profile.get("cpus", 1)),
                "--pids-limit",
                str(profile.get("pids_limit", 128)),
                "--read-only",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=128m",
                "--mount",
                f"type=bind,src={work},dst=/work",
                "--mount",
                f"type=bind,src={output},dst=/output",
                "--mount",
                f"type=bind,src={control},dst=/control,readonly",
                "--workdir",
                "/work",
                "--env",
                "PYTHONPATH=/control:/work:/work/src",
                "--env",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--env",
                "HOME=/tmp",
                "--entrypoint",
                profile.get("python", "python"),
                profile["image"],
                "-m",
                "pytest",
                "-p",
                "reposcope_pytest",
                "-o",
                "addopts=",
                "-p",
                "no:cacheprovider",
            ]
            for plugin in profile.get("pytest_plugins", []):
                command.extend(["-p", plugin])
            if collect:
                command.append("--collect-only")
            command.extend(["--", *(nodeids if nodeids is not None else profile.get("test_paths", ["tests"]))])
            if nodeids == [] and not collect:
                result.update(status="completed", exit_code=0)
                return result
            result.update(status="running", command_hash=digest(command[command.index("--entrypoint") :]))
            self._save(execution_id, result)  # Durable identity before any container submission.
            if cancelled():
                result["status"] = "cancelled"
                return result
            log_path = directory / "docker.log"
            deadline = time.monotonic() + min(float(profile.get("timeout", 120)), self.store.settings.test_seconds)
            with log_path.open("wb") as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                submitted = True
                while process.poll() is None:
                    if cancelled() or time.monotonic() > deadline or log_path.stat().st_size > 5_000_000:
                        result["status"] = "cancelled" if cancelled() else "timeout"
                        self._remove(docker, container)
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        break
                    time.sleep(0.1)
            result["log"] = log_path.read_bytes()[:200000].decode(errors="replace")
            result["exit_code"] = process.returncode
            raw_path = output / "result.json"
            raw = {}
            if raw_path.exists() and not raw_path.is_symlink() and raw_path.stat().st_size <= 10_000_000:
                raw = json.loads(raw_path.read_text())
            result["nodeids"] = raw.get("nodeids", nodeids or [])
            terminal = result["status"] if result["status"] in {"timeout", "cancelled"} else None
            if not terminal:
                result["status"] = (
                    "collection_failed"
                    if raw.get("collection_errors")
                    else "completed"
                    if raw and process.returncode in {0, 1, 5}
                    else "test_environment_unavailable"
                )
            result["collection_errors"] = raw.get("collection_errors", [])
            result["results"] = parse_results(raw, nodeids, terminal)
            artifact_dir = self.store.settings.home / "executions" / digest(execution_id)
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "result.json").write_text(json.dumps(raw))
            (artifact_dir / "execution.log").write_text(result["log"])
            data_path = output / ".coverage"
            if data_path.is_file() and not data_path.is_symlink() and data_path.stat().st_size <= 20_000_000:
                shutil.copyfile(data_path, artifact_dir / ".coverage")
            result["artifact_dir"] = str(artifact_dir)
            result["coverage_ids"] = [
                r["evidence_id"]
                for r in import_coverage(
                    self.store, {"binding": expected, "contexts": raw.get("contexts", [])}, expected, execution_id
                )
            ]
            return result
        except RepoScopeError as exc:
            result.update(status=exc.code, error=exc.message)
            return result
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result.update(status="test_environment_unavailable", error=str(exc))
            return result
        finally:
            if submitted:
                result["cleanup_status"] = "completed" if self._remove(docker, container) else "unconfirmed"
            if temp and result.get("cleanup_status") != "unconfirmed":
                shutil.rmtree(temp)
                result.pop("temporary_directory", None)
            result["wall_seconds"] = time.perf_counter() - started_clock
            self._save(execution_id, result)

    @staticmethod
    def _remove(docker, container):
        try:
            response = subprocess.run([docker, "rm", "-f", container], capture_output=True, timeout=10)
            if response.returncode == 0:
                return True
            return b"No such container" in getattr(response, "stderr", b"")
        except (OSError, subprocess.SubprocessError):
            return False
