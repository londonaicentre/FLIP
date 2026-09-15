#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
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
"""Pick the release a trust site upgrades to, confirm it, and pin the kit to it (FLIP#1204).

The operator-side half of ``make upgrade-onprem-trust`` / ``make -C trust upgrade-trust``.
Everything that decides *what* to move to happens here, before any container is touched:

1. **Target.** ``--tag`` when given, otherwise the build the hub runs — read from
   ``GET <CENTRAL_HUB_API_URL>/health`` ``version``, the URL trust-api already polls, so no
   GitHub egress is needed from the trust host. Defaulting to the hub, not to the newest
   GitHub release, is deliberate: hub↔site coupling (the FL framework pin, cipher flag-days)
   means the hub's release is by construction the latest a site *can* run. A hub that does
   not report a pullable image tag (built before FLIP#1204, or unreachable) makes this
   script stop and ask for ``TAG=`` rather than guess.
2. **Guards.** The tag must look like an image tag (``v<X.Y.Z>`` or ``sha-<short7>`` — never
   ``prod``/``stag``, which move under a site); a release→release downgrade needs
   ``--force``; every image the site pulls must exist at that tag in the registry
   (``docker manifest inspect``, so a tag that was only ever built for *some* images — every
   ``sha-`` tag, since each image's CI build is path-filtered — is refused before the kit is
   rewritten, rather than failing half-way through ``compose pull`` or, worse, letting
   ``docker stack deploy`` stop a running XNAT task for an image that cannot be pulled); and
   the operator confirms the printed ``site vA → target vB`` unless ``--yes``.
3. **Pin.** ``DOCKER_TAG`` and ``DOCKER_FL_TAG`` in the kit's Hub-shared block are rewritten
   in place, so the kit always records what is installed. The Makefile then re-includes the
   kit in a sub-make and does the pull / recreate.

Usage:
    uv run --no-config scripts/site_upgrade.py plan --kit-file trust/.env.<CODE>.<env> \\
        [--tag vX.Y.Z] [--force] [--yes] [--hub-url URL] [--dry-run]

Exit codes (the Makefile's contract): 0 pinned; 2 needs ``--tag``; 3 refused downgrade
(pass ``--force``); 4 not confirmed (pass ``--yes`` for a scripted run); 5 an image is
missing at the target tag.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trust_kit_lib as tkl  # noqa: E402

#: A pullable, immutable image tag: a platform release (`v0.6.0`, pre-releases like
#: `v0.6.1-rc.1` included) or the CI short-sha tag. Deliberately NOT the floating
#: `prod` / `stag` / `latest`, and not a bare pyproject version (`0.6.0`).
_RELEASE_TAG = re.compile(r"^v\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.-]+)?$")
_SHA_TAG = re.compile(r"^sha-[0-9a-f]{7}$")

EXIT_NEEDS_TAG = 2
EXIT_DOWNGRADE = 3
EXIT_NOT_CONFIRMED = 4
EXIT_MISSING_IMAGES = 5

_HUB_TIMEOUT_SECONDS = 10.0
_MANIFEST_TIMEOUT_SECONDS = 60.0

#: Every repo-built image a compose site pulls (`trust/deploy/compose_trust.production.yml`,
#: `trust/xnat/docker-compose-stack.yml`, `trust/deploy/compose_trust.production.<backend>.yml`)
#: with the kit key that pins it away from ``DOCKER_TAG`` when set — the compose files'
#: ``${OMOP_DB_TAG:-${DOCKER_TAG}}`` / ``${ORTHANC_TAG:-…}`` opt-outs and the XNAT Makefile's
#: ``XNAT_TAG ?= ${DOCKER_TAG}``. A pinned image is checked at its pin, not at the target.
_DEFAULT_REGISTRY = "ghcr.io/londonaicentre/"
_SITE_IMAGE_PINS = {
    "trust-api": None,
    "imaging-api": None,
    "data-access-api": None,
    "omop-db": "OMOP_DB_TAG",
    "orthanc": "ORTHANC_TAG",
    "xnat-web": "XNAT_TAG",
    "xnat-db": "XNAT_TAG",
    "xnat-nginx": "XNAT_TAG",
}
_FL_CLIENT_IMAGE = {"nvflare": "flare-fl-client", "flower": "flower-supernode"}


class NeedsTag(Exception):
    """The target release cannot be resolved without an explicit ``--tag``."""


def is_image_tag(tag: str | None) -> bool:
    """True for ``v<X.Y.Z>[-pre]`` and ``sha-<short7>``; False for anything else."""
    return bool(tag) and (bool(_RELEASE_TAG.match(tag)) or bool(_SHA_TAG.match(tag)))  # type: ignore[arg-type]


def _release_numbers(tag: str) -> tuple[int, int, int] | None:
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)", tag)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def is_downgrade(current: str, target: str) -> bool:
    """Whether ``current → target`` moves a site to an older release.

    Only a release→release move is decidable; shas carry no order, so any move involving one
    is not called a downgrade (the operator chose it explicitly, or the hub runs it).
    """
    cur, tgt = _release_numbers(current), _release_numbers(target)
    if cur is None or tgt is None:
        return False
    return tgt < cur


def read_kit(kit_file: Path) -> dict[str, str]:
    """``KEY=value`` lines of the kit file (comments and blanks skipped; first ``=`` splits)."""
    out: dict[str, str] = {}
    for raw in kit_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value
    return out


def fetch_hub_version(hub_url: str, timeout: float = _HUB_TIMEOUT_SECONDS) -> str | None:
    """``version`` from the hub's ``GET /health``, or None when the body carries none.

    Raises:
        urllib.error.URLError: The hub is unreachable (the caller turns this into NeedsTag).
    """
    request = urllib.request.Request(f"{hub_url.rstrip('/')}/health", headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — the kit's own hub URL
        body = json.loads(response.read().decode() or "{}")
    version = body.get("version") if isinstance(body, dict) else None
    return version if isinstance(version, str) else None


def resolve_target(cli_tag: str | None, hub_url: str) -> tuple[str, str]:
    """The tag to move to and where it came from (``"cli"`` or ``"hub"``).

    Raises:
        NeedsTag: No usable tag — the CLI value is not an image tag, the hub is unreachable,
            or the hub reports something that is not a pullable tag.
    """
    if cli_tag is not None:
        if not is_image_tag(cli_tag):
            raise NeedsTag(
                f"TAG={cli_tag!r} is not an image tag. Use a release (vX.Y.Z) or a CI sha tag "
                "(sha-<short7>) — floating tags such as prod/stag move under a site."
            )
        return cli_tag, "cli"
    try:
        reported = fetch_hub_version(hub_url)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise NeedsTag(
            f"Could not read the hub's version from {hub_url}/health ({type(e).__name__}: {e}). "
            "Pass the release explicitly: TAG=vX.Y.Z"
        ) from e
    if not is_image_tag(reported):
        raise NeedsTag(
            f"The hub at {hub_url} reports version {reported!r}, which is not a pullable image tag "
            "(a hub built before FLIP#1204 reports its pyproject version, or nothing). "
            "Pass the release explicitly: TAG=vX.Y.Z"
        )
    assert reported is not None
    return reported, "hub"


def site_images(kit: dict[str, str], tag: str) -> list[str]:
    """Every image reference the site would pull once the kit pins ``tag``."""
    registry = kit.get("DOCKER_REGISTRY") or _DEFAULT_REGISTRY
    refs = [f"{registry}{name}:{(pin and kit.get(pin)) or tag}" for name, pin in _SITE_IMAGE_PINS.items()]
    fl_client = _FL_CLIENT_IMAGE.get(kit.get("FL_BACKEND") or "nvflare", _FL_CLIENT_IMAGE["nvflare"])
    return [*refs, f"{registry}{fl_client}:{tag}"]


def manifest_exists(ref: str, timeout: float = _MANIFEST_TIMEOUT_SECONDS) -> bool:
    """``docker manifest inspect`` as an existence probe: the registry's answer, nothing pulled."""
    try:
        result = subprocess.run(
            ["docker", "manifest", "inspect", ref], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def missing_images(kit: dict[str, str], tag: str) -> list[str]:
    """The site's image references that the registry does not serve at ``tag``."""
    return [ref for ref in site_images(kit, tag) if not manifest_exists(ref)]


def write_tags(kit_file: Path, tag: str) -> None:
    """Pin ``DOCKER_TAG`` and ``DOCKER_FL_TAG`` in the kit, touching nothing else, kit kept 0600."""
    lines = kit_file.read_text().split("\n")
    trailing_newline = lines and lines[-1] == ""
    if trailing_newline:
        lines = lines[:-1]
    for key in ("DOCKER_TAG", "DOCKER_FL_TAG"):
        lines = tkl.upsert(lines, key, tag)
    tkl.write_secure(kit_file, lines)


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not getattr(sys.stdin, "isatty", lambda: False)():
        print(f"{prompt}\n  (no terminal to confirm on — pass YES=1 for a scripted run)")
        return False
    answer = input(f"{prompt} Proceed? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def plan(args: argparse.Namespace) -> int:
    kit_file = Path(args.kit_file)
    kit = read_kit(kit_file)
    current = kit.get("DOCKER_TAG", "") or "<unset>"
    hub_url = args.hub_url or kit.get("CENTRAL_HUB_API_URL", "")
    if not args.tag and not hub_url:
        print("❌ No CENTRAL_HUB_API_URL in the kit and no --hub-url — cannot ask the hub. Pass TAG=vX.Y.Z")
        return EXIT_NEEDS_TAG

    try:
        target, source = resolve_target(args.tag, hub_url)
    except NeedsTag as e:
        print(f"❌ {e}")
        return EXIT_NEEDS_TAG

    origin = "from the hub" if source == "hub" else "from TAG="
    print(f"⬆️  site {current} → target {target} ({origin})")
    if target == current:
        print("   The kit already pins this tag — re-applying (the pull + recreate only touches what changed).")
    elif is_downgrade(current, target):
        if not args.force:
            print(f"❌ {current} → {target} is a downgrade. Re-run with FORCE=1 if that is intended.")
            return EXIT_DOWNGRADE
        print("   ⚠️  downgrade forced (FORCE=1) — XNAT database migrations are forward-only; restore from a dump")

    if shutil.which("docker") is None:
        print("   ⚠️  docker CLI not found — skipping the registry check; the pull will report a missing image")
    else:
        missing = missing_images(kit, target)
        if missing:
            print(f"❌ {target} is not published for every image this site runs (log in to the registry first):")
            for ref in missing:
                print(f"     {ref}")
            print("   A release tag (vX.Y.Z) builds every image; a sha- tag only carries the images that")
            print("   commit changed. Nothing changed — pick a tag that exists for all of them.")
            return EXIT_MISSING_IMAGES
        print(f"   ✓ all {len(site_images(kit, target))} images are published at {target}")

    if args.dry_run:
        print("   (dry run — kit not modified)")
        return 0
    if not _confirm(f"   This pins DOCKER_TAG and DOCKER_FL_TAG in {kit_file} to {target}.", args.yes):
        print("❌ Not confirmed — nothing changed.")
        return EXIT_NOT_CONFIRMED

    write_tags(kit_file, target)
    print(f"✅ {kit_file.name}: DOCKER_TAG=DOCKER_FL_TAG={target}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="resolve the target release, confirm, and pin the kit to it")
    p.add_argument("--kit-file", required=True, help="trust/.env.<CODE>.<env>")
    p.add_argument("--tag", default=None, help="release to move to (default: the build the hub runs)")
    p.add_argument("--hub-url", default=None, help="override the kit's CENTRAL_HUB_API_URL")
    p.add_argument("--force", action="store_true", help="allow a release downgrade")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.add_argument("--dry-run", action="store_true", help="resolve and report only; never write the kit")
    args = parser.parse_args()
    sys.exit(plan(args))


if __name__ == "__main__":
    main()
