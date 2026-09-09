"""Local M0 base/head test probe, not a product isolation acceptance test."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILES = {"click-01": ["tests/test_types.py"], "httpx-02": ["tests/models/test_responses.py"]}


def main():
    cases = json.loads((ROOT / "benchmarks/cases/development.json").read_text())
    manifests = {
        r["repo_id"]: r
        for r in json.loads((ROOT / "benchmarks/manifests/repositories.json").read_text())["repositories"]
    }
    results = []
    for case in cases:
        if case["case_id"] not in PROFILES:
            continue
        manifest = manifests[case["repo_id"]]
        path = ROOT / "artifacts/regression-probes" / case["case_id"]
        if path.exists():
            shutil.rmtree(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(ROOT / manifest["path"]), str(path)], check=True
        )
        sha = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
        if sha != manifest["commit"]:
            raise RuntimeError("Base revision mismatch")
        python = ROOT / manifest["environment"] / "bin/python"
        env = {
            **os.environ,
            "PYTHONPATH": str(path / "src" if case["repo_id"] == "click" else path),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
        # HTTPX fixtures need anyio plugin, even when this selected pool is synchronous.
        command = [str(python), "-m", "pytest", "-q", *PROFILES[case["case_id"]]]
        if case["repo_id"] == "httpx":
            command += ["-p", "anyio.pytest_plugin"]
        phases = {}
        for phase in ["base", "head"]:
            if phase == "head":
                mutation = case["mutation"]
                target = path / mutation["path"]
                source = target.read_text()
                if mutation["old"] not in source:
                    raise RuntimeError("Mutation anchor missing")
                target.write_text(source.replace(mutation["old"], mutation["new"]))
            start = time.perf_counter()
            proc = subprocess.run(command, cwd=path, env=env, text=True, capture_output=True, timeout=120)
            log = path.parent / f"{case['case_id']}-{phase}.txt"
            log.write_text(proc.stdout + proc.stderr)
            phases[phase] = {
                "exit_code": proc.returncode,
                "seconds": time.perf_counter() - start,
                "summary": proc.stdout.strip().splitlines()[-1:] or proc.stderr.strip().splitlines()[-1:],
                "log": str(log.relative_to(ROOT)),
            }
        results.append(
            {
                "case_id": case["case_id"],
                "base_commit": sha,
                "mutation": case["mutation"],
                "test_files": PROFILES[case["case_id"]],
                "phases": phases,
                "execution_kind": "local-environment-probe",
                "product_isolation_verified": False,
                "annotation_status": "unreviewed",
                "observed_regression": phases["base"]["exit_code"] == 0 and phases["head"]["exit_code"] == 1,
            }
        )
    (ROOT / "benchmarks/results/regression-probes.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    return int(not all(r["observed_regression"] for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
