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
