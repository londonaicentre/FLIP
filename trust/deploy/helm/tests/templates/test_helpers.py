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

"""The image-tag helper in templates/_helpers.tpl, rendered: pin, then global release, then own tag (FLIP#1204).

``helm upgrade --set global.image.tag=<release>`` moves every FLIP-built image to one release; an
image's ``image.pin`` holds it apart (a ``sha-`` move where that image was never built at the sha).
Asserted on ``helm template`` output rather than the helper's text, so a reformatted helper or a
template that stops calling it is caught either way. Skipped without helm; the chart workflow's
helm-template job runs it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
FLIP_REGISTRY = "ghcr.io/londonaicentre/"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _images(*sets: str) -> list[str]:
    """Every container / initContainer image in the chart rendered with ``--set`` ``sets``."""
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    for item in sets:
        args += ["--set", item]
    rendered = subprocess.run(args, capture_output=True, text=True, check=True, timeout=120).stdout
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("containers", "initContainers") and isinstance(value, list):
                    found.extend(c["image"] for c in value if isinstance(c, dict) and "image" in c)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for doc in yaml.safe_load_all(rendered):
        walk(doc)
    return found


def test_the_release_moves_every_flip_image_and_a_pin_beats_it():
    images = _images("global.image.tag=v0.7.0", "orthanc.image.pin=sha-1111111", "flClient.image.pin=sha-2222222")
    flip = {image for image in images if image.startswith(FLIP_REGISTRY)}
    assert flip, images
    for image in flip:
        name, tag = image.removeprefix(FLIP_REGISTRY).rsplit(":", 1)
        expected = {"orthanc": "sha-1111111", "flare-fl-client": "sha-2222222"}.get(name, "v0.7.0")
        assert tag == expected, image


def test_the_release_never_touches_third_party_images():
    images = _images("global.image.tag=v0.7.0")
    others = [image for image in images if not image.startswith(FLIP_REGISTRY)]
    assert others, images
    assert not any(image.endswith(":v0.7.0") for image in others), others
