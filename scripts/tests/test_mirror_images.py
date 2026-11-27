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

"""Every third-party image a trust node runs is FLIP's GHCR mirror of it, pinned by digest (FLIP#1721).

A site's proxy allowlists registries by host name, so each registry a node pulls from is a whole
public registry opened, and the control over what runs is the image reference. The compose files,
the XNAT stack, the Helm chart, the Ansible roles and the trust Makefiles' throwaway containers
(``HOST_TOOLS_IMAGE``) must therefore name only
``ghcr.io/londonaicentre/*``, with the mirrored images as ``mirror/<name>:<tag>@<digest>`` at the
digest ``trust/deploy/mirror-images.manifest`` records (what ``scripts/mirror_images.sh`` copies).

Stdlib-only and executable as a plain script, like the rest of ``scripts/tests``.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "trust" / "deploy" / "mirror-images.manifest"
MIRROR = "ghcr.io/londonaicentre/mirror/"
HELM = REPO / "trust" / "deploy" / "helm"

# Everything a trust node runs, outside the chart: `image:` (compose, swarm) and `<name>_image:` (the
# Ansible roles' defaults, the only place a role may name an image).
ROLES = "trust/deploy/ansible/roles"


def role_files(pattern: str) -> list[str]:
    """The tracked role files: a machine that has run a play also holds gitignored Galaxy roles there."""
    listed = subprocess.run(
        ["git", "ls-files", "--", f"{ROLES}/{pattern}"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return listed.stdout.split()


ROLE_DEFAULTS = role_files("*/defaults/main.yml")
# `docker run` options that take no value; every other option is followed by one.
RUN_FLAGS = {"-d", "--rm"}


def run_image(run: str) -> str:
    """What a `docker run …` command names as its image: the first word that is no option or option value.

    A `{{ name }}` is returned as `name`, any other Jinja expression as `<expr>`."""
    run = re.sub(r"\{\{ *(\w+) *\}\}", r"\1", run)
    words = shlex.split(re.sub(r"\{\{.*?\}\}", "<expr>", run))[2:]
    while words and words[0].startswith("-"):
        words = words[1 if words[0] in RUN_FLAGS or "=" in words[0] else 2 :]
    return words[0] if words else ""


PINNED_FILES = (
    "trust/deploy/compose/compose_trust.prod.yml",
    "trust/deploy/compose/compose_trust.development.yml",
    "trust/xnat/docker-compose-stack.yml",
    *ROLE_DEFAULTS,
)
MANIFEST_LINE = re.compile(r"^([a-z0-9.-]+\.[a-z]+/[a-z0-9./_-]+):([A-Za-z0-9._-]+)\s+(sha256:[0-9a-f]{64})$")
PIN = re.compile(r"^ghcr\.io/londonaicentre/mirror/([a-z0-9./_-]+):([A-Za-z0-9._-]+)@(sha256:[0-9a-f]{64})$")
# The trust Makefiles' throwaway containers (a chown without root, the XNAT dump): one image, defined in
# the fragment both include. A literal there was docker.io's alpine, which a GHCR-only host cannot pull (FLIP#1784).
HOST_TOOLS_DEFINITION = "deploy/portable.mk"
HOST_TOOLS_USERS = ("trust/Makefile", "trust/xnat/Makefile")
HELM_IMAGE = re.compile(r'repository: (\S+)\n\s+tag: "([^"]+)"\n(?:\s+digest: "([^"]+)"\n)?')


def manifest() -> dict[str, str]:
    """``<name>:<tag>`` (upstream path without its registry) -> index digest."""
    pins = {}
    for line in MANIFEST.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        match = MANIFEST_LINE.match(line)
        assert match, f"malformed manifest line: {line!r}"
        name = match.group(1).split("/", 1)[1]
        key = f"{name}:{match.group(2)}"
        assert key not in pins, f"{key} listed twice"
        pins[key] = match.group(3)
    assert pins, "empty manifest"
    return pins


def image_refs() -> list[tuple[str, str]]:
    """(file, reference) for every image the compose, swarm and Ansible files name."""
    refs = []
    for rel in PINNED_FILES:
        for match in re.finditer(r"^\s*(?:image|\w+_image):\s*\"?([^\"\s#]+)", (REPO / rel).read_text(), re.M):
            refs.append((rel, match.group(1)))
    host_tools = re.search(r"^HOST_TOOLS_IMAGE := (\S+)$", (REPO / HOST_TOOLS_DEFINITION).read_text(), re.M)
    assert host_tools, f"{HOST_TOOLS_DEFINITION} defines no HOST_TOOLS_IMAGE"
    refs.append((HOST_TOOLS_DEFINITION, host_tools.group(1)))
    return refs


def helm_images() -> list[tuple[str, str, str | None]]:
    """(repository, tag, digest) for every image block in the chart's values.yaml."""
    return HELM_IMAGE.findall((HELM / "values.yaml").read_text())


class MirrorPins(unittest.TestCase):
    def test_every_image_a_trust_runs_comes_from_ghcr(self):
        for rel, ref in image_refs():
            # The XNAT stack's own images go through DOCKER_REGISTRY, which trust/Makefile fixes to GHCR.
            assert ref.startswith(("ghcr.io/londonaicentre/", "${DOCKER_REGISTRY}")), f"{rel}: {ref}"
        for repository, _tag, _digest in helm_images():
            assert repository.startswith("ghcr.io/londonaicentre/"), f"values.yaml: {repository}"
        for template in sorted((HELM / "templates").glob("*.yaml")):
            for match in re.finditer(r"^\s*image:\s*\"?([^\"\s]+)", template.read_text(), re.M):
                ref = match.group(1)
                # A literal reference bypasses values.yaml, and so the manifest.
                assert ref.startswith("{{"), f"{template.name}: {ref} is not taken from values.yaml"

    def test_the_makefiles_run_only_the_host_tools_image(self):
        for rel in HOST_TOOLS_USERS:
            text = (REPO / rel).read_text().replace("\\\n", " ")
            # An invocation passes options; prose such as "(docker run failed)" in a message does not.
            runs = re.findall(r"docker run\s+-[^|;]*", text)
            assert runs, f"{rel}: no docker run found (did the guard go stale?)"
            for run in runs:
                assert "$(HOST_TOOLS_IMAGE)" in run, f"{rel}: {run[:120]} runs an image other than HOST_TOOLS_IMAGE"

    def test_the_ansible_roles_run_only_images_their_defaults_name(self):
        """A literal in a task (`docker run … ubuntu:24.04`, `image: …`) is an image the pins above never see."""
        runs = 0
        for rel in role_files("*/tasks/*.yml"):
            defaults = REPO / rel.split("/tasks/")[0] / "defaults" / "main.yml"
            named = set(re.findall(r"^(\w+_image):", defaults.read_text(), re.M)) if defaults.exists() else set()
            text = re.sub(r"^\s*#.*$", "", (REPO / rel).read_text(), flags=re.M)
            # A run ends where its task's next key starts.
            for run in re.findall(r"docker run\s.*?(?=\n\s*\w+:|\Z)", text, re.S):
                runs += 1
                image = run_image(run)
                assert image in named, f"{rel}: `docker run` names {image!r}, which the role's defaults do not define"
            for image in re.findall(r"^\s*image:\s*(.*)$", text, re.M):
                assert re.fullmatch(r'"?\{\{ *(\w+) *\}\}"?', image), f"{rel}: image: {image} is not a variable"
                assert re.sub(r"[^\w]", "", image) in named, f"{rel}: image: {image} is not in the role's defaults"
        assert runs, f"{ROLES}: no docker run found (did the guard go stale?)"

    def test_the_role_guard_reads_the_image_position(self):
        for run, image in (
            ("docker run --rm --gpus all {{ gpu_check_image }} nvidia-smi --format=csv", "gpu_check_image"),
            ("docker run ubuntu:24.04 true", "ubuntu:24.04"),
            ("docker run -d --name x -e A={{ a | quote }} ubuntu:24.04 {{ uv_image }}", "ubuntu:24.04"),
            ('docker run --rm -e T="a @ b" -v {{ d }}:/seed {{ uv_image }} bash /seed/x.sh', "uv_image"),
            ("docker run -d ghcr.io/x/{{ name }}:{{ tag }}", "ghcr.io/x/name:tag"),
        ):
            assert run_image(run) == image, run

    def test_mirrored_images_are_pinned_to_the_manifest(self):
        pins = manifest()
        seen = set()
        for rel, ref in image_refs():
            if not ref.startswith(MIRROR):
                continue
            match = PIN.match(ref)
            assert match, f"{rel}: {ref} is not <name>:<tag>@<digest>"
            key = f"{match.group(1)}:{match.group(2)}"
            assert pins.get(key) == match.group(3), f"{rel}: {ref} is not the manifest's digest for {key}"
            seen.add(key)
        for repository, tag, digest in helm_images():
            if not repository.startswith(MIRROR):
                continue
            key = f"{repository.removeprefix(MIRROR)}:{tag}"
            assert digest, f"values.yaml: {key} has no digest"
            assert pins.get(key) == digest, f"values.yaml: {key}@{digest} is not the manifest's digest"
            seen.add(key)
        assert seen == set(pins), f"manifest entries pinned nowhere: {sorted(set(pins) - seen)}"

    def test_chart_renders_the_observability_digests(self):
        for name in ("loki", "alloy", "grafana"):
            text = (HELM / "templates" / f"{name}.yaml").read_text()
            image = next(line for line in text.splitlines() if line.strip().startswith("image:"))
            assert f".Values.observability.{name}.image.digest" in image, f"{name}.yaml: {image.strip()}"

    def test_the_mirror_workflow_and_this_guard_watch_the_manifest(self):
        workflows = REPO / ".github" / "workflows"
        mirror = (workflows / "mirror_images.yml").read_text()
        assert mirror.count('- "trust/deploy/mirror-images.manifest"') == 2, "mirror_images.yml: push and pull_request"
        assert mirror.count('- "scripts/mirror_images.sh"') == 2
        guard = (workflows / "test_trust_kit_scripts.yml").read_text()
        watched = (
            "trust/deploy/mirror-images.manifest",
            PINNED_FILES[2],
            f"{ROLES}/*/defaults/main.yml",
            f"{ROLES}/*/tasks/*.yml",
        )
        for rel in watched:
            assert f'- "{rel}"' in guard, f"test_trust_kit_scripts.yml does not run on {rel}"


if __name__ == "__main__":
    unittest.main()
