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
Stock NVFLARE filter classes are used deliberately: unlike FLIP's app-level subclass they have no
``off`` switch, so an app config cannot disable the site filter.

Stdlib-only on purpose — it must run before NVFLARE starts and never fail on framework imports.
Validation is strict because stock ``PercentilePrivacy`` fails *open* (silently forwards the update
unfiltered) when ``gamma <= 0`` or ``percentile`` is outside ``[0, 100]``; a mis-set policy must stop
the container, not run unprotected. When no policy is configured, any previously rendered file is
removed — the env vars are the single source of truth, and the target lives on a persistent bind mount.
"""

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

SCOPE_NAME = "site_default"
PERCENTILE_FILTER_PATH = "nvflare.app_common.filters.percentile_privacy.PercentilePrivacy"
SVT_FILTER_PATH = "nvflare.app_common.filters.svt_privacy.SVTPrivacy"

_POLICY_VAR = "FL_SITE_PRIVACY_POLICY"


class SitePolicyError(ValueError):
    """Invalid ``FL_SITE_PRIVACY_*`` configuration."""


@dataclass(frozen=True)
class SitePolicy:
    """A validated site privacy policy selection.

    Attributes:
        preset: Selected preset name (``percentile`` or ``svt``).
        args: Validated filter constructor args for the preset's stock NVFLARE filter class.
    """

    preset: str
    args: dict[str, float]


@dataclass(frozen=True)
class _Param:
    var: str
    arg: str
    default: float
    is_valid: Callable[[float], bool]
    bounds: str


_PRESET_PARAMS: dict[str, tuple[_Param, ...]] = {
    "percentile": (
        _Param("FL_SITE_PRIVACY_PERCENTILE", "percentile", 10, lambda v: 0 <= v <= 100, "a number in [0, 100]"),
        _Param("FL_SITE_PRIVACY_GAMMA", "gamma", 0.01, lambda v: v > 0, "a number > 0"),
    ),
    "svt": (
        _Param("FL_SITE_PRIVACY_SVT_FRACTION", "fraction", 0.1, lambda v: 0 < v <= 1, "a number in (0, 1]"),
        _Param("FL_SITE_PRIVACY_SVT_EPSILON", "epsilon", 0.1, lambda v: v > 0, "a number > 0"),
        _Param("FL_SITE_PRIVACY_SVT_NOISE_VAR", "noise_var", 0.1, lambda v: v > 0, "a number > 0"),
        _Param("FL_SITE_PRIVACY_SVT_GAMMA", "gamma", 1e-5, lambda v: v > 0, "a number > 0"),
        _Param("FL_SITE_PRIVACY_SVT_TAU", "tau", 1e-6, lambda v: v > 0, "a number > 0"),
    ),
}

_FILTER_PATHS = {"percentile": PERCENTILE_FILTER_PATH, "svt": SVT_FILTER_PATH}


def _get(env: Mapping[str, str], var: str) -> str | None:
    """Reads an env var, treating empty/whitespace-only values as unset.

    Commented-out example files and compose ``${VAR:-}`` interpolation both surface as ``""``,
    which must behave exactly like an absent variable.
    """
    value = env.get(var, "").strip()
    return value or None


def _parse_value(param: _Param, raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise SitePolicyError(f"{param.var}={raw!r} is not a number (expected {param.bounds})") from None
    if not param.is_valid(value):
        raise SitePolicyError(f"{param.var}={raw!r} is out of bounds (expected {param.bounds})")
    # Integral values (e.g. percentile) are emitted as ints so the JSON matches the filters' documented args.
    return int(value) if value.is_integer() else value


def parse_env(env: Mapping[str, str]) -> SitePolicy | None:
    """Parses and validates ``FL_SITE_PRIVACY_*`` env vars into a :class:`SitePolicy`.

    Args:
        env: Environment mapping (typically ``os.environ``).

    Returns:
        The validated policy, or ``None`` when no policy is configured.

    Raises:
        SitePolicyError: On an unknown preset, an out-of-bounds/non-numeric parameter, a parameter
            belonging to a preset that is not selected, or parameters set with no preset selected.
    """
    policy_raw = _get(env, _POLICY_VAR)

    if policy_raw is None:
        stray = [p.var for params in _PRESET_PARAMS.values() for p in params if _get(env, p.var) is not None]
        if stray:
            raise SitePolicyError(
                f"{', '.join(sorted(stray))} set but {_POLICY_VAR} is not — refusing to guess a policy; "
                f"set {_POLICY_VAR} or unset the parameter(s)"
            )
        return None

    preset = policy_raw.lower()
    if preset not in _PRESET_PARAMS:
        known = ", ".join(sorted(_PRESET_PARAMS))
        raise SitePolicyError(f"{_POLICY_VAR}={policy_raw!r} is not a known preset (expected one of: {known})")

    foreign = [
        p.var
        for other, params in _PRESET_PARAMS.items()
        if other != preset
        for p in params
        if _get(env, p.var) is not None
    ]
    if foreign:
        raise SitePolicyError(
            f"{', '.join(sorted(foreign))} set but {_POLICY_VAR}={preset!r} — these parameters belong to a "
            f"different preset; unset them or change {_POLICY_VAR}"
        )

    args: dict[str, float] = {}
    for param in _PRESET_PARAMS[preset]:
        raw = _get(env, param.var)
        args[param.arg] = param.default if raw is None else _parse_value(param, raw)
    return SitePolicy(preset=preset, args=args)


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
                "task_result_filters": [{"path": _FILTER_PATHS[policy.preset], "args": dict(policy.args)}],
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
        args_str = ", ".join(f"{k}={v}" for k, v in policy.args.items())
        print(
            f"[site-privacy] site privacy policy ACTIVE: {policy.preset} ({args_str}) — {verb} {parsed.out_path} "
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
