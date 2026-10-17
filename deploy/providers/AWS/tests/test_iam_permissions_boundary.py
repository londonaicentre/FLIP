#  Copyright 2026 London AI Centre
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

"""Static guard on the IAM permissions boundary (FLIP#1082, FLIP#1199, FLIP#1280).

Every IAM role this root owns carries ``var.iam_permissions_boundary_name``: the
platform's ``AICentre-WorkloadRoleBoundary`` (londonaicentre/lza#51), which the
accelerator deploys to every LZA workload account. An LZA SCP denies creating a role,
or attaching or writing a policy onto one, unless the role carries it — so a blank or
a different name fails every role change, from a laptop or from CI.

These probes run ``make`` for real, with the env-file include pointed at nothing
so they are independent of any local ``.env.*`` file, and read back what
Terraform would see.
"""

import os
from pathlib import Path

import pytest
from make_probe import probe_make

AWS_DIR = Path(__file__).resolve().parents[1]


# The smallest env file the Makefile's parse-time guards accept (kit date,
# profile pins, tenant bucket names, hub service key). Values are inert: the
# probe target makes no AWS call. A new guard fails these tests by name, which
# is the intended failure mode — add its key here.
STUB_ENV = {
    "AWS_PROFILE": "probe",
    "PROD_AWS_PROFILE": "probe",
    "STAG_AWS_PROFILE": "probe",
    "LZA_VPC_NAME": "probe-vpc",
    "MANAGE_DNS": "false",
    "FLARE_KIT_DATE": "00000000",
    "FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME": "probe-uploads",
    "FLIP_FL_RESULTS_BUCKET_NAME": "probe-results",
    "FLIP_APP_BUNDLES_BUCKET_NAME": "probe-bundles",
    "AICENTRE_BUCKET_NAME": "probe-aicentre",
    "INTERNAL_SERVICE_KEY": "probe",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY_HASH": "probe",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY_HEADER": "X-Probe",  # pragma: allowlist secret
}


def _probe(tmp_path: Path, hub: str, items: list[str], extra_env: dict[str, str] | None = None) -> list[str]:
    """Evaluate ``items`` in a recipe of the AWS Makefile, with a stub env file and no TF_VAR_* inherited."""
    env_file = tmp_path / f".env.probe-{hub}"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in {**STUB_ENV, **(extra_env or {})}.items()))
    env = {k: v for k, v in os.environ.items() if not k.startswith("TF_VAR_")}
    return probe_make(AWS_DIR, items, {"HUB": hub, "MAIN_ENV_FILE": str(env_file)}, env=env)


#: ``${V-UNSET}`` prints UNSET only when the variable is absent from the environment (an
#: empty export prints ""); ``${V+exported}`` prints "exported" whenever it is present.
BOUNDARY = ["$${TF_VAR_iam_permissions_boundary_name-UNSET}", "$${TF_VAR_iam_permissions_boundary_name+exported}"]


@pytest.mark.parametrize("hub", ["stag", "prod"])
def test_every_estate_carries_the_platform_boundary(tmp_path: Path, hub: str) -> None:
    """The platform's boundary, never ``""``: an empty export strips it from every role."""
    assert _probe(tmp_path, hub, BOUNDARY) == ["AICentre-WorkloadRoleBoundary", "exported"]


@pytest.mark.parametrize("hub", ["stag", "prod"])
def test_an_env_file_boundary_name_still_wins(tmp_path: Path, hub: str) -> None:
    # `?=` supplies a default, it does not override: an operator who sets the
    # variable in the env file (which `include` exports) gets that name.
    probe = _probe(tmp_path, hub, BOUNDARY, {"TF_VAR_iam_permissions_boundary_name": "Custom-Boundary"})
    assert probe == ["Custom-Boundary", "exported"]


@pytest.mark.parametrize("hub", ["stag", "prod"])
def test_terraform_sees_the_estate_hub_names(tmp_path: Path, hub: str) -> None:
    """The environment class is the one HUB names; the network-mode input is gone with the self-contained mode."""
    items = ["$${TF_VAR_environment}", "$${TF_VAR_lza_managed_network-UNSET}"]
    assert _probe(tmp_path, hub, items) == [hub, "UNSET"]
