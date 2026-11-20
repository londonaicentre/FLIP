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

"""The FL images install torch from uv.lock, and only a hash makes that install verified (FLIP#944).

The PyTorch index omits sha256 for some wheels, so uv.lock may carry hand-added ones, and a relock that picks a wheel
without one still makes a valid lock: `uv lock --check` cannot catch it. The Flower fl-base would then fail
`pip --require-hashes` and the NVFLARE fl-base would install torch unverified.
"""

import re
import tomllib
from pathlib import Path
from urllib.parse import urlparse

FLIP_UTILS = Path(__file__).parents[2]
FL_SERVICES = FLIP_UTILS.parent / "fl-services"


def _fl_image_python_tag() -> str:
    """The CPython wheel tag of the FL images, read from both fl-base Dockerfiles, which must agree."""
    dockerfiles = [(FL_SERVICES / backend / "fl-base" / "Dockerfile").read_text() for backend in ("flower", "nvflare")]
    versions = {re.search(r"^FROM \S*?(?:python:|-py)(3\.\d+)", text, re.M)[1] for text in dockerfiles}
    assert len(versions) == 1, f"The fl-base images run different Pythons: {versions}"
    return "cp" + versions.pop().replace(".", "")


def test_every_pytorch_wheel_the_fl_images_install_carries_a_sha256():
    tag = _fl_image_python_tag()
    lock = tomllib.loads((FLIP_UTILS / "uv.lock").read_text())
    wheels = {
        package["name"]: [
            wheel
            for wheel in package.get("wheels", [])
            if "manylinux" in wheel["url"] and re.search(rf"-({tag}-{tag}|py3-none)-", wheel["url"])
        ]
        for package in lock["package"]
        if package["name"] in ("torch", "torchvision")
        and urlparse(package["source"].get("registry", "")).hostname == "download.pytorch.org"
    }
    assert set(wheels) == {"torch", "torchvision"}
    # Linux x86_64 for CI and the hosts, aarch64 for a native build on an arm64 workstation.
    assert all(len(found) >= 2 for found in wheels.values()), wheels

    unhashed = [w["url"].rsplit("/", 1)[-1] for found in wheels.values() for w in found if "hash" not in w]
    assert unhashed == [], "Hash these wheels by hand in uv.lock (see the pytorch index note in pyproject.toml)"
