import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from reposcope.config import RepoScopeError, Settings
from reposcope.coverage import binding, import_coverage, validity
from reposcope.execution.runner import TestRunner, parse_results
from reposcope.graph.store import Store
from reposcope.models import Snapshot


@pytest.fixture
def store(tmp_path):
    return Store(Settings(home=tmp_path / "state"))


def snapshot(tree="tree", commit="commit"):
    return Snapshot(
        snapshot_id="snap",
        repo_id="repo",
        commit_sha=commit,
        tree_hash=tree,
        manifest_hash="manifest",
        files={},
        symbols=[],
        relations=[],
        unresolved=[],
        diagnostics=[],
    )


def profile(store):
    folder = store.settings.home / "profiles"
    folder.mkdir()
    (folder / "test.json").write_text(json.dumps({"image": "python@sha256:" + "a" * 64, "test_paths": ["tests"]}))
    return {"path": "/unused", "profile_id": "test"}


def test_no_docker_never_runs_on_host_and_identity_is_idempotent(store):
    repo = profile(store)
    runner = TestRunner(store)
    with patch("reposcope.execution.runner.shutil.which", return_value=None), patch("subprocess.Popen") as popen:
        result = runner.run(snapshot(), repo, ["tests/test_x.py::test_x"], "e1")
        assert result["status"] == "test_environment_unavailable"
        assert runner.run(snapshot(), repo, ["tests/test_x.py::test_x"], "e1") == result
        popen.assert_not_called()
        with pytest.raises(RepoScopeError, match="different inputs"):
            runner.run(snapshot(), repo, [], "e1")


def test_phase_results_and_terminal_status():
    raw = {
        "phases": [
            {"nodeid": "a", "phase": "call", "status": "passed"},
            {"nodeid": "a", "phase": "teardown", "status": "error"},
            {"nodeid": "b", "phase": "setup", "status": "xfail"},
            {"nodeid": "c", "phase": "call", "status": "xpass"},
        ]
    }
    assert [r["status"] for r in parse_results(raw, ["a", "b", "c", "d"], "timeout")] == [
        "error",
        "xfail",
        "xpass",
        "timeout",
    ]


def test_coverage_binding_and_shared_context(store):
    expected = binding(snapshot(), "environment", "suite")
    records = import_coverage(
        store,
        {
            "binding": expected,
            "contexts": [
                {"path": "app.py", "nodeid": None, "phase": "unattributed", "lines": [2, 1, 2]},
                {"path": "app.py", "nodeid": "test_a", "phase": "setup", "lines": [4]},
            ],
        },
        expected,
        "execution",
    )
    assert records[0]["lines"] == [1, 2]
    assert records[0]["nodeid"] is None
    assert validity(records[0], expected)["status"] == "valid"
    for key in expected:
        changed = {**expected, key: "changed"}
        assert validity(records[0], changed) == {"status": "stale", "mismatches": [key]}
    with pytest.raises(RepoScopeError):
        import_coverage(store, {"binding": {}}, expected, "execution")
    with pytest.raises(RepoScopeError):
        import_coverage(
            store, {"binding": expected, "contexts": [{"path": "../escape", "lines": [1]}]}, expected, "execution"
        )


def test_export_full_git_tree_and_reject_symlinks(store, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout.decode().strip()

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "asset.bin").write_bytes(b"\x00binary")
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    git("add", ".")
    git("commit", "-m", "fixture")
    frozen = snapshot(git("rev-parse", "HEAD^{tree}"), git("rev-parse", "HEAD"))
    (repo / "asset.bin").write_bytes(b"dirty")
    destination = tmp_path / "export"
    destination.mkdir()
    TestRunner(store).export_tree(frozen, {"path": str(repo)}, destination)
    assert (destination / "asset.bin").read_bytes() == b"\x00binary"
    assert (destination / "pyproject.toml").is_file()
    (repo / "escape").symlink_to("/etc/passwd")
    git("add", "escape")
    git("commit", "-m", "symlink")
    with pytest.raises(RepoScopeError, match="Symlinks"):
        TestRunner(store).export_tree(
            snapshot(git("rev-parse", "HEAD^{tree}"), git("rev-parse", "HEAD")), {"path": str(repo)}, tmp_path / "other"
        )


def test_container_arguments_results_and_cleanup(store):
    repo = profile(store)
    runner = TestRunner(store)
    commands = []

    def popen(command, **kwargs):
        commands.append(command)
        output_mount = next(
            value for value in command if value.startswith("type=bind,src=") and value.endswith("dst=/output")
        )
        output = Path(output_mount.split("src=")[1].split(",dst=")[0])
        (output / "result.json").write_text(
            json.dumps(
                {
                    "nodeids": ["tests/test_x.py::test_x"],
                    "phases": [{"nodeid": "tests/test_x.py::test_x", "phase": "call", "status": "passed"}],
                    "contexts": [],
                }
            )
        )
        return SimpleNamespace(poll=lambda: 0, returncode=0)

    with (
        patch("reposcope.execution.runner.shutil.which", return_value="/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run,
        patch.object(runner, "export_tree", return_value="suite"),
        patch("subprocess.Popen", side_effect=popen),
    ):
        assert runner.collect(snapshot(), repo, "collection")["status"] == "completed"
        result = runner.run(snapshot(), repo, ["tests/test_x.py::test_x"], "execution")
    assert result["status"] == "completed"
    assert result["results"][0]["status"] == "passed"
    command = commands[0]
    for flag in ["--read-only", "--pids-limit", "--memory", "--cpus", "--user"]:
        assert flag in command
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in command
    assert "/var/run/docker.sock" not in str(command)
    assert run.call_args.args[0][1:3] == ["rm", "-f"]


def test_bad_profile_and_precancel(store):
    runner = TestRunner(store)
    with pytest.raises(RepoScopeError):
        runner.profile({"profile_id": "../escape"})
    result = runner.collect(snapshot(), {}, "cancel", lambda: True)
    assert result["status"] == "cancelled"


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_or_cancellation_reclaims_container(store, cancel):
    repo = profile(store)
    runner = TestRunner(store)
    process = SimpleNamespace(
        poll=lambda: None, returncode=-15, terminate=lambda: None, wait=lambda **kwargs: -15, kill=lambda: None
    )
    checks = iter([False, False, cancel, cancel])
    with (
        patch("reposcope.execution.runner.shutil.which", return_value="/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run,
        patch.object(runner, "export_tree", return_value="suite"),
        patch("subprocess.Popen", return_value=process),
        patch("reposcope.execution.runner.time.monotonic", side_effect=[0, 1000]),
    ):
        result = runner.run(snapshot(), repo, None, "timed", lambda: next(checks, cancel))
    assert result["status"] == ("cancelled" if cancel else "timeout")
    assert any(call.args[0][1:3] == ["rm", "-f"] for call in run.call_args_list)
    assert runner.get("timed")["status"] == result["status"]


def test_profile_change_conflicts_with_existing_execution_identity(store):
    repo = profile(store)
    runner = TestRunner(store)
    with patch("reposcope.execution.runner.shutil.which", return_value=None):
        runner.collect(snapshot(), repo, "profile-bound")
        path = store.settings.home / "profiles" / "test.json"
        value = json.loads(path.read_text())
        value["timeout"] = 80
        path.write_text(json.dumps(value))
        with pytest.raises(RepoScopeError, match="different inputs"):
            runner.collect(snapshot(), repo, "profile-bound")


def test_unconfirmed_cleanup_preserves_checkout_until_recovery(store):
    repo = profile(store)
    runner = TestRunner(store)
    process = SimpleNamespace(poll=lambda: 0, returncode=0)
    with (
        patch("reposcope.execution.runner.shutil.which", return_value="/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch.object(runner, "export_tree", return_value="suite"),
        patch("subprocess.Popen", return_value=process),
        patch.object(runner, "_remove", return_value=False),
    ):
        result = runner.collect(snapshot(), repo, "retained")
    checkout = Path(result["temporary_directory"])
    assert result["cleanup_status"] == "unconfirmed"
    assert checkout.exists()
    with (
        patch("reposcope.execution.runner.shutil.which", return_value="/docker"),
        patch.object(runner, "_remove", return_value=True),
    ):
        recovered = runner.recover("retained")
    assert recovered["cleanup_status"] == "completed"
    assert not checkout.exists()
    assert "temporary_directory" not in recovered
