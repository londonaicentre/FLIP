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

"""Static guard on the LZA-mode permissions-boundary default (FLIP#1199).

The ``AICentre-FLIPTerraformBoundary`` policy is owned by the ``ci/`` root, which
fences the GitHub OIDC apply role and is applied only in the accounts whose
applies run through that pipeline. The LZA workload accounts are applied by hand
and never receive ``ci/``, so the policy does not exist there: with the default
name in force every role update fails with ``NoSuchEntity`` (the 2026-09-14
web-edge swap hit exactly this). The AWS Makefile therefore exports
``TF_VAR_iam_permissions_boundary_name`` as ``""`` on the LZA modes only, and only
when the environment has not already set it. These probes run ``make`` for real,
with the env-file include pointed at nothing so they are independent of any
local ``.env.lza-*`` file, and read back what Terraform would see.
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
    "LZA_AWS_PROFILE": "probe",
    "LZA_STAG_AWS_PROFILE": "probe",
    "LZA_VPC_NAME": "probe-vpc",
    "FLARE_KIT_DATE": "00000000",
    "FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME": "probe-uploads",
    "FLIP_FL_RESULTS_BUCKET_NAME": "probe-results",
    "FLIP_APP_BUNDLES_BUCKET_NAME": "probe-bundles",
    "AICENTRE_BUCKET_NAME": "probe-aicentre",
    "INTERNAL_SERVICE_KEY": "probe",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY_HASH": "probe",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY_HEADER": "X-Probe",  # pragma: allowlist secret
}


def _probe(tmp_path: Path, prod: str, extra_env: dict[str, str] | None = None) -> str:
    """Return ``<value>|<exported>`` for the boundary variable as terraform would see it.

    ``${V-UNSET}`` prints UNSET only when the variable is absent from the environment (an
    empty export prints ""); ``${V+exported}`` prints "exported" whenever it is present.
    """
    env_file = tmp_path / f".env.probe-{prod}"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in {**STUB_ENV, **(extra_env or {})}.items()))
    env = {k: v for k, v in os.environ.items() if not k.startswith("TF_VAR_")}
    values = probe_make(
        AWS_DIR,
        ["$${TF_VAR_iam_permissions_boundary_name-UNSET}", "$${TF_VAR_iam_permissions_boundary_name+exported}"],
        {"PROD": prod, "MAIN_ENV_FILE": str(env_file)},
        env=env,
    )
    return "|".join(values)


@pytest.mark.parametrize("prod", ["lza", "lza-stag"])
def test_lza_modes_export_an_empty_boundary_name(tmp_path: Path, prod: str) -> None:
    assert _probe(tmp_path, prod) == "|exported"


@pytest.mark.parametrize("prod", ["stag", "true"])
def test_legacy_modes_leave_the_boundary_to_the_terraform_default(tmp_path: Path, prod: str) -> None:
    assert _probe(tmp_path, prod) == "UNSET|"


def test_an_env_file_boundary_name_still_wins_on_lza(tmp_path: Path) -> None:
    # The operator override path is the env file (it is what `include` exports).
    probe = _probe(tmp_path, "lza", {"TF_VAR_iam_permissions_boundary_name": "Custom-Boundary"})
    assert probe == "Custom-Boundary|exported"
