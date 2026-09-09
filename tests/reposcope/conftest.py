import subprocess

import pytest

from reposcope.config import Settings
from reposcope.graph.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(Settings(home=tmp_path / "state", allowed_roots=(tmp_path,)))


@pytest.fixture
def git_repo(tmp_path):
    path = tmp_path / "repo with spaces"
    path.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(path), *args], stderr=subprocess.DEVNULL).decode().strip()

    git("init", "-b", "main")
    git("config", "user.name", "RepoScope fixture")
    git("config", "user.email", "fixture@example.invalid")

    def commit(files, message="fixture"):
        for name, content in files.items():
            target = path / name
            if content is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
        git("add", "-A")
        git("commit", "-m", message)
        return git("rev-parse", "HEAD")

    return {"repo_id": "fixture", "name": "fixture", "path": str(path)}, commit, git
