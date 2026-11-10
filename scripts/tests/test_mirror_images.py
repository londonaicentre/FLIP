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
the XNAT stack, the Helm chart and the Ansible seed role must therefore name only
``ghcr.io/londonaicentre/*``, with the mirrored images as ``mirror/<name>:<tag>@<digest>`` at the
digest ``trust/deploy/mirror-images.manifest`` records (what ``scripts/mirror_images.sh`` copies).

Stdlib-only and executable as a plain script, like the rest of ``scripts/tests``.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "trust" / "deploy" / "mirror-images.manifest"
MIRROR = "ghcr.io/londonaicentre/mirror/"
HELM = REPO / "trust" / "deploy" / "helm"

# Everything a trust node runs, outside the chart: `image:` (compose, swarm) and `uv_image:` (Ansible).
PINNED_FILES = (
    "trust/deploy/compose/compose_trust.prod.yml",
    "trust/deploy/compose/compose_trust.development.yml",
    "trust/xnat/docker-compose-stack.yml",
    "trust/deploy/ansible/roles/flip_trust_seed/defaults/main.yml",
)
MANIFEST_LINE = re.compile(r"^([a-z0-9.-]+\.[a-z]+/[a-z0-9./_-]+):([A-Za-z0-9._-]+)\s+(sha256:[0-9a-f]{64})$")
PIN = re.compile(r"^ghcr\.io/londonaicentre/mirror/([a-z0-9./_-]+):([A-Za-z0-9._-]+)@(sha256:[0-9a-f]{64})$")
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
        for match in re.finditer(r"^\s*(?:image|uv_image):\s*\"?([^\"\s#]+)", (REPO / rel).read_text(), re.M):
            refs.append((rel, match.group(1)))
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
        for rel in ("trust/deploy/mirror-images.manifest", *PINNED_FILES[2:]):
            assert f'- "{rel}"' in guard, f"test_trust_kit_scripts.yml does not run on {rel}"


if __name__ == "__main__":
    unittest.main()
