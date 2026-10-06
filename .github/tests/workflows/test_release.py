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
"""``release.yml`` dispatches every image workflow at the release tag it creates (FLIP#1204),
waits for them, then dispatches the production apply with that tag (FLIP#1283).

Usage:
    python3 .github/tests/workflows/test_release.py
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_workflows import IMAGE_WORKFLOWS, WORKFLOWS, code_text, step_block  # noqa: E402

RELEASE_WORKFLOW = WORKFLOWS / "release.yml"


class ReleaseDispatchesTheBuilds(unittest.TestCase):
    """release.yml must dispatch every image workflow at the tag it creates.

    The tag is pushed with the workflow's own GITHUB_TOKEN, and GitHub starts no workflow for an
    event created that way (only workflow_dispatch / repository_dispatch are exempt) — so the
    `push.tags` trigger the image workflows carry never fires on a real release. A hand-pushed
    tag (a release candidate) does fire it, which is exactly why a manual proof would not catch
    a workflow missing from the roster below.
    """

    def test_release_dispatches_exactly_the_publishing_image_workflows(self) -> None:
        step = step_block(code_text(RELEASE_WORKFLOW), "Build every image at the release tag")
        roster = re.compile(r"^\s+((?:docker_build_|fl-docker-build-)[A-Za-z0-9_-]+\.yml)", re.MULTILINE)
        dispatched = set(roster.findall(step))
        expected = {wf.name for wf in IMAGE_WORKFLOWS}
        assert dispatched == expected, f"missing={sorted(expected - dispatched)} extra={sorted(dispatched - expected)}"
        assert 'gh workflow run "$wf" --ref "$TAG"' in step

    def test_release_job_may_dispatch_workflows(self) -> None:
        text = code_text(RELEASE_WORKFLOW)
        assert re.search(r"^\s+actions: write", text, re.MULTILINE), "release.yml needs actions: write to dispatch"

    def test_a_rerun_after_a_partial_failure_still_dispatches_and_releases(self) -> None:
        """A run that pushed the tag and then failed leaves the tag behind; keyed on the tag, every re-run
        would skip the builds and the release and still go green."""
        text = code_text(RELEASE_WORKFLOW)
        assert 'gh release view "${{ steps.version.outputs.tag }}"' in text
        for step in ("Build every image at the release tag", "Prepare release notes", "Create GitHub Release"):
            with self.subTest(step=step):
                assert "if: steps.release_check.outputs.exists == 'false'" in step_block(text, step), step
        assert "if: steps.tag_check.outputs.exists == 'false'" in step_block(text, "Create tag")

    def test_one_failed_dispatch_does_not_stop_the_rest(self) -> None:
        step = step_block(code_text(RELEASE_WORKFLOW), "Build every image at the release tag")
        assert 'if gh workflow run "$wf" --ref "$TAG"; then' in step
        assert 'failed+=("$wf")' in step
        loop_end = step.index("done")
        assert step.index("exit 1") > loop_end, "the failure exit must come after every dispatch was tried"

    def test_release_notes_start_from_the_previous_stable_release(self) -> None:
        """Release-candidate tags sort above their release (sort -V), so they must not be the notes' start."""
        step = step_block(code_text(RELEASE_WORKFLOW), "Prepare release notes")
        prev = next(line for line in step.splitlines() if "PREV_TAG=$(" in line)
        assert "grep -E '^v[0-9]+\\.[0-9]+\\.[0-9]+$'" in prev, prev
        assert prev.index("grep -E") < prev.index("sort -V"), prev


class ReleaseWaitsThenDispatchesTheApply(unittest.TestCase):
    """The release re-pins production, and only after its own builds are green (FLIP#1283).

    The hub bakes FLIP_RELEASE at build time, so only the ``:v<X.Y.Z>`` images name the release.
    They exist only once the twelve dispatches above conclude, and the resolver fails closed when
    told a release tag it cannot find — so waiting is what makes the dispatch safe, and the ref it
    is dispatched at is what makes the apply's OIDC work. Both are read as text here.
    """

    def setUp(self) -> None:
        self.text = code_text(RELEASE_WORKFLOW)

    def test_the_dispatch_step_records_what_the_wait_step_must_wait_for(self) -> None:
        """One roster, not two: the wait reads the dispatch step's output."""
        build = step_block(self.text, "Build every image at the release tag")
        assert 'dispatched+=("$wf")' in build
        assert 'echo "workflows=${dispatched[*]}" >> "$GITHUB_OUTPUT"' in build
        assert "started_at=" in build, "the wait needs a cut-off to ignore a previous attempt's runs"
        wait = step_block(self.text, "Wait for the release builds to go green")
        assert "steps.dispatch.outputs.workflows" in wait
        assert "steps.dispatch.outputs.started_at" in wait

    def test_the_wait_is_bounded_and_a_timeout_fails_the_release(self) -> None:
        """An unbounded wait hangs the job; a timeout that passed would dispatch an apply whose
        release images do not exist, and the resolver would then stop production's apply."""
        wait = step_block(self.text, "Wait for the release builds to go green")
        assert "BUILD_WAIT_SECONDS" in wait
        assert "deadline=" in wait
        assert "$SECONDS -ge $deadline" in wait
        timeout = next(line for line in wait.splitlines() if "timed out" in line)
        assert "::error::" in timeout, timeout
        assert wait.count("exit 1") >= 2, "both the timeout and a red build must fail the job"

    def test_a_red_build_is_a_red_release(self) -> None:
        wait = step_block(self.text, "Wait for the release builds to go green")
        assert 'conclusion" == "success"' in wait, "only a concluded success may clear a workflow"
        assert "red+=(" in wait

    def test_the_apply_is_dispatched_on_main_never_at_the_tag(self) -> None:
        """The apply role's trust policy pins job_workflow_ref to refs/heads/{develop,main}; a
        dispatch at refs/tags/v<X.Y.Z> cannot assume it. This is the likely regression."""
        step = step_block(self.text, "Dispatch the production Terraform apply at the release tag")
        assert 'gh workflow run terraform_apply.yml --ref refs/heads/main -f release_tag="$TAG"' in step
        assert "--ref refs/tags" not in step
        assert '--ref "$TAG"' not in step

    def test_the_apply_is_dispatched_only_after_the_wait(self) -> None:
        order = [
            self.text.index("- name: Build every image at the release tag"),
            self.text.index("- name: Wait for the release builds to go green"),
            self.text.index("- name: Dispatch the production Terraform apply at the release tag"),
        ]
        assert order == sorted(order), "dispatch → wait → apply is the whole point of the ordering"

    def test_the_new_steps_are_skipped_when_the_release_already_exists(self) -> None:
        """Same re-run contract as the builds: keyed on the release, not the tag."""
        for step in (
            "Wait for the release builds to go green",
            "Dispatch the production Terraform apply at the release tag",
        ):
            with self.subTest(step=step):
                assert "if: steps.release_check.outputs.exists == 'false'" in step_block(self.text, step)


class ReleaseKeepsMinimalPermissions(unittest.TestCase):
    """Dispatching a production apply needs `actions: write` and nothing more."""

    def test_the_workflow_default_is_read_only(self) -> None:
        text = code_text(RELEASE_WORKFLOW)
        top = text[text.index("permissions:") :].split("jobs:")[0]
        assert top.strip() == "permissions:\n  contents: read", top

    def test_the_job_grants_only_contents_write_and_actions_write(self) -> None:
        text = code_text(RELEASE_WORKFLOW)
        job = text[text.index("jobs:") :]
        block = re.search(r"^        permissions:[^\n]*\n(?P<body>(?:            [^\n]*\n)+)", job, re.MULTILINE)
        assert block, "the release job must declare its own permissions"
        granted = {
            line.split(":")[0].strip(): line.split(":")[1].split("#")[0].strip()
            for line in block["body"].splitlines()
            if line.strip()
        }
        assert granted == {"contents": "write", "actions": "write"}, granted


class TerraformApplyTakesTheReleaseTag(unittest.TestCase):
    """terraform_apply.yml must accept, validate and forward `release_tag` (FLIP#1283)."""

    def setUp(self) -> None:
        self.text = code_text(WORKFLOWS / "terraform_apply.yml")

    def test_the_dispatch_input_exists_and_defaults_to_empty(self) -> None:
        """Empty is the ordinary value: a push-triggered apply must take the sha path unchanged."""
        inputs = self.text[self.text.index("workflow_dispatch:") :].split("permissions:")[0]
        assert "release_tag:" in inputs
        block = inputs[inputs.index("release_tag:") :]
        assert "type: string" in block
        assert 'default: ""' in block

    def test_the_fl_quiesce_input_and_gate_are_untouched(self) -> None:
        assert "fl_quiesced:" in self.text
        gate = step_block(self.text, "Check the plan for FL impact")
        assert "scripts/check-fl-plan-impact.sh tfplan.json" in gate
        assert "if: ${{ !(github.event_name == 'workflow_dispatch' && inputs.fl_quiesced) }}" in gate

    def test_the_release_tag_is_validated(self) -> None:
        step = step_block(self.text, "Validate the release tag")
        assert r"^v[0-9]+\.[0-9]+\.[0-9]+$" in step, step
        assert "if: ${{ inputs.release_tag != '' }}" in step
        # A release is a main/prod event; which estate is applied is chosen by the ref.
        assert 'GITHUB_REF_NAME}" == "main"' in step

    def test_the_release_tag_is_forwarded_to_the_resolver(self) -> None:
        step = step_block(self.text, "Resolve the image tags to pin")
        assert "RELEASE_TAG: ${{ inputs.release_tag }}" in step
        assert "deploy/providers/AWS/scripts/resolve-image-tags.sh" in step

    def test_the_sha_path_is_still_what_a_push_resolves(self) -> None:
        """GIT_SHA stays the resolver's input; release_tag is additive, not a replacement."""
        step = step_block(self.text, "Resolve the image tags to pin")
        assert "GIT_SHA: ${{ github.sha }}" in step

    def test_the_apply_still_serialises_on_one_concurrency_group(self) -> None:
        """The push apply and the release dispatch of the same commit share `tf-apply-main`, and
        cancel-in-progress: false makes the later (release) one queue rather than race."""
        block = self.text[self.text.index("concurrency:") :].split("jobs:")[0]
        assert "group: tf-apply-${{ github.ref_name }}" in block
        assert "cancel-in-progress: false" in block

    def test_the_apply_job_permissions_stay_minimal(self) -> None:
        job = self.text[self.text.index("  apply:") :]
        block = re.search(r"^    permissions:\n(?P<body>(?:      [^\n]*\n)+)", job, re.MULTILINE)
        assert block, "the apply job must declare its own permissions"
        granted = {
            line.split(":")[0].strip(): line.split(":")[1].split("#")[0].strip()
            for line in block["body"].splitlines()
            if line.strip()
        }
        assert granted == {"contents": "read", "id-token": "write", "packages": "read"}, granted


if __name__ == "__main__":
    unittest.main()
