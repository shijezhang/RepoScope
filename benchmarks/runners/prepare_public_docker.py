"""Build minimal-context Docker environments from the frozen host-probe manifests."""

import argparse
import io
import json
import os
import subprocess
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", choices=["click", "httpx"])
    parser.add_argument("--base-image", required=True)
    args = parser.parse_args()
    name = args.repository
    versions = {"click": "8.1.8", "httpx": "0.28.1"}
    lines = (ROOT / f"benchmarks/manifests/{name}-environment.txt").read_text().splitlines()
    requirements = "\n".join(line for line in lines if line and not line.startswith("-e "))
    requirements += f"\n{name}=={versions[name]}\ncoverage==7.10.6\npytest-cov==6.2.1\n"
    dockerfile = "ARG TEST_BASE\nFROM ${TEST_BASE}\nCOPY requirements.txt /tmp/requirements.txt\nRUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path, content in [("Dockerfile", dockerfile), ("requirements.txt", requirements)]:
            data = content.encode()
            item = tarfile.TarInfo(path)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
    directory = ROOT / "artifacts/public-docker-builds"
    directory.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with (directory / f"{name}.log").open("wb") as log:
        result = subprocess.run(
            [
                "docker",
                "build",
                "--build-arg",
                "TEST_BASE=" + args.base_image,
                "-t",
                f"reposcope-{name}-tests:local",
                "-",
            ],
            input=buffer.getvalue(),
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "DOCKER_BUILDKIT": "0"},
        )
    if result.returncode:
        raise RuntimeError(f"Build failed; inspect {directory / (name + '.log')}")
    image = subprocess.check_output(
        ["docker", "image", "inspect", f"reposcope-{name}-tests:local", "--format", "{{.Id}}"], text=True
    ).strip()
    manifest = {
        "build_wall_seconds": time.perf_counter() - started,
        "repository": name,
        "image_id": image,
        "base_image": args.base_image,
        "requirements": requirements.splitlines(),
        "build_context": "Dockerfile and requirements only",
    }
    (ROOT / f"benchmarks/manifests/{name}-docker-image.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
