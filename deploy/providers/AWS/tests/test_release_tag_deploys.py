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
"""Static guards on the release-tag deploy paths (FLIP#1204).

``deploy-centralhub`` pins ECS to an immutable image tag. Until FLIP#1204 the only such tag
was ``sha-<short7>``; a release now also publishes ``:v<X.Y.Z>`` on every image, and the hub
deploy must accept it — while still refusing the floating ``:prod`` / ``:stag``, which would
defeat the revision audit trail the guard exists for. The guard's regex is the single line
that decides both, so it is asserted here by running the tag check the way the recipe does.

``upgrade-trust-ec2`` is the EC2 twin of ``make -C trust upgrade-trust``: it must not chain
through ``seed-trust-data`` (which ``rm -rf``s the OMOP/Orthanc dirs on the host) or
``down-trust-ec2`` — those belong to ``deploy-trust``, the first-install verb.
"""

import re
import subprocess
from pathlib import Path

import pytest

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
MAKEFILE = AWS_PROVIDER_DIR / "Makefile"


def _tag_guard_regex() -> str:
    """The grep -E pattern deploy-centralhub applies to TAG, un-escaped from the Makefile."""
    text = MAKEFILE.read_text()
    m = re.search(r"printf '%s' \"\$\$TAG\" \| grep -Eq '(?P<pattern>[^']+)'", text)
    assert m, "deploy-centralhub's TAG guard (printf … | grep -Eq '…') not found"
    return m.group("pattern").replace("$$", "$")


@pytest.mark.parametrize("tag", ["sha-badcff1", "v0.6.0", "v10.2.13"])
def test_hub_deploy_accepts_immutable_tags(tag: str) -> None:
    pattern = _tag_guard_regex()
    assert subprocess.run(["grep", "-Eq", pattern], input=tag, text=True).returncode == 0, f"{tag} refused by {pattern}"


@pytest.mark.parametrize("tag", ["prod", "stag", "latest", "0.6.0", "sha-badcff", "v0.6", "develop", "sha-BADCFF1"])
def test_hub_deploy_refuses_floating_and_malformed_tags(tag: str) -> None:
    pattern = _tag_guard_regex()
    accepted = subprocess.run(["grep", "-Eq", pattern], input=tag, text=True).returncode == 0
    assert not accepted, f"{tag} accepted by {pattern}"


def _recipe(target: str) -> str:
    """The recipe lines of one Makefile target, comments stripped."""
    text = MAKEFILE.read_text()
    m = re.search(rf"^{re.escape(target)}:[^\n]*\n((?:\t[^\n]*\n)+)", text, re.M)
    assert m, f"target {target} not found"
    return "\n".join(line for line in m.group(1).splitlines() if not line.strip().startswith("#"))


def test_upgrade_trust_ec2_is_the_data_safe_verb() -> None:
    text = MAKEFILE.read_text()
    header = re.search(r"^upgrade-trust-ec2:([^\n]*)\n", text, re.M)
    assert header, "upgrade-trust-ec2 target not found"
    prerequisites = header.group(1)
    for forbidden in ("seed-trust-data", "update-env", "stage-fl-kit"):
        assert forbidden not in prerequisites, f"upgrade-trust-ec2 must not depend on {forbidden}"
    recipe = _recipe("upgrade-trust-ec2")
    assert "upgrade-trust" in recipe, "upgrade-trust-ec2 must delegate to trust/Makefile's upgrade-trust"
    for forbidden in ("down-trust-ec2", "up-trust-ec2", "image prune"):
        assert forbidden not in recipe, f"upgrade-trust-ec2 must not run {forbidden}"


def test_deploy_trust_still_reseeds_so_the_two_verbs_stay_distinct() -> None:
    """deploy-trust is the first-install verb; if it ever stops re-seeding, the split above is moot."""
    text = MAKEFILE.read_text()
    header = re.search(r"^deploy-trust:([^\n]*)\n", text, re.M)
    assert header, "deploy-trust target not found"
    assert "seed-trust-data" in header.group(1)
