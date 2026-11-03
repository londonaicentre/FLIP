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

"""The Snowflake trust CI (epic #1589): the tofu wrapper, run for real with tofu stubbed, and the
three workflows read as text.

What these pin is the containment, not the YAML layout: a pull request plans as the read-only role
with refresh off; only develop, in the sf-stag environment, reaches the apply identity; no workflow
names a stored Snowflake credential; and the state key is the prefix the AWS state roles allow.

Usage:
    python3 .github/tests/workflows/test_snowflake_trust_ci.py
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"
SCRIPT = REPO / "trust" / "deploy" / "terraform" / "scripts" / "sf-ci-tofu.sh"
ROOTS = {"platform": "snowflake-platform", "workload": "snowflake"}

STUB = """#!/usr/bin/env bash
{
  printf 'ARGS %s\\n' "$*"
  env | grep -E '^TF_VAR_' | grep -v state_passphrase | sort
} >> "$STUB_LOG"
"""

BASE_ENV = {
    "SF_TRUST_CODE": "SFTEST",
    "SF_WAREHOUSE": "FLIP_SFTEST_WH",
    "SF_TFSTATE_BUCKET": "example-state-bucket",
    "AWS_REGION": "eu-west-2",
    "SNOWFLAKE_ORGANIZATION_NAME": "EXAMPLEORG",
    "SNOWFLAKE_ACCOUNT_NAME": "EXAMPLEACCOUNT",
    "SF_PLATFORM_STATE_PASSPHRASE": "probe-platform-passphrase",  # pragma: allowlist secret
    "SF_WORKLOAD_STATE_PASSPHRASE": "probe-workload-passphrase",  # pragma: allowlist secret
    "SF_DEPLOY_USER_PUBLIC_KEY": "MIIBIjANprobe",
}


def code_of(path: Path) -> str:
    """The file without its comment lines, so prose about a pattern never satisfies a check."""
    return "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("#"))


class Wrapper(unittest.TestCase):
    """sf-ci-tofu.sh against a copy of the two roots, tofu replaced by a recorder."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for directory in ROOTS.values():
            target = self.tmp / directory
            target.mkdir()
            shutil.copy(SCRIPT.parents[1] / directory / "encryption.tf.example", target)
        stub = self.tmp / "tofu"
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        self.log = self.tmp / "log"

    def run_script(self, root: str, mode: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        env = {"PATH": os.environ["PATH"], "TOFU": str(self.tmp / "tofu"), "SF_TF_DIR": str(self.tmp)}
        env["STUB_LOG"] = str(self.log)
        env.update(BASE_ENV)
        env.update(overrides)
        env = {k: v for k, v in env.items() if v != "UNSET"}
        return subprocess.run(["bash", str(SCRIPT), root, mode], env=env, capture_output=True, text=True)

    def calls(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def test_a_pull_request_plan_is_read_only_and_unrefreshed(self) -> None:
        for root, role_var in (("platform", "TF_VAR_platform_role"), ("workload", "TF_VAR_terraform_role")):
            with self.subTest(root=root):
                self.log.unlink(missing_ok=True)
                result = self.run_script(root, "plan")
                assert result.returncode == 0, result.stderr
                calls = self.calls()
                assert f"{role_var}=FLIP_PLATFORM_CI_READ" in calls
                plans = [line for line in calls.splitlines() if line.startswith("ARGS plan")]
                assert plans == ["ARGS plan -input=false -no-color -refresh=false -lock=false -out=tfplan"], plans
                assert "ARGS apply" not in calls

    def test_apply_runs_as_the_write_role_of_each_root(self) -> None:
        for root, expected in (
            ("platform", "TF_VAR_platform_role=FLIP_PLATFORM_CI"),
            ("workload", "TF_VAR_terraform_role=FLIP_SFTEST_DEPLOY"),
        ):
            with self.subTest(root=root):
                self.log.unlink(missing_ok=True)
                result = self.run_script(root, "apply")
                assert result.returncode == 0, result.stderr
                calls = self.calls()
                assert re.search(rf"^{expected}$", calls, re.M), calls
                assert "ARGS apply -input=false -no-color tfplan" in calls
                assert "-refresh=false" not in calls

    def test_drift_is_a_refreshed_plan_that_applies_nothing(self) -> None:
        result = self.run_script("workload", "drift")
        assert result.returncode == 0, result.stderr
        calls = self.calls()
        assert "ARGS plan -input=false -no-color -lock=false -detailed-exitcode" in calls
        assert "ARGS apply" not in calls
        assert "TF_VAR_terraform_role=FLIP_SFTEST_DEPLOY" in calls

    def test_the_platform_root_grants_the_deploy_role_to_the_ci_apply_user(self) -> None:
        self.run_script("platform", "apply")
        assert "TF_VAR_ci_apply_user_name=FLIP_CI_APPLY_USER" in self.calls()
        assert "TF_VAR_authenticator=WORKLOAD_IDENTITY" in self.calls()  # pragma: allowlist secret

    def test_state_lives_under_the_prefix_the_state_roles_allow(self) -> None:
        self.run_script("workload", "plan")
        init = next(line for line in self.calls().splitlines() if line.startswith("ARGS init"))
        assert "key=snowflake-trust/SFTEST/workload.tfstate" in init
        assert "workspace_key_prefix=snowflake-trust/SFTEST/workspaces" in init
        assert "use_lockfile=true" in init
        assert "encrypt=true" in init
        assert (self.tmp / "snowflake" / "backend_override.tf").read_text().count('backend "s3"') == 1
        assert (self.tmp / "snowflake" / "encryption.tf").exists()

    def test_a_fresh_oidc_token_is_minted_before_each_command_that_connects(self) -> None:
        """GitHub OIDC tokens live five minutes; plan + apply of one root can outlast one."""
        server = self.tmp / "oidc"
        server.write_text('{"value": "fresh-oidc-token"}')  # pragma: allowlist secret
        curl = self.tmp / "curl"
        curl.write_text(f'#!/usr/bin/env bash\necho mint >> "$STUB_LOG"\ncat {server}\n')
        curl.chmod(0o755)
        stub = self.tmp / "tofu"
        stub.write_text(STUB.replace("{\n", '{\n  printf "TOKEN %s\\n" "${SNOWFLAKE_TOKEN:-none}"\n', 1))
        result = self.run_script(
            "workload",
            "apply",
            PATH=f"{self.tmp}:{os.environ['PATH']}",
            ACTIONS_ID_TOKEN_REQUEST_URL="https://example.invalid/token?x=1",
            ACTIONS_ID_TOKEN_REQUEST_TOKEN="request-token",  # pragma: allowlist secret
        )
        assert result.returncode == 0, result.stderr
        lines = self.calls().splitlines()
        plan, apply = (
            lines.index("ARGS plan -input=false -no-color -out=tfplan"),
            next(i for i, line in enumerate(lines) if line.startswith("ARGS apply")),
        )
        assert lines[plan - 1] == "TOKEN fresh-oidc-token"  # pragma: allowlist secret
        assert "mint" in lines[plan + 1 : apply], "no fresh token between plan and apply"
        assert "::add-mask::fresh-oidc-token" in result.stdout  # pragma: allowlist secret

    def test_no_passphrase_or_secret_reaches_argv_or_output(self) -> None:
        result = self.run_script("platform", "apply")
        seen = self.calls() + result.stdout + result.stderr
        for secret in ("probe-platform-passphrase", "probe-workload-passphrase"):  # pragma: allowlist secret
            assert secret not in seen

    def test_missing_inputs_and_bad_arguments_are_refused_before_tofu_runs(self) -> None:
        cases = (
            (("workload", "plan"), {"SF_WORKLOAD_STATE_PASSPHRASE": "UNSET"}, 65),
            (("platform", "plan"), {"SF_DEPLOY_USER_PUBLIC_KEY": "UNSET"}, 65),
            (("workload", "plan"), {"SF_TRUST_CODE": "sftest; rm"}, 65),
            (("workload", "destroy"), {}, 64),
            (("hub", "plan"), {}, 64),
        )
        for (root, mode), overrides, code in cases:
            with self.subTest(root=root, mode=mode, overrides=overrides):
                self.log.unlink(missing_ok=True)
                result = self.run_script(root, mode, **overrides)
                assert result.returncode == code, (result.returncode, result.stderr)
                assert self.calls() == ""


class Workflows(unittest.TestCase):
    def test_pull_requests_plan_in_the_plan_environment_with_the_read_role(self) -> None:
        text = code_of(WORKFLOWS / "snowflake_trust_plan.yml")
        assert "environment: sf-stag-plan" in text
        assert "pull_request:" in text
        assert "push:" not in text
        assert "vars.SF_STATE_PLAN_ROLE_ARN" in text
        assert "APPLY_ROLE" not in text
        assert re.search(r'sf-ci-tofu\.sh "\$\{\{ matrix\.root \}\}" plan$', text, re.M)

    def test_only_develop_reaches_the_apply_environment(self) -> None:
        text = code_of(WORKFLOWS / "snowflake_trust_apply.yml")
        assert "environment: sf-stag" in text
        assert "sf-stag-plan" not in text
        assert re.search(r"push:\n    branches: \[develop\]\n", text)
        assert "pull_request" not in text
        assert "github.ref == 'refs/heads/develop'" in text
        assert "vars.SF_STATE_APPLY_ROLE_ARN" in text

    def test_drift_holds_no_credential_and_dispatches_the_apply_file(self) -> None:
        text = code_of(WORKFLOWS / "snowflake_trust_drift.yml")
        for forbidden in ("environment:", "id-token", "role-to-assume", "secrets.", "snowflake-actions"):
            assert forbidden not in text, forbidden
        assert "gh workflow run snowflake_trust_apply.yml --ref develop -f mode=drift" in text

    def test_the_state_role_workflow_files_match_the_aws_module_defaults(self) -> None:
        variables = (REPO / "deploy/providers/AWS/modules/terraform_ci_bootstrap/variables.tf").read_text()
        for kind in ("plan", "apply", "drift"):
            block = variables.split(f'variable "snowflake_{kind}_workflow_file"', 1)
            if len(block) == 1:
                self.skipTest("terraform_ci_bootstrap has no Snowflake state roles on this branch yet (#1745)")
            found = re.search(r'default\s*=\s*"([^"]+)"', block[1])
            assert found, kind
            assert (WORKFLOWS / found[1]).exists(), found[1]

    def test_every_snowflake_workflow_is_contained(self) -> None:
        for name in ("snowflake_trust_plan.yml", "snowflake_trust_apply.yml", "snowflake_trust_drift.yml"):
            with self.subTest(workflow=name):
                text = code_of(WORKFLOWS / name)
                assert "vars.SF_CI_ENABLED == 'true'" in text, "off until the account side exists"
                assert "github.repository == 'londonaicentre/FLIP'" in text
                for stored in ("PRIVATE_KEY", "SNOWFLAKE_PASSWORD", "SNOWFLAKE_TOKEN:", "pull_request_target"):
                    assert stored not in text, stored
                for use in re.findall(r"uses: (\S+)", text):
                    assert re.search(r"@[0-9a-f]{40}$", use), f"{use} is not pinned to a commit"


if __name__ == "__main__":
    unittest.main(verbosity=2)
