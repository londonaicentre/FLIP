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

"""Guards over the CI runner preparation (scripts/ci-prepare.sh) and the deploy workflow."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
from conftest import SENTINELS

SNOWFLAKE_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SNOWFLAKE_DIR / "scripts" / "ci-prepare.sh"
WORKFLOW = SNOWFLAKE_DIR.parents[2] / ".github" / "workflows" / "snowflake_trust_deploy.yml"

KIT = "\n".join(
    [
        "TRUST_CODE=SFTEST",
        "CENTRAL_HUB_API_URL=https://hub.example.org/api",
        f"TRUST_API_KEY={SENTINELS['TRUST_API_KEY']}",
        f"TRUST_INTERNAL_SERVICE_KEY={SENTINELS['TRUST_INTERNAL_SERVICE_KEY']}",
        f"AES_KEY_BASE64={SENTINELS['AES_KEY_BASE64']}",
        "DOCKER_TAG=stag",
        "XNAT_TAG=stag",
    ]
)


def prepare(tmp_path: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "trust").mkdir(exist_ok=True)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "REPO_ROOT": str(tmp_path),
        "SNOWFLAKE_HOME": str(tmp_path / "sf"),
        "SF_TRUST_CODE": "SFTEST",
        "SF_KIT_FILE": KIT,
        "SNOWFLAKE_ORGANIZATION_NAME": "EXAMPLEORG",
        "SNOWFLAKE_ACCOUNT_NAME": "EXAMPLEACCT",
        "SF_WAREHOUSE": "FLIP_SFTEST_WH",
    }
    env.update(overrides)
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def test_the_kit_is_written_private_and_nothing_is_printed(tmp_path: Path) -> None:
    result = prepare(tmp_path)
    assert result.returncode == 0, result.stderr
    kit = tmp_path / "trust" / ".env.SFTEST.stag"
    assert stat.S_IMODE(kit.stat().st_mode) == 0o600
    assert kit.read_text().strip() == KIT
    for secret in SENTINELS.values():
        assert secret not in result.stdout + result.stderr


def test_the_connection_is_workload_identity_with_no_credential(tmp_path: Path) -> None:
    prepare(tmp_path)
    config = (tmp_path / "sf" / "config.toml").read_text()
    assert stat.S_IMODE((tmp_path / "sf" / "config.toml").stat().st_mode) == 0o600
    assert 'authenticator = "WORKLOAD_IDENTITY"' in config
    assert 'user = "FLIP_CI_APPLY_USER"' in config
    assert 'role = "FLIP_SFTEST_DEPLOY"' in config
    for forbidden in ("password", "private_key", "token"):
        assert forbidden not in config.lower()


def test_a_tag_input_pins_the_release_images(tmp_path: Path) -> None:
    assert prepare(tmp_path, DOCKER_TAG_INPUT="v1.4.0").returncode == 0
    kit = (tmp_path / "trust" / ".env.SFTEST.stag").read_text()
    assert re.search(r"^DOCKER_TAG=v1\.4\.0$", kit, re.M)
    assert re.search(r"^DOCKER_FL_TAG=v1\.4\.0$", kit, re.M)
    assert re.search(r"^XNAT_TAG=stag$", kit, re.M), "per-image opt-outs stay as the kit says"


@pytest.mark.parametrize(
    "overrides",
    [
        {"DOCKER_TAG_INPUT": "latest"},
        {"DOCKER_TAG_INPUT": "v1.4.0;rm -rf /"},
        {"SF_TRUST_CODE": "sftest"},
        {"SF_KIT_FILE": KIT.replace("TRUST_CODE=SFTEST", "TRUST_CODE=GSTT")},
        {"SF_KIT_FILE": KIT.replace(f"AES_KEY_BASE64={SENTINELS['AES_KEY_BASE64']}", "AES_KEY_BASE64=<aes>")},
        {"SNOWFLAKE_ACCOUNT_NAME": "acct.evil.example"},
        {"SF_WAREHOUSE": ""},
    ],
)
def test_bad_inputs_are_refused_and_leave_no_kit_behind(tmp_path: Path, overrides: dict[str, str]) -> None:
    result = prepare(tmp_path, **overrides)
    assert result.returncode == 65, (result.returncode, result.stderr)
    assert not (tmp_path / "trust" / ".env.SFTEST.stag").exists()


def workflow() -> str:
    return "\n".join(line for line in WORKFLOW.read_text().splitlines() if not line.lstrip().startswith("#"))


def test_the_deploy_runs_on_develop_in_the_apply_environment_only() -> None:
    text = workflow()
    assert "environment: sf-stag\n" in text
    assert "sf-stag-plan" not in text
    assert "pull_request" not in text
    assert "github.ref == 'refs/heads/develop'" in text
    assert "vars.SF_CI_ENABLED == 'true'" in text


def test_the_fl_gate_runs_unless_quiesced_and_a_skip_is_announced() -> None:
    text = workflow()
    assert "if: ${{ !(github.event_name == 'workflow_dispatch' && inputs.fl_quiesced) }}" in text
    assert "ci_deploy.py fl-gate" in text
    assert "::warning title=FL gate skipped::" in text


def test_ci_pushes_only_the_kit_secrets() -> None:
    assert "--no-fl-kit --no-governance" in workflow()


def test_the_notify_job_holds_no_snowflake_access() -> None:
    notify = workflow().split("\n  notify:\n", 1)[1]
    for forbidden in ("environment:", "id-token", "snowflake-actions", "secrets."):
        assert forbidden not in notify, forbidden


def test_the_kit_never_leaves_the_runner() -> None:
    text = workflow()
    assert 'rm -f "trust/.env.$SF_TRUST_CODE.stag"' in text
    upload = text.split("Upload the report", 1)[1].split("\n  notify:", 1)[0]
    assert "sf-report/" in upload
    assert ".env" not in upload


def test_every_action_is_pinned_to_a_commit() -> None:
    for use in re.findall(r"uses: (\S+)", workflow()):
        assert re.search(r"@[0-9a-f]{40}$", use), use
