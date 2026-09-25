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
declared by the ``ci/`` root, so the boundary resolves in any account the stack is
applied to — which is why ``ci/`` is applied there first.

The self-contained modes are the ones the pipeline applies today, so they carry
it. The LZA modes are detached, and this is the *current* state rather than a
preference: ``ci/`` has not been applied in either LZA account yet and those
estates are still changed by laptop applies, where an attach whose name resolves
to nothing fails every role update with ``NoSuchEntity`` (the failure the
Makefile default was added for). Re-attaching is a follow-up, ordered after
``make -C ci apply PROD=lza-stag`` / ``PROD=lza`` has run in both accounts: delete
the ``ifneq ($(IS_LZA),)`` block in the Makefile and merge
``test_the_lza_modes_detach_the_boundary_until_ci_lands_there`` into
``test_the_self_contained_modes_let_the_terraform_default_apply``.

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


@pytest.mark.parametrize("prod", ["stag", "true"])
def test_the_self_contained_modes_let_the_terraform_default_apply(tmp_path: Path, prod: str) -> None:
    """The modes the pipeline applies: no export at all, so the default stands.

    An exported empty string here would be the FLIP#1199 bug in reverse — those
    accounts *have* a boundary policy, so blanking it silently strips the boundary
    from every role the apply creates or updates.
    """
    assert _probe(tmp_path, prod) == "UNSET|"


@pytest.mark.parametrize("prod", ["lza-stag", "lza"])
def test_the_lza_modes_detach_the_boundary_until_ci_lands_there(tmp_path: Path, prod: str) -> None:
    """The LZA carve-out, still in place: ``ci/`` has not been applied there (#1199).

    ``AICentre-FLIPTerraformBoundary`` does not exist in either LZA account yet, and
    both estates are still changed by manual ``make plan/apply PROD=lza|lza-stag``
    runs — where an attach whose name resolves to nothing fails every role update
    with ``NoSuchEntity``. So the Makefile exports the variable as ``""`` for these
    two modes and an apply there keeps working.

    This test flips when the ordering has actually happened: once
    ``make -C ci apply PROD=lza-stag`` and ``PROD=lza`` have run in both accounts,
    delete the ``ifneq ($(IS_LZA),)`` block in the Makefile and fold this case into
    the self-contained one above.
    """
    assert _probe(tmp_path, prod) == "|exported"


@pytest.mark.parametrize("prod", ["stag", "true", "lza-stag", "lza"])
def test_an_env_file_boundary_name_still_wins(tmp_path: Path, prod: str) -> None:
    # `?=` supplies a default, it does not override: an operator who sets the
    # variable in the env file (which `include` exports) gets that name on every
    # mode, LZA included — the escape hatch for an account where ci/ has not been
    # applied is the same mechanism as re-attaching it by hand.
    probe = _probe(tmp_path, prod, {"TF_VAR_iam_permissions_boundary_name": "Custom-Boundary"})
    assert probe == "Custom-Boundary|exported"
