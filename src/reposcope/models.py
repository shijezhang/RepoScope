import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class Symbol(BaseModel):
    symbol_id: str
    snapshot_id: str
    path: str
    module: str
    qualname: str
    kind: str
    start: int
    end: int
    signature: str = ""
    content_hash: str


class Relation(BaseModel):
    source_id: str
    target_id: str
    relation_type: Literal["CONTAINS", "IMPORTS", "CALLS", "INHERITS"]
    snapshot_id: str
    line: int
    resolution: Literal["resolved", "candidate"] = "resolved"
    method: str = "ast-static"


class Snapshot(BaseModel):
    snapshot_id: str
    repo_id: str
    commit_sha: str
    tree_hash: str
    manifest_hash: str
    parser_version: str = "ast312-resolver-v4"
    status: str = "ready"
    files: dict[str, str]
    symbols: list[Symbol]
    relations: list[Relation]
    unresolved: list[dict]
    diagnostics: list[dict]
    stats: dict = Field(default_factory=dict)


class AnalysisInput(BaseModel):
    repo_id: str
    base: str = Field(min_length=1, max_length=200)
    head: str = Field(min_length=1, max_length=200)
    mode: Literal["direct", "pr"] = "direct"
    question: str = Field(default="", max_length=4000)
    agent: bool = False
    allow_tests: bool = False


class RegisterInput(BaseModel):
    path: str
    name: str | None = None
    profile_id: str | None = None


class TestRunInput(BaseModel):
    plan_id: str
    attempt: int = Field(default=1, ge=1, le=3)
    reason: str | None = Field(default=None, max_length=500)


class Claim(BaseModel):
    claim_id: str
    text: str
    status: Literal["verified", "inferred", "unknown"]
    evidence_ids: list[str]
    limitations: list[str] = Field(default_factory=list)
