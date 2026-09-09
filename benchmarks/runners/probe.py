"""Collect public benchmark tests in explicitly prepared local environments.

This is an M0 developer environment probe, NOT the product's isolated runner.
No packages are installed and no user's repository is modified by this script.
"""

import json
import platform
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    results = []
    for repo in json.loads((ROOT / "benchmarks/manifests/repositories.json").read_text())["repositories"]:
        path = ROOT / repo["path"]
        python = ROOT / repo["environment"] / "bin/python"
        sha = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
        if sha != repo["commit"]:
            raise RuntimeError(f"Unexpected {repo['repo_id']} revision: {sha}")
        files = list((path / repo["source_root"]).rglob("*.py"))
        command = [str(python), "-m", "pytest", "--collect-only", "-q", *repo["test_args"]]
        start = time.perf_counter()
        proc = subprocess.run(command, cwd=path, text=True, capture_output=True, timeout=120)
        output = proc.stdout + proc.stderr
        log = ROOT / "artifacts/probes" / f"{repo['repo_id']}-collection.txt"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(output)
        nodes = [line for line in proc.stdout.splitlines() if "::" in line and not line.startswith(" ")]
        freeze = subprocess.check_output(
            ["uv", "--cache-dir", str(ROOT / "artifacts/uv-cache"), "pip", "freeze", "--python", str(python)], text=True
        )
        freeze = freeze.replace("file://" + str(ROOT) + "/", "")
        (ROOT / "benchmarks/manifests" / f"{repo['repo_id']}-environment.txt").write_text(freeze)
        results.append(
            {
                "repo_id": repo["repo_id"],
                "commit": sha,
                "python": subprocess.check_output([str(python), "--version"], text=True).strip(),
                "source_python_files": len(files),
                "source_lines": sum(len(p.read_text().splitlines()) for p in files),
                "source_bytes": sum(p.stat().st_size for p in files),
                "collection_exit_code": proc.returncode,
                "collected_nodeids": len(nodes),
                "collection_seconds": time.perf_counter() - start,
                "log": str(log.relative_to(ROOT)),
                "execution_kind": "local-environment-probe",
                "product_isolation_verified": False,
            }
        )
    result = {
        "platform": platform.platform(),
        "repositories": results,
        "quality_evaluation": "not_run",
        "annotation_status": "unreviewed",
    }
    (ROOT / "benchmarks/results/preparation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return int(any(r["collection_exit_code"] for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
