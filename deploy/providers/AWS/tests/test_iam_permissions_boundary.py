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

"""Static guard on the IAM permissions boundary (FLIP#1082, FLIP#1199).

Every IAM role this root owns carries ``var.iam_permissions_boundary_name``,
whose ``variables.tf`` default is the ``AICentre-FLIPTerraformBoundary`` policy
declared by the ``ci/`` root, so the boundary must resolve in any account the
stack is applied to — which is why ``ci/`` is applied there first.

The LZA modes used to be the exception: those accounts were applied by hand and
never received ``ci/``, so the Makefile exported the variable as ``""`` on
``PROD=lza`` / ``PROD=lza-stag`` to keep role updates from failing with
``NoSuchEntity``. They are applied through the same pipeline now and that
carve-out is gone. These probes run ``make`` for real, with the env-file include
pointed at nothing so they are independent of any local ``.env.*`` file, and read
back what Terraform would see.
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


@pytest.mark.parametrize("prod", ["stag", "true", "lza-stag", "lza"])
def test_no_mode_detaches_the_boundary(tmp_path: Path, prod: str) -> None:
    """Every deployed mode lets the Terraform default apply — the LZA pair included.

    An exported empty string here is the FLIP#1199 bug in reverse: on an account
    whose ``ci/`` root *has* been applied, it silently strips the boundary from
    every role the apply creates or updates.
    """
    assert _probe(tmp_path, prod) == "UNSET|"


@pytest.mark.parametrize("prod", ["stag", "true", "lza-stag", "lza"])
def test_an_env_file_boundary_name_still_wins(tmp_path: Path, prod: str) -> None:
    # The escape hatch for an account where ci/ has not been applied: the operator
    # writes TF_VAR_iam_permissions_boundary_name= into the env file (it is what
    # `include` exports).
    probe = _probe(tmp_path, prod, {"TF_VAR_iam_permissions_boundary_name": "Custom-Boundary"})
    assert probe == "Custom-Boundary|exported"
