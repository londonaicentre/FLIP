#!/usr/bin/env python3
# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Prove a release instance runs this commit's code before any gate counts (FLIP#1608).

``make release-verify TAG=v0.13.0 [INSTANCE=rel0130] [PR=<n>]`` fails unless:

- the checkout is clean and, with ``PR``, at the PR's head;
- every ``<INSTANCE>-`` compose container was started from this checkout;
- every repo-built image (``ghcr.io/londonaicentre/*``, ``flare-*``, ``flower-*``, the local flip-ui build) of a
  container or XNAT swarm service is at ``:<INSTANCE>`` — never a pulled ``:stag`` / ``:dev``;
- every XNAT swarm service is fully replicated;
- the hub's ``/api/health`` is ok and reports the release version;
- flip-api and every trust API container hold the env file's ``AES_KEY_BASE64`` (compared by digest, never shown);
- the env file and kits carry no unfilled ``<placeholder>``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_lib import (  # noqa: E402
    REPO,
    ReleaseError,
    assert_at_pr_head,
    assert_clean,
    default_instance,
    env_file_name,
    parse_tag,
    placeholders,
    read_env,
)

REPO_IMAGE_PREFIXES = ("ghcr.io/londonaicentre/", "flare-", "flower-")
AES_SERVICES = ("flip-api", "trust-api", "imaging-api", "data-access-api")


def _docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout


def containers(instance: str) -> list[dict]:
    """``docker inspect`` of every container (running or not) named ``<instance>-…``."""
    ids = _docker("ps", "-aq", "--filter", f"name=^{instance}-").split()
    return json.loads(_docker("inspect", *ids)) if ids else []


def services(instance: str) -> list[dict]:
    """The instance's swarm services (XNAT), as ``docker service ls`` JSON lines."""
    out = _docker("service", "ls", "--filter", f"name={instance}-", "--format", "{{json .}}")
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def is_repo_built(image: str, instance: str) -> bool:
    """Whether an image is one this repo builds, so its tag must be the instance's."""
    return image.startswith(REPO_IMAGE_PREFIXES) or image.startswith(f"{instance}-")


def image_ok(image: str, instance: str) -> bool:
    """A repo-built image passes only at the instance tag; compose's own flip-ui build is named for the project."""
    if image.startswith(f"{instance}-"):
        return True
    name = image.split("@", 1)[0].split("/")[-1]  # a swarm task's image carries @sha256:…
    return name.partition(":")[2] == instance


def check_containers(found: list[dict], instance: str, repo: Path) -> list[str]:
    """Problems with the instance's containers: foreign checkout, or a repo image not at the instance tag."""
    problems = []
    if not found:
        return [f"no containers named {instance}-*"]
    for c in found:
        name = c["Name"].lstrip("/")
        labels = c["Config"].get("Labels") or {}
        image = c["Config"]["Image"]
        workdir = labels.get("com.docker.compose.project.working_dir")
        if workdir is None and "com.docker.stack.namespace" not in labels:
            problems.append(f"{name}: neither a compose container nor a swarm task")
        elif workdir is not None and not Path(workdir).resolve().is_relative_to(repo.resolve()):
            problems.append(f"{name}: started from {workdir}, not this checkout")
        if is_repo_built(image, instance) and not image_ok(image, instance):
            problems.append(f"{name}: runs {image}, not :{instance}")
    return problems


def check_services(found: list[dict], instance: str) -> list[str]:
    """Problems with the XNAT swarm services: not fully replicated, or a repo image not at the instance tag."""
    problems = []
    for s in found:
        running, _, wanted = s["Replicas"].partition("/")
        if running != wanted or wanted in ("", "0"):
            problems.append(f"{s['Name']}: {s['Replicas']} replicas")
        if is_repo_built(s["Image"], instance) and not image_ok(s["Image"], instance):
            problems.append(f"{s['Name']}: runs {s['Image']}, not :{instance}")
    return problems


def check_health(api_port: str, tag: str) -> list[str]:
    """The hub answers and names the release (a local build reports the pyproject version)."""
    want = ".".join(map(str, parse_tag(tag)))
    try:
        with urllib.request.urlopen(f"http://localhost:{api_port}/api/health", timeout=10) as r:  # noqa: S310
            body = json.loads(r.read())
    except OSError as e:
        return [f"hub /api/health on :{api_port} unreachable: {e}"]
    if body.get("status") != "ok" or body.get("version") not in (want, tag):
        return [f"hub /api/health says {body.get('status')!r} version {body.get('version')!r}, want {want}"]
    return []


def digest(value: str) -> str:
    """A short digest that identifies a secret without revealing it."""
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def check_aes(found: list[dict], expected: str) -> list[str]:
    """Every container that encrypts or decrypts hub↔trust payloads holds the env file's key."""
    problems = []
    for c in found:
        labels = c["Config"].get("Labels") or {}
        if labels.get("com.docker.compose.service") not in AES_SERVICES or not c["State"]["Running"]:
            continue
        name = c["Name"].lstrip("/")
        value = _docker("exec", name, "printenv", "AES_KEY_BASE64").strip()
        if digest(value) != expected:
            problems.append(f"{name}: AES key {digest(value)} differs from the env file's {expected}")
    return problems


def check_placeholders(repo: Path, instance: str) -> list[str]:
    """No env file or kit is left with an unfilled ``<placeholder>``."""
    files = [repo / env_file_name(instance), *sorted((repo / "trust").glob(".env.*.development"))]
    return [
        f"{p.relative_to(repo)}: unfilled {', '.join(placeholders(p.read_text()))}"
        for p in files
        if placeholders(p.read_text())
    ]


def verify(tag: str, instance: str, pr: str | None = None, repo: Path = REPO) -> list[str]:
    """Every check above; returns the problems (empty when the instance is provably this commit)."""
    assert_clean(repo)
    sha = assert_at_pr_head(pr, repo)
    env = read_env(repo / env_file_name(instance))
    found = containers(instance)
    problems = [
        *check_containers(found, instance, repo),
        *check_services(services(instance), instance),
        *check_health(env["API_PORT"], tag),
        *check_aes(found, digest(env["AES_KEY_BASE64"])),
        *check_placeholders(repo, instance),
    ]
    print(f"{'instance':<12}{instance}\n{'commit':<12}{sha[:9]}\n{'containers':<12}{len(found)}")
    for line in problems:
        print(f"  ✗ {line}")
    print("✅ verified" if not problems else f"❌ {len(problems)} problem(s)")
    return problems


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--instance", default="")
    parser.add_argument("--pr", default="")
    a = parser.parse_args(argv)
    try:
        return 1 if verify(a.tag, a.instance or default_instance(a.tag), a.pr or None) else 0
    except (ReleaseError, subprocess.CalledProcessError, KeyError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
