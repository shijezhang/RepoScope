# ADR 003 — Immutable local images and managed execution workspaces

Status: accepted, 2026-09-09.

A prepared test image may be addressed either by registry digest
`repository@sha256:<64 hex>` or by Docker's local content-addressed image ID
`sha256:<64 hex>`. Both freeze image content. Mutable tags are rejected.
Local validation does not require publishing project images to a registry.
Environment preparation may download public base images and Python packages;
actual collection/test containers remain offline with `--pull never`.

Execution workspaces live below the configured RepoScope state directory in
`execution-tmp/`. This makes host bind mounts work on macOS Docker VMs whose
shared paths include the user home, without sharing arbitrary system temporary
folders. A workspace is deleted only once container cleanup is confirmed;
unconfirmed cleanup retains its recorded path for explicit recovery.

The local Docker runtime uses a dedicated Colima profile on Apple Silicon.
VM resource limits and the separate per-container limits are recorded by the
real validation script. This is a local development/validation environment,
not a public service for arbitrary untrusted repository execution.
