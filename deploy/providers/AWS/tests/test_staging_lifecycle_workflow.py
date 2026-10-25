# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Static guards on the staging stop/start wiring (FLIP#1596).

``staging_lifecycle.yml`` scales ECS services to zero and stops EC2 and RDS by names that are
identical in staging and production: ``flip-cluster``, ``flip-database``, ``Ec2Instance``. Pointed
at the wrong account it takes production down, and it would do so on a schedule.

``scripts/tests/test_staging_power.sh`` pins the refusal in the script, which is the check that
actually runs at the moment of the call. These pin the layer above it, which the script cannot see:
that no job in this workflow can be handed production's credentials in the first place. Both
matter, because they fail for different reasons. The script's guard reads environment variables the
workflow supplies, so a workflow that supplied ``FLIP_ENVIRONMENT: aws-stag`` while assuming
production's OIDC role would satisfy the script and still be wrong.

Also pinned here, because it is the same class of mistake: the nightly guard must stay on a
schedule (a stop that is only ever dispatched by hand is not a guard), and the hold job must keep
the concurrency group that lets a second dispatch replace a window instead of queueing a second
stop behind it.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
LIFECYCLE = WORKFLOWS / "staging_lifecycle.yml"
APPLY = WORKFLOWS / "terraform_apply.yml"
DRIFT = WORKFLOWS / "terraform_drift.yml"

PROD_ENVIRONMENT = "aws-prod"


def _load(path: Path) -> dict:
    # `on:` is parsed as the boolean True by YAML 1.1, which is why the trigger assertions below
    # index with True rather than "on".
    return yaml.safe_load(path.read_text())


def _jobs(path: Path) -> dict:
    return _load(path)["jobs"]


def test_lifecycle_never_names_the_production_environment() -> None:
    # Comment lines are stripped so the assertion cannot be satisfied or broken by prose: the
    # file explains at length why it must never reach production, and that explanation has to be
    # allowed to name the thing it is refusing.
    live = [line for line in LIFECYCLE.read_text().splitlines() if not line.lstrip().startswith("#")]
    offenders = [line.strip() for line in live if PROD_ENVIRONMENT in line]
    assert not offenders, (
        f"{LIFECYCLE.name} must not name {PROD_ENVIRONMENT} in any live line: it stops ECS, EC2 and "
        f"RDS by names that are the same in both estates. Found: {offenders}"
    )


def test_lifecycle_has_no_production_trigger() -> None:
    triggers = _load(LIFECYCLE)[True]
    assert "push" not in triggers, "staging_lifecycle.yml must not fire on a push, least of all main's"
    assert "schedule" in triggers, "the nightly guard needs a schedule, or nothing re-stops a forgotten estate"


@pytest.mark.parametrize("job_name", ["nightly-guard", "hold", "power"])
def test_every_lifecycle_job_refuses_main(job_name: str) -> None:
    job = _jobs(LIFECYCLE)[job_name]
    assert "github.ref_name != 'main'" in job.get("if", ""), (
        f"staging_lifecycle.yml job '{job_name}' must refuse to run on main; aws-prod is pinned to "
        "main, so a job that runs there is one environment edit away from stopping production"
    )


def test_power_job_pins_the_staging_environment_literally() -> None:
    environment = _jobs(LIFECYCLE)["power"]["environment"]
    assert environment == "aws-stag", (
        "the environment must be the literal 'aws-stag', never an expression: an expression here is "
        f"one typo away from selecting {PROD_ENVIRONMENT} (got {environment!r})"
    )


def test_the_hold_can_be_replaced_by_a_later_dispatch() -> None:
    concurrency = _jobs(LIFECYCLE)["hold"]["concurrency"]
    assert concurrency["cancel-in-progress"] is True, (
        "the hold job must cancel an in-progress hold, or dispatching again queues a second stop "
        "behind the first instead of extending the window"
    )
    assert concurrency["group"] == "stag-hold"


def test_the_stop_declines_a_cancelled_or_failed_hold() -> None:
    condition = _jobs(LIFECYCLE)["power"]["if"]
    assert "needs.hold.result != 'cancelled'" in condition, (
        "a cancelled hold means a newer dispatch owns the window; stopping anyway would cut short "
        "the hold the developer just extended"
    )
    assert "needs.hold.result != 'failure'" in condition


def test_the_nightly_guard_stands_down_when_work_is_in_flight() -> None:
    condition = _jobs(LIFECYCLE)["power"]["if"]
    assert "needs.nightly-guard.outputs.proceed == 'true'" in condition, (
        "the scheduled stop must consult the guard, or the nightly run can stop an estate that is "
        "mid-apply or inside somebody's hold window"
    )


@pytest.mark.parametrize(
    ("path", "job_names"),
    [
        (APPLY, ["start-staging", "restore-staging-services", "staging-end-to-end", "hold-then-stop-staging"]),
        (DRIFT, ["staging-state"]),
    ],
    ids=["terraform_apply.yml", "terraform_drift.yml"],
)
def test_staging_only_jobs_refuse_main(path: Path, job_names: list[str]) -> None:
    jobs = _jobs(path)
    for job_name in job_names:
        assert job_name in jobs, f"{path.name} lost the '{job_name}' job"
        assert "github.ref_name != 'main'" in jobs[job_name].get("if", ""), (
            f"{path.name} job '{job_name}' is a staging-only job and must refuse to run on main"
        )


def test_the_apply_still_runs_on_main_when_the_staging_start_is_skipped() -> None:
    """The staging jobs must not be able to stop a production apply by being skipped."""
    condition = _jobs(APPLY)["apply"]["if"]
    assert "always()" in condition, (
        "the apply depends on start-staging, which is skipped on main; without always() a skipped "
        "dependency silently skips every production apply"
    )
    assert "needs.start-staging.result != 'failure'" in condition, (
        "an apply must not plan against a database that failed to start: the refresh error reads as "
        "a broken pipeline rather than a sleeping instance"
    )


def test_drift_still_runs_on_main_when_the_staging_probe_is_skipped() -> None:
    condition = _jobs(DRIFT)["drift"]["if"]
    assert "always()" in condition
    assert "needs.staging-state.outputs.stopped != 'true'" in condition, (
        "the nightly drift run must stand down on a stopped staging estate, or it reports the "
        "intended state as drift every night"
    )
    assert "needs.staging-state.result != 'failure'" in condition, (
        "not being able to tell whether staging is stopped is not a licence to plan it"
    )


def test_the_fl_gate_is_released_by_a_measurement_not_a_literal() -> None:
    """The gate's release must come from the describe step, never a hard-coded true."""
    gate = next(step for step in _jobs(APPLY)["apply"]["steps"] if step.get("name") == "Check the plan for FL impact")
    released_by = gate["env"]["FL_SERVICES_STOPPED"]
    assert released_by == "${{ steps.fl_state.outputs.fl_stopped }}", (
        "FL_SERVICES_STOPPED must be the measured ECS state, not a constant: a literal true would "
        f"disable hazard B's gate outright (got {released_by!r})"
    )
    probe = next(step for step in _jobs(APPLY)["apply"]["steps"] if step.get("id") == "fl_state")
    assert probe["if"] == "github.ref_name != 'main'", (
        "the measurement must never run on main: production is not stopped by anything, so a true "
        "there could only come from a bug"
    )


def test_the_end_to_end_check_declares_the_staging_environment() -> None:
    """Without it the probe reads an empty address, skips, and the deploy is never verified."""
    job = _jobs(APPLY)["staging-end-to-end"]
    assert job["environment"] == "aws-stag", (
        "LZA_WEB_EDGE_DOMAIN is an aws-stag environment variable, so a job with no environment "
        "probes nothing and still passes"
    )


def test_the_end_to_end_check_can_fail_the_job() -> None:
    """A check that renders a cross on the summary and exits 0 is not a check."""
    steps = _jobs(APPLY)["staging-end-to-end"]["steps"]
    report = next(step for step in steps if step.get("name") == "Report the result")
    assert "exit 1" in report["run"], "the report step must fail the job on a failed or skipped verdict"
    assert report.get("continue-on-error") is not True, "the step that decides the verdict cannot ignore it"
    # And the stop must still happen whatever that verdict is.
    stop = _jobs(APPLY)["hold-then-stop-staging"]
    assert "always()" in stop["if"], "a failed end to end check must not leave staging running"


def test_an_apply_cancels_a_sleeping_hold() -> None:
    """Otherwise a merge during a hold queues behind the whole window and then its stop."""
    start = _jobs(APPLY)["start-staging"]
    assert start["concurrency"] == {"group": "stag-hold", "cancel-in-progress": True}, (
        "the apply must claim the hold's concurrency group so a newer deploy replaces the window"
    )
    hold = _jobs(LIFECYCLE)["hold"]
    assert hold["concurrency"]["group"] == "stag-hold", "both sides have to name the same group"


def test_the_staging_apply_role_trusts_the_power_workflow() -> None:
    """The power job runs from staging_lifecycle.yml, so the apply role has to accept that ref."""
    module = (REPO_ROOT / "deploy" / "providers" / "AWS" / "modules" / "terraform_ci_bootstrap").resolve()
    main = (module / "main.tf").read_text()
    assert "power_workflow_ref" in main, "the staging power workflow needs its own job_workflow_ref local"
    assert 'var.environment == "stag" ? [local.power_workflow_ref] : []' in main, (
        "only staging may trust it: there is no production power workflow and there must not be one"
    )
    assert "local.apply_workflow_refs" in (module / "iam_apply.tf").read_text(), (
        "the apply trust policy must use the set, not the single apply ref"
    )
