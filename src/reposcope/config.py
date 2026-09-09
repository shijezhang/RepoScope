import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    home: Path = field(default_factory=lambda: Path(os.getenv("REPOSCOPE_HOME", "artifacts/state")).resolve())
    allowed_roots: tuple[Path, ...] = field(
        default_factory=lambda: tuple(
            Path(p).resolve() for p in os.getenv("REPOSCOPE_ALLOWED_ROOTS", str(Path.cwd())).split(os.pathsep)
        )
    )
    max_files: int = 15000
    max_file_bytes: int = 1_000_000
    max_total_bytes: int = 80_000_000
    max_nodes: int = 1500
    max_depth: int = 8
    job_seconds: int = 180
    test_seconds: int = 120
    lease_seconds: int = 30


class RepoScopeError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable
