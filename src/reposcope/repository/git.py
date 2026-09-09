import re
import subprocess
from pathlib import Path, PurePosixPath

from reposcope.config import RepoScopeError, Settings
from reposcope.models import digest

ALLOWED = {".py", ".md", ".toml", ".ini", ".cfg", ".txt", ".lock", ".yaml", ".yml"}


def git(root: Path, *args: str, binary=False):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=45)
    if result.returncode:
        raise RepoScopeError("git_error", result.stderr.decode(errors="replace")[:2000])
    return result.stdout if binary else result.stdout.decode("utf-8", errors="replace").strip()


def validate_repository(path: str, settings: Settings) -> Path:
    root = Path(path).expanduser().resolve()
    if not any(root == allowed or root.is_relative_to(allowed) for allowed in settings.allowed_roots):
        raise RepoScopeError("repository_not_allowed", "Repository is outside configured allowed roots")
    if git(root, "rev-parse", "--show-toplevel") != str(root):
        raise RepoScopeError("invalid_repository", "Register the Git repository root")
    return root


def resolve(root: Path, revision: str) -> str:
    if not revision or revision.startswith("-") or len(revision) > 200:
        raise RepoScopeError("invalid_revision", "Invalid Git revision")
    try:
        return git(root, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
    except RepoScopeError as exc:
        raise RepoScopeError("invalid_revision", f"Cannot resolve commit: {revision}") from exc


def comparison(root: Path, base: str, head: str, mode: str) -> tuple[str, str]:
    b, h = resolve(root, base), resolve(root, head)
    return (git(root, "merge-base", b, h) if mode == "pr" else b), h


def read_tree(root: Path, sha: str, settings: Settings, cancelled=lambda: False) -> dict[str, str]:
    entries = git(root, "ls-tree", "-rz", "--full-tree", sha, binary=True).split(b"\0")
    files = {}
    total = 0
    for entry in entries:
        if cancelled():
            raise RepoScopeError("cancelled", "Snapshot read interrupted")
        if not entry:
            continue
        meta, raw_path = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        path = raw_path.decode("utf-8", "strict")
        if PurePosixPath(path).suffix not in ALLOWED:
            continue
        if mode not in {"100644", "100755"} or kind != "blob":
            raise RepoScopeError("unsupported_file", f"Symlink or submodule: {path}")
        size = int(git(root, "cat-file", "-s", oid))
        total += size
        if size > settings.max_file_bytes or total > settings.max_total_bytes or len(files) >= settings.max_files:
            raise RepoScopeError("repository_limit", f"Source size limit exceeded: {path}")
        content = git(root, "cat-file", "blob", oid, binary=True)
        try:
            if path.endswith(".py"):
                import io
                import tokenize

                encoding, _ = tokenize.detect_encoding(io.BytesIO(content).readline)
                files[path] = content.decode(encoding)
            else:
                files[path] = content.decode("utf-8")
        except (UnicodeError, SyntaxError) as exc:
            raise RepoScopeError("unsupported_encoding", f"Cannot decode {path}: {exc}") from exc
    return files


def changes(root: Path, base: str, head: str) -> tuple[list[dict], str]:
    raw = git(root, "diff", "--name-status", "-z", "-M", base, head, "--", binary=True)
    fields = raw.decode("utf-8").split("\0")
    result, i = [], 0
    while i < len(fields) and fields[i]:
        status, old = fields[i], fields[i + 1]
        i += 2
        new = old
        if status[0] in {"R", "C"}:
            new = fields[i]
            i += 1
        patch = git(root, "diff", "--no-ext-diff", "--no-textconv", "--unified=0", base, head, "--", old, new)
        hunks = []
        for match in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", patch, re.M):
            a, ac, b, bc = match.groups()
            hunks.append(
                {"old_start": int(a), "old_count": int(ac or 1), "new_start": int(b), "new_count": int(bc or 1)}
            )
        result.append({"path": new, "old_path": old, "status": status[0], "hunks": hunks})
    patch = git(root, "diff", "--no-ext-diff", "--no-textconv", "--binary", base, head, "--", binary=True)
    return result, digest(patch.hex())
