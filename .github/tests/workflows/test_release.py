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

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_workflows import IMAGE_WORKFLOWS, WORKFLOWS, code_text, step_block  # noqa: E402

RELEASE_WORKFLOW = WORKFLOWS / "release.yml"
# The wait is a file rather than an inline `run:` block so it can be executed
# against a stub `gh` below — it is the step that decides whether production is
# re-pinned to the release.
WAIT_SCRIPT = WORKFLOWS.parent / "scripts" / "wait-for-release-builds.sh"
CONFIRM_SCRIPT = WORKFLOWS.parent / "scripts" / "confirm-release-apply.sh"


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
        assert "wait-for-release-builds.sh" in wait

    def test_the_wait_is_bounded_and_a_timeout_fails_the_release(self) -> None:
        """An unbounded wait hangs the job; a timeout that passed would dispatch an apply whose
        release images do not exist, and the resolver would then stop production's apply."""
        wait = step_block(self.text, "Wait for the release builds to go green")
        assert "BUILD_WAIT_SECONDS" in wait
        script = WAIT_SCRIPT.read_text()
        assert "deadline=" in script
        assert '"${SECONDS}" -ge "${deadline}"' in script
        timeout = next(line for line in script.splitlines() if "timed out" in line)
        assert "::error::" in timeout, timeout
        assert script.count("exit 1") >= 2, "both the timeout and a red build must fail the job"

    def test_a_red_build_is_a_red_release(self) -> None:
        script = WAIT_SCRIPT.read_text()
        assert '${conclusion}" == "success"' in script, "only a concluded success may clear a workflow"
        assert "red+=(" in script

    def test_the_wait_identifies_the_dispatched_runs_not_the_branch_builds(self) -> None:
        """`--commit` alone matches main's branch builds of the same commit — including the five
        image workflows that run from `workflow_run` minutes after STARTED_AT. Only the runs this
        step dispatched present event=workflow_dispatch AND headBranch=<tag>."""
        script = WAIT_SCRIPT.read_text()
        assert "event,headBranch" in script
        assert r".event == \"workflow_dispatch\"" in script
        assert r".headBranch == \"${TAG}\"" in script
        assert r".createdAt >= \"${STARTED_AT}\"" in script

    def test_the_apply_is_dispatched_on_main_never_at_the_tag(self) -> None:
        """The apply role's trust policy pins job_workflow_ref to refs/heads/{develop,main}; a
        dispatch at refs/tags/v<X.Y.Z> cannot assume it. This is the likely regression."""
        step = step_block(self.text, "Dispatch the production Terraform apply at the release tag")
        assert 'gh workflow run terraform_apply.yml --ref refs/heads/main -f release_tag="$TAG"' in step
        assert "--ref refs/tags" not in step
        assert '--ref "$TAG"' not in step

    def test_a_failed_dispatch_is_loud_and_names_the_manual_command(self) -> None:
        step = step_block(self.text, "Dispatch the production Terraform apply at the release tag")
        assert "::error::could not dispatch terraform_apply.yml" in step
        assert "gh workflow run terraform_apply.yml --repo" in step, "the operator needs the exact command"

    def test_the_dispatch_refuses_when_main_moved_on(self) -> None:
        """The dispatch runs main AS IT IS, up to the whole build wait after the tag. A hotfix
        merged during the wait would get the release's images applied over its code."""
        step = step_block(self.text, "Dispatch the production Terraform apply at the release tag")
        assert 'gh api "repos/${{ github.repository }}/commits/main"' in step
        assert '"$MAIN_SHA" != "$HEAD_SHA"' in step

    def test_the_apply_is_dispatched_only_after_the_wait(self) -> None:
        order = [
            self.text.index("- name: Build every image at the release tag"),
            self.text.index("- name: Wait for the release builds to go green"),
            self.text.index("- name: Dispatch the production Terraform apply at the release tag"),
        ]
        assert order == sorted(order), "dispatch → wait → apply is the whole point of the ordering"

    def test_the_release_is_created_after_the_apply_is_dispatched(self) -> None:
        """Both are gated on the release not existing, so the Release must be the LAST thing to
        happen: created first, a failed dispatch leaves a re-run skipping the wait and the
        dispatch and going green with production still on the previous release."""
        assert self.text.index("- name: Dispatch the production Terraform apply at the release tag") < self.text.index(
            "- name: Create GitHub Release"
        )

    def test_the_new_steps_are_skipped_when_the_release_already_exists(self) -> None:
        """Same re-run contract as the builds: keyed on the release, not the tag."""
        for step in (
            "Wait for the release builds to go green",
            "Dispatch the production Terraform apply at the release tag",
        ):
            with self.subTest(step=step):
                assert "if: steps.release_check.outputs.exists == 'false'" in step_block(self.text, step)


class TheWaitScriptRuns(unittest.TestCase):
    """The wait decides whether production is re-pinned, so it is executed here rather than
    grepped: a substring check is satisfied by an `echo`, and an inverted condition passes it.

    `gh` is stubbed with a script that answers from a JSON fixture keyed by workflow name.
    """

    STUB = """#!/usr/bin/env bash
# Stub `gh run list`: echo the fixture for the requested --workflow, after applying the
# --jq filter with real jq, so the script's own selection logic is what is under test.
wf=""
jqf=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --workflow) wf="$2"; shift ;;
    --jq) jqf="$2"; shift ;;
  esac
  shift
done
if [[ -f "${FIXTURE_DIR}/fail" ]]; then
  echo "HTTP 502: Bad gateway" >&2
  exit 1
fi
fx="${FIXTURE_DIR}/${wf}.json"
[[ -f "$fx" ]] || fx="${FIXTURE_DIR}/default.json"
jq -c "$jqf" < "$fx"
"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        stub = self.bin / "gh"
        stub.write_text(self.STUB)
        stub.chmod(0o755)
        self.fixtures = self.tmp / "fx"
        self.fixtures.mkdir()

    @staticmethod
    def run_entry(name: str, **over) -> dict:
        run = {
            "databaseId": 1,
            "status": "completed",
            "conclusion": "success",
            "createdAt": "2026-01-01T12:00:00Z",
            "url": f"https://example.invalid/{name}",
            "event": "workflow_dispatch",
            "headBranch": "v1.2.3",
        }
        run.update(over)
        return run

    def write(self, workflow: str, runs: list[dict]) -> None:
        (self.fixtures / f"{workflow}.json").write_text(json.dumps(runs))

    def wait(self, workflows: str = "a.yml", **env) -> subprocess.CompletedProcess:
        environ = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FIXTURE_DIR": str(self.fixtures),
            "TAG": "v1.2.3",
            "HEAD_SHA": "a" * 40,
            "STARTED_AT": "2026-01-01T11:00:00Z",
            "WORKFLOWS": workflows,
            "BUILD_WAIT_SECONDS": "2",
            "BUILD_POLL_SECONDS": "1",
        }
        environ.update({k: str(v) for k, v in env.items()})
        return subprocess.run(["bash", str(WAIT_SCRIPT)], capture_output=True, text=True, env=environ, timeout=120)

    def test_a_green_dispatched_run_clears_the_wait(self) -> None:
        self.write("a.yml", [self.run_entry("a")])
        result = self.wait()
        assert result.returncode == 0, result.stderr + result.stdout
        assert "every dispatched build at v1.2.3 is green" in result.stdout

    def test_a_branch_build_of_the_same_commit_is_ignored(self) -> None:
        """THE BUG. A `workflow_run`-triggered build of the tagged commit, newer than STARTED_AT
        and green, must NOT clear the wait: the `:v1.2.3` build may still be running."""
        self.write(
            "a.yml",
            [
                self.run_entry("branch", event="workflow_run", headBranch="main", createdAt="2026-01-01T13:00:00Z"),
                self.run_entry("push", event="push", headBranch="main", createdAt="2026-01-01T12:30:00Z"),
            ],
        )
        result = self.wait()
        assert result.returncode != 0, result.stdout
        assert "timed out" in result.stdout

    def test_a_skipped_branch_run_does_not_fail_a_good_release(self) -> None:
        """The other half: a branch run that ended `skipped` because main's tests went red must
        not be reported as a failed release build."""
        self.write(
            "a.yml",
            [
                self.run_entry("branch", event="workflow_run", headBranch="main", conclusion="skipped"),
                self.run_entry("tag"),
            ],
        )
        result = self.wait()
        assert result.returncode == 0, result.stdout + result.stderr
        assert "skipped" not in result.stdout

    def test_runs_older_than_started_at_are_ignored(self) -> None:
        """A previous release attempt's runs on the same commit, at the same tag."""
        self.write("a.yml", [self.run_entry("old", createdAt="2026-01-01T09:00:00Z")])
        result = self.wait()
        assert result.returncode != 0
        assert "timed out" in result.stdout

    def test_a_pending_run_is_waited_for_then_cleared(self) -> None:
        self.write("a.yml", [self.run_entry("a", status="in_progress", conclusion=None)])
        result = self.wait(BUILD_WAIT_SECONDS=1)
        assert result.returncode != 0
        assert "waiting on 1" in result.stdout

    def test_a_failed_build_fails_the_release_and_names_it(self) -> None:
        self.write("a.yml", [self.run_entry("a", conclusion="failure")])
        result = self.wait()
        assert result.returncode != 0
        assert "::error::release build failed: a.yml (failure)" in result.stdout
        assert "NOT re-pinned" in result.stdout

    def test_a_timeout_names_the_pending_workflows(self) -> None:
        self.write("a.yml", [self.run_entry("a")])
        self.write("b.yml", [])
        result = self.wait(workflows="a.yml b.yml")
        assert result.returncode != 0
        assert "timed out" in result.stdout
        assert "b.yml" in result.stdout
        assert "::error::" in result.stdout

    def test_one_transient_poll_failure_is_not_a_failed_release(self) -> None:
        """A single 502 used to abort the wait under `set -e`, costing a rebuild of all twelve
        images. It must read as 'still pending' instead."""
        self.write("a.yml", [self.run_entry("a")])
        (self.fixtures / "fail").write_text("")
        result = self.wait(BUILD_WAIT_SECONDS=1, BUILD_POLL_MAX_FAILURES=50)
        assert result.returncode != 0
        assert "treating as pending" in result.stdout
        assert "consecutive failures" not in result.stdout
        assert "timed out" in result.stdout

    def test_a_run_of_poll_failures_stops_the_release(self) -> None:
        self.write("a.yml", [self.run_entry("a")])
        (self.fixtures / "fail").write_text("")
        result = self.wait(BUILD_WAIT_SECONDS=30, BUILD_POLL_MAX_FAILURES=2)
        assert result.returncode != 0
        assert "consecutive failures listing workflow runs" in result.stdout
        assert "not a failed build" in result.stdout

    def test_an_empty_roster_is_a_failure_not_a_silent_pass(self) -> None:
        result = self.wait(workflows="")
        assert result.returncode != 0
        assert "no builds were dispatched" in result.stdout

    def test_no_failure_message_tells_the_operator_to_re_run_the_release(self) -> None:
        """A re-run goes back through the dispatch step: it rebuilds all twelve images and
        republishes :v<X.Y.Z> with different digests from the ones sites already pulled. Every
        message must name the manual apply instead."""
        self.write("a.yml", [self.run_entry("a", conclusion="failure")])
        red = self.wait()
        self.write("a.yml", [])
        timeout = self.wait(BUILD_WAIT_SECONDS=1)
        (self.fixtures / "fail").write_text("")
        outage = self.wait(BUILD_WAIT_SECONDS=30, BUILD_POLL_MAX_FAILURES=2)
        for result in (red, timeout, outage):
            assert result.returncode != 0
            instructs_rerun = re.search(r"(?<!do not )re-run this workflow(?! will not help)", result.stdout.lower())
            assert not instructs_rerun, result.stdout
            assert "gh workflow run terraform_apply.yml --ref main -f release_tag=v1.2.3" in result.stdout

    def test_an_empty_pending_list_does_not_trip_set_u_on_old_bash(self) -> None:
        """`pending=("${still[@]}")` on an empty array is unbound-variable on bash < 4.4 (macOS),
        so the loop must break before the assignment."""
        source = WAIT_SCRIPT.read_text()
        assert 'pending=("${still[@]}")' in source
        assert source.index("${#still[@]} -eq 0 ]] && break") < source.index('pending=("${still[@]}")')


class TheApplyConfirmationRuns(unittest.TestCase):
    """A successful `gh workflow run` is not a run: `tf-apply-main` holds one pending run, so
    the next apply queued on main replaces a queued release apply and the release goes green
    with production on the previous version. The script is executed, not grepped."""

    STUB = """#!/usr/bin/env bash
# Stub `gh`: `run list` answers from a JSON fixture (through real jq, so the script's own
# filter is under test); `workflow run` records the dispatch and swaps in the next fixture.
sub="$1"; shift
if [[ "$sub" == "workflow" ]]; then
  echo "dispatch" >> "${FIXTURE_DIR}/dispatches"
  [[ -f "${FIXTURE_DIR}/after.json" ]] && mv "${FIXTURE_DIR}/after.json" "${FIXTURE_DIR}/runs.json"
  exit "$(cat "${FIXTURE_DIR}/dispatch-exit" 2>/dev/null || echo 0)"
fi
jqf=""
while [[ $# -gt 0 ]]; do
  case "$1" in --jq) jqf="$2"; shift ;; esac
  shift
done
jq -c "$jqf" < "${FIXTURE_DIR}/runs.json"
"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        stub = self.bin / "gh"
        stub.write_text(self.STUB)
        stub.chmod(0o755)
        self.fixtures = self.tmp / "fx"
        self.fixtures.mkdir()

    @staticmethod
    def run_entry(**over) -> dict:
        run = {
            "databaseId": 9,
            "status": "completed",
            "conclusion": "success",
            "createdAt": "2026-01-01T12:00:00Z",
            "url": "https://example.invalid/apply",
        }
        run.update(over)
        return run

    def write(self, runs: list[dict], name: str = "runs") -> None:
        (self.fixtures / f"{name}.json").write_text(json.dumps(runs))

    def confirm(self, **env) -> subprocess.CompletedProcess:
        environ = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FIXTURE_DIR": str(self.fixtures),
            "REPO": "londonaicentre/FLIP",
            "TAG": "v1.2.3",
            "STARTED_AT": "2026-01-01T11:00:00Z",
            "APPLY_CONFIRM_SECONDS": "2",
            "APPLY_CONFIRM_POLL_SECONDS": "1",
        }
        environ.update({k: str(v) for k, v in env.items()})
        return subprocess.run(["bash", str(CONFIRM_SCRIPT)], capture_output=True, text=True, env=environ, timeout=120)

    def dispatches(self) -> int:
        path = self.fixtures / "dispatches"
        return len(path.read_text().splitlines()) if path.exists() else 0

    def test_a_running_apply_confirms_the_release(self) -> None:
        self.write([self.run_entry(status="in_progress", conclusion=None)])
        result = self.confirm()
        assert result.returncode == 0, result.stdout + result.stderr
        assert "is running" in result.stdout
        assert self.dispatches() == 0

    def test_a_concluded_apply_is_not_re_dispatched(self) -> None:
        """Its outcome is its own run's to report; failing here would only block the Release."""
        self.write([self.run_entry(conclusion="failure")])
        result = self.confirm()
        assert result.returncode == 0, result.stdout + result.stderr
        assert self.dispatches() == 0

    def test_a_cancelled_queued_apply_is_dispatched_again(self) -> None:
        self.write([self.run_entry(conclusion="cancelled")])
        self.write([self.run_entry(status="in_progress", conclusion=None, createdAt="2099-01-01T00:00:00Z")], "after")
        result = self.confirm()
        assert result.returncode == 0, result.stdout + result.stderr
        assert "was cancelled before it ran" in result.stdout
        assert self.dispatches() == 1

    def test_a_second_cancellation_fails_the_release(self) -> None:
        self.write([self.run_entry(conclusion="cancelled")])
        self.write([self.run_entry(conclusion="cancelled", createdAt="2099-01-01T00:00:00Z")], "after")
        result = self.confirm()
        assert result.returncode != 0
        assert "cancelled twice" in result.stdout
        assert "NOT re-pinned" in result.stdout

    def test_a_still_queued_apply_warns_rather_than_failing_the_release(self) -> None:
        """Queuing behind a long push apply is legitimate — the release is not wrong."""
        self.write([self.run_entry(status="queued", conclusion=None)])
        result = self.confirm()
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" in result.stdout
        assert "still queued" in result.stdout

    def test_release_runs_the_confirmation_after_the_dispatch(self) -> None:
        text = code_text(WORKFLOWS / "release.yml")
        dispatch = text.index("Dispatch the production Terraform apply")
        confirm = text.index("Confirm the release apply actually ran")
        assert dispatch < confirm
        assert "confirm-release-apply.sh" in text


class TheReleaseTagValidationRuns(unittest.TestCase):
    """terraform_apply.yml's validate step, executed over tag × ref × repository state.

    Extracted from the workflow and run with a real git repository behind it, because the
    rollback guard (`the tag must BE this commit`) cannot be checked by reading the YAML.
    """

    def setUp(self) -> None:
        text = code_text(WORKFLOWS / "terraform_apply.yml")
        step = step_block(text, "Validate the release tag")
        body = step[step.index("run: |") + len("run: |") :]
        self.script = textwrap.dedent(body)
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo, True)
        git = ["git", "-C", str(self.repo)]
        subprocess.run(git + ["init", "-q", "-b", "main"], check=True)
        subprocess.run(git + ["config", "user.email", "t@example.invalid"], check=True)
        subprocess.run(git + ["config", "user.name", "t"], check=True)
        (self.repo / "f").write_text("1")
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-qm", "one"], check=True)
        self.tagged = subprocess.run(
            git + ["rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        subprocess.run(git + ["tag", "v1.2.3"], check=True)
        (self.repo / "f").write_text("2")
        subprocess.run(git + ["commit", "-qam", "two"], check=True)
        self.newer = subprocess.run(
            git + ["rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()

    def validate(self, tag: str, ref: str, sha: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-c", self.script],
            cwd=self.repo,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "RELEASE_TAG": tag,
                "GITHUB_REF_NAME": ref,
                "GITHUB_SHA": sha,
            },
            timeout=60,
        )

    def test_a_stable_tag_on_main_at_its_own_commit_passes(self) -> None:
        result = self.validate("v1.2.3", "main", self.tagged)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_a_tag_that_is_not_this_commit_is_refused(self) -> None:
        """THE ROLLBACK. A hotfix merged during the build wait moves main; applying the older
        release's images over it is a silent production rollback."""
        result = self.validate("v1.2.3", "main", self.newer)
        assert result.returncode != 0
        assert "roll production back" in result.stdout

    def test_an_unknown_tag_is_refused(self) -> None:
        result = self.validate("v9.9.9", "main", self.tagged)
        assert result.returncode != 0
        assert "does not resolve to a commit" in result.stdout

    def test_only_main_is_accepted(self) -> None:
        result = self.validate("v1.2.3", "develop", self.tagged)
        assert result.returncode != 0
        assert "only valid on main" in result.stdout

    def test_unstable_and_malformed_tags_are_refused(self) -> None:
        for tag in ("v1.2", "1.2.3", "v1.2.3-rc1", "latest", " v1.2.3", "refs/tags/v1.2.3"):
            with self.subTest(tag=tag):
                result = self.validate(tag, "main", self.tagged)
                assert result.returncode != 0, result.stdout
                assert "stable release tag" in result.stdout


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
