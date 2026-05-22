# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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
"""Supply-chain cooldown gate for FLIP dependency lockfiles.

Recent npm/PyPI account-takeover attacks follow a consistent pattern: a
maintainer's credentials are compromised, a malicious release is published, the
community detects it, and the package is yanked — usually within hours. FLIP
therefore refuses to install any release younger than a cooldown window
(default 72 hours).

This script is the *detective* half of that policy. It parses every ``uv.lock``
and ``package-lock.json`` it is pointed at, independently queries PyPI and npm
for each pinned version's upstream release timestamp, and exits non-zero if any
locked version — direct or transitive — is younger than the cooldown window.

The release timestamp is read from the registry, never from the lockfile
itself: an attacker who can edit the lockfile could also forge its embedded
``upload-time`` fields, so the lockfile cannot be trusted to attest its own age.
"""

from __future__ import annotations

import argparse
import gzip
import http.client
import json
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_COOLDOWN_HOURS = 72.0
OVERRIDE_LABEL = "supply-chain-override"
OVERRIDE_COMMIT_MARKER = "[skip-cooldown]"

PYPI = "pypi"
NPM = "npm"

USER_AGENT = "flip-supply-chain-cooldown (+https://github.com/londonaicentre/FLIP)"
REQUEST_TIMEOUT_SECONDS = 20
HTTP_RETRIES = 4
MAX_WORKERS = 10

# Directories never worth descending into when discovering lockfiles: virtual
# environments and installed dependency trees contain their own lockfiles that
# are build artefacts, not first-party manifests.
EXCLUDED_DIRS = frozenset(
    {".git", ".venv", "venv", "node_modules", "build", "dist", ".tox", "__pycache__"}
)


class RegistryError(RuntimeError):
    """Raised when a registry could not be reached to verify a package.

    Treated as a *blocking* finding: if the cooldown cannot be verified, the
    gate fails closed rather than letting an unverified release through.
    """


@dataclass(frozen=True)
class LockedPackage:
    """A single registry-sourced dependency pinned in a lockfile."""

    ecosystem: str
    name: str
    version: str
    lockfile: str

    @property
    def cache_key(self) -> str:
        """Stable key for memoising this exact (ecosystem, name, version)."""
        return f"{self.ecosystem}:{self.name}:{self.version}"


@dataclass(frozen=True)
class Violation:
    """A locked package whose release is younger than the cooldown window."""

    package: LockedPackage
    released: datetime
    age_hours: float


# --------------------------------------------------------------------------- #
# Lockfile parsing
# --------------------------------------------------------------------------- #
def parse_uv_lock(path: Path) -> list[LockedPackage]:
    """Extract PyPI-registry-sourced packages from a uv lockfile.

    Editable, virtual, directory, git and other VCS/path sources are skipped:
    they are first-party or revision-pinned and not subject to the registry
    cooldown.

    Args:
        path: Path to a ``uv.lock`` file.

    Returns:
        The registry-sourced packages declared in the lockfile.
    """
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    packages: list[LockedPackage] = []
    for entry in data.get("package", []):
        name = entry.get("name")
        version = entry.get("version")
        if not name or not version:
            continue
        source = entry.get("source", {})
        registry = source.get("registry", "") if isinstance(source, dict) else ""
        if not registry or "pypi.org" not in registry:
            continue
        packages.append(LockedPackage(PYPI, str(name), str(version), str(path)))
    return packages


def parse_package_lock(path: Path) -> list[LockedPackage]:
    """Extract npm-registry-sourced packages from an npm lockfile.

    Supports ``lockfileVersion`` 2 and 3 (the ``packages`` map). The repository
    root, workspace symlinks (``link: true``) and non-registry sources (git,
    file, tarball URLs) are skipped.

    Args:
        path: Path to a ``package-lock.json`` file.

    Returns:
        The npm-registry-sourced packages declared in the lockfile.

    Raises:
        ValueError: If the lockfile predates ``lockfileVersion`` 2.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("packages")
    if not isinstance(entries, dict):
        raise ValueError(
            f"{path}: unsupported npm lockfile — expected a 'packages' object "
            "(lockfileVersion 2 or 3). Re-run `npm install` with npm >= 7."
        )
    packages: list[LockedPackage] = []
    for key, info in entries.items():
        if key == "" or not isinstance(info, dict):
            continue  # repository root
        if info.get("link"):
            continue  # workspace symlink — first-party
        version = info.get("version")
        resolved = info.get("resolved", "")
        if not version or not isinstance(resolved, str):
            continue
        if not resolved.startswith("https://registry.npmjs.org/"):
            continue  # git / file / private-registry source
        name = info.get("name") or _npm_name_from_key(key)
        if not name:
            continue
        packages.append(LockedPackage(NPM, str(name), str(version), str(path)))
    return packages


def _npm_name_from_key(key: str) -> str | None:
    """Derive a package name from an npm lockfile ``packages`` key.

    Keys are install paths such as ``node_modules/@scope/name`` or, for nested
    duplicates, ``node_modules/a/node_modules/b``. The package name is whatever
    follows the final ``node_modules/`` segment.
    """
    marker = "node_modules/"
    index = key.rfind(marker)
    if index == -1:
        return None
    return key[index + len(marker) :] or None


def discover_lockfiles(raw_paths: Iterable[str]) -> list[Path]:
    """Resolve input paths to a sorted, de-duplicated list of lockfiles.

    A path may be an explicit lockfile or a directory; directories are searched
    recursively for ``uv.lock`` and ``package-lock.json`` files, skipping
    virtual environments and installed dependency trees.
    """
    found: set[Path] = set()
    for raw in raw_paths:
        candidate = Path(raw)
        if candidate.is_file():
            found.add(candidate)
        elif candidate.is_dir():
            for filename in ("uv.lock", "package-lock.json"):
                for match in candidate.rglob(filename):
                    if any(part in EXCLUDED_DIRS for part in match.parts):
                        continue
                    found.add(match)
        else:
            print(f"::warning title=check-package-age::lockfile path not found: {raw}")
    return sorted(found)


# --------------------------------------------------------------------------- #
# Registry lookups
# --------------------------------------------------------------------------- #
def _parse_iso(value: str) -> datetime:
    """Parse a registry ISO-8601 timestamp into a UTC-aware datetime."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _http_get_json(url: str) -> dict | None:
    """Fetch and JSON-decode ``url``.

    Returns:
        The decoded body, or ``None`` if the registry returns 404 (the package
        or version is genuinely absent from the public registry).

    Raises:
        RegistryError: On persistent transport failures, so an unreachable
            registry fails the gate closed instead of silently passing.
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
    )
    last_error: Exception | None = None
    for attempt in range(HTTP_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    payload = gzip.decompress(payload)
            return json.loads(payload.decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            last_error = error
            if error.code < 500 and error.code != 429:
                break  # client errors other than rate-limiting will not recover
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            ConnectionError,
            TimeoutError,
            EOFError,
            gzip.BadGzipFile,
            json.JSONDecodeError,
        ) as error:
            last_error = error
        if attempt < HTTP_RETRIES - 1:
            time.sleep(2**attempt)
    raise RegistryError(f"could not reach registry for {url}: {last_error}")


def fetch_pypi_release_time(name: str, version: str) -> datetime | None:
    """Return the earliest upload time for a specific PyPI release.

    The earliest of all distribution files is used as the release timestamp —
    that is the moment the version first became installable.
    """
    url = (
        f"https://pypi.org/pypi/{urllib.parse.quote(name)}"
        f"/{urllib.parse.quote(version)}/json"
    )
    data = _http_get_json(url)
    if data is None:
        return None
    uploads = [
        _parse_iso(item["upload_time_iso_8601"])
        for item in data.get("urls", [])
        if item.get("upload_time_iso_8601")
    ]
    return min(uploads) if uploads else None


def fetch_npm_release_times(name: str) -> dict[str, str] | None:
    """Return the ``time`` map (version -> ISO timestamp) for an npm package.

    The full packument is required because npm only exposes per-version publish
    times on the unabbreviated document.
    """
    encoded = name.replace("/", "%2f")  # scoped packages: @scope/name
    data = _http_get_json(f"https://registry.npmjs.org/{encoded}")
    if data is None:
        return None
    times = data.get("time", {})
    return times if isinstance(times, dict) else {}


def resolve_release_times(
    packages: Sequence[LockedPackage], cache: dict[str, str]
) -> tuple[dict[str, datetime | None], dict[str, RegistryError]]:
    """Resolve every package's release time, using and updating ``cache``.

    Registry responses are fetched concurrently. PyPI is queried once per
    (name, version); npm is queried once per package name and reused across all
    locked versions of that name. Successful lookups are written back to
    ``cache`` — release timestamps are immutable, so the cache never expires.

    Returns:
        A ``(results, errors)`` pair. ``results`` maps a package cache key to
        its release datetime (or ``None`` when the registry has no record).
        ``errors`` maps a cache key to the failure that prevented verification.
    """
    results: dict[str, datetime | None] = {}
    errors: dict[str, RegistryError] = {}

    pypi_todo: list[LockedPackage] = []
    npm_names: set[str] = set()
    for package in packages:
        key = package.cache_key
        if key in results:
            continue
        cached = cache.get(key)
        if cached:
            try:
                results[key] = _parse_iso(cached)
                continue
            except ValueError:
                pass  # corrupt cache entry — fall through and re-fetch
        if package.ecosystem == PYPI:
            pypi_todo.append(package)
        else:
            npm_names.add(package.name)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        pypi_futures = {
            executor.submit(fetch_pypi_release_time, pkg.name, pkg.version): pkg
            for pkg in pypi_todo
        }
        for future in as_completed(pypi_futures):
            package = pypi_futures[future]
            try:
                results[package.cache_key] = future.result()
            except RegistryError as error:
                errors[package.cache_key] = error

    npm_times: dict[str, dict[str, str] | RegistryError | None] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        npm_futures = {
            executor.submit(fetch_npm_release_times, name): name for name in npm_names
        }
        for future in as_completed(npm_futures):
            name = npm_futures[future]
            try:
                npm_times[name] = future.result()
            except RegistryError as error:
                npm_times[name] = error

    for package in packages:
        if package.ecosystem != NPM:
            continue
        key = package.cache_key
        if key in results or key in errors:
            continue
        times = npm_times.get(package.name)
        if isinstance(times, RegistryError):
            errors[key] = times
        elif times is None:
            results[key] = None
        else:
            stamp = times.get(package.version)
            results[key] = _parse_iso(stamp) if stamp else None

    for key, released in results.items():
        if released is not None:
            cache[key] = released.isoformat()

    return results, errors


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def evaluate(
    packages: Sequence[LockedPackage],
    results: dict[str, datetime | None],
    errors: dict[str, RegistryError],
    now: datetime,
    cooldown_hours: float,
) -> tuple[list[Violation], list[tuple[LockedPackage, RegistryError]], list[LockedPackage]]:
    """Classify each package against the cooldown window.

    Returns:
        A ``(violations, unverified, skipped)`` triple. ``violations`` are
        packages released within the window; ``unverified`` could not be checked
        against their registry; ``skipped`` have no registry record (assumed
        first-party or private) and are not blocking.
    """
    cutoff = now - timedelta(hours=cooldown_hours)
    violations: list[Violation] = []
    unverified: list[tuple[LockedPackage, RegistryError]] = []
    skipped: list[LockedPackage] = []
    for package in packages:
        key = package.cache_key
        if key in errors:
            unverified.append((package, errors[key]))
            continue
        released = results.get(key)
        if released is None:
            skipped.append(package)
            continue
        if released > cutoff:
            age_hours = (now - released).total_seconds() / 3600.0
            violations.append(Violation(package, released, age_hours))
    return violations, unverified, skipped


# --------------------------------------------------------------------------- #
# Emergency override
# --------------------------------------------------------------------------- #
def resolve_override(label_active: bool, commit_messages: Iterable[str]) -> tuple[bool, str]:
    """Decide whether the cooldown gate is overridden for this run.

    The gate may be bypassed for a genuine same-day security patch, either via
    the ``supply-chain-override`` pull-request label or a ``[skip-cooldown]``
    marker in a commit message.

    Returns:
        An ``(active, reason)`` pair; ``reason`` is empty when not overridden.
    """
    if label_active:
        return True, f"the '{OVERRIDE_LABEL}' pull-request label"
    for message in commit_messages:
        if message and OVERRIDE_COMMIT_MARKER in message:
            return True, f"the '{OVERRIDE_COMMIT_MARKER}' marker in a commit message"
    return False, ""


def read_recent_commit_messages(limit: int = 30) -> list[str]:
    """Return recent commit messages, best-effort (empty if git is unavailable).

    On a shallow pull-request checkout only the merge commit is visible, so the
    ``[skip-cooldown]`` marker is best-effort; the label is the reliable path.
    """
    try:
        completed = subprocess.run(
            ["git", "log", f"-{limit}", "--format=%B%x00"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    return [chunk.strip() for chunk in completed.stdout.split("\0") if chunk.strip()]


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _parse_bool(value: str | None) -> bool:
    """Interpret a workflow-supplied string as a boolean."""
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def _format_age(hours: float) -> str:
    """Render an age in the most readable unit."""
    if hours < 48:
        return f"{hours:.1f} hours"
    return f"{hours / 24:.1f} days"


def report(
    violations: Sequence[Violation],
    unverified: Sequence[tuple[LockedPackage, RegistryError]],
    skipped: Sequence[LockedPackage],
    *,
    cooldown_hours: float,
    override: bool,
    override_reason: str,
) -> int:
    """Print findings as GitHub annotations and return the process exit code.

    With an active override, blocking findings are downgraded to warnings and
    the gate still passes (exit 0).
    """
    for package in skipped:
        print(
            f"::notice title=check-package-age::{package.name}@{package.version} "
            f"({package.ecosystem}) has no public registry record — skipped "
            "(assumed first-party or private)."
        )

    blocking = bool(violations) or bool(unverified)
    level = "warning" if override else "error"

    if violations:
        print(
            f"\n{len(violations)} dependency(ies) were published within the "
            f"{cooldown_hours:g}h supply-chain cooldown window:\n"
        )
        for violation in sorted(violations, key=lambda item: item.age_hours):
            package = violation.package
            print(
                f"::{level} file={package.lockfile},title=Supply-chain cooldown::"
                f"{package.name}@{package.version} ({package.ecosystem}) was "
                f"released {_format_age(violation.age_hours)} ago "
                f"({violation.released.isoformat()}) — inside the "
                f"{cooldown_hours:g}h cooldown. Pinned in {package.lockfile}."
            )

    if unverified:
        print(
            f"\n{len(unverified)} dependency(ies) could not be verified against "
            "their registry:\n"
        )
        for package, error in unverified:
            print(
                f"::{level} file={package.lockfile},title=Supply-chain cooldown::"
                f"Could not verify {package.name}@{package.version} "
                f"({package.ecosystem}): {error}. Pinned in {package.lockfile}."
            )

    if not blocking:
        print(
            f"\nAll dependencies are older than the {cooldown_hours:g}h "
            "supply-chain cooldown window. Gate passed."
        )
        return 0

    if override:
        print(
            f"\n::warning title=Supply-chain cooldown::Cooldown gate OVERRIDDEN via "
            f"{override_reason}. {len(violations)} fresh and {len(unverified)} "
            "unverified dependency(ies) were allowed through — this must be "
            "justified in the pull-request description."
        )
        return 0

    print(
        f"\nSupply-chain cooldown gate FAILED. FLIP waits {cooldown_hours:g}h "
        "before installing a release so that poisoned npm/PyPI packages can be "
        "yanked before they reach a build.\n"
        "Resolve this by one of:\n"
        "  - wait until the release clears the cooldown, then re-run CI;\n"
        "  - pin an older, already-vetted version in the manifest and re-lock;\n"
        f"  - for a genuine same-day security patch, add the '{OVERRIDE_LABEL}' "
        "label to the pull request (justify it in the description) and re-run "
        "this job.\n"
        'See CONTRIBUTING.md ("Dependency cooldown") for the full policy.'
    )
    return 1


# --------------------------------------------------------------------------- #
# Cache persistence
# --------------------------------------------------------------------------- #
def load_cache(path: str | None) -> dict[str, str]:
    """Load the registry-timestamp cache, tolerating a missing/corrupt file."""
    if not path:
        return {}
    cache_path = Path(path)
    if not cache_path.is_file():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, str)}


def save_cache(path: str | None, cache: dict[str, str]) -> None:
    """Persist the registry-timestamp cache, tolerating write failures."""
    if not path:
        return
    try:
        Path(path).write_text(json.dumps(cache, sort_keys=True), encoding="utf-8")
    except OSError as error:
        print(f"::warning title=check-package-age::could not write cache: {error}")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def _dedupe(packages: Iterable[LockedPackage]) -> list[LockedPackage]:
    """Collapse identical (ecosystem, name, version) tuples to a single entry."""
    seen: set[tuple[str, str, str]] = set()
    unique: list[LockedPackage] = []
    for package in packages:
        identity = (package.ecosystem, package.name, package.version)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(package)
    return unique


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Fail the build if a locked Python or JavaScript dependency was "
            "published within the supply-chain cooldown window."
        )
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Lockfile or directory paths to scan (directories are searched recursively).",
    )
    parser.add_argument(
        "--cooldown-hours",
        type=float,
        default=DEFAULT_COOLDOWN_HOURS,
        help="Minimum dependency release age, in hours (default: 72).",
    )
    parser.add_argument(
        "--cache-file",
        default=None,
        help="JSON file used to memoise registry lookups across runs.",
    )
    parser.add_argument(
        "--override-label",
        default="false",
        help="'true' when the supply-chain-override pull-request label is present.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the cooldown gate and return a process exit code."""
    args = parse_args(argv)
    now = datetime.now(timezone.utc)

    lockfiles = discover_lockfiles(args.paths)
    if not lockfiles:
        print(
            "::warning title=check-package-age::no uv.lock or package-lock.json "
            "files found for the given paths — nothing to check."
        )
        return 0

    print("Supply-chain cooldown check")
    print(f"  cooldown window : {args.cooldown_hours:g}h")
    print(f"  reference time  : {now.isoformat()}")
    print(f"  lockfiles       : {', '.join(str(path) for path in lockfiles)}")

    packages: list[LockedPackage] = []
    for lockfile in lockfiles:
        if lockfile.name == "uv.lock":
            packages.extend(parse_uv_lock(lockfile))
        elif lockfile.name == "package-lock.json":
            packages.extend(parse_package_lock(lockfile))
    packages = _dedupe(packages)
    pypi_count = sum(1 for pkg in packages if pkg.ecosystem == PYPI)
    npm_count = len(packages) - pypi_count
    print(f"  packages        : {len(packages)} ({pypi_count} PyPI, {npm_count} npm)\n")

    cache = load_cache(args.cache_file)
    results, errors = resolve_release_times(packages, cache)
    save_cache(args.cache_file, cache)

    violations, unverified, skipped = evaluate(
        packages, results, errors, now, args.cooldown_hours
    )
    override, override_reason = resolve_override(
        _parse_bool(args.override_label), read_recent_commit_messages()
    )
    return report(
        violations,
        unverified,
        skipped,
        cooldown_hours=args.cooldown_hours,
        override=override,
        override_reason=override_reason,
    )


if __name__ == "__main__":
    sys.exit(main())
