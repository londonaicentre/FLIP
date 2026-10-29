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
"""Static guards on the release-tag deploy paths (FLIP#1204).

``deploy-centralhub`` pins ECS to an immutable image tag. Until FLIP#1204 the only such tag
was ``sha-<short7>``; a release now also publishes ``:v<X.Y.Z>`` on every image, and the hub
deploy must accept it — while still refusing the floating ``:prod`` / ``:stag``, which would
defeat the revision audit trail the guard exists for. The guard's regex is the single line
that decides both, so it is asserted here by running the tag check the way the recipe does.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
MAKEFILE = AWS_PROVIDER_DIR / "Makefile"


def _tag_guard_regex() -> str:
    """The grep -E pattern deploy-centralhub applies to TAG, un-escaped from the Makefile."""
    text = MAKEFILE.read_text()
    m = re.search(r"printf '%s' \"\$\$TAG\" \| grep -Eq '(?P<pattern>[^']+)'", text)
    assert m, "deploy-centralhub's TAG guard (printf … | grep -Eq '…') not found"
    return m.group("pattern").replace("$$", "$")


@pytest.mark.parametrize("tag", ["sha-badcff1", "v0.6.0", "v10.2.13"])
def test_hub_deploy_accepts_immutable_tags(tag: str) -> None:
    pattern = _tag_guard_regex()
    assert subprocess.run(["grep", "-Eq", pattern], input=tag, text=True).returncode == 0, f"{tag} refused by {pattern}"


@pytest.mark.parametrize("tag", ["prod", "stag", "latest", "0.6.0", "sha-badcff", "v0.6", "develop", "sha-BADCFF1"])
def test_hub_deploy_refuses_floating_and_malformed_tags(tag: str) -> None:
    pattern = _tag_guard_regex()
    accepted = subprocess.run(["grep", "-Eq", pattern], input=tag, text=True).returncode == 0
    assert not accepted, f"{tag} accepted by {pattern}"


def _recipe(target: str) -> str:
    """The recipe lines of one Makefile target, comments stripped."""
    text = MAKEFILE.read_text()
    m = re.search(rf"^{re.escape(target)}:[^\n]*\n((?:\t[^\n]*\n)+)", text, re.M)
    assert m, f"target {target} not found"
    return "\n".join(line for line in m.group(1).splitlines() if not line.strip().startswith("#"))


def _base_ref_shell() -> str:
    """deploy-centralhub's base_ref() helper, un-escaped from the Makefile into runnable sh."""
    recipe = _recipe("deploy-centralhub")
    m = re.search(r"(base_ref\(\) \{.*?\n\t\}; \\)", recipe, re.S)
    assert m, "deploy-centralhub's base_ref() helper not found"
    body = m.group(1).replace("$$", "$")
    return "\n".join(line.rstrip(" \\").lstrip("\t") for line in body.splitlines())


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        ("ghcr.io/londonaicentre/flip-api:stag", "ghcr.io/londonaicentre/flip-api"),
        ("ghcr.io/londonaicentre/flip-api:v1.2.3@sha256:" + "a" * 64, "ghcr.io/londonaicentre/flip-api"),
    ],
)
def test_base_ref_strips_the_digest_before_the_tag_swap(image: str, expected: str) -> None:
    """A release apply leaves ECS on ``repo:vX.Y.Z@sha256:…`` (FLIP#1204).

    ``${image%:*}`` on that raw reference cuts at the digest's own colon and yields
    ``repo:v1.2.3@sha256:sha-1234567`` — a reference that passes nothing and pulls as
    CannotPullContainer. Both loops in deploy-centralhub must go through base_ref().
    """
    script = _base_ref_shell() + f'\nb=$(base_ref "{image}" flip-api) || exit 1\nprintf \'%s\' "${{b%:*}}:sha-1234567"'
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout == f"{expected}:sha-1234567"


def test_base_ref_refuses_a_digest_only_reference() -> None:
    script = _base_ref_shell() + '\nbase_ref "ghcr.io/londonaicentre/flip-api@sha256:' + "a" * 64 + '" flip-api'
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
    assert out.returncode != 0, "a digest-only reference has no tag to swap and must be refused"
    assert "digest-only" in out.stderr


def test_both_deploy_loops_swap_the_tag_onto_the_stripped_reference() -> None:
    """The verify loop and the register loop must agree, or the deploy verifies A and registers B."""
    recipe = _recipe("deploy-centralhub")
    assert '"$${image%:*}:$$TAG"' not in recipe, "a tag swap on the raw (possibly digest-pinned) image reference"
    assert recipe.count('base_ref "$$image" "$$service"') == 2, "both loops must resolve through base_ref()"


# ── Static goals run without the deploy env file (FLIP#1440) ─────────────────────────────────


def _make_dry_run(
    *goals: str, tmp_path: Path, env_file: Path | None = None, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """`make -n <goals>` with the given env file (none by default) and nothing else from the caller's shell.

    MAIN_ENV_FILE points at a path that does not exist unless one is given, so a contributor's
    gitignored .env.stag (if any) cannot make a test pass for the wrong reason. Goal-like words
    containing "=" are passed through as command-line variables; HUB defaults to stag.
    """
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", str(tmp_path)), **(extra_env or {})}
    hub = [] if any(goal.startswith("HUB=") for goal in goals) else ["HUB=stag"]
    return subprocess.run(
        ["make", "-n", "-s", *goals, *hub, f"MAIN_ENV_FILE={env_file or tmp_path / 'absent.env'}"],
        cwd=AWS_PROVIDER_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


# The smallest env file every guard accepts, as in test_iam_permissions_boundary.py.
COMPLETE_ENV = {
    "AWS_PROFILE": "probe",
    "PROD_AWS_PROFILE": "probe",
    "STAG_AWS_PROFILE": "probe",
    "LZA_VPC_NAME": "probe-vpc",
    "MANAGE_DNS": "false",
    "FLARE_KIT_DATE": "00000000",
    "FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME": "probe-uploads",
    "FLIP_FL_RESULTS_BUCKET_NAME": "probe-results",
    "FLIP_APP_BUNDLES_BUCKET_NAME": "probe-bundles",
    "AICENTRE_BUCKET_NAME": "probe-aicentre",
    "INTERNAL_SERVICE_KEY": "probe",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY_HASH": "probe",  # pragma: allowlist secret
    "INTERNAL_SERVICE_KEY_HEADER": "X-Probe",  # pragma: allowlist secret
}


def _env_file(tmp_path: Path, **changes: str | None) -> Path:
    """COMPLETE_ENV with `changes` applied; a value of None leaves the key out."""
    values = {**COMPLETE_ENV, **changes}
    path = tmp_path / ".env.probe"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items() if v is not None))
    return path


@pytest.mark.parametrize("hub", ["", "stag", "prod"])
@pytest.mark.parametrize("goal", ["lint", "fmt-check", "tflint-lint", "checkov-lint", "help"])
def test_a_static_goal_needs_no_env_file(goal: str, hub: str, tmp_path: Path) -> None:
    result = _make_dry_run(goal, f"HUB={hub}", tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr


def test_lint_runs_the_three_static_checks_ci_runs(tmp_path: Path) -> None:
    result = _make_dry_run("lint", tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr
    for command in ("terraform fmt -check -recursive", "scripts/tflint_lint.sh", "scripts/checkov_lint.sh"):
        assert command in result.stdout, f"lint must run {command}"


def test_a_complete_env_file_passes_every_guard(tmp_path: Path) -> None:
    """The baseline for the guard tests below: without it, a refusal could come from the wrong guard."""
    result = _make_dry_run("print-tf-env", tmp_path=tmp_path, env_file=_env_file(tmp_path))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("change", "named"),
    [
        ({"FLARE_KIT_DATE": None}, "FLARE_KIT_DATE"),
        ({"AWS_PROFILE": "someone-else"}, "AWS_PROFILE must be set"),
        ({"FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME": None}, "FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME"),
        ({"FLIP_FL_RESULTS_BUCKET_NAME": None}, "FLIP_FL_RESULTS_BUCKET_NAME"),
        ({"FLIP_APP_BUNDLES_BUCKET_NAME": "<your-bundles-bucket>"}, "FLIP_APP_BUNDLES_BUCKET_NAME"),
        ({"AICENTRE_BUCKET_NAME": None}, "AICENTRE_BUCKET_NAME"),
        ({"INTERNAL_SERVICE_KEY": None}, "INTERNAL_SERVICE_KEY must be set"),
        ({"INTERNAL_SERVICE_KEY_HASH": None}, "INTERNAL_SERVICE_KEY_HASH"),
        ({"INTERNAL_SERVICE_KEY_HEADER": None}, "INTERNAL_SERVICE_KEY_HEADER"),
        # No default: true is wrong on a zone-less estate, false wrong on one with a zone.
        ({"MANAGE_DNS": None}, "MANAGE_DNS must be true or false"),
        ({"MANAGE_DNS": "yes"}, "MANAGE_DNS must be true or false"),
    ],
)
def test_each_env_guard_still_refuses_a_real_goal(change: dict[str, str | None], named: str, tmp_path: Path) -> None:
    result = _make_dry_run("plan", tmp_path=tmp_path, env_file=_env_file(tmp_path, **change))
    assert result.returncode != 0
    assert named in result.stderr, result.stderr


def test_an_unset_hub_refuses_a_real_goal(tmp_path: Path) -> None:
    """No default estate any more (FLIP#1377): an unset HUB is development, which has none."""
    result = _make_dry_run("plan", "HUB=", tmp_path=tmp_path, env_file=_env_file(tmp_path))
    assert result.returncode != 0
    assert "HUB=stag or HUB=prod is required" in result.stderr, result.stderr


def test_the_staging_vpc_guard_still_refuses_a_real_goal(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path, LZA_VPC_NAME=None)
    result = _make_dry_run("plan", tmp_path=tmp_path, env_file=env_file)
    assert result.returncode != 0
    assert "LZA_VPC_NAME must be set" in result.stderr, result.stderr


@pytest.mark.parametrize("goals", [(), ("plan",), ("deploy-centralhub",), ("lint", "plan")])
def test_any_other_goal_keeps_the_env_guards(goals: tuple[str, ...], tmp_path: Path) -> None:
    """The guards stand aside only when EVERY goal is static: no goal, or a mixed list, keeps them."""
    result = _make_dry_run(*goals, tmp_path=tmp_path)
    assert result.returncode != 0
    assert "FLARE_KIT_DATE must be set" in result.stderr, result.stderr


@pytest.mark.parametrize(
    ("args", "extra_env"),
    [
        (("plan", "STATIC_ONLY=yes"), {}),
        (("plan", "STATIC_GOALS=plan"), {}),
        (("plan", "MAKECMDGOALS=lint"), {}),
        (("plan",), {"MAKECMDGOALS": "lint"}),
        (("plan",), {"MAKEFLAGS": "STATIC_ONLY=yes"}),
        (("-e", "plan"), {"STATIC_ONLY": "yes"}),
    ],
)
def test_the_static_switch_cannot_be_set_from_outside(
    args: tuple[str, ...], extra_env: dict[str, str], tmp_path: Path
) -> None:
    """One variable must not switch off every guard, the AWS account check included."""
    result = _make_dry_run(*args, tmp_path=tmp_path, extra_env=extra_env)
    assert result.returncode != 0
    assert "FLARE_KIT_DATE must be set" in result.stderr, result.stderr


def test_register_trusts_finding_no_kit_warns_and_passes() -> None:
    """full-deploy registers whatever kits exist; a new hub has none yet, and its trusts register later."""
    env = {"PATH": os.environ["PATH"], "AWS_PROFILE": "probe", "AWS_REGION": "eu-west-2", "HUB": "stag"}
    script = subprocess.run(
        ["bash", "scripts/register-trusts.sh"],
        cwd=AWS_PROVIDER_DIR,
        env={**env, "KIT_FILES": ""},
        capture_output=True,
        text=True,
    )
    assert script.returncode == 0, script.stdout + script.stderr
    assert "No trust/.env.<CODE>.stag kits found" in script.stdout + script.stderr


def test_register_trusts_mints_the_xnat_credentials_into_the_kit(tmp_path: Path) -> None:
    """As the dev register-trust does, so a packaged kit carries no XNAT placeholders (FLIP#1419)."""
    result = _make_dry_run("register-trusts", "KIT=AZ1", tmp_path=tmp_path, env_file=_env_file(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "generate_xnat_credentials --env-file " + str(AWS_PROVIDER_DIR.parents[2] / "trust" / ".env.AZ1.stag") in (
        result.stdout
    )


def test_register_trusts_never_rotates_the_xnat_credentials(tmp_path: Path) -> None:
    """A FORCE=1 on the command line reaches sub-makes; it must not rotate a live trust's XNAT passwords."""
    result = _make_dry_run("register-trusts", "KIT=AZ1", "FORCE=1", tmp_path=tmp_path, env_file=_env_file(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "generate_xnat_credentials --env-file " in result.stdout
    assert "--force" not in result.stdout
