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

"""Render the NVFLARE site privacy policy (``local/privacy.json``) from ``FL_SITE_PRIVACY_*`` env vars.

Run by the fl-client at container start (``python -m flip.nvflare.site_policy /app/local/privacy.json``)
so each trust can enforce its own update-privacy filter through its kit env file, independently of
whatever ``task_result_filters`` the submitted job carries: NVFLARE applies site scope filters *before*
job filters and never lets a job opt out (``nvflare/apis/utils/task_utils.py::apply_filters``).

The rendered document defines exactly one scope, set as ``default_scope`` — FLIP jobs never carry a
``scope`` meta key, so every job lands in it, and any other scope name is rejected at deploy time.
The stock NVFLARE filter class is used deliberately: unlike FLIP's app-level subclass it has no
``off`` switch, so an app config cannot disable the site filter.

Stdlib-only on purpose — it must run before NVFLARE starts and never fail on framework imports.
Validation is strict because stock ``PercentilePrivacy`` fails *open* (silently forwards the update
unfiltered) when ``gamma <= 0`` or ``percentile`` is outside ``[0, 100]``; a mis-set policy must stop
the container, not run unprotected. When no policy is configured, any previously rendered file is
removed — the env vars are the single source of truth, and the target lives on a persistent bind mount.
"""

import argparse
import json
import math
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

SCOPE_NAME = "site_default"
PERCENTILE_FILTER_PATH = "nvflare.app_common.filters.percentile_privacy.PercentilePrivacy"
_POLICY_VAR = "FL_SITE_PRIVACY_POLICY"


class SitePolicyError(ValueError):
    """Invalid ``FL_SITE_PRIVACY_*`` configuration."""


@dataclass(frozen=True)
class SitePolicy:
    """A validated site privacy policy selection.

    Attributes:
        percentile: Percentage threshold passed to NVFLARE's stock filter.
        gamma: Maximum absolute update value passed to NVFLARE's stock filter.
    """

    percentile: float
    gamma: float


def _get(env: Mapping[str, str], var: str) -> str | None:
    """Reads an env var, treating empty/whitespace-only values as unset.

    Commented-out example files and compose ``${VAR:-}`` interpolation both surface as ``""``,
    which must behave exactly like an absent variable.
    """
    value = env.get(var, "").strip()
    return value or None


def _parse_value(
    var: str,
    raw: str,
    *,
    minimum: float,
    maximum: float | None = None,
    minimum_exclusive: bool = False,
) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise SitePolicyError(f"{var}={raw!r} is not a number") from None
    below_minimum = value <= minimum if minimum_exclusive else value < minimum
    if not math.isfinite(value) or below_minimum or (maximum is not None and value > maximum):
        if maximum is not None:
            bounds = f"[{minimum:g}, {maximum:g}]"
        else:
            bounds = f"> {minimum:g}" if minimum_exclusive else f">= {minimum:g}"
        raise SitePolicyError(f"{var}={raw!r} is out of bounds (expected a finite number {bounds})")
    # Integral values (e.g. percentile) are emitted as ints so the JSON matches the filters' documented args.
    return int(value) if value.is_integer() else value


def parse_env(env: Mapping[str, str]) -> SitePolicy | None:
    """Parses and validates ``FL_SITE_PRIVACY_*`` env vars into a :class:`SitePolicy`.

    Args:
        env: Environment mapping (typically ``os.environ``).

    Returns:
        The validated policy, or ``None`` when no policy is configured.

    Raises:
        SitePolicyError: On an unknown policy, an invalid parameter, or parameters set without a policy.
    """
    policy_raw = _get(env, _POLICY_VAR)

    if policy_raw is None:
        stray = [var for var in ("FL_SITE_PRIVACY_PERCENTILE", "FL_SITE_PRIVACY_GAMMA") if _get(env, var) is not None]
        if stray:
            raise SitePolicyError(
                f"{', '.join(sorted(stray))} set but {_POLICY_VAR} is not — refusing to guess a policy; "
                f"set {_POLICY_VAR} or unset the parameter(s)"
            )
        return None

    if policy_raw.lower() != "percentile":
        raise SitePolicyError(f"{_POLICY_VAR}={policy_raw!r} is not a known policy (expected: percentile)")

    percentile_raw = _get(env, "FL_SITE_PRIVACY_PERCENTILE")
    gamma_raw = _get(env, "FL_SITE_PRIVACY_GAMMA")
    percentile = 10 if percentile_raw is None else _parse_value(
        "FL_SITE_PRIVACY_PERCENTILE", percentile_raw, minimum=0, maximum=100
    )
    gamma = 0.01 if gamma_raw is None else _parse_value(
        "FL_SITE_PRIVACY_GAMMA", gamma_raw, minimum=0, minimum_exclusive=True
    )
    return SitePolicy(percentile=percentile, gamma=gamma)


def build_policy_json(policy: SitePolicy) -> dict:
    """Builds the NVFLARE ``privacy.json`` document for a validated policy.

    Exactly one scope, set as ``default_scope`` so scope-less FLIP jobs always land in it. The filter
    entry omits ``direction`` (client-side result filters default to ``out``) and the scope omits
    ``task_data_filters`` (FLIP only constrains what leaves the site).
    """
    return {
        "scopes": [
            {
                "name": SCOPE_NAME,
                "task_result_filters": [
                    {"path": PERCENTILE_FILTER_PATH, "args": {"percentile": policy.percentile, "gamma": policy.gamma}}
                ],
            }
        ],
        "default_scope": SCOPE_NAME,
    }


def render(env: Mapping[str, str], out_path: Path, check_only: bool = False) -> str:
    """Renders (or removes) the site privacy policy file according to the environment.

    Args:
        env: Environment mapping to read ``FL_SITE_PRIVACY_*`` from.
        out_path: Target ``privacy.json`` path.
        check_only: When ``True``, validate and report without touching the filesystem.

    Returns:
        ``"written"`` (policy configured), ``"removed"`` (no policy, stale file found), or
        ``"absent"`` (no policy, no file).

    Raises:
        SitePolicyError: On invalid ``FL_SITE_PRIVACY_*`` configuration.
    """
    policy = parse_env(env)

    if policy is None:
        if out_path.exists():
            if not check_only:
                out_path.unlink()
            return "removed"
        return "absent"

    if not check_only:
        tmp_path = out_path.with_name(out_path.name + ".tmp")
        tmp_path.write_text(json.dumps(build_policy_json(policy), indent=2) + "\n")
        os.replace(tmp_path, out_path)
    return "written"


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """CLI entry point: ``python -m flip.nvflare.site_policy [--check] <out_path>``.

    Returns:
        ``0`` on success (policy written, removed, or not configured), ``1`` on invalid
        configuration or I/O failure — callers must treat ``1`` as fatal (fail closed).
    """
    parser = argparse.ArgumentParser(prog="flip.nvflare.site_policy", description=__doc__)
    parser.add_argument("out_path", type=Path, help="Target privacy.json path (e.g. /app/local/privacy.json)")
    parser.add_argument("--check", action="store_true", help="Validate the configuration without writing")
    parsed = parser.parse_args(argv)
    if env is None:
        env = os.environ

    try:
        status = render(env, parsed.out_path, check_only=parsed.check)
    except (SitePolicyError, OSError) as e:
        print(f"[site-privacy] FATAL: {e}", file=sys.stderr)
        return 1

    verb = "validated (--check, not written)" if parsed.check else "wrote"
    if status == "written":
        policy = parse_env(env)
        assert policy is not None  # "written" implies a configured policy
        print(
            f"[site-privacy] site privacy policy ACTIVE: percentile "
            f"(percentile={policy.percentile}, gamma={policy.gamma}) — {verb} {parsed.out_path} "
            f"(scope '{SCOPE_NAME}'; site filters run before app-level filters and jobs cannot opt out)"
        )
    elif status == "removed":
        removal = "would remove (--check)" if parsed.check else "REMOVED"
        print(
            f"[site-privacy] no site privacy policy configured — {removal} stale {parsed.out_path} "
            f"left by an earlier configuration"
        )
    else:
        print(
            f"[site-privacy] no site privacy policy configured — {parsed.out_path} absent; "
            f"running without a site privacy policy (previous default)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
