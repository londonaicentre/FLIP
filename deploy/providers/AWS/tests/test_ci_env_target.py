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

"""The CI compose script's table must agree with ``deploy/env_mode.mk`` and the Makefile.

``scripts/compose-ci-env.sh`` writes the env file that ``deploy/providers/AWS/Makefile``
``include``s, and it names that file from the HUB it is given. Two failures are
possible and neither is loud:

* it writes a file the Makefile does not derive — the include is wildcard-guarded,
  so a file that does not exist is skipped *silently* and Terraform sees an empty
  input set rather than a missing one;
* it exports an ``AWS_PROFILE`` the Makefile's account guard rejects, which at
  least fails loudly, but only in the deploy run.

Both are checked here against a real ``make`` probe of the same HUB and network mode,
so the two tables cannot drift apart without a red test. The shell harness
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

# Every (HUB, LZA_MANAGED_NETWORK) pair the script has to answer for. A literal list:
# a new selector value must be added here, which is the reminder to teach CI about it.
ESTATES = [("stag", "false"), ("prod", "false"), ("stag", "true"), ("prod", "true")]

# The smallest env file the Makefile's parse-time guards accept (kit date, tenant
# bucket names, hub service key). Taken from test_iam_permissions_boundary.py on
# purpose: one stub, one maintenance point. LZA_MANAGED_NETWORK is added per case.
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

# Selector inputs the caller's shell may carry; every run here states its own.
_SELECTORS = {"GITHUB_ACTIONS", "EXPECTED_ENV_CLASS", "HUB", "PROD", "LZA_MANAGED_NETWORK"}


def _script_env(**extra: str) -> dict[str, str]:
    """The environment to run the compose script in, independent of where this runs.

    ``GITHUB_ACTIONS`` is dropped because the script treats it as "this is CI" and then
    *requires* ``EXPECTED_ENV_CLASS``; a pytest run on a runner inherits it, so leaving
    it in would make these probes assert the CI requirement instead of the table. The
    requirement has its own test below, which sets it deliberately.
    """
    env = {k: v for k, v in os.environ.items() if k not in _SELECTORS}
    env.update(extra)
    return env


def _make_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _SELECTORS and not k.startswith("TF_VAR_")}


def _env_file(tmp_path: Path, hub: str, lza: str) -> Path:
    env_file = tmp_path / f".env.probe-{hub}-{lza}"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in {**STUB_ENV, "LZA_MANAGED_NETWORK": lza}.items()))
    return env_file


def _script_target(hub: str, lza: str) -> dict[str, str]:
    """Run the compose script's ``--print-env`` and parse its KEY=value output."""
    result = subprocess.run(
        [str(SCRIPT), "--print-env"],
        env=_script_env(HUB=hub, LZA_MANAGED_NETWORK=lza),
        text=True,
        capture_output=True,
        check=True,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


@pytest.mark.parametrize(("hub", "lza"), ESTATES)
def test_compose_target_matches_the_makefile(hub: str, lza: str, tmp_path: Path) -> None:
    """ENV, env-file name, class and profile: the script's table against a real probe.

    The probe hands the Makefile the profile the script derives, so it parses only if
    that profile passes the account guard — and EXPECTED_AWS_PROFILE is then asserted
    equal to it, so neither side can drift alone.
    """
    script = _script_target(hub, lza)
    env, env_file_name, env_class, expected_profile = probe_make(
        AWS_DIR,
        ["$(ENV)", "$(ENV_FILE_NAME)", "$(ENV_CLASS)", "$(EXPECTED_AWS_PROFILE)"],
        {"HUB": hub, "MAIN_ENV_FILE": str(_env_file(tmp_path, hub, lza)), "AWS_PROFILE": script["AWS_PROFILE"]},
        env=_make_env(),
    )
    assert script["ENV"] == env
    assert script["ENV_FILE_NAME"] == env_file_name
    # The class the script asserts against is env_mode.mk's own derivation: that
    # mapping is the whole content of the class guard the workflows rely on.
    assert script["ENV_CLASS"] == env_class
    assert script["AWS_PROFILE"] == expected_profile


@pytest.mark.parametrize(("hub", "expected"), [("stag", "prod"), ("prod", "stag")])
def test_a_class_mismatch_is_refused(hub: str, expected: str) -> None:
    """``TF_HUB`` alone chooses the estate, so the caller's class is asserted.

    TF_HUB=stag on aws-prod composes a prod-sized plan whose RDS is refreshed as
    ``deletion_protection = false``, ``skip_final_snapshot = true`` — and the apply
    job then applies it unattended. The compose step is the last place that can stop
    it cheaply.
    """
    result = subprocess.run(
        [str(SCRIPT), "--print-env"],
        env=_script_env(HUB=hub, LZA_MANAGED_NETWORK="true", EXPECTED_ENV_CLASS=expected),
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "but this run expects" in result.stderr
    assert f"HUB={hub}" in result.stderr


@pytest.mark.parametrize(("hub", "lza"), ESTATES)
def test_a_matching_class_composes_the_same_target(hub: str, lza: str) -> None:
    plain = _script_target(hub, lza)
    declared = dict(
        line.split("=", 1)
        for line in subprocess.run(
            [str(SCRIPT), "--print-env"],
            env=_script_env(HUB=hub, LZA_MANAGED_NETWORK=lza, EXPECTED_ENV_CLASS=plain["ENV_CLASS"]),
            text=True,
            capture_output=True,
            check=True,
        ).stdout.splitlines()
        if "=" in line
    )
    assert declared == plain


def test_ci_runs_must_state_their_class() -> None:
    """In CI the assertion is mandatory, or a workflow could silently drop it.

    The guard is only worth having if it cannot be forgotten: every workflow that
    composes this file passes EXPECTED_ENV_CLASS from its ref, and a new one that
    does not gets a red compose step naming the line to add.
    """
    missing = subprocess.run(
        [str(SCRIPT), "--print-env"],
        env=_script_env(HUB="stag", LZA_MANAGED_NETWORK="true", GITHUB_ACTIONS="true"),
        text=True,
        capture_output=True,
    )
    assert missing.returncode != 0
    assert "EXPECTED_ENV_CLASS is not set" in missing.stderr

    stated = subprocess.run(
        [str(SCRIPT), "--print-env"],
        env=_script_env(HUB="stag", LZA_MANAGED_NETWORK="true", GITHUB_ACTIONS="true", EXPECTED_ENV_CLASS="stag"),
        text=True,
        capture_output=True,
        check=True,
    )
    assert "ENV_CLASS=stag" in stated.stdout


@pytest.mark.parametrize(("hub", "lza"), [("prod", "false"), ("stag", "true"), ("prod", "true")])
def test_the_wrong_profile_is_still_refused(hub: str, lza: str, tmp_path: Path) -> None:
    """...and the guard is not vacuous: self-contained staging's profile is not accepted elsewhere."""
    with pytest.raises(subprocess.CalledProcessError):
        probe_make(
            AWS_DIR,
            ["$(ENV)"],
            {"HUB": hub, "MAIN_ENV_FILE": str(_env_file(tmp_path, hub, lza)), "AWS_PROFILE": "stag"},
            env=_make_env(),
        )


@pytest.mark.parametrize(
    ("selectors", "message"),
    [
        ({"LZA_MANAGED_NETWORK": "true"}, "HUB is not set"),
        ({"HUB": "true", "LZA_MANAGED_NETWORK": "true"}, "HUB must be stag or prod"),
        ({"HUB": "stag"}, "LZA_MANAGED_NETWORK must be true"),
        ({"HUB": "stag", "LZA_MANAGED_NETWORK": "yes"}, "LZA_MANAGED_NETWORK must be true"),
    ],
)
def test_an_unset_or_unknown_selector_is_refused(selectors: dict[str, str], message: str) -> None:
    """Neither input defaults: each default would compose some estate nobody chose."""
    result = subprocess.run([str(SCRIPT), "--print-env"], env=_script_env(**selectors), text=True, capture_output=True)
    assert result.returncode != 0
    assert message in result.stderr


def test_the_makefile_refuses_an_env_file_without_the_network_mode(tmp_path: Path) -> None:
    """The Makefile is the other half: it reads LZA_MANAGED_NETWORK from the file the script writes."""
    env_file = tmp_path / ".env.probe"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in STUB_ENV.items()))
    result = subprocess.run(
        ["make", "-s", "-n", f"MAIN_ENV_FILE={env_file}", "HUB=stag", "AWS_PROFILE=stag", "print-tf-env"],
        cwd=AWS_DIR,
        env=_make_env(),
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "LZA_MANAGED_NETWORK" in result.stderr


# A complete value set for the one test that really composes. The shell harness
# (scripts/tests/test_compose_ci_env.sh) is the place that mutates a value per
# case; this is only "enough keys to produce a file".
COMPOSE_ENV = {
    "AWS_REGION": "eu-west-2",
    "FLIP_TFSTATE_BUCKET_NAME": "flip-terraform-state-lza-stag",
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
    "FL_ADMIN_DIRECTORY": "/app",
    "FL_SERVER_PORT": "8002",
    "INTERNAL_SERVICE_KEY_HEADER": "X-Probe",  # pragma: allowlist secret
    "TRUST_API_KEY_HEADER": "X-Probe",  # pragma: allowlist secret
    "SES_VERIFIED_EMAIL": "probe@example.com",
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


@pytest.mark.parametrize("hub", ["stag", "prod"])
def test_the_default_output_path_is_where_the_makefile_includes_it(hub: str) -> None:
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
    script = _script_target(hub, "true")
    target = Path(script["OUT_FILE"])
    if target.exists():
        # A checkout that carries an operator's env file: stand down rather than
        # write over it. CI has none.
        pytest.skip(f"{target} exists — refusing to overwrite an operator's env file")
    try:
        target.write_text("".join(f"{k}={v}\n" for k, v in {**STUB_ENV, "LZA_MANAGED_NETWORK": "true"}.items()))
        (included,) = probe_make(
            AWS_DIR,
            ["$(abspath $(MAIN_ENV_FILE))"],
            {"HUB": hub, "AWS_PROFILE": script["AWS_PROFILE"]},
            env=_make_env(),
        )
        assert included == str(target)
    finally:
        target.unlink(missing_ok=True)


def test_a_real_compose_lands_on_that_default_path() -> None:
    """...and the write happens there, not only in the printed plan.

    If a file already exists there it belongs to an operator, and the test stands
    down rather than overwriting it.
    """
    target = Path(_script_target("stag", "true")["OUT_FILE"])
    if target.exists():
        pytest.skip(f"{target} exists — refusing to overwrite an operator's env file")
    try:
        subprocess.run(
            [str(SCRIPT)],
            env=_script_env(**COMPOSE_ENV, HUB="stag", LZA_MANAGED_NETWORK="true"),
            text=True,
            capture_output=True,
            check=True,
        )
        assert target.is_file()
        text = target.read_text()
        assert "AWS_PROFILE=lza-stag" in text
        assert "LZA_MANAGED_NETWORK=true" in text
    finally:
        target.unlink(missing_ok=True)
