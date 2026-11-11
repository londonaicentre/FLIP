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
"""Install a trust kit downloaded from the FLIP UI on a Docker Compose trust host (stdlib only).

The download is an OpenSSL password-encrypted gzip tar holding ``kit.json`` (the kit dict
``trust_kit_lib.write_kit`` consumes, plus a SHA-256 for every other member) and the trust's FL
files under ``fl-kit/``. This decrypts it, checks it, creates or updates ``trust/.env.<CODE>.<env>``
and stages the FL files under the kit file's ``FL_KIT_DIR``. Driven by ``make -C trust install-kit``.

A *full* kit carries the trust's two keys: it is the first install, or the keys were re-issued. An
*update* kit carries none and leaves the installed ones alone. Site-local lines are never changed
(``FL_KIT_DIR`` is added when absent), and on an installed trust neither are ``DOCKER_TAG`` /
``DOCKER_FL_TAG``, which record what runs and belong to the upgrade verb (site_upgrade.py).

The passphrase is prompted for, or read as one line of stdin, and reaches openssl through a pipe:
never an argument or an environment variable, which ``ps`` and the shell history would show.
"""

from __future__ import annotations

import argparse
import getpass
import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zlib
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

import new_trust  # noqa: E402
import trust_kit_lib as tkl  # noqa: E402
from onboard_onprem_trust import invoking_ids, is_filled, read_kit_vars  # noqa: E402

SCHEMA = 1
# The inverse of the hub's encryption; the passphrase is the first line of stdin.
OPENSSL_DECRYPT = ("openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "600000", "-md", "sha256")
NOT_A_KIT = "could not decrypt the kit: wrong passphrase, or not a kit downloaded from FLIP."

# What flip-api's generate_xnat_credentials.py mints, in its format: a trust host has no flip-api
# environment to run it in.
XNAT_PASSWORD_KEYS = ("XNAT_DATASOURCE_PASSWORD", "XNAT_DATASOURCE_ADMIN_PASSWORD", "XNAT_ACTIVEMQ_PASSWORD")
TAG_KEYS = ("DOCKER_TAG", "DOCKER_FL_TAG")
DEFAULT_FL_KIT_DIR = "/opt/flip/fl-kit"
# The uid each backend's FL client image runs as, unless the kit sets FLIP_UID.
CLIENT_UID = {"nvflare": 1000, "flower": 49999}
# Names a kit file answered to before FLIP#1479 (deploy/env_mode.mk's kit_names).
OLD_KIT_SUFFIXES = {"prod": (".production", ".lza-prod", ""), "stag": (".lza-stag",)}
_NAME = re.compile(r"[A-Za-z0-9_-]+")


class KitError(Exception):
    """The kit cannot be installed; the message is the whole of what the operator is told."""


def read_passphrase() -> str:
    """Prompt without echo on a terminal, else take one line of stdin."""
    if sys.stdin.isatty():
        passphrase = getpass.getpass("Kit passphrase: ")
    else:
        passphrase = sys.stdin.readline().rstrip("\r\n")
    if not passphrase:
        raise KitError("no passphrase given.")
    return passphrase


def decrypt(source: Path, dest: Path, passphrase: str) -> None:
    """Decrypt the downloaded file into ``dest``, the passphrase going to openssl on its stdin."""
    result = subprocess.run(
        [*OPENSSL_DECRYPT, "-pass", "stdin", "-in", str(source), "-out", str(dest)],
        input=passphrase.encode() + b"\n",
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        # Anything else is the host's fault (no room, no write access, an openssl without -pbkdf2), not the kit's.
        if "bad decrypt" in stderr or "bad magic number" in stderr:
            raise KitError(NOT_A_KIT)
        raise KitError(f"openssl could not decrypt the kit:\n{stderr}")


def unpack(archive: Path, dest: Path) -> dict:
    """Extract the kit into ``dest`` and return its manifest, every file checked against it.

    Only regular files named ``kit.json`` or below ``fl-kit/`` are written; anything else in the
    archive refuses the whole kit.
    """
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                path = PurePosixPath(member.name)
                inside = path.parts and not path.is_absolute() and ".." not in path.parts
                if not inside or not (str(path) == "kit.json" or path.parts[0] == "fl-kit"):
                    raise KitError(f"the kit holds a member it must not: {member.name!r}")
                if member.isdir():
                    continue
                target = dest / path
                if not member.isfile() or target.exists():
                    raise KitError(f"the kit holds a link, a device or a duplicate: {member.name!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, open(target, "wb") as out:  # type: ignore[union-attr]
                    shutil.copyfileobj(src, out)
                target.chmod(0o600 | (member.mode & 0o100))
    # A wrong passphrase that happens to leave valid padding decrypts to noise.
    except (tarfile.TarError, EOFError, zlib.error, gzip.BadGzipFile) as error:
        raise KitError(NOT_A_KIT) from error
    except (FileExistsError, NotADirectoryError) as error:
        raise KitError(f"the kit holds a file where it also holds a directory: {error.filename!r}") from error

    try:
        manifest = json.loads((dest / "kit.json").read_text())
        schema, listed = manifest.get("schema"), dict(manifest.get("files") or {})
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise KitError("the kit has no readable kit.json.") from error
    if schema != SCHEMA:
        raise KitError(f"kit schema {schema!r} is not one this checkout installs ({SCHEMA}): update the checkout.")
    found = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()} - {"kit.json"}
    if found != set(listed):
        raise KitError(f"the kit's contents differ from its file list: {', '.join(sorted(found ^ set(listed)))}")
    for name, digest in listed.items():
        if hashlib.sha256((dest / name).read_bytes()).hexdigest() != digest:
            raise KitError(f"the kit is corrupt: {name} does not match its checksum.")
    return manifest


def fl_units(manifest: dict) -> list[str]:
    """The paths under ``fl-kit/`` this kit replaces, refusing any file outside its own slot's layout.

    FLARE: the slot's service directory. Flower: the certificates directory and the slot's one
    SuperNode credential, since another trust on the host may keep its own beside it.
    """
    kit = manifest["kit"]
    if manifest["fl_backend"] == "nvflare":
        layout = re.compile(rf"fl-kit/(net-\d+/services/{kit['fl_kit_slot']})/(?:startup|local)/.+")
    else:
        number = kit["fl_kit_slot_number"]
        layout = re.compile(
            rf"fl-kit/(net-\d+/certificates)/ca\.crt|fl-kit/(net-\d+/keys/supernode_credentials_{number})"
        )
    units: set[str] = set()
    for name in manifest.get("files") or {}:
        match = layout.fullmatch(name)
        if not match:
            raise KitError(f"{name} is not an FL file of {manifest['fl_backend']} slot {kit['fl_kit_slot']}.")
        units.add(match.group(1) or match.group(2))
    if not units:
        raise KitError("the kit holds no FL files.")
    return sorted(units)


def validate(manifest: dict) -> None:
    """Refuse a manifest whose values could not be written safely into a kit file."""
    try:
        trust, kit, hub_shared = manifest["trust"], manifest["kit"], manifest["kit"].get("hub_shared") or {}
        keys = [kit.get("trust_api_key"), kit.get("trust_internal_service_key")]
        written = [trust["name"], trust.get("region") or "", kit["trust_id"], *filter(None, keys), *hub_shared.values()]
        problems = [
            manifest["kind"] not in ("full", "update") and "kind",
            manifest["fl_backend"] not in CLIENT_UID and "fl_backend",
            hub_shared.get("FL_BACKEND", manifest["fl_backend"]) != manifest["fl_backend"] and "FL_BACKEND",
            not _NAME.fullmatch(trust["code"]) and "trust code",
            not _NAME.fullmatch(kit["fl_kit_slot"]) and "fl_kit_slot",
            not str(kit["fl_kit_slot_number"]).isdigit() and "fl_kit_slot_number",
            (not kit["trust_id"] or kit["trust_id"] != trust["id"]) and "trust id",
            # A full kit carries both keys, an update kit neither.
            (all(keys) if manifest["kind"] == "update" else not all(keys)) and "trust keys",
            # The kit file is read by make and by compose, which both expand `$`; a value is one line of it.
            any(not isinstance(value, (str, int)) or re.search(r"[\r\n$`\\]", str(value)) for value in written)
            and "values",
        ]
    except (KeyError, TypeError, AttributeError) as error:
        raise KitError("kit.json is not a FLIP trust kit manifest.") from error
    if any(problems):
        raise KitError(f"kit.json is not valid: {', '.join(filter(None, problems))}.")
    unknown = sorted(set(hub_shared) - set(tkl.HUB_SHARED_KEYS))
    if unknown:
        raise KitError(f"the kit sets hub-shared keys this checkout does not know ({', '.join(unknown)}): update it.")


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _own(path: Path, owner: tuple[int, int]) -> None:
    """Readable by the FL client alone: the kit holds its private key (the flip_fl_kit role's rule)."""
    st = path.stat()
    path.chmod(0o700 if path.is_dir() else 0o600 | (st.st_mode & 0o100))
    if (st.st_uid, st.st_gid) != owner:
        os.chown(path, *owner)


def prepare_fl_kit(source: Path, fl_kit_dir: Path, units: list[str], owner: tuple[int, int]) -> list[tuple[Path, Path]]:
    """Copy each unit beside its destination, owned and moded for the client; nothing live changes yet.

    Returns:
        list[tuple[Path, Path]]: (staged copy, destination) per unit, for :func:`swap_fl_kit`.
    """
    swaps: list[tuple[Path, Path]] = []
    try:
        for unit in units:
            dest = fl_kit_dir / unit
            staged = dest.with_name(f".{dest.name}.new")
            dest.parent.mkdir(parents=True, exist_ok=True)
            _remove(staged)
            swaps.append((staged, dest))
            if (source / unit).is_dir():
                shutil.copytree(source / unit, staged)
                if "/services/" in unit:
                    # FLARE's client writes both at start; the kit carries no transfer/, and Docker
                    # would create a missing mount source as root.
                    for name in ("local", "transfer"):
                        (staged / name).mkdir(exist_ok=True)
            else:
                shutil.copy(source / unit, staged)
                _own(dest.parent, owner)  # keys/ is the mount root, so the client must enter it
            for path in (staged, *staged.rglob("*")):
                _own(path, owner)
    except BaseException:
        for staged, _ in swaps:
            _remove(staged)
        raise
    return swaps


def swap_fl_kit(swaps: list[tuple[Path, Path]]) -> None:
    """Move each staged unit into place, removing what it replaces."""
    for staged, dest in swaps:
        old = dest.with_name(f".{dest.name}.old")
        _remove(old)
        if dest.exists():
            dest.rename(old)
        staged.rename(dest)
        _remove(old)


def install(args: argparse.Namespace) -> None:
    """Install the kit named on the command line and print what changed and what to run next."""
    source = Path(args.file).expanduser()
    if not source.is_file():
        raise KitError(f"{source} not found (a relative FILE is read from trust/).")
    if shutil.which("openssl") is None:
        raise KitError("openssl is not installed: the kit is decrypted with it.")
    trust_dir = Path(args.trust_dir) if args.trust_dir else Path(__file__).resolve().parents[3] / "trust"
    passphrase = read_passphrase()

    # Private (0700) and removed however this block is left.
    with tempfile.TemporaryDirectory(prefix="flip-kit-") as tmp:
        decrypt(source, Path(tmp) / "kit.tar.gz", passphrase)
        manifest = unpack(Path(tmp) / "kit.tar.gz", Path(tmp) / "kit")
        validate(manifest)
        units = fl_units(manifest)
        kit, backend, code = manifest["kit"], manifest["fl_backend"], manifest["trust"]["code"]

        if args.kit and args.kit != code:
            raise KitError(f"this kit is for trust {code}, not KIT={args.kit}.")
        kit_file = trust_dir / f".env.{code}.{args.env}"
        old_names = [name for s in OLD_KIT_SUFFIXES[args.env] if (trust_dir / (name := f".env.{code}{s}")).is_file()]
        if old_names and not kit_file.exists():
            raise KitError(f"trust/{old_names[0]} is this trust's kit under an old name: rename it to {kit_file.name}.")

        if kit_file.exists() and not os.access(kit_file, os.R_OK | os.W_OK):
            raise KitError(f"{kit_file} is not yours to read and write: run this as the user who owns it.")
        before = read_kit_vars(kit_file)
        # Also true of a kit file an earlier, failed first install left with its placeholders.
        first = not is_filled(before.get("TRUST_API_KEY"))
        if first and manifest["kind"] != "full":
            raise KitError(
                f"{kit_file} holds no trust keys and an update kit carries none. Ask a FLIP admin to issue new keys"
                " from the trust card, then download and install that kit."
            )
        if before.get("TRUST_CODE", code) != code:
            raise KitError(f"{kit_file} is trust {before['TRUST_CODE']}'s, and this kit is for {code}.")
        if is_filled(before.get("EXPECTED_TRUST_ID")) and before["EXPECTED_TRUST_ID"] != kit["trust_id"]:
            raise KitError(f"{kit_file} belongs to another trust id than this kit's: nothing was changed.")

        created = not kit_file.exists()
        if created:
            template = trust_dir / ".env.example"
            if not template.is_file():
                raise KitError(f"base template {template} not found.")
            new_trust.scaffold(kit_file, template, manifest["trust"]["name"], code, manifest["trust"].get("region"))
            lines = kit_file.read_text().splitlines()
            for key in XNAT_PASSWORD_KEYS:
                lines = tkl.upsert(lines, key, secrets.token_urlsafe(32))
            tkl.write_secure(kit_file, lines)
            if os.geteuid() == 0:
                # Under sudo the file would be root's, and make skips a kit file it cannot read without a word.
                os.chown(kit_file, *invoking_ids())

        settings = read_kit_vars(kit_file)
        if not settings.get("FL_KIT_DIR"):
            tkl.write_secure(kit_file, tkl.upsert(kit_file.read_text().splitlines(), "FL_KIT_DIR", DEFAULT_FL_KIT_DIR))
        fl_kit_dir = trust_dir / (settings.get("FL_KIT_DIR") or DEFAULT_FL_KIT_DIR)  # relative: from trust/, as compose
        uid = settings.get("FLIP_UID", "").strip() or str(CLIENT_UID[backend])
        gid = settings.get("FLIP_GID", "").strip() or uid
        if not (uid.isdigit() and gid.isdigit()):
            raise KitError(f"FLIP_UID / FLIP_GID in {kit_file} must be numbers.")

        # Stage first, so a host that cannot hold the FL files leaves an installed kit's values as they were.
        kept = [
            key for key in TAG_KEYS if not first and is_filled(before.get(key)) and key in kit.get("hub_shared", {})
        ]
        try:
            swaps = prepare_fl_kit(Path(tmp) / "kit" / "fl-kit", fl_kit_dir, units, (int(uid), int(gid)))
            try:
                hub_shared = {key: value for key, value in (kit.get("hub_shared") or {}).items() if key not in kept}
                tkl.write_kit(kit_file, {**kit, "hub_shared": hub_shared})
                swap_fl_kit(swaps)
            finally:
                for staged, _ in swaps:
                    _remove(staged)
        except PermissionError as error:
            raise KitError(
                f"cannot stage the FL kit in {fl_kit_dir} for uid {uid}:{gid}, the {backend} client's"
                f" ({error.strerror}: {error.filename}).\n"
                f"   Re-run as root, or in {kit_file} set FL_KIT_DIR to a directory you own and FLIP_UID / FLIP_GID to"
                " your own ids (a first-install choice: the containers then run as you), and re-run."
            ) from error

    after = read_kit_vars(kit_file)
    managed = (*tkl.CREDENTIAL_KEYS, *tkl.METADATA_KEYS, *tkl.HUB_SHARED_KEYS)
    unfilled = [key for key, value in after.items() if "<run-make-" in value]
    changed = [key for key in managed if after.get(key) != before.get(key) and key not in unfilled]
    hub = "" if args.env == "prod" else f" HUB={args.env}"

    print(f"✅ Installed the {manifest['kind']} kit for {code} in {kit_file}")
    print(f"   Set: {', '.join(changed) or 'nothing (already current)'}")
    if created:
        print(f"   Minted on this host: {', '.join((*new_trust.TRUST_LOCAL_PASSWORD_KEYS, *XNAT_PASSWORD_KEYS))}")
    if kept:
        print(f"   Kept: {', '.join(kept)} (they record what is installed; upgrade-onprem-trust moves them)")
    if not first and set(changed) & set(tkl.CREDENTIAL_KEYS):
        print("   The trust's keys were replaced: the hub refuses the old ones until the containers are recreated.")
    if unfilled:
        print(f"   ⚠️  Still unfilled, so the stack will not start: {', '.join(unfilled)}. Ask the FLIP hub team.")
    print(f"   FL kit ({backend}, {kit['fl_kit_slot']}) staged in {fl_kit_dir}, owned by uid {uid}:{gid}:")
    for unit in units:
        print(f"     {unit}")
    print()
    if first:
        print("   Next:")
        print(f"   1. Edit {kit_file}: the site's own settings (PACS_*, XNAT_AETITLE, ports, the data dirs,")
        print("      NUM_AVAILABLE_GPUS, and TRUST_DATA_SEED=false if OMOP and the PACS hold real data).")
        print("   2. Send this host's public egress IP to the FLIP hub team, so the FL endpoint admits it.")
        print("   3. From the repo root, run the readiness checklist, then start the trust:")
        print(f"        make onboard-onprem-trust KIT={code}{hub}")
        print(f"        make up-onprem-trust KIT={code}{hub}")
    else:
        print("   Next, from the repo root, with no FL job running (never up-onprem-trust on a running trust:")
        print("   it resets XNAT and re-seeds):")
        print(f"        make -C trust down-fl-clients-kit KIT={code} HUB={args.env}   # to remount the new FL kit")
        print(f"        make upgrade-onprem-trust KIT={code} YES=1{hub}   # recreates what changed, data intact")
    print()
    if args.delete_file:
        source.unlink()
        print(f"   Deleted {source}.")
    else:
        print(f"   Now delete the downloaded kit, which holds the trust's credentials: rm {source}")
        print("   (DELETE=1 does it for you.)")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", help="The kit file downloaded from the FLIP UI.")
    parser.add_argument("--env", default="prod", choices=("stag", "prod"), help="The hub: the kit-file suffix.")
    parser.add_argument("--kit", default=None, help="Trust code the kit must be for (optional check).")
    parser.add_argument("--delete-file", action="store_true", help="Delete the downloaded file once installed.")
    parser.add_argument("--trust-dir", default=None, help="Override the trust/ directory (testing).")
    try:
        install(parser.parse_args())
    except KitError as error:
        sys.exit(f"❌ {error}")


if __name__ == "__main__":
    main()
