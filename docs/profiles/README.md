# Test execution profiles

The API accepts only a profile ID. Administrators place JSON at
`$REPOSCOPE_HOME/profiles/<id>.json`; repository contents cannot replace it.
Build `Dockerfile.tests`, install the target project's dependencies in a derived
image, publish it to your chosen registry and resolve its immutable digest.
Prepare/pull the image before execution. The runner never pulls or installs.

```json
{
  "image": "registry.example/repository-tests@sha256:<64 lowercase hex digits>",
  "python": "python",
  "timeout": 120,
  "memory": "512m",
  "cpus": 1,
  "pids_limit": 128,
  "test_paths": ["tests"]
}
```

The image value above is a placeholder, not a ready-to-run environment. Fixed
pytest commands execute in Docker with no network, dropped capabilities,
no-new-privileges, a read-only root filesystem and bounded resources. Only the
exported Git tree and temporary output/control directories are mounted. Symlink
and submodule entries are rejected. The original working tree is never modified.
Extra pytest plugin autoload and addopts are disabled to keep instrumentation
predictable; explicitly unsupported target environments report unavailable.

Collection must succeed before selected nodeids can execute. Every execution ID
is recorded before submission, and is never submitted twice. `recover(id)` is
for a supervisor that has confirmed the owning worker lease expired: it requests
container cleanup and marks the execution interrupted, without rerunning it.

Coverage records contain exact snapshot, full-tree test-suite hash, environment
profile hash and collector configuration hash. Full-tree hashing is deliberately
conservative; any tracked-file change invalidates direct reuse. Base coverage can
inform a head recommendation with explicit base provenance but is never evidence
that head code executed. Setup and teardown contexts remain separate from call
contexts: a shared fixture's setup may only run for its first consumer, so these
records do not prove all fixture consumers or assertions. Import-time lines are
unattributed. Container output is runtime evidence from the target program,
not protection against a deliberately falsified result by malicious test code.

The execution unit tests mock Docker submission and verify controls/result
handling; they are not a successful real-container acceptance run.
