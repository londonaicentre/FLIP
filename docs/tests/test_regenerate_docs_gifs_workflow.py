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
"""Static guard over ``.github/workflows/regenerate_docs_gifs.yml`` (FLIP#1236).

The workflow's header comments make security claims — the recording job sees no secret, the dataset write
token reaches exactly one step, only ``develop`` publishes — that nothing at run time would notice drifting.
This pins them, plus the two shapes a silent failure would hide behind: a tag minted after the recording it
names, and a piped ``run:`` step without ``pipefail``.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml
from conftest import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "regenerate_docs_gifs.yml"
PIPE_RE = re.compile(r"\|\s*(?:\n|$)|\s\|\s")


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text())


@pytest.fixture(scope="module")
def jobs(workflow) -> dict[str, dict[str, Any]]:
    return workflow["jobs"]


def steps_of(job: dict[str, Any]) -> list[dict[str, Any]]:
    return job.get("steps", [])


def mentions_secrets(value: Any) -> bool:
    return "secrets." in yaml.safe_dump(value)


def test_the_workflow_and_the_recording_job_are_read_only(workflow, jobs):
    assert workflow["permissions"] == {"contents": "read"}
    assert jobs["record"]["permissions"] == {"contents": "read"}
    assert not mentions_secrets(jobs["record"]), "the recording job must run without any secret"


def test_the_dataset_token_reaches_exactly_one_step(workflow, jobs):
    assert not mentions_secrets(workflow.get("env", {}))
    for job in jobs.values():
        assert not mentions_secrets(job.get("env", {}))
    holders = [
        step["name"]
        for job in jobs.values()
        for step in steps_of(job)
        if "secrets.HF_TOKEN" in yaml.safe_dump(step.get("env", {}))
    ]
    assert holders == ["Publish to the dataset"]
    for job in jobs.values():
        for step in steps_of(job):
            assert not mentions_secrets(step.get("run", "")), f"{step['name']}: a secret expanded into a script"
            assert not mentions_secrets(step.get("with", {})), f"{step['name']}: a secret handed to an action"


def test_the_verification_fetch_is_anonymous(jobs):
    verify = next(step for step in steps_of(jobs["publish"]) if step["name"].startswith("Verify"))
    assert "HF_TOKEN" not in verify.get("env", {})


def test_only_develop_publishes(jobs):
    assert "github.ref == 'refs/heads/develop'" in jobs["publish"].get("if", "")


def test_the_tag_is_minted_by_the_recording_job(jobs):
    """``recorded_at`` in the manifest is the tag's timestamp, so the tag must be minted before Cypress runs."""
    record_steps = [step["name"] for step in steps_of(jobs["record"])]
    assert record_steps.index("Mint the version tag") < record_steps.index("Record docs demo specs")
    assert jobs["record"]["outputs"] == {"tag": "${{ steps.tag.outputs.tag }}"}
    assert not any(step["name"] == "Mint the version tag" for step in steps_of(jobs["publish"]))
    assert "needs.record.outputs.tag" in yaml.safe_dump(jobs["publish"])


def test_every_piped_run_step_fails_on_a_broken_pipe(jobs):
    """GitHub's default shell is ``bash -e`` without ``pipefail``: a failing producer would exit 0."""
    offenders = [
        step["name"]
        for job in jobs.values()
        for step in steps_of(job)
        if PIPE_RE.search(step.get("run", "")) and step.get("shell") != "bash"
    ]
    assert offenders == []
