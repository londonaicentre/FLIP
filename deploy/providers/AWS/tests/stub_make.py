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
"""Run a target of this tree's Makefile for real, with its external commands stubbed on PATH.

For deploy targets that drive the AWS CLI (and GHCR through curl): the stubs replace them, so a
test needs no credentials or network and reads back what the target called or produced.
"""

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

AWS_DIR = Path(__file__).resolve().parents[1]

# The smallest env file the Makefile's parse-time guards accept, as in test_iam_permissions_boundary.py.
STUB_ENV = {
    "AWS_PROFILE": "probe",
    "PROD_AWS_PROFILE": "probe",
    "STAG_AWS_PROFILE": "probe",
    "LZA_VPC_NAME": "probe-vpc",
    "MANAGE_DNS": "false",
    "FLARE_KIT_DATE": "00000000",
    "FLOWER_KIT_DATE": "00000000",
    "FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME": "probe-uploads",
    "FLIP_FL_RESULTS_BUCKET_NAME": "probe-results",
    "FLIP_APP_BUNDLES_BUCKET_NAME": "probe-bundles",
    "AICENTRE_BUCKET_NAME": "probe-aicentre",
    "INTERNAL_SERVICE_KEY": "probe",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY_HASH": "probe",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY_HEADER": "X-Probe",  # pragma: allowlist secret
}


@dataclass
class StubRun:
    """What a stubbed make run did."""

    returncode: int
    calls: list[str]
    output: str


def run_make_with_stubs(
    tmp_path: Path,
    target: str,
    stubs: Mapping[str, str],
    env: Mapping[str, str] = (),
    **variables: str,
) -> StubRun:
    """Run ``make -s <target> HUB=stag`` against a stub env file with ``stubs`` first on PATH.

    Args:
        tmp_path (Path): Scratch directory for the stubs, the env file and the call log.
        target (str): The Makefile target to run.
        stubs (Mapping[str, str]): Executable name -> script body. Each can append to ``$CALLS``.
        env (Mapping[str, str]): Extra process environment for the stubs (fixture paths and the like).
        **variables (str): Command-line ``NAME=value`` overrides; ``HUB`` and ``MAIN_ENV_FILE`` default.

    Returns:
        StubRun: The exit code, every line the stubs logged to ``$CALLS``, and stdout plus stderr.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in stubs.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    calls = tmp_path / "calls"
    calls.touch()
    env_file = tmp_path / ".env.probe-stag"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in STUB_ENV.items()))

    process_env = {k: v for k, v in os.environ.items() if not k.startswith("TF_VAR_")}
    process_env.update(PATH=f"{bin_dir}{os.pathsep}{process_env['PATH']}", CALLS=str(calls), **dict(env))
    args = {"HUB": "stag", "MAIN_ENV_FILE": str(env_file), **variables}
    result = subprocess.run(
        ["make", "-s", target, *(f"{k}={v}" for k, v in args.items())],
        cwd=AWS_DIR,
        env=process_env,
        text=True,
        capture_output=True,
    )
    return StubRun(result.returncode, calls.read_text().splitlines(), result.stdout + result.stderr)
