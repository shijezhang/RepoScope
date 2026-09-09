# Test execution profiles

The API accepts only a profile ID. Administrators place JSON at
`$REPOSCOPE_HOME/profiles/<id>.json`; repository contents cannot replace it.
Build `Dockerfile.tests`, install the target project's dependencies in a derived
image, then resolve its immutable local image ID or registry digest. Publishing
to an external registry is optional; mutable image tags are rejected.
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
handling. Run the separate real-container acceptance script to verify the local
runtime; its JSON output is evidence only for the tested image and fixture.

## Local Apple Silicon runtime

```bash
brew install colima docker
colima start reposcope --cpus 2 --memory 3 --disk 12 --vm-type vz --runtime docker \
  --mount "$PWD:w" --ssh-config=false
docker info
DOCKER_BUILDKIT=0 docker build -t reposcope-fixture-tests:local - < Dockerfile.tests
docker image inspect reposcope-fixture-tests:local --format '{{.Id}}'
.venv/bin/python benchmarks/runners/docker_validation.py --image sha256:<actual-image-id>
```

The script creates `artifacts/state/profiles/fixture.json` and uses a separate
`artifacts/docker-validation-state` for its snapshots and execution evidence.
It validates base/head collection, regression results, coverage version binding,
a live-container cancellation, timeout cleanup, actual Docker isolation flags,
and unchanged original working-tree state. Summary evidence is written to
`benchmarks/results/docker-validation.json`; full logs and coverage stay in the
separate state directory. The small fixture uses `test_calc.py`, not a `tests/`
directory. Other repositories need their own prepared images and profiles.

Workspaces under the configured state directory's `execution-tmp/` must be
shared with the Docker VM. Completed cleanup removes them; unconfirmed cleanup
retains them for recovery. Stop the optional local VM with
`colima stop reposcope` when it is no longer needed.

## Recorded local acceptance — 2026-09-09

[The checked-in result](../../benchmarks/results/docker-validation.json) records
an actual ARM Linux Docker run: fixture-01 base passed 4/4 tests; head passed 1
and failed 3. The comparison is `suspected_regression`, because one run does not
exclude flaky behavior. Both sides exported 15 coverage context records each.
Cancellation was requested only after observing the slow-test container running;
cancellation and a separate 3-second timeout both reclaimed the container and
checkout. Runtime inspection confirmed no network, read-only root, all
capabilities dropped, no-new-privileges, 512 MiB memory, 1 CPU and 128 PIDs.
The source working-tree status was unchanged.

The validation image ID was
`sha256:f8f79ca52e3acbd15007628491597ce67846037dc04dbb404b04d12536deea9f`.
Its interpreter and packages are frozen in
[the environment manifest](../../benchmarks/manifests/docker-fixture-environment.txt).
The Docker VM used Engine 29.5.2 on Ubuntu 24.04.4 ARM, 2 CPUs and 3 GiB configured
memory, a 12 GiB data disk and the default 20 GiB sparse root disk. Colima is not
registered as a login service.

For a fresh checkout, first recreate only the owned fixture:

```bash
.venv/bin/python benchmarks/runners/replay.py --case fixture-01
```

Docker Hub timed out in this local network. The successful build instead used
Docker's official Python image on AWS ECR Public, frozen to this base digest:

```bash
DOCKER_BUILDKIT=0 docker build \
  --build-arg PYTHON_IMAGE=public.ecr.aws/docker/library/python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea \
  -t reposcope-fixture-tests:local - < Dockerfile.tests
```

This uses Docker's built-in legacy builder on the minimal CLI installation;
install Docker Buildx if you prefer BuildKit. AWS's public registry is an
[official Docker image distribution channel](https://www.docker.com/blog/news-from-aws-reinvent-docker-official-images-on-amazon-ecr-public/).
The build sends only the Dockerfile as context. No image publication or account
credentials are needed. Different architectures or rebuilt images get their own
content IDs and must be frozen in a new profile before executing.

## Public-project fixed pools

The additional `click` and `httpx` profiles intentionally register only the
previously frozen regression pools: `tests/test_types.py` for Click 8.1.8 and
`tests/models/test_responses.py` for HTTPX 0.28.1. “All” tests under one of these
profiles means all collected tests in that registered pool, not the upstream
repository's entire suite.

The image preparation script uses the checked-in dependency manifests and
preinstalls the matching project release for package metadata. During actual
execution, `/work` and `/work/src` take precedence on `PYTHONPATH`; collected
coverage must demonstrate execution of the frozen source tree. HTTPX's profile
explicitly allows `anyio.pytest_plugin` for async tests. Plugin autoload remains
disabled. `pytest_plugins` is a server-managed list of Python module names;
it cannot carry shell commands, arguments, paths or pytest disable directives.

```bash
.venv/bin/python benchmarks/runners/prepare_public_docker.py click --base-image sha256:<fixture-image-id>
.venv/bin/python benchmarks/runners/prepare_public_docker.py httpx --base-image sha256:<fixture-image-id>
.venv/bin/python benchmarks/runners/public_docker_validation.py --repository click
.venv/bin/python benchmarks/runners/public_docker_validation.py --repository httpx
```

Prepare the corresponding benchmark replay repositories first when absent:

```bash
.venv/bin/python benchmarks/runners/replay.py --case click-01
.venv/bin/python benchmarks/runners/replay.py --case httpx-02
```

Replay now creates a new immutable run directory each time. Validation resolves
both `<case>-index.json` and `index-consistency.json`, tries the newer manifest
first, and accepts a candidate only when its repository directory and both
recorded commit objects exist. It uses the recorded SHAs, not the repository's
current `HEAD`; historical evidence therefore survives later replay runs.
Missing or stale paths fall back to the other available manifest with a recorded
reason. You can verify input resolution without Docker, testing or state writes:

```bash
.venv/bin/python benchmarks/runners/docker_validation.py --locate-only
.venv/bin/python benchmarks/runners/public_docker_validation.py --locate-only
```
Image identities and complete installed environments are saved under
`benchmarks/manifests/*-docker-*`; the two repository results are saved in
`benchmarks/results/public-docker-validation.json`. Runtime state and raw
coverage/logs use `artifacts/public-docker-validation-state`, separate from the
interactive workbench state. Each side executes the identical collected pool
twice, with separate execution IDs and an explicit repeat reason. Matching
observations are reported as such; they do not prove that tests are never flaky.

Each collection and execution records `started_at` and `wall_seconds`, including
image inspection, Git export, Docker invocation and cleanup. `pytest_phase_seconds`
counts only pytest setup/call/teardown durations and excludes container overhead.
Image preparation is a separate phase. The first two public-image builds began
before build timing was added, so their first-build wall time is not reconstructed;
subsequent preparation runs record their own measured build wall time.

### Recorded public-pool acceptance — 2026-09-10

| Frozen pool | First base result | First head result | Repeat observation | Selected / pool |
|---|---|---|---|---|
| Click `click-01` | 39 passed | 35 passed, 4 failed | Same per-nodeid statuses on both sides | 39 / 39 |
| HTTPX `httpx-02` | 106 passed | 105 passed, 1 failed | Same per-nodeid statuses on both sides | 106 / 106 |

These are actual product `TestRunner` Docker executions. The host Click probe
had one platform-specific skip; the Linux container executed all 39 tests.
The parser versions actually attached to these snapshots were v2 for Click and
v3 for HTTPX. We did not relabel the earlier snapshots as a newer parser run.
First-run collection wall times were 6.24/4.78 seconds (Click base/head) and
4.44/4.64 seconds (HTTPX). First-run test wall times were 5.65/5.13 seconds and
9.80/11.54 seconds, respectively; corresponding pytest phase sums were only
0.122/0.125 and 0.647/0.961 seconds. Full precision and repeat timings remain in
the JSON evidence.

[The selection engineering replay](../../benchmarks/results/public-selection-engineering.json)
used only source snapshots, collected nodeids and exactly base-bound coverage.
Both repositories triggered conservative full-pool fallback for unresolved
calls. Test-count reduction was **0%**, so this observation supports no selection
saving claim. It did not feed the head failure set into selection and did not
run an alternative policy. It is not a held-out quality benchmark.

HTTPX's complete Git tree includes a 1,997,816-byte documentation PNG. The initial
1 MB source-oriented checkout limit correctly refused collection before any
container submission. Its profile now sets `max_checkout_file_bytes: 4000000`;
the server bounds this field at 20 MB and retains the total checkout/file-count
budgets. The asset remains in the frozen export. Failed preparation execution
records are retained in the validation state; subsequent attempts use new IDs.

All successful execution records contain coverage from the frozen project source
paths, and all their test containers and temporary checkouts were reclaimed.
Default-state profiles are `click` and `httpx`; immutable image IDs and the complete
installed dependency versions are recorded in their checked-in manifests.
