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
   A hub deployed by the CI Terraform apply runs the ``sha-<short7>`` build of its commit; when
   this checkout sits on that commit and carries its release tag, the release tag is targeted
   instead — it names the same code, and every image is published at it.
2. **Guards**, in this order. The tag must look like an image tag (``v<X.Y.Z>`` or
   ``sha-<short7>`` — never ``prod``/``stag``, which move under a site); a move to an older
   release (a release's own pre-releases included) needs ``--force``; for a release target the
   **checkout must be at that tag** — the compose files, Makefiles and XNAT stack that run the
   images come from this git checkout, not from the images, so a site on the v0.6.0 tree
   pulling v1.0.0 images is an untested pairing (and the verb itself may differ);
   ``--allow-checkout-drift`` overrides for a deliberate mismatch such as testing a branch, and
   ``sha-`` targets are never checked (there is no tag to check out for one); every image the
   site pulls must exist at that tag in the registry (``docker manifest inspect``, so a tag
   that was only ever built for *some* images — every ``sha-`` tag, since each image's CI
   build is path-filtered — is refused before the kit is rewritten, rather than failing
   half-way through ``compose pull`` or, worse, letting ``docker stack deploy`` stop a running
   XNAT task for an image that cannot be pulled); and last, the operator confirms the printed
   ``site vA → target vB`` unless ``--yes``.
3. **Pin.** ``DOCKER_TAG`` and ``DOCKER_FL_TAG`` in the kit's Hub-shared block are rewritten
   in place, so the kit always records what is installed. The Makefile then re-includes the
   kit in a sub-make and does the pull / recreate. ``--fl-tag`` pins ``DOCKER_FL_TAG`` apart
   from the rest (the FL images' CI builds are path-filtered like orthanc's, so a ``sha-``
   move usually needs it); a release moves everything to one tag and never does.

Usage:
    uv run --no-config scripts/site_upgrade.py plan --kit-file trust/.env.<CODE>.<env> \\
        [--tag vX.Y.Z] [--fl-tag vX.Y.Z] [--force] [--yes] [--hub-url URL] [--dry-run] \\
        [--allow-checkout-drift]

Exit codes (the Makefile's contract): 0 pinned (or, with ``--dry-run``, resolved and checked
without pinning); 2 no usable target — pass ``--tag`` (also a malformed ``--fl-tag``, or no hub
URL to ask); 3 refused downgrade (pass ``--force``); 4 not confirmed (pass ``--yes`` for a
scripted run); 5 an image is missing at the target tag, or the registry could not be asked
(the message says which); 6 the checkout is not at the target release
(``git fetch --tags origin && git checkout <tag>``, then re-run from the new checkout), or git
could not say which commit it is at.
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
EXIT_CHECKOUT_MISMATCH = 6

#: The FLIP checkout this script runs from — what the compose files, Makefiles and XNAT stack
#: come from. Not configurable: the verb is `make …` in that checkout.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_GIT_TIMEOUT_SECONDS = 10.0

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


def _release_order(tag: str) -> tuple | None:
    """A sort key for a release tag, semver precedence: ``v0.7.0-rc.1 < v0.7.0-rc.2 < v0.7.0``.

    A release sorts above its own pre-releases, and pre-release identifiers compare numerically
    when both are numbers (``rc.10`` > ``rc.2``). None for anything that is not a release tag.
    """
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)(?:[-.]([0-9A-Za-z.-]+))?$", tag)
    if not m:
        return None
    pre = m.group(4)
    if pre is None:
        pre_key: tuple = (1,)
    else:
        pre_key = (0, *((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), pre_key)


def is_downgrade(current: str, target: str) -> bool:
    """Whether ``current → target`` moves a site to an older release.

    Only a release→release move is decidable; shas carry no order, so any move involving one
    is not called a downgrade (the operator chose it explicitly, or the hub runs it). Moving a
    release back to one of its own pre-releases is a downgrade.
    """
    cur, tgt = _release_order(current), _release_order(target)
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


def site_images(kit: dict[str, str], tag: str, fl_tag: str | None = None) -> list[str]:
    """Every image reference the site would pull once the kit pins ``tag`` (FL client at ``fl_tag``)."""
    registry = kit.get("DOCKER_REGISTRY") or _DEFAULT_REGISTRY
    refs = [f"{registry}{name}:{(pin and kit.get(pin)) or tag}" for name, pin in _SITE_IMAGE_PINS.items()]
    fl_client = _FL_CLIENT_IMAGE.get(kit.get("FL_BACKEND") or "nvflare", _FL_CLIENT_IMAGE["nvflare"])
    return [*refs, f"{registry}{fl_client}:{fl_tag or tag}"]


class RegistryUnavailable(Exception):
    """The registry could not be asked whether an image exists (auth, network, rate limit)."""


#: What ``docker manifest inspect`` prints when the registry says the image or tag is absent —
#: the same list deploy/providers/AWS/scripts/resolve-image-tags.sh treats as "not published".
_ABSENT_MARKERS = (
    "manifest unknown",
    "manifest_unknown",
    "no such manifest",
    "not found",
    "name unknown",
    "name_unknown",
)


def manifest_exists(ref: str, timeout: float = _MANIFEST_TIMEOUT_SECONDS) -> bool:
    """``docker manifest inspect`` as an existence probe: the registry's answer, nothing pulled.

    Raises:
        RegistryUnavailable: The probe failed without the registry saying the image is absent —
            reporting that as "not published" would send the operator after a different tag
            when the fault is a login, the network or a rate limit.
    """
    try:
        result = subprocess.run(
            ["docker", "manifest", "inspect", ref], capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as e:
        raise RegistryUnavailable(f"{ref}: no answer within {timeout:.0f}s") from e
    except OSError as e:
        raise RegistryUnavailable(f"{ref}: could not run docker ({e})") from e
    if result.returncode == 0:
        return True
    output = f"{result.stdout}\n{result.stderr}"
    if any(marker in output.lower() for marker in _ABSENT_MARKERS):
        return False
    detail = output.strip() or "(no output)"
    raise RegistryUnavailable(f"{ref}: docker manifest inspect exited {result.returncode}: {detail}")


def missing_images(kit: dict[str, str], tag: str, fl_tag: str | None = None) -> list[str]:
    """The site's image references that the registry does not serve at ``tag`` / ``fl_tag``.

    Raises:
        RegistryUnavailable: Propagated from :func:`manifest_exists`.
    """
    return [ref for ref in site_images(kit, tag, fl_tag) if not manifest_exists(ref)]


class CheckoutUnknown(Exception):
    """This is a git checkout, but git could not say which commit or tags it is at."""


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """``git -C repo_root <args>``.

    Raises:
        CheckoutUnknown: git is not installed, timed out, or refused (e.g. "dubious ownership"
            when a checkout owned by another user is run under sudo) — with git's own message.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CheckoutUnknown(f"git {' '.join(args)}: {e}") from e
    if result.returncode != 0:
        raise CheckoutUnknown(f"git {' '.join(args)}: {(result.stderr or result.stdout).strip() or 'failed'}")
    return result


def _git(repo_root: Path, *args: str) -> str | None:
    """stdout of ``git -C repo_root <args>``, or None when the call fails (for display-only reads)."""
    try:
        return _run_git(repo_root, *args).stdout
    except CheckoutUnknown:
        return None


def checkout_tags(repo_root: Path = _REPO_ROOT) -> list[str] | None:
    """Every git tag on the checkout's HEAD (``[]`` when untagged), or None outside a git checkout.

    Reads the tags the checkout has *fetched*: a release that was cut after the last
    ``git fetch --tags`` is not on HEAD however the tree got there, which is the right answer —
    the fix the caller prints starts with the fetch.

    Raises:
        CheckoutUnknown: ``repo_root`` is a git checkout but git failed to answer. Only a tree
            with no ``.git`` at all (an unpacked archive) is "not a checkout".
    """
    if not (repo_root / ".git").exists():
        return None
    out = _run_git(repo_root, "tag", "--points-at", "HEAD").stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def release_for_sha(target: str, repo_root: Path = _REPO_ROOT) -> str | None:
    """The release tag this checkout carries when ``target`` is the ``sha-`` build of its HEAD.

    A hub deployed by the CI Terraform apply runs the ``sha-<short7>`` build of the release
    commit rather than ``:v<X.Y.Z>``. From a checkout at that commit's release tag the two name
    the same code, and only the release tag is published for every site image. None when the
    target is not a sha tag, the checkout is elsewhere, or git cannot say.
    """
    if not _SHA_TAG.match(target):
        return None
    try:
        tags = checkout_tags(repo_root)
        head = _run_git(repo_root, "rev-parse", "HEAD").stdout.strip() if tags else ""
    except CheckoutUnknown:
        return None
    if not tags or f"sha-{head[:7]}" != target:
        return None
    releases = sorted((t for t in tags if _RELEASE_TAG.match(t)), key=lambda t: _release_order(t) or ())
    return releases[-1] if releases else None


def describe_checkout(repo_root: Path = _REPO_ROOT) -> str:
    """Where the checkout sits, for the message — a tag, ``v0.6.0-12-gabc1234``, or a bare sha.

    Matched against ``v[0-9]*`` so only PLATFORM releases are named. The repo also carries
    component tags (``flip-utils-v0.5.0``) and one-off ones, and an unmatched ``git describe``
    picks whichever is nearest — telling an operator their tree is at "flip-utils-v0.5.0"
    when the release it is being compared against is a platform v0.6.0.
    """
    described = (_git(repo_root, "describe", "--tags", "--always", "--match", "v[0-9]*") or "").strip()
    return described or "<unknown>"


def write_tags(kit_file: Path, tag: str, fl_tag: str | None = None) -> None:
    """Pin ``DOCKER_TAG`` (and ``DOCKER_FL_TAG``, to ``fl_tag`` or the same) in the kit, kit kept 0600."""
    lines = kit_file.read_text().split("\n")
    trailing_newline = lines and lines[-1] == ""
    if trailing_newline:
        lines = lines[:-1]
    lines = tkl.upsert(lines, "DOCKER_TAG", tag)
    lines = tkl.upsert(lines, "DOCKER_FL_TAG", fl_tag or tag)
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

    fl_tag = args.fl_tag
    if fl_tag is not None and not is_image_tag(fl_tag):
        print(f"❌ FL_TAG={fl_tag!r} is not an image tag (vX.Y.Z or sha-<short7>).")
        return EXIT_NEEDS_TAG

    origin = "from the hub" if source == "hub" else "from TAG="
    if source == "hub":
        release = release_for_sha(target)
        if release:
            origin = f"the hub runs {target}, the build of this checkout's {release}"
            target = release
    fl_note = f", FL client {kit.get('DOCKER_FL_TAG', '') or '<unset>'} → {fl_tag}" if fl_tag else ""
    print(f"⬆️  site {current} → target {target} ({origin}{fl_note})")
    if target == current:
        print("   The kit already pins this tag — re-applying (the pull + recreate only touches what changed).")
    elif is_downgrade(current, target):
        if not args.force:
            print(f"❌ {current} → {target} is a downgrade. Re-run with FORCE=1 if that is intended.")
            return EXIT_DOWNGRADE
        print("   ⚠️  downgrade forced (FORCE=1) — XNAT database migrations are forward-only; restore from a dump")

    # The compose files, Makefiles and XNAT stack that run the target's images come from this
    # checkout, so a release target must be run from the tree tagged with it. Checked before the
    # registry: a stale tree is the cheaper thing to fix, and the verb itself may differ there.
    if _RELEASE_TAG.match(target):
        try:
            tags = checkout_tags()
            git_error = None
        except CheckoutUnknown as e:
            tags, git_error = None, str(e)
        if git_error is not None and not args.allow_checkout_drift:
            print(f"❌ git could not say which commit {_REPO_ROOT} is at, so it cannot be checked against {target}:")
            print(f"     {git_error}")
            print("   'dubious ownership' means the checkout belongs to another user than the one running this")
            print("   (typically under sudo) — mark it safe as that user, then re-run:")
            print(f"     git config --global --add safe.directory {_REPO_ROOT}")
            print("   Nothing changed. ALLOW_CHECKOUT_DRIFT=1 overrides.")
            return EXIT_CHECKOUT_MISMATCH
        if git_error is not None:
            print(
                f"   ⚠️  git could not say where this checkout is ({git_error}) — drift allowed (ALLOW_CHECKOUT_DRIFT=1)"
            )
        elif tags is None:
            print(f"   ⚠️  {_REPO_ROOT} is not a git checkout — cannot confirm its files match {target}; continuing")
        elif target in tags:
            print(f"   ✓ checkout is at {target}")
        elif args.allow_checkout_drift:
            print(f"   ⚠️  checkout is {describe_checkout()}, not {target} — drift allowed (ALLOW_CHECKOUT_DRIFT=1)")
        else:
            print(f"❌ This checkout is {describe_checkout()}, but the target is {target}.")
            print("   The compose files, Makefiles and XNAT stack that run the images come from the checkout,")
            print("   not from the images — move it to the release first, then re-run from the new checkout:")
            print(f"     git -C {_REPO_ROOT} fetch --tags origin && git -C {_REPO_ROOT} checkout {target}")
            print("   (your kit, FL kit and data directories are untracked and stay in place). Nothing changed.")
            print("   ALLOW_CHECKOUT_DRIFT=1 overrides, for a deliberate mismatch such as testing a branch.")
            return EXIT_CHECKOUT_MISMATCH

    if shutil.which("docker") is None:
        print("   ⚠️  docker CLI not found — skipping the registry check; the pull will report a missing image")
    else:
        try:
            missing = missing_images(kit, target, fl_tag)
        except RegistryUnavailable as e:
            print(f"❌ Could not ask the registry whether {target} is published:")
            print(f"     {e}")
            print("   That is a login, network or rate-limit fault, not a missing image: check `docker login`")
            print("   for the registry and this host's route to it, then re-run. Nothing changed.")
            return EXIT_MISSING_IMAGES
        if missing:
            print(f"❌ {target} is not published for every image this site runs:")
            for ref in missing:
                print(f"     {ref}")
            print("   A release tag (vX.Y.Z) builds every image; a sha- tag only carries the images that")
            print("   commit changed. Nothing changed — pick a tag that exists for all of them, or hold")
            print("   the odd one at its own build (FL_TAG=, or OMOP_DB_TAG / ORTHANC_TAG / XNAT_TAG in the kit).")
            return EXIT_MISSING_IMAGES
        print(f"   ✓ all {len(site_images(kit, target, fl_tag))} images are published")

    if args.dry_run:
        print("   (dry run — kit not modified)")
        return 0
    pins = f"DOCKER_TAG={target} DOCKER_FL_TAG={fl_tag}" if fl_tag else f"DOCKER_TAG=DOCKER_FL_TAG={target}"
    if not _confirm(f"   This pins {pins} in {kit_file}.", args.yes):
        print("❌ Not confirmed — nothing changed.")
        return EXIT_NOT_CONFIRMED

    write_tags(kit_file, target, fl_tag)
    print(f"✅ {kit_file.name}: {pins}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="resolve the target release, confirm, and pin the kit to it")
    p.add_argument("--kit-file", required=True, help="trust/.env.<CODE>.<env>")
    p.add_argument("--tag", default=None, help="release to move to (default: the build the hub runs)")
    p.add_argument("--fl-tag", default=None, help="pin the FL client apart from the rest (default: same as --tag)")
    p.add_argument("--hub-url", default=None, help="override the kit's CENTRAL_HUB_API_URL")
    p.add_argument("--force", action="store_true", help="allow a release downgrade")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.add_argument("--dry-run", action="store_true", help="resolve and report only; never write the kit")
    p.add_argument(
        "--allow-checkout-drift",
        action="store_true",
        help="proceed when this checkout is not at the target release tag (testing a branch)",
    )
    args = parser.parse_args()
    sys.exit(plan(args))


if __name__ == "__main__":
    main()
