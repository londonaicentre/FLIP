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
"""The flip-ui publish after an apply (FLIP#1186) keeps credentials away from the build.

``npm ci`` runs third-party install scripts, so the job that builds the UI must hold no AWS token
and no secret, and only the job that syncs the bucket may assume the UI-publish role. The UI must
follow a successful apply, never run beside it. Read as text, like the other workflow tests.

Usage:
    python3 .github/tests/workflows/test_publish_ui.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_workflows import step_block  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"
MODULE_VARIABLES = REPO / "deploy" / "aws" / "modules" / "terraform_ci_bootstrap" / "variables.tf"


def code(workflow: str) -> str:
    """The workflow's text without comment lines, so prose cannot satisfy or trip an assertion."""
    text = (WORKFLOWS / workflow).read_text()
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def job(workflow: str, name: str) -> str:
    """The body of the top-level job ``name`` in ``workflow``."""
    block = re.search(rf"^  {name}:\n(?P<body>(?:    [^\n]*\n|\s*\n)*)", code(workflow) + "\n", re.MULTILINE)
    assert block, f"{workflow}: no job '{name}'"
    return block["body"]


def permissions(body: str) -> dict[str, str]:
    """The job-level ``permissions:`` map of a job body."""
    block = re.search(r"^    permissions:\n(?P<map>(?:      [^\n]*\n)*)", body, re.MULTILINE)
    assert block, "job declares no permissions"
    return dict(re.findall(r"^      ([\w-]+): (\S+)", block["map"], re.MULTILINE))


class PublishUi(unittest.TestCase):
    def test_it_only_runs_when_called(self) -> None:
        """No push or dispatch of its own: the apply decides when the UI moves."""
        on = re.search(r"^on:\n(?P<body>(?:  [^\n]*\n)*)", code("publish_ui.yml"), re.MULTILINE)
        assert on, "publish_ui.yml has no on: block"
        assert re.findall(r"^  (\w+):", on["body"], re.MULTILINE) == ["workflow_call"]

    def test_the_build_holds_no_credentials(self) -> None:
        build = job("publish_ui.yml", "build")
        assert permissions(build) == {"contents": "read"}
        assert "id-token" not in build
        assert "secrets." not in build
        assert "aws-actions/" not in build
        assert "compose-ci-env" not in build
        assert "persist-credentials: false" in build

    def test_the_build_is_the_deploy_build(self) -> None:
        """The same npm scripts as make deploy-ui; postbuild:deploy writes window.js."""
        build = job("publish_ui.yml", "build")
        assert "npm ci" in build
        assert "npm run build:deploy" in build
        assert "AUTH_BACKEND: cognito" in build

    def test_only_the_publish_job_assumes_the_ui_role(self) -> None:
        publish = job("publish_ui.yml", "publish")
        assert permissions(publish) == {"contents": "read", "id-token": "write"}
        assert "needs: build" in publish
        assert "role-to-assume: ${{ vars.TF_UI_PUBLISH_ROLE_ARN }}" in publish
        assert "npm" not in publish
        assert "deploy/aws/scripts/publish-ui.sh dist" in publish
        assert '"${FLIP_UI_BUCKET_NAME}"' in publish
        others = [path.name for path in WORKFLOWS.glob("*.yml") if path.name != "publish_ui.yml"]
        assert not [name for name in others if "TF_UI_PUBLISH_ROLE_ARN" in code(name)]

    def test_an_environment_without_the_role_skips_rather_than_fails(self) -> None:
        """Merging this before the platform creates the role must not turn every apply run red."""
        build = job("publish_ui.yml", "build")
        steps = re.findall(r"^      - name: (.+)$", build, re.MULTILINE)
        assert steps[0] == "Check for the UI-publish role"
        guard = r"^      - name: .+\n        if: steps\.role\.outputs\.set == 'true'$"
        guarded = re.findall(guard, build, re.MULTILINE)
        assert len(guarded) == len(steps) - 1, "every build step after the role check must be skipped without it"
        assert "configured: ${{ steps.role.outputs.set }}" in build
        assert "if: needs.build.outputs.configured == 'true'" in job("publish_ui.yml", "publish")

    def test_the_api_url_must_end_in_api(self) -> None:
        """The check after the publish finds the site by dropping /api, so a URL without it fails the build."""
        step = re.search(
            r"^      - name: Check the window\.js inputs\n(?:        [^\n]*\n)*?        run: \|\n"
            r"(?P<run>(?:          [^\n]*\n)+)",
            job("publish_ui.yml", "build"),
            re.MULTILINE,
        )
        assert step, "no 'Check the window.js inputs' step"
        script = "\n".join(line[10:] for line in step["run"].splitlines())
        env = {
            "PATH": os.environ["PATH"],
            "ENVIRONMENT": "aws-stag",
            "AWS_COGNITO_USER_POOL_ID": "eu-west-2_x",
            "AWS_COGNITO_APP_CLIENT_ID": "x",
            "AWS_REGION": "eu-west-2",
        }
        for url, ok in [
            ("https://stag.example.org/api", True),
            ("https://stag.example.org/api/", False),
            ("https://stag.example.org", False),
        ]:
            with self.subTest(url=url):
                run = subprocess.run(
                    ["bash", "-c", script], env={**env, "CENTRAL_HUB_API_URL": url}, capture_output=True, text=True
                )
                assert (run.returncode == 0) is ok, run.stdout + run.stderr
                if not ok:
                    assert "CENTRAL_HUB_API_URL must end in /api" in run.stdout

    def test_no_secret_is_read_anywhere(self) -> None:
        assert "secrets." not in code("publish_ui.yml")

    def test_the_module_trusts_this_file(self) -> None:
        default = re.search(
            r'variable "ui_publish_workflow_file" \{[^}]*default\s*=\s*"([^"]+)"', MODULE_VARIABLES.read_text()
        )
        assert default, "module has no ui_publish_workflow_file default"
        assert (WORKFLOWS / default.group(1)).is_file()


class ApplyPublishesTheUi(unittest.TestCase):
    def test_the_ui_follows_a_successful_apply(self) -> None:
        """A held or failed apply publishes no UI, and a production apply does publish one.

        start-staging is skipped on main, so the implicit success() of a bare ``needs: apply``
        skipped every production publish (v0.15.0); ``always()`` lifts it, the result check holds the gate.
        """
        ui = job("terraform_apply.yml", "ui")
        assert "needs: [apply, ui-cognito-ids]" in ui
        assert "uses: ./.github/workflows/publish_ui.yml" in ui
        gate = "if: always() && needs.apply.result == 'success' && needs.ui-cognito-ids.result == 'success'"
        assert f"    {gate}\n" in ui, ui
        assert permissions(ui) == {"contents": "read", "id-token": "write"}

    @staticmethod
    def _bash(body: str, step: str, **env: str) -> subprocess.CompletedProcess:
        """Run the script of the step named ``step`` in a job body."""
        script = textwrap.dedent(step_block(body, step).split("run: |", 1)[1])
        return subprocess.run(
            ["bash", "-c", script], env={"PATH": os.environ["PATH"], **env}, capture_output=True, text=True
        )

    def test_the_apply_job_compares_the_cognito_ids_and_cannot_fail(self) -> None:
        """The staging restore and stop key on the apply job's result, so the step only reports a verdict."""
        apply = job("terraform_apply.yml", "apply")
        step = "Compare the UI's Cognito ids with Terraform"
        assert "2>/dev/null" not in step_block(apply, step), "Terraform's error must reach the log"
        assert "ui_cognito_ids: ${{ steps.cognito.outputs.verdict }}" in apply

        new = {"TF_POOL": "eu-west-2_new", "TF_CLIENT": "newclient"}
        ui = {"UI_POOL_ID": "eu-west-2_new", "UI_CLIENT_ID": "newclient"}
        blank = dict.fromkeys((*new, *ui), "")
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp, "terraform")
            stub.write_text(
                "#!/usr/bin/env bash\n"
                'case "$3" in CognitoUserPoolId) v="${TF_POOL}" ;; *) v="${TF_CLIENT}" ;; esac\n'
                '[[ -n "$v" ]] || { echo "Error: Output $3 not found" >&2; exit 1; }\n'
                'printf %s "$v"\n'
            )
            stub.chmod(0o755)
            output = Path(tmp, "output")
            path = f"{tmp}:{os.environ['PATH']}"
            for env, verdict, says in [
                ({**new, **ui}, "match", ""),
                ({**new, **ui, "UI_POOL_ID": "eu-west-2_old"}, "mismatch", "AWS_COGNITO_USER_POOL_ID differs"),
                ({**new, **ui, "UI_CLIENT_ID": "oldclient"}, "mismatch", "AWS_COGNITO_APP_CLIENT_ID differs"),
                ({**new, "UI_POOL_ID": "eu-west-2_new"}, "mismatch", "AWS_COGNITO_APP_CLIENT_ID differs"),
                # A first apply: nobody could have set them yet, and publish_ui.yml refuses to build without them.
                (new, "unset", "are not set on this environment"),
                # terraform output fails: its own verdict, and Terraform's error is not swallowed.
                (ui, "unreadable", "could not be read"),
                ({}, "unreadable", "could not be read"),
            ]:
                with self.subTest(env=env):
                    output.write_text("")
                    run = self._bash(apply, step, **{"PATH": path, "GITHUB_OUTPUT": str(output), **blank, **env})
                    assert run.returncode == 0, run.stdout + run.stderr
                    assert output.read_text() == f"verdict={verdict}\n"
                    assert says in run.stdout, run.stdout
                    assert ("not found" in run.stderr) is (verdict == "unreadable"), run.stderr
                    # Names only: the ids themselves stay out of the log and the outputs.
                    assert not [value for value in env.values() if value in run.stdout], run.stdout

    def test_a_cognito_verdict_other_than_match_or_unset_holds_the_ui(self) -> None:
        """A mismatch was a warning on a green run, and the UI published could sign nobody in (FLIP#2047)."""
        check = job("terraform_apply.yml", "ui-cognito-ids")
        assert "VERDICT: ${{ needs.apply.outputs.ui_cognito_ids }}" in check
        for verdict, ok, says in [
            ("match", True, "match Terraform"),
            ("unset", True, "::warning title=UI Cognito ids not set"),
            ("mismatch", False, "::error title=UI Cognito ids differ"),
            ("unreadable", False, "::error title=Cognito ids not read"),
            # The step never reported: unchecked ids are not a pass.
            ("", False, "::error title=Cognito ids not read"),
        ]:
            with self.subTest(verdict=verdict):
                run = self._bash(check, "Act on the apply job's verdict", VERDICT=verdict)
                assert (run.returncode == 0) is ok, run.stdout + run.stderr
                assert says in run.stdout, run.stdout
        mismatch = next(line for line in check.splitlines() if "UI Cognito ids differ" in line)
        assert "AWS_COGNITO_USER_POOL_ID" in mismatch
        assert "AWS_COGNITO_APP_CLIENT_ID" in mismatch
        # A re-run applies its own commit, so only the newest run cannot roll back; a fresh dispatch
        # on main carries no release_tag and would pin sha images over a release.
        assert "newest apply run" in mismatch
        assert "Do not dispatch a new apply" in mismatch

    def test_the_cognito_check_job_asks_for_no_environment(self) -> None:
        """aws-prod has required reviewers: an environment here is one more approval on every production apply."""
        check = job("terraform_apply.yml", "ui-cognito-ids")
        assert "needs: apply\n" in check
        assert "if: always() && needs.apply.result == 'success'\n" in check
        assert "permissions: {}" in check
        assert "environment:" not in check
        assert "vars." not in check
        assert "secrets." not in check
        for other in ("demo", "restore-staging-services", "staging-end-to-end", "hold-then-stop-staging"):
            assert "ui-cognito-ids" not in job("terraform_apply.yml", other), other

    def test_the_ui_publishes_into_the_applys_environment(self) -> None:
        apply_env = re.search(r"^    environment: (.+)$", job("terraform_apply.yml", "apply"), re.MULTILINE)
        assert apply_env, "apply job declares no environment"
        assert f"environment: {apply_env.group(1)}" in job("terraform_apply.yml", "ui")
        assert "release_tag: ${{ inputs.release_tag }}" in job("terraform_apply.yml", "ui")

    def test_every_push_applies(self) -> None:
        """A path filter left flip-api and flip-ui merges undeployed (FLIP#1186)."""
        push = re.search(r"^  push:\n(?P<body>(?:    [^\n]*\n)*)", code("terraform_apply.yml"), re.MULTILINE)
        assert push, "terraform_apply.yml has no push trigger"
        assert "paths" not in push["body"]
        assert "branches: [develop, main]" in push["body"]


if __name__ == "__main__":
    unittest.main()
