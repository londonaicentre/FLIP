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
"""The public demo's publish (FLIP#2012): its own workflow, so its own role, after a successful apply.

The demo-publish role trusts publish_demo.yml and the UI-publish role publish_ui.yml. A job's OIDC token
names the workflow file, not the job, so the two files are what keep each role away from the other's bucket.

Usage:
    python3 .github/tests/workflows/test_publish_demo.py
"""

from __future__ import annotations

import os
import re
import subprocess
import unittest

from test_publish_ui import MODULE_VARIABLES, WORKFLOWS, code, job, permissions


class PublishDemo(unittest.TestCase):
    def test_it_only_runs_when_called(self) -> None:
        on = re.search(r"^on:\n(?P<body>(?:  [^\n]*\n)*)", code("publish_demo.yml"), re.MULTILINE)
        assert on, "publish_demo.yml has no on: block"
        assert re.findall(r"^  (\w+):", on["body"], re.MULTILINE) == ["workflow_call"]

    def test_the_build_holds_no_credentials(self) -> None:
        build = job("publish_demo.yml", "build")
        assert permissions(build) == {"contents": "read"}
        assert "id-token" not in build
        assert "secrets." not in build
        assert "aws-actions/" not in build
        assert "persist-credentials: false" in build
        assert "npm ci && npm run build:demo" in build
        assert re.search(r"name: flip-ui-demo\n\s+path: flip-ui/dist-demo/\n", build)

    def test_only_the_publish_job_assumes_the_demo_role(self) -> None:
        publish = job("publish_demo.yml", "publish")
        assert permissions(publish) == {"contents": "read", "id-token": "write"}
        assert "needs: build" in publish
        assert "role-to-assume: ${{ vars.TF_DEMO_PUBLISH_ROLE_ARN }}" in publish
        assert 'deploy/providers/AWS/scripts/publish-ui.sh dist-demo "${FLIP_DEMO_BUCKET_NAME}"' in publish
        assert '"${DEMO_SITE_URL}/js/window.js"' in publish
        others = [path.name for path in WORKFLOWS.glob("*.yml") if path.name != "publish_demo.yml"]
        assert not [name for name in others if "TF_DEMO_PUBLISH_ROLE_ARN" in code(name)]

    def test_an_environment_without_the_role_skips_rather_than_fails(self) -> None:
        build = job("publish_demo.yml", "build")
        steps = re.findall(r"^      - name: (.+)$", build, re.MULTILINE)
        assert steps[0] == "Check for the demo-publish role"
        guard = r"^      - name: .+\n        if: steps\.role\.outputs\.set == 'true'$"
        guarded = re.findall(guard, build, re.MULTILINE)
        assert len(guarded) == len(steps) - 1, "every build step after the role check must be skipped without it"
        assert "if: needs.build.outputs.configured == 'true'" in job("publish_demo.yml", "publish")

    def test_the_role_needs_its_bucket_and_an_https_host(self) -> None:
        """The readback finds the demo at DEMO_SITE_URL, so a half-configured environment fails the build."""
        step = re.search(
            r"^      - name: Check for the demo-publish role\n(?:        [^\n]*\n)*?        run: \|\n"
            r"(?P<run>(?:          [^\n]*\n)+)",
            job("publish_demo.yml", "build"),
            re.MULTILINE,
        )
        assert step, "no 'Check for the demo-publish role' step"
        script = "\n".join(line[10:] for line in step["run"].splitlines())
        role = {"ROLE_ARN": "arn:aws:iam::000000000000:role/x", "FLIP_DEMO_BUCKET_NAME": "demo-bucket"}
        for env, ok, says in [
            ({}, True, "TF_DEMO_PUBLISH_ROLE_ARN"),
            ({**role, "DEMO_SITE_URL": "https://demo.example.org"}, True, ""),
            ({**role, "DEMO_SITE_URL": ""}, False, "DEMO_SITE_URL"),
            ({**role, "FLIP_DEMO_BUCKET_NAME": "", "DEMO_SITE_URL": "https://demo.example.org"}, False, "FLIP_DEMO"),
            ({**role, "DEMO_SITE_URL": "https://demo.example.org/"}, False, "no path"),
            ({**role, "DEMO_SITE_URL": "http://demo.example.org"}, False, "no path"),
        ]:
            with self.subTest(env=env):
                full = {
                    "PATH": os.environ["PATH"],
                    "ENVIRONMENT": "aws-stag",
                    "GITHUB_OUTPUT": os.devnull,
                    "ROLE_ARN": "",
                    "FLIP_DEMO_BUCKET_NAME": "",
                    "DEMO_SITE_URL": "",
                    **env,
                }
                run = subprocess.run(["bash", "-c", script], env=full, capture_output=True, text=True)
                assert (run.returncode == 0) is ok, run.stdout + run.stderr
                assert says in run.stdout

    def test_no_secret_is_read_anywhere(self) -> None:
        assert "secrets." not in code("publish_demo.yml")

    def test_the_module_trusts_this_file(self) -> None:
        default = re.search(
            r'variable "demo_publish_workflow_file" \{[^}]*default\s*=\s*"([^"]+)"', MODULE_VARIABLES.read_text()
        )
        assert default, "module has no demo_publish_workflow_file default"
        assert default.group(1) == "publish_demo.yml"


class ApplyPublishesTheDemo(unittest.TestCase):
    def test_the_demo_follows_a_successful_apply_into_its_environment(self) -> None:
        """The same gate as the UI's ui job, and the apply's environment."""
        demo, ui = job("terraform_apply.yml", "demo"), job("terraform_apply.yml", "ui")
        assert "uses: ./.github/workflows/publish_demo.yml" in demo
        for line in ("needs: apply", "if: always() && needs.apply.result == 'success'"):
            assert line in demo, line
            assert line in ui, line
        assert permissions(demo) == {"contents": "read", "id-token": "write"}
        environment = re.search(r"^      environment: (.+)$", ui, re.MULTILINE)
        assert environment, "ui passes no environment"
        assert f"environment: {environment.group(1)}" in demo


if __name__ == "__main__":
    unittest.main()
