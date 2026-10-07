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

"""Render the NVFLARE site privacy policy (``local/privacy.json``) from the trust's governance config.

Run by the fl-client at container start (``python -m flip.nvflare.site_policy /app/local/privacy.json``)
so each trust can enforce its own update-privacy filter through its kit env file, independently of
whatever ``task_result_filters`` the submitted job carries: NVFLARE applies site scope filters *before*
job filters and never lets a job opt out (``nvflare/apis/utils/task_utils.py::apply_filters``).

Two sources, and a trust uses one of them (FLIP#1259):

1. The ``[fl_privacy.nvflare]`` section of the trust governance document — the same document
   ``data-access-api`` reads for its own sections, so a trust states every runtime control in one
   place. The fl-client never mounts that whole document: researcher code runs in the client as the
   same user, and would read every access rule. An init step runs ``--extract`` over it and the
   client reads only the extracted ``[fl_privacy]`` table, at ``ACCESS_POLICY_FILE``.
2. The ``FL_SITE_PRIVACY_*`` environment variables — the original #851 interface.

Both set is an error. Letting either win let the document weaken the kit's filter while every piece
of documentation said the document could only tighten it; two descriptions of one filter are refused,
not ranked.

The section is nested under the backend because ``percentile``/``gamma`` are NVFLARE's
``PercentilePrivacy`` parameters; Flower's knobs (#852) will differ. ``[fl_privacy.flower]`` is
refused until something on Flower enforces it, and ``--check --fl-backend flower`` fails a
configuration that would be silently ignored there.

The rendered document defines exactly one scope, set as ``default_scope`` — FLIP jobs never carry a
``scope`` meta key, so every job lands in it, and any other scope name is rejected at deploy time.
The stock NVFLARE filter class is used deliberately: unlike FLIP's app-level subclass it has no
``off`` switch, so an app config cannot disable the site filter.

Stdlib-only on purpose — it must run before NVFLARE starts and never fail on framework imports, and
``check-governance`` runs it on the trust host with a bare interpreter. That constraint is why the
governance document is TOML (stdlib ``tomllib``) rather than YAML.
Validation is strict because stock ``PercentilePrivacy`` fails *open* (silently forwards the update
unfiltered) when ``gamma <= 0`` or ``percentile`` is outside ``[0, 100]``; a mis-set policy must stop
the container, not run unprotected. An unrecognised ``FL_SITE_PRIVACY_*`` name or section key is
rejected for the same reason: a typo'd parameter would otherwise leave the weaker default silently
in force. When no policy is configured, any previously rendered file is removed — the configuration
is the single source of truth, and the target lives on a persistent bind mount.
"""

import argparse
import json
import math
import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

SCOPE_NAME = "site_default"
PERCENTILE_FILTER_PATH = "nvflare.app_common.filters.percentile_privacy.PercentilePrivacy"
_VAR_PREFIX = "FL_SITE_PRIVACY_"
_POLICY_VAR = f"{_VAR_PREFIX}POLICY"
_PERCENTILE_VAR = f"{_VAR_PREFIX}PERCENTILE"
_GAMMA_VAR = f"{_VAR_PREFIX}GAMMA"
_KNOWN_VARS = (_POLICY_VAR, _PERCENTILE_VAR, _GAMMA_VAR)

# The trust governance document (FLIP#1259). Optional, and the same file data-access-api
# reads for its [disclosure]/[access] sections — one file per trust, one section per
# concern. Only [fl_privacy] is read here.
_GOVERNANCE_FILE_VAR = "ACCESS_POLICY_FILE"

# Top-level tables the governance format defines. Mirrors KNOWN_SECTIONS in
# data_access_api/policy/model.py — the two must be kept in step, since each plane
# validates its own section and must tolerate the other's.
_KNOWN_SECTIONS = ("disclosure", "access", "fl_privacy")

# Keys accepted inside [fl_privacy.nvflare]. Same strictness as the env-var path: an unknown
# key is an error, because a typo'd parameter would leave the weaker default in force.
_KNOWN_FL_PRIVACY_KEYS = ("policy", "percentile", "gamma")

# The one backend whose client enforces a section today. Backends FLIP runs but cannot yet
# enforce are named so their section is refused with the reason rather than as a typo.
_ENFORCING_BACKEND = "nvflare"
_UNENFORCED_BACKENDS = ("flower",)
_SECTION = f"[fl_privacy.{_ENFORCING_BACKEND}]"


class SitePolicyError(ValueError):
    """Invalid site privacy configuration (``FL_SITE_PRIVACY_*`` or the governance document)."""


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
        SitePolicyError: On an unrecognised ``FL_SITE_PRIVACY_*`` name, an unknown policy, an invalid
            parameter, or parameters set without a policy.
    """
    # Checked before the policy branch: once a policy is selected a typo'd parameter name (e.g.
    # FL_SITE_PRIVACY_PERCENTIL) would otherwise be ignored and the site would silently run the
    # default — a weaker filter than the operator asked for.
    unknown = sorted(
        var for var in env if var.startswith(_VAR_PREFIX) and var not in _KNOWN_VARS and _get(env, var) is not None
    )
    if unknown:
        raise SitePolicyError(
            f"unrecognised site privacy variable(s) {', '.join(unknown)} — expected only "
            f"{', '.join(_KNOWN_VARS)}; refusing to run a policy that ignores them"
        )

    policy_raw = _get(env, _POLICY_VAR)

    if policy_raw is None:
        stray = [var for var in (_PERCENTILE_VAR, _GAMMA_VAR) if _get(env, var) is not None]
        if stray:
            raise SitePolicyError(
                f"{', '.join(sorted(stray))} set but {_POLICY_VAR} is not — refusing to guess a policy; "
                f"set {_POLICY_VAR} or unset the parameter(s)"
            )
        return None

    if policy_raw.lower() != "percentile":
        raise SitePolicyError(f"{_POLICY_VAR}={policy_raw!r} is not a known policy (expected: percentile)")

    percentile_raw = _get(env, _PERCENTILE_VAR)
    gamma_raw = _get(env, _GAMMA_VAR)
    percentile = 10 if percentile_raw is None else _parse_value(_PERCENTILE_VAR, percentile_raw, minimum=0, maximum=100)
    gamma = 0.01 if gamma_raw is None else _parse_value(_GAMMA_VAR, gamma_raw, minimum=0, minimum_exclusive=True)
    return SitePolicy(percentile=percentile, gamma=gamma)


def _read_governance_document(path_raw: str) -> dict:
    """Reads and parses the governance document, checking only its top-level shape."""
    path = Path(path_raw)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        # A configured-but-unreadable document is the dangerous case: the operator believes
        # a filter is in force. Refuse to start rather than fall back to the env vars.
        raise SitePolicyError(f"{_GOVERNANCE_FILE_VAR}={path_raw!r} could not be read: {e}") from None

    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise SitePolicyError(f"{_GOVERNANCE_FILE_VAR}={path_raw!r} is not valid TOML: {e}") from None

    if not document:
        raise SitePolicyError(
            f"{_GOVERNANCE_FILE_VAR}={path_raw!r} configures nothing (empty, or every line commented out) — "
            f"refusing it, since a truncated file would otherwise drop every control; unset {_GOVERNANCE_FILE_VAR} "
            f"to run without one"
        )

    unknown_sections = sorted(k for k in document if k not in _KNOWN_SECTIONS)
    if unknown_sections:
        raise SitePolicyError(
            f"unrecognised section(s) {', '.join(repr(s) for s in unknown_sections)} in {path_raw} — "
            f"expected only {', '.join(_KNOWN_SECTIONS)}; refusing to load a policy that ignores them"
        )
    return document


def _nvflare_section(document: dict, path_raw: str) -> dict | None:
    """Returns the ``[fl_privacy.nvflare]`` table, or ``None`` when the document has none."""
    fl_privacy = document.get("fl_privacy")
    if fl_privacy is None:
        return None
    if not isinstance(fl_privacy, dict):
        raise SitePolicyError(f"[fl_privacy] in {path_raw} must be a table, got {type(fl_privacy).__name__}")

    flat = sorted(k for k in fl_privacy if k in _KNOWN_FL_PRIVACY_KEYS)
    if flat:
        raise SitePolicyError(
            f"{', '.join(flat)} sit directly under [fl_privacy] in {path_raw} — the section is per backend "
            f"now: move them under {_SECTION}"
        )
    unenforced = sorted(k for k in fl_privacy if k in _UNENFORCED_BACKENDS)
    if unenforced:
        raise SitePolicyError(
            f"[fl_privacy.{unenforced[0]}] in {path_raw} is not supported: nothing enforces it on {unenforced[0]} "
            f"yet (FLIP#852), so it would be ignored while reading as configured — remove it"
        )
    unknown = sorted(k for k in fl_privacy if k != _ENFORCING_BACKEND)
    if unknown:
        raise SitePolicyError(
            f"unrecognised table(s) {', '.join(repr(k) for k in unknown)} under [fl_privacy] in {path_raw} — "
            f"expected only {_SECTION}"
        )

    section = fl_privacy.get(_ENFORCING_BACKEND)
    if section is None:
        return None
    if not isinstance(section, dict):
        raise SitePolicyError(f"{_SECTION} in {path_raw} must be a table, got {type(section).__name__}")
    return section


def _parse_section(section: dict, path_raw: str) -> SitePolicy | None:
    unknown_keys = sorted(k for k in section if k not in _KNOWN_FL_PRIVACY_KEYS)
    if unknown_keys:
        raise SitePolicyError(
            f"unrecognised key(s) {', '.join(repr(k) for k in unknown_keys)} in {_SECTION} of {path_raw} — "
            f"expected only {', '.join(_KNOWN_FL_PRIVACY_KEYS)}; refusing to run a policy that ignores them"
        )

    policy_name = section.get("policy")
    if policy_name is None:
        stray = sorted(k for k in ("percentile", "gamma") if k in section)
        if stray:
            raise SitePolicyError(
                f"{_SECTION} in {path_raw} sets {', '.join(stray)} but no 'policy' — "
                f"refusing to guess a policy; set policy or remove the parameter(s)"
            )
        return None
    if not isinstance(policy_name, str) or policy_name.lower() != "percentile":
        raise SitePolicyError(
            f"{_SECTION} policy={policy_name!r} in {path_raw} is not a known policy (expected: percentile)"
        )

    # Reuse the env path's numeric validation so both sources enforce identical bounds —
    # the fail-open cases (gamma <= 0, percentile outside [0, 100]) must be rejected the
    # same way regardless of where the operator wrote them.
    percentile_raw = section.get("percentile")
    gamma_raw = section.get("gamma")
    percentile = (
        10
        if percentile_raw is None
        else _parse_value(f"{_SECTION} percentile in {path_raw}", str(percentile_raw), minimum=0, maximum=100)
    )
    gamma = (
        0.01
        if gamma_raw is None
        else _parse_value(f"{_SECTION} gamma in {path_raw}", str(gamma_raw), minimum=0, minimum_exclusive=True)
    )
    return SitePolicy(percentile=percentile, gamma=gamma)


def parse_governance_file(path_raw: str) -> SitePolicy | None:
    """Parses the ``[fl_privacy.nvflare]`` section of the trust governance document (FLIP#1259).

    Reads either the whole document or the ``[fl_privacy]`` table ``--extract`` writes from it.

    Args:
        path_raw: Path to the governance TOML document.

    Returns:
        The validated policy, or ``None`` when the document defines no ``[fl_privacy.nvflare]``
        section — a trust may use the document for disclosure rules only.

    Raises:
        SitePolicyError: If the file cannot be read, is not valid TOML, configures nothing, carries
            an unknown section, table or key, a section for a backend nothing enforces, or
            describes an invalid filter.
    """
    document = _read_governance_document(path_raw)
    section = _nvflare_section(document, path_raw)
    if section is None:
        return None
    return _parse_section(section, path_raw)


def resolve_policy(env: Mapping[str, str]) -> tuple[SitePolicy | None, str]:
    """Resolves the effective site privacy policy across both configuration sources.

    Both are validated whichever is used, so a broken env var cannot hide behind a valid
    document. Configuring the filter in both is an error.

    Args:
        env: Environment mapping (typically ``os.environ``).

    Returns:
        A ``(policy, source)`` pair. ``policy`` is ``None`` when neither source configures
        one; ``source`` is a short human-readable label for the log line.

    Raises:
        SitePolicyError: On invalid configuration in either source, or a policy in both.
    """
    env_policy = parse_env(env)

    governance_path = _get(env, _GOVERNANCE_FILE_VAR)
    if governance_path is None:
        return env_policy, "FL_SITE_PRIVACY_* env"

    file_policy = parse_governance_file(governance_path)
    if file_policy is None:
        return env_policy, "FL_SITE_PRIVACY_* env"

    if env_policy is not None:
        raise SitePolicyError(
            f"the site privacy filter is configured twice — both {_SECTION} in the governance document and "
            f"{_POLICY_VAR} in the kit; set it in exactly one of them (a document that could override the kit "
            f"could also weaken it)"
        )
    return file_policy, f"{_GOVERNANCE_FILE_VAR} {_SECTION}"


def extract(source_raw: str, dest: Path) -> SitePolicy | None:
    """Writes the governance document's ``[fl_privacy]`` table, and nothing else, to ``dest``.

    The fl-client's init step: it reads the whole document, the client mounts only what this
    writes. The section is validated first, so a document the client would refuse is refused
    here, and nothing is written. The table is written as validated values (defaults filled
    in), always under an ``[fl_privacy]`` header, so a document without the section extracts
    to a file that parses to "no policy" rather than to an empty one.

    Args:
        source_raw: Path to the whole governance document.
        dest: Where the fl-client will read the extract from.

    Returns:
        The validated policy, or ``None`` when the document carries no ``[fl_privacy.nvflare]``.

    Raises:
        SitePolicyError: On an invalid document.
        OSError: If ``dest`` cannot be written.
    """
    policy = parse_governance_file(source_raw)
    lines = [
        "# The fl-client's section of the trust governance document, extracted at container start",
        "# by `python -m flip.nvflare.site_policy --extract`. The rest of the document stays out of the",
        "# client, where researcher code runs. Edit the governance document, not this file.",
        "[fl_privacy]",
    ]
    if policy is not None:
        lines += [
            "",
            _SECTION,
            'policy = "percentile"',
            f"percentile = {policy.percentile!r}",
            f"gamma = {policy.gamma!r}",
        ]
    tmp_path = dest.with_name(dest.name + ".tmp")
    tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp_path, dest)
    return policy


def check_backend(env: Mapping[str, str], backend: str) -> None:
    """Refuses a site privacy configuration the trust's FL backend would not enforce.

    Raises:
        SitePolicyError: If ``backend`` is not NVFLARE and either source configures a filter.
    """
    if backend == _ENFORCING_BACKEND:
        return
    policy, source = resolve_policy(env)
    if policy is not None:
        raise SitePolicyError(
            f"a site privacy filter is configured ({source}) but this trust runs FL_BACKEND={backend}, and "
            f"nothing on {backend} enforces it — remove it (Flower's site privacy is FLIP#852)"
        )


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


def render(env: Mapping[str, str], out_path: Path, check_only: bool = False) -> tuple[str, SitePolicy | None, str]:
    """Renders (or removes) the site privacy policy file according to the configuration.

    Args:
        env: Environment mapping to read ``ACCESS_POLICY_FILE`` and ``FL_SITE_PRIVACY_*`` from.
        out_path: Target ``privacy.json`` path.
        check_only: When ``True``, validate and report without touching the filesystem.

    Returns:
        A ``(status, policy, source)`` triple. ``status`` is ``"written"`` (policy configured),
        ``"removed"`` (no policy, stale file found), or ``"absent"`` (no policy, no file).
        The resolved policy and its source are returned rather than re-resolved by the
        caller, so the precedence warning is emitted exactly once per run.

    Raises:
        SitePolicyError: On invalid configuration in either source.
    """
    policy, source = resolve_policy(env)

    if policy is None:
        if out_path.exists():
            if not check_only:
                out_path.unlink()
            return "removed", None, source
        return "absent", None, source

    if not check_only:
        tmp_path = out_path.with_name(out_path.name + ".tmp")
        tmp_path.write_text(json.dumps(build_policy_json(policy), indent=2) + "\n")
        os.replace(tmp_path, out_path)
    return "written", policy, source


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """CLI entry point.

    ``python -m flip.nvflare.site_policy [--check [--fl-backend NAME]] <out_path>`` renders (or, with
    ``--check``, validates) ``privacy.json``. ``python -m flip.nvflare.site_policy --extract SRC DEST``
    is the fl-client's init step: it writes the governance document's ``[fl_privacy]`` table alone.

    Returns:
        ``0`` on success (policy written, removed, extracted or not configured), ``1`` on invalid
        configuration or I/O failure — callers must treat ``1`` as fatal (fail closed).
    """
    parser = argparse.ArgumentParser(prog="flip.nvflare.site_policy", description=__doc__)
    parser.add_argument(
        "out_path", type=Path, nargs="?", help="Target privacy.json path (e.g. /app/local/privacy.json)"
    )
    parser.add_argument("--check", action="store_true", help="Validate the configuration without writing")
    parser.add_argument(
        "--fl-backend",
        default=_ENFORCING_BACKEND,
        help="With --check: the trust's FL_BACKEND; a filter configured for a backend that does not enforce it fails",
    )
    parser.add_argument(
        "--extract",
        nargs=2,
        metavar=("SRC", "DEST"),
        help="Write only the [fl_privacy] table of the governance document SRC to DEST (the fl-client's init step)",
    )
    parsed = parser.parse_args(argv)
    if env is None:
        env = os.environ

    if parsed.extract:
        source_raw, dest_raw = parsed.extract
        try:
            policy = extract(source_raw, Path(dest_raw))
        except (SitePolicyError, OSError) as e:
            print(f"[site-privacy] FATAL: {e}", file=sys.stderr)
            return 1
        state = f"{_SECTION} present" if policy is not None else f"no {_SECTION}"
        print(f"[site-privacy] extracted the fl-client's section of {source_raw} to {dest_raw} ({state})")
        return 0

    if parsed.out_path is None:
        parser.error("out_path is required unless --extract is given")

    backend = parsed.fl_backend.strip().lower()
    try:
        if parsed.check and backend != _ENFORCING_BACKEND:
            # Validates both sources too: an invalid document fails on every backend.
            check_backend(env, backend)
            print(f"[site-privacy] no site privacy policy configured — not applicable on FL_BACKEND={backend}")
            return 0
        status, policy, source = render(env, parsed.out_path, check_only=parsed.check)
    except (SitePolicyError, OSError) as e:
        print(f"[site-privacy] FATAL: {e}", file=sys.stderr)
        return 1

    verb = "validated (--check, not written)" if parsed.check else "wrote"
    if status == "written":
        assert policy is not None  # "written" implies a configured policy
        print(
            f"[site-privacy] site privacy policy ACTIVE: percentile "
            f"(percentile={policy.percentile}, gamma={policy.gamma}) from {source} — {verb} {parsed.out_path} "
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
