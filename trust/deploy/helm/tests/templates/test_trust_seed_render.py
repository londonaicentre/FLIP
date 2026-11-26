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

"""The trust-seed Job, rendered: what trust/seed_trust.sh is told (FLIP#2111, FLIP#2118).

The hook seeds only an empty store, so `make reseed` (trustData.seed.reseed=true) is the one way a
seeded trust reaches a new trustData.version; the value has to arrive as RESEED in the container,
and the hook must not retry. Its tools come from the git ref of the release the site runs, so a
release keeps seeding with the code it shipped. Skipped without helm.
"""

import functools
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _render(*sets: str) -> subprocess.CompletedProcess:
    """``helm template`` with each item as a ``--set``, or passed as is when it is a flag."""
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    it = iter(sets)
    for item in it:
        args += [item, next(it)] if item.startswith("--") else ["--set", item]
    return subprocess.run(args, capture_output=True, text=True, timeout=120)


@functools.cache
def _job(*sets: str) -> dict:
    result = _render("--show-only", "templates/trust-seed-job.yaml", *sets)
    assert result.returncode == 0, result.stderr
    for doc in yaml.safe_load_all(result.stdout):
        if doc and doc.get("kind") == "Job" and doc["metadata"]["name"].endswith("-trust-seed"):
            return doc
    raise AssertionError("no trust-seed Job rendered")


def _env(job: dict) -> dict[str, str]:
    (container,) = job["spec"]["template"]["spec"]["containers"]
    return {e["name"]: e.get("value") for e in container["env"]}


@pytest.mark.parametrize(("sets", "reseed"), [((), "false"), (("trustData.seed.reseed=true",), "true")])
def test_reseed_reaches_the_container(sets, reseed):
    assert _env(_job(*sets))["RESEED"] == reseed


def test_the_hook_does_not_retry():
    """A retry would find the store a failed attempt part-loaded, skip it, and report success."""
    assert _job()["spec"]["backoffLimit"] == 0


@pytest.mark.parametrize(
    ("sets", "ref"),
    [
        (("global.image.tag=v0.16.2",), "v0.16.2"),
        (("global.image.tag=v0.17.0-rc.1",), "v0.17.0-rc.1"),
        (("global.image.tag=v0.17.0-beta.1",), "v0.17.0-beta.1"),
        (("global.image.tag=sha-deee9f7",), "deee9f7"),
        (("global.image.tag=prod",), "main"),
        (("global.image.tag=main",), "main"),
        (("global.image.tag=develop",), "develop"),
        ((), "develop"),  # no release pin: the services' own :stag tag
        (("trustApi.image.tag=v0.16.2",), "v0.16.2"),  # the ref follows trust-api's tag
        (("global.image.tag=v0.16.2", "trustData.seed.sourceRef=abc1234"), "abc1234"),
        (("global.image.tag=latest", "trustData.seed.sourceRef=abc1234"), "abc1234"),
    ],
)
def test_the_seed_tools_come_from_the_release_the_site_runs(sets, ref):
    """The seed tools follow the image tag, so a release seeds with the tools it shipped (FLIP#2118)."""
    assert _env(_job(*sets))["FLIP_REF"] == ref


# One per anchor and suffix rule: a loosened pattern would hand a malformed tag to the hook, which
# 404s after Helm has applied the release instead of failing the render before it.
@pytest.mark.parametrize("tag", ["latest", "dev", "xv0.16.2", "v0.16", "v0.16.2-", "sha-DEEE9F7", "sha-deee9f7x"])
def test_a_tag_that_names_no_ref_fails_the_render(tag):
    result = _render(f"global.image.tag={tag}")
    assert result.returncode != 0
    assert "trustData.seed.sourceRef is required" in result.stderr


def test_a_tag_that_names_no_ref_is_fine_when_nothing_seeds():
    """The ref is only needed by the hook: a trust with seeding off renders on any tag."""
    assert _render("global.image.tag=latest", "trustData.seed.enabled=false").returncode == 0
