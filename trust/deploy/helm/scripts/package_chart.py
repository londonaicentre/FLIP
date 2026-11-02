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
"""Stamp the release into the chart and publish it to GHCR as an OCI artifact (FLIP#1646).

A trust upgrade needs a deployment definition it can name. Today the only one is "whatever is in
the checkout", which is why the upgrade agent has to clone the repo, verify the tree is clean and
verify it sits on a release tag before it dares deploy (FLIP#1640). A published chart replaces all
of that with one immutable reference::

    oci://ghcr.io/londonaicentre/charts/flip-trust:0.13.0

Stamping is three edits, and every one of them is load-bearing:

* ``Chart.yaml`` ``version`` -- the chart's own SemVer, which is what the OCI tag becomes. It
  tracks the platform release exactly, so there is one number for an operator to reason about
  rather than a chart version that drifts from the release it deploys.
* ``Chart.yaml`` ``appVersion`` -- the release tag (``v0.13.0``), i.e. the thing this chart
  deploys, named the way the git tag and the GHCR image tags name it.
* ``values.yaml`` ``global.image.tag`` -- the pin every FLIP-built service already honours. The
  chart's checked-in default is empty, which means "each service's own mutable ``stag`` tag". A
  chart published with that default would be a release-numbered artifact that deploys whatever
  ``stag`` happens to point at today -- versioned in name only. Stamping it is what makes the
  published chart immutable in substance as well as in tag.

The stamping functions are pure text in, text out, so the whole contract is testable without a
cluster, a registry or helm. Every one of them fails closed: a version that is not SemVer, or a
file that does not contain exactly the one line meant to be rewritten, raises rather than
publishing a chart that is quietly wrong.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

CHART_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = "oci://ghcr.io/londonaicentre/charts"

# X.Y.Z with an optional leading `v` and an optional pre-release suffix, anchored. Helm requires
# SemVer for a chart version, and the registry would reject a bad one -- but only after the
# release workflow had already tagged and built everything, so it is checked here instead.
_VERSION = re.compile(r"^v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$")

# The `version:`/`appVersion:` keys at the top level of Chart.yaml. Anchored to the line start so
# a `version:` nested under `dependencies:` or `maintainers:` can never be the one rewritten.
_CHART_VERSION = re.compile(r"^version:.*$", re.MULTILINE)
_CHART_APP_VERSION = re.compile(r"^appVersion:.*$", re.MULTILINE)

# `global.image.tag` in values.yaml: the `tag:` indented four spaces that follows the `global:`
# line and its `image:` child. Every other `tag:` in the file sits under a service and is indented
# differently, but relying on indentation alone would be fragile -- so the search is bounded to
# the global block rather than run over the whole file.
_GLOBAL_BLOCK = re.compile(r"^global:\n(?: {2,}.*\n|\n)*", re.MULTILINE)
_GLOBAL_TAG = re.compile(r"^( {4}tag: ).*$", re.MULTILINE)


class StampError(RuntimeError):
    """A file did not contain exactly the one line this script exists to rewrite."""


def normalise_version(raw: str) -> tuple[str, str]:
    """``v0.13.0`` or ``0.13.0`` -> (chart version ``0.13.0``, release tag ``v0.13.0``).

    Helm chart versions are bare SemVer; the platform's git tag and GHCR image tags all carry the
    ``v``. Both forms are accepted as input so the caller does not have to care which one it has.
    """
    match = _VERSION.match(raw.strip())
    if not match:
        raise StampError(f"{raw!r} is not a release version (expected X.Y.Z or vX.Y.Z)")
    bare = match.group(1)
    return bare, f"v{bare}"


def _substitute_once(
    pattern: re.Pattern[str],
    replacement: Callable[[re.Match[str]], str],
    text: str,
    what: str,
) -> str:
    """Replace exactly one match, or raise.

    Zero matches means the file moved on without this script; two or more means the pattern is no
    longer specific enough to know which line is meant. Either way, publishing a chart stamped on
    a guess is worse than failing the release.
    """
    new_text, count = pattern.subn(replacement, text)
    if count != 1:
        raise StampError(f"expected exactly one {what} to rewrite, found {count}")
    return new_text


def stamp_chart(text: str, version: str, tag: str) -> str:
    """Set the chart's own version to the release, and appVersion to the release tag."""
    text = _substitute_once(_CHART_VERSION, lambda _m: f"version: {version}", text, "`version:` line")
    return _substitute_once(_CHART_APP_VERSION, lambda _m: f'appVersion: "{tag}"', text, "`appVersion:` line")


def stamp_values(text: str, tag: str) -> str:
    """Pin ``global.image.tag`` to the release tag.

    Without this the published chart's images float on `stag`, so two installs of the same chart
    version could run different code.
    """
    block_match = _GLOBAL_BLOCK.search(text)
    if block_match is None:
        raise StampError("values.yaml has no `global:` block")
    block = block_match.group(0)
    stamped = _substitute_once(_GLOBAL_TAG, lambda m: f'{m.group(1)}"{tag}"', block, "`global.image.tag` line")
    return text[: block_match.start()] + stamped + text[block_match.end() :]


def _run(argv: list[str], *, dry_run: bool) -> None:
    print("+ " + " ".join(argv), flush=True)
    if dry_run:
        return
    subprocess.run(argv, check=True)


def publish(
    *,
    raw_version: str,
    registry: str,
    chart_dir: Path = CHART_DIR,
    destination: Path | None = None,
    dry_run: bool = False,
    push: bool = True,
) -> Path:
    """Stamp, package and push the chart. Returns the packaged ``.tgz`` path."""
    version, tag = normalise_version(raw_version)

    chart_yaml = chart_dir / "Chart.yaml"
    values_yaml = chart_dir / "values.yaml"
    for path in (chart_yaml, values_yaml):
        if not path.is_file():
            raise StampError(f"{path} is missing -- is {chart_dir} a chart directory?")

    chart_yaml.write_text(stamp_chart(chart_yaml.read_text(), version, tag))
    values_yaml.write_text(stamp_values(values_yaml.read_text(), tag))
    print(f"stamped {chart_dir.name} {version} (appVersion {tag}, global.image.tag {tag})")

    if shutil.which("helm") is None and not dry_run:
        raise StampError("helm is not on PATH")

    out_dir = destination or chart_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    _run(["helm", "package", str(chart_dir), "--destination", str(out_dir)], dry_run=dry_run)

    package = out_dir / f"{chart_dir.name}-{version}.tgz"
    if push:
        # `helm push` needs a registry login; the workflow does that with the job's GITHUB_TOKEN.
        _run(["helm", "push", str(package), registry], dry_run=dry_run)
        print(f"published {registry}/{chart_dir.name}:{version}")
    return package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True, help="the release, as X.Y.Z or vX.Y.Z")
    parser.add_argument("--registry", default=DEFAULT_REGISTRY, help=f"OCI target (default: {DEFAULT_REGISTRY})")
    parser.add_argument("--chart-dir", type=Path, default=CHART_DIR)
    parser.add_argument("--destination", type=Path, default=None, help="where to write the .tgz")
    parser.add_argument("--no-push", action="store_true", help="stamp and package only")
    parser.add_argument("--dry-run", action="store_true", help="stamp, but only print helm commands")
    args = parser.parse_args(argv)

    try:
        publish(
            raw_version=args.version,
            registry=args.registry,
            chart_dir=args.chart_dir,
            destination=args.destination,
            dry_run=args.dry_run,
            push=not args.no_push,
        )
    except (StampError, subprocess.CalledProcessError) as exc:
        print(f"::error::chart publish failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
