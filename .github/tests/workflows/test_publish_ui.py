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

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"
MODULE_VARIABLES = REPO / "deploy" / "providers" / "AWS" / "modules" / "terraform_ci_bootstrap" / "variables.tf"


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
        assert "deploy/providers/AWS/scripts/publish-ui.sh dist" in publish
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
        """needs: apply, so a held or failed apply publishes no UI."""
        ui = job("terraform_apply.yml", "ui")
        assert "needs: apply" in ui
        assert "uses: ./.github/workflows/publish_ui.yml" in ui
        assert not re.search(r"^    if:", ui, re.MULTILINE), "the ui job must not override needs: apply"
        assert permissions(ui) == {"contents": "read", "id-token": "write"}

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
