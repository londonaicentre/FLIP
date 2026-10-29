#!/usr/bin/env python3
#
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

"""Mirror the FLIP images from GHCR into a trust's Snowflake image repository.

SPCS can only pull from an image repository in the account, and the epic's no-egress
rule means a running trust must not reach GHCR at all. So every image is copied once,
from an operator machine that does have egress, and the digest the account registry
returns is recorded in ``images.lock.json``. The renderer then references images by
digest: a tag in the account repository can be re-pushed, a digest cannot, so this is
what makes a deploy reproducible.

``--platform linux/amd64`` on every pull, always. SPCS runs amd64 only, and an arm64
workstation pulling by tag gets arm64 silently - the service then fails to start with an
exec-format error that names nothing useful.

Usage:
  python3 mirror_images.py --kit GSTT --env stag --images "trust-api orthanc"
  python3 mirror_images.py --kit GSTT --env stag --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from kitfile import kit_path, read_env_vars, resolve_env  # noqa: E402
from naming import Names, NamingError, fl_client_image  # noqa: E402

REPO_ROOT = HERE.parents[2]
LOCK_FILE = HERE / "images.lock.json"

#: ``<anything>@sha256:<64 hex>`` as printed by `docker inspect RepoDigests`.
DIGEST = re.compile(r"@(sha256:[0-9a-f]{64})$")


class MirrorError(RuntimeError):
    """An image could not be mirrored."""


def run(command: list[str], dry_run: bool, capture: bool = False) -> str:
    """Run a command, or print it under ``--dry-run``.

    No secret ever reaches this: the registry login is the operator's own docker
    credential store and the snow CLI connection, neither of which passes through here.
    """
    if dry_run:
        print(f"     {' '.join(command)}")
        return ""
    result = subprocess.run(command, text=True, capture_output=capture)
    if result.returncode != 0:
        detail = (result.stderr or "").strip() if capture else ""
        raise MirrorError(f"`{' '.join(command[:3])} ...` failed (exit {result.returncode}) {detail}")
    return (result.stdout or "").strip()


def registry_host(snow: str, connection: str, dry_run: bool) -> str:
    """The account's image-registry hostname, from the snow CLI.

    Asked rather than configured: it is derived from the account identifier and the
    region, and a hand-written copy in a kit file is one account migration away from
    pushing a trust's images into the wrong account.
    """
    if dry_run:
        return "<account>.registry.snowflakecomputing.com"
    out = run([snow, "spcs", "image-registry", "url", "--connection", connection], False, capture=True)
    host = out.splitlines()[-1].strip() if out else ""
    if not host:
        raise MirrorError("`snow spcs image-registry url` returned nothing - is the connection configured?")
    return host


def source_tag(kit: dict[str, str], repo: str) -> str:
    """The GHCR tag to mirror, honouring the per-image kit opt-outs.

    OMOP_DB_TAG / ORTHANC_TAG / XNAT_TAG hold one image back from the release pin, which
    matters here for exactly the reason it matters in Compose: those images' CI builds
    are path-filtered, so a sha- tag of a commit that did not touch them does not exist.
    """
    default = kit.get("DOCKER_TAG", "").strip() or "stag"
    if repo.startswith("xnat"):
        return kit.get("XNAT_TAG", "").strip() or default
    if repo == "orthanc":
        return kit.get("ORTHANC_TAG", "").strip() or default
    if repo.startswith("fl-client"):
        return kit.get("DOCKER_FL_TAG", "").strip() or default
    return default


def digest_of(image: str, dry_run: bool) -> str:
    """The digest the account registry assigned, read back from the local image."""
    if dry_run:
        return "sha256:" + "0" * 64
    out = run(["docker", "inspect", "--format", '{{join .RepoDigests "\\n"}}', image], False, capture=True)
    for line in out.splitlines():
        if line.startswith(image.split(":")[0]):
            match = DIGEST.search(line.strip())
            if match:
                return match.group(1)
    raise MirrorError(f"docker reported no digest for {image} after the push")


def mirror(
    repos: list[str],
    kit: dict[str, str],
    names: Names,
    host: str,
    platform: str,
    ghcr_org: str,
    dry_run: bool,
    pull: bool = True,
) -> dict[str, dict[str, str]]:
    """Copy each image into the account repository and record the digest it got there.

    With ``pull=False`` the source must already be in the local daemon (a local build of an
    unpublished change, tagged with its GHCR name); it is pushed as is, never fetched, so a
    stale registry copy cannot stand in for it.
    """
    recorded: dict[str, dict[str, str]] = {}
    repo_path = names.image_repository_path.lstrip("/").lower()
    for repo in repos:
        tag = source_tag(kit, repo)
        source = f"{ghcr_org}/{repo}:{tag}"
        target = f"{host}/{repo_path}/{repo}:{tag}"
        print(f"  → {repo}:{tag}")
        if pull:
            run(["docker", "pull", "--platform", platform, source], dry_run)
        elif not dry_run and subprocess.run(["docker", "image", "inspect", source], capture_output=True).returncode:
            raise MirrorError(f"--no-pull: {source} is not in the local daemon; build it first")
        run(["docker", "tag", source, target], dry_run)
        run(["docker", "push", target], dry_run)
        recorded[repo] = {
            "source": source,
            "target": target,
            "tag": tag,
            "digest": digest_of(target, dry_run),
            "platform": platform,
        }
    return recorded


def main() -> int:
    parser = argparse.ArgumentParser(description="Mirror FLIP images from GHCR into a Snowflake image repository")
    parser.add_argument("--kit", required=True, help="Trust CODE")
    parser.add_argument("--env", default=None, help="Kit-file env token (the Makefile injects it from HUB)")
    parser.add_argument("--connection", default="flip-test", help="snow CLI connection name")
    parser.add_argument("--snow", default="snow", help="snow CLI binary")
    parser.add_argument("--ghcr-org", default="ghcr.io/londonaicentre", help="Source registry and org")
    parser.add_argument("--platform", default="linux/amd64", help="Image platform (SPCS runs amd64 only)")
    parser.add_argument("--images", default="", help="Space-separated repository names to mirror")
    parser.add_argument("--kit-file", type=Path, default=None, help="Read the kit from this file instead")
    parser.add_argument("--dry-run", action="store_true", help="Print the commands; run none")
    parser.add_argument(
        "--no-pull", action="store_true", help="Push the local daemon's image as is (a local build); never pull"
    )
    args = parser.parse_args()

    env = resolve_env(args.env)
    try:
        names = Names(args.kit)
    except NamingError as e:
        print(f"❌ {e}")
        return 1

    path = args.kit_file or kit_path(REPO_ROOT, args.kit, env)
    kit = read_env_vars(path)
    repos = args.images.split()
    if not repos:
        print("❌ --images named no repositories")
        return 1
    # The FL client image follows the kit's backend, so it is appended rather than listed.
    try:
        fl_repo = fl_client_image(kit)
    except NamingError as e:
        print(f"❌ {e}")
        return 1
    if fl_repo not in repos:
        repos.append(fl_repo)

    print(f"📦 Mirroring {len(repos)} image(s) into {names.image_repository} ({args.platform})")
    try:
        host = registry_host(args.snow, args.connection, args.dry_run)
        recorded = mirror(
            repos, kit, names, host, args.platform, args.ghcr_org, args.dry_run, pull=not args.no_pull
        )
    except MirrorError as e:
        print(f"❌ {e}")
        return 1

    lock = {
        "code": names.code,
        "env": env,
        "registry": host,
        "mirrored_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "images": recorded,
    }
    if args.dry_run:
        print("ⓘ  --dry-run: images.lock.json not written")
    else:
        LOCK_FILE.write_text(json.dumps(lock, indent=2) + "\n")
        print(f"  ✓ Recorded {len(recorded)} digest(s) in {LOCK_FILE.name}")
    print("     Re-run `make render` so the specs reference the digests just pushed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
