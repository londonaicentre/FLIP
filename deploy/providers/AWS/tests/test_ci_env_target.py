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

"""The CI compose script's mode table must agree with ``deploy/env_mode.mk``.

``scripts/compose-ci-env.sh`` writes the env file that ``deploy/providers/AWS/Makefile``
``include``s, and it names that file from the PROD token it is given. Two failures
are possible and neither is loud:

* it writes ``.env.stag`` where the Makefile derives ``.env.lza-stag`` — the
  include is wildcard-guarded, so a file that does not exist is skipped *silently*
  and Terraform sees an empty input set rather than a missing one;
* it exports an ``AWS_PROFILE`` the Makefile's account guard rejects, which at
  least fails loudly, but only in the deploy run.

Both are checked here against a real ``make`` probe of the same token, so the two
tables cannot drift apart without a red test. The shell harness
(``scripts/tests/test_compose_ci_env.sh``) covers the manifest and the key
semantics; this file covers the target the run is aimed at.
"""

import os
import subprocess
from pathlib import Path

import pytest
from make_probe import probe_make

AWS_DIR = Path(__file__).resolve().parents[1]
SCRIPT = AWS_DIR / "scripts" / "compose-ci-env.sh"

# Every token deploy/env_mode.mk accepts, and therefore every token the script has
# to answer for. Deliberately a literal list: a new PROD value must be added here,
# which is the reminder to teach the CI path about it too.
TOKENS = ["stag", "true", "lza-stag", "lza"]

# The smallest env file the Makefile's parse-time guards accept (kit date, profile
# pins, tenant bucket names, hub service key). Taken from
# test_iam_permissions_boundary.py on purpose: one stub, one maintenance point.
STUB_ENV = {
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


def _script_target(token: str) -> dict[str, str]:
    """Run the compose script's ``--print-env`` and parse its KEY=value output."""
    result = subprocess.run(
        [str(SCRIPT), "--print-env"],
        env={**os.environ, "PROD": token},
        text=True,
        capture_output=True,
        check=True,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def _env_mode(token: str, tmp_path: Path, extra: dict[str, str] | None = None) -> list[str]:
    """Ask the real Makefile what ENV / env-file / class it derives for ``token``.

    The probe has to hand the Makefile a profile it accepts — its guard runs at
    parse time, before any target — so it passes the one the script derives. That
    is not circular: which profile the script derives is asserted separately
    (``test_compose_profile_satisfies_the_makefile_guard``), and this test is
    about the env-file name.
    """
    env_file = tmp_path / f".env.probe-{token}"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in {**STUB_ENV, **(extra or {})}.items()))
    env = {k: v for k, v in os.environ.items() if not k.startswith("TF_VAR_")}
    return probe_make(
        AWS_DIR,
        ["$(ENV)", "$(ENV_FILE_NAME)", "$(ENV_CLASS)", "$(IS_LZA)"],
        {
            "PROD": token,
            "MAIN_ENV_FILE": str(env_file),
            "AWS_PROFILE": _script_target(token)["AWS_PROFILE"],
        },
        env=env,
    )


@pytest.mark.parametrize("token", TOKENS)
def test_compose_target_matches_env_mode(token: str, tmp_path: Path) -> None:
    script = _script_target(token)
    env, env_file_name, _env_class, is_lza = _env_mode(token, tmp_path)
    assert script["ENV"] == env
    assert script["ENV_FILE_NAME"] == env_file_name
    assert bool(script.get("ENV_FILE_NAME", "").startswith(".env.lza-")) == bool(is_lza)


@pytest.mark.parametrize("token", TOKENS)
def test_compose_profile_satisfies_the_makefile_guard(token: str, tmp_path: Path) -> None:
    """The AWS_PROFILE the script writes must be the one the Makefile demands.

    The Makefile refuses to parse unless ``AWS_PROFILE`` equals the profile knob
    for that ENV, which is why the script derives it rather than storing it. A
    mismatch here means every CI run composing this token dies at `make
    print-tf-env`, after the env file has already been written.
    """
    profile = _script_target(token)["AWS_PROFILE"]
    env_file = tmp_path / f".env.probe-{token}"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in STUB_ENV.items()))
    env = {k: v for k, v in os.environ.items() if not k.startswith("TF_VAR_")}
    probe_make(
        AWS_DIR,
        ["$(ENV)"],
        {"PROD": token, "MAIN_ENV_FILE": str(env_file), "AWS_PROFILE": profile},
        env=env,
    )  # check=True inside probe_make: the guard passed


@pytest.mark.parametrize("token", ["true", "lza-stag", "lza"])
def test_the_wrong_profile_is_still_refused(token: str, tmp_path: Path) -> None:
    """...and the guard is not vacuous: staging's profile is not accepted elsewhere."""
    env_file = tmp_path / f".env.probe-{token}"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in STUB_ENV.items()))
    env = {k: v for k, v in os.environ.items() if not k.startswith("TF_VAR_")}
    with pytest.raises(subprocess.CalledProcessError):
        probe_make(
            AWS_DIR,
            ["$(ENV)"],
            {"PROD": token, "MAIN_ENV_FILE": str(env_file), "AWS_PROFILE": "stag"},
            env=env,
        )


def test_an_unknown_token_is_refused_by_the_script() -> None:
    result = subprocess.run(
        [str(SCRIPT), "--print-env"],
        env={**os.environ, "PROD": "prod"},
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "PROD must be one of" in result.stderr


# A complete value set for the one test that really composes. The shell harness
# (scripts/tests/test_compose_ci_env.sh) is the place that mutates a value per
# case; this is only "enough keys to produce a file".
COMPOSE_ENV = {
    "AWS_REGION": "eu-west-2",
    "FLIP_TFSTATE_BUCKET_NAME": "flip-terraform-state-stag",
    "VPC_NAME": "flip-vpc",
    "AICENTRE_BUCKET_NAME": "probe-aicentre",
    "FLIP_APP_BUNDLES_BUCKET_NAME": "probe-bundles",
    "FLIP_FL_RESULTS_BUCKET_NAME": "probe-results",
    "FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME": "probe-uploads",
    "FLIP_UI_BUCKET_NAME": "probe-ui",
    "ADMIN_USER_PASSWORD": "probe",  # pragma: allowlist secret
    "AES_KEY_BASE64": "probe",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY": "probe",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY_HASH": "probe",  # pragma: allowlist secret
    "POSTGRES_DB": "flip",
    "POSTGRES_USER": "flip",
    "API_PORT": "8000",
    "DB_PORT": "5432",
    "FL_ADMIN_DIRECTORY": "/app",
    "FL_API_PORT": "8001",
    "FL_SERVER_PORT": "8002",
    "INTERNAL_SERVICE_KEY_HEADER": "X-Probe",  # pragma: allowlist secret
    "TRUST_API_KEY_HEADER": "X-Probe",  # pragma: allowlist secret
    "SES_VERIFIED_EMAIL": "probe@example.com",
    "UI_PORT": "80",
    "ALB_SUBDOMAIN": "alb",
    "NLB_SUBDOMAIN": "nlb",
    "DOCKER_REGISTRY": "example.com/",
    "DOCKER_TAG": "probe",
    "DOCKER_FL_TAG": "probe",
    "FL_BACKEND": "nvflare",
    "FLARE_KIT_DATE": "20260101",
    "FL_KIT_SLOT_NAMES": '["Trust_1"]',
    "DEPLOY_TRUST_EC2": "false",
    "K8S_TRUST_PUBLIC_IPS": "[]",
    "LOCAL_TRUST_PUBLIC_IPS": "[]",
    "ACCESS_LOGS_BUCKET_NAME": "probe-access-logs",
    "EFS_PROVISION_IMAGE": "probe/image:1",
    "LZA_VPC_NAME": "AWSAccelerator-eu-west-2-stag",
    "MANAGE_DNS": "false",
    "LZA_ELB_ACCESS_LOGS_BUCKET": "probe-elb-access-logs",
    "NETWORKING_INGRESS_CIDRS": '["10.12.0.0/24"]',
}


@pytest.mark.parametrize("token", TOKENS)
def test_the_default_output_path_is_where_the_makefile_includes_it(token: str) -> None:
    """The file the script writes by default has to be the file the Makefile reads.

    Writing it anywhere else is invisible: the include is wildcard-guarded, so a
    missing file is skipped and Terraform reads an empty input set — the run then
    fails later, in `make`, naming a missing key rather than a missing file. It cost
    a red PR-plan run to find that.

    The check puts a known-good file at the path the script *says* is its default
    and then asks the Makefile, with no MAIN_ENV_FILE override so that it derives
    its own, which file it includes. Both sides are therefore the real thing: no
    second literal path in this test to drift.
    """
    target = Path(_script_target(token)["OUT_FILE"])
    if target.exists():
        # A checkout that carries an operator's env file: stand down rather than
        # write over it. The remaining tokens still cover this, and CI has none.
        pytest.skip(f"{target} exists — refusing to overwrite an operator's env file")
    try:
        target.write_text("".join(f"{k}={v}\n" for k, v in STUB_ENV.items()))
        (included,) = probe_make(
            AWS_DIR,
            ["$(abspath $(MAIN_ENV_FILE))"],
            {"PROD": token, "AWS_PROFILE": _script_target(token)["AWS_PROFILE"]},
            env={k: v for k, v in os.environ.items() if not k.startswith("TF_VAR_")},
        )
        assert included == str(target)
    finally:
        target.unlink(missing_ok=True)


def test_a_real_compose_lands_on_that_default_path() -> None:
    """...and the write happens there, not only in the printed plan.

    Composed for the LZA staging token alone: no checkout carries its env file, so
    there is nothing to clobber. If one does exist it belongs to an operator and
    the test stands down rather than overwriting it.
    """
    target = Path(_script_target("lza-stag")["OUT_FILE"])
    if target.exists():
        pytest.skip(f"{target} exists — refusing to overwrite an operator's env file")
    try:
        subprocess.run(
            [str(SCRIPT)],
            env={**os.environ, **COMPOSE_ENV, "PROD": "lza-stag"},
            text=True,
            capture_output=True,
            check=True,
        )
        assert target.is_file()
        assert "AWS_PROFILE=lza-stag" in target.read_text()
    finally:
        target.unlink(missing_ok=True)
