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
"""Black-box tests for trust/deploy/scripts/install_kit.py, on kits encrypted here with openssl.

Each test builds a throwaway trust/ directory from the real kit template, with FL_KIT_DIR inside
it and FLIP_UID / FLIP_GID set to this user, so the FL files can be owned without root.

Usage (also collected by pytest):
    uv run --no-config trust/deploy/scripts/tests/test_install_kit.py
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
INSTALL_KIT = SCRIPTS_DIR / "install_kit.py"
REPO_ROOT = SCRIPTS_DIR.parents[2]
TEMPLATE = REPO_ROOT / "trust" / ".env.example"

sys.path.insert(0, str(SCRIPTS_DIR))

if shutil.which("openssl") is None:
    if __name__ == "__main__":
        print("⏭️  openssl not on PATH: install_kit tests skipped")
        sys.exit(0)
    raise unittest.SkipTest("openssl not on PATH")

PASSPHRASE = "correct horse battery staple"  # pragma: allowlist secret
TRUST_ID = "0b8f2d0e-5c1a-4a52-9d55-1f2c3a4b5c6d"
API_KEY = "api-key-from-the-hub"  # pragma: allowlist secret
INTERNAL_KEY = "internal-key-from-the-hub"  # pragma: allowlist secret
HUB_SHARED = {
    "AES_KEY_BASE64": "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU=",  # pragma: allowlist secret
    "CENTRAL_HUB_API_URL": "https://hub.example/api",
    "TRUST_API_KEY_HEADER": "X-Trust-Api-Key",  # pragma: allowlist secret
    "FLOWER_KIT_DATE": "20261101",
    "FLARE_KIT_DATE": "20261102",
    "DOCKER_TAG": "v0.16.0",
    "DOCKER_FL_TAG": "v0.16.0",
    "NLB_SUBDOMAIN": "fl.hub.example",
    "FL_SERVER_PORT": "8002",
    "FL_EDGE_PORT": "8002",
}
FL_FILES = {
    "nvflare": {
        "fl-kit/net-1/services/Trust_2/startup/client.key": b"flare private key",
        "fl-kit/net-1/services/Trust_2/startup/fed_client.json": b'{"client": {"fqsn": "Trust_2"}}',
        "fl-kit/net-1/services/Trust_2/local/resources.json.default": b"{}",
    },
    "flower": {
        "fl-kit/net-1/certificates/ca.crt": b"flower ca",
        "fl-kit/net-1/keys/supernode_credentials_2": b"flower private key",
    },
}


def _manifest(backend: str, kind: str, files: dict[str, bytes]) -> dict:
    kit = {
        "trust_id": TRUST_ID,
        "trust_name": "Demo Hospital",
        "fl_kit_slot": "Trust_2",
        "fl_kit_slot_number": 2,
        "hub_shared": {**HUB_SHARED, "FL_BACKEND": backend},
    }
    if kind == "full":
        kit |= {"trust_api_key": API_KEY, "trust_internal_service_key": INTERNAL_KEY}
    return {
        "schema": 1,
        "kind": kind,
        "issued_at": "2026-11-10T16:37:02Z",
        "fl_backend": backend,
        "trust": {"id": TRUST_ID, "name": "Demo Hospital", "code": "DEMO", "region": None},
        "kit": kit,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }


def _encrypt(dest: Path, manifest: dict, files: dict[str, bytes], links: dict[str, str] | None = None) -> Path:
    """Write the kit as the hub does: gzip tar, then openssl password encryption."""
    plain = io.BytesIO()
    with tarfile.open(fileobj=plain, mode="w:gz") as tar:
        for name, data in {"kit.json": json.dumps(manifest).encode(), **files}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            tar.addfile(info)
    (dest.parent / "plain.tar.gz").write_bytes(plain.getvalue())
    subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "600000", "-md", "sha256", "-salt", "-pass", "stdin"]
        + ["-in", str(dest.parent / "plain.tar.gz"), "-out", str(dest)],
        input=PASSPHRASE.encode() + b"\n",
        check=True,
        capture_output=True,
    )
    (dest.parent / "plain.tar.gz").unlink()
    return dest


class Site:
    """A throwaway trust host: trust/ with the kit template, an FL kit dir, a download and a TMPDIR."""

    def __init__(self, root: Path, own_as_me: bool = True) -> None:
        self.root = root
        self.trust, self.fl_kit, self.tmp, self.download = root / "trust", root / "fl-kit", root / "tmp", root / "dl"
        for path in (self.trust, self.tmp, self.download):
            path.mkdir()
        template = TEMPLATE.read_text().replace("FL_KIT_DIR=/opt/flip/fl-kit", f"FL_KIT_DIR={self.fl_kit}")
        if own_as_me:
            template += f"FLIP_UID={os.getuid()}\nFLIP_GID={os.getgid()}\n"
        (self.trust / ".env.example").write_text(template)
        self.kit_file = self.trust / ".env.DEMO.prod"

    def kit(self, backend: str = "nvflare", kind: str = "full", **changes) -> Path:
        """An encrypted kit; ``changes`` override manifest fields or pass ``files`` / ``links`` / ``listed``."""
        files = changes.pop("files", FL_FILES[backend])
        links = changes.pop("links", None)
        manifest = _manifest(backend, kind, changes.pop("listed", files))
        for key, value in changes.items():
            section, _, field = key.partition("__")
            if field:
                manifest[section][field] = value
            else:
                manifest[section] = value
        return _encrypt(self.download / "kit.enc", manifest, files, links)

    def install(self, kit: Path, *args: str, passphrase: str = PASSPHRASE) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INSTALL_KIT), str(kit), "--trust-dir", str(self.trust), *args],
            input=passphrase + "\n",
            env={**os.environ, "TMPDIR": str(self.tmp)},
            capture_output=True,
            text=True,
        )

    def values(self) -> dict[str, str]:
        lines = self.kit_file.read_text().splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))


@contextlib.contextmanager
def site(**kwargs) -> Iterator[Site]:
    root = Path(tempfile.mkdtemp())
    try:
        yield Site(root, **kwargs)
    finally:
        shutil.rmtree(root)


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _assert_first_install(s: Site, result: subprocess.CompletedProcess[str], backend: str) -> None:
    assert result.returncode == 0, result.stderr
    assert _mode(s.kit_file) == 0o600
    values, template = s.values(), dict(re.findall(r"^([A-Z0-9_]+)=(.*)$", TEMPLATE.read_text(), re.M))
    assert values["TRUST_CODE"] == "DEMO"
    assert values["TRUST_NAME"] == "Demo Hospital"
    assert (values["TRUST_API_KEY"], values["TRUST_INTERNAL_SERVICE_KEY"]) == (API_KEY, INTERNAL_KEY)
    assert (values["EXPECTED_TRUST_ID"], values["FL_KIT_SLOT"], values["FL_KIT_SLOT_NUMBER"]) == (
        TRUST_ID,
        "Trust_2",
        "2",
    )
    assert all(values[key] == value for key, value in {**HUB_SHARED, "FL_BACKEND": backend}.items())
    assert not [key for key, value in values.items() if "<run-make-" in value], "a placeholder is left"

    import install_kit
    import new_trust

    for key in new_trust.TRUST_LOCAL_PASSWORD_KEYS:
        assert values[key] != template[key], f"{key} still the template's"
        assert len(values[key]) >= 24
    xnat = [values[key] for key in install_kit.XNAT_PASSWORD_KEYS]
    assert all(len(value) >= 32 for value in xnat)
    assert len(set(xnat)) == 3

    # Nothing secret in the output, and the operator is sent to the first-install verbs.
    output = result.stdout + result.stderr
    assert not [
        secret
        for secret in (PASSPHRASE, API_KEY, INTERNAL_KEY, HUB_SHARED["AES_KEY_BASE64"], *xnat)
        if secret in output
    ]
    assert "TRUST_API_KEY" in result.stdout
    assert "make up-onprem-trust KIT=DEMO" in result.stdout
    assert "egress IP" in result.stdout
    assert f"rm {s.download / 'kit.enc'}" in result.stdout
    assert not list(s.tmp.iterdir()), "temp dir left behind"


def _assert_owned(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        assert (path.stat().st_uid, path.stat().st_gid) == (os.getuid(), os.getgid()), path
        assert _mode(path) == (0o700 if path.is_dir() else 0o600), path


def test_first_install_nvflare() -> None:
    with site() as s:
        _assert_first_install(s, s.install(s.kit("nvflare")), "nvflare")
        slot = s.fl_kit / "net-1" / "services" / "Trust_2"
        assert (slot / "startup" / "client.key").read_bytes() == b"flare private key"
        assert (slot / "local" / "resources.json.default").is_file()
        assert (slot / "transfer").is_dir(), "the kit carries no transfer/, the client needs one"
        _assert_owned(slot)
        assert not [p.name for p in slot.parent.iterdir() if p.name.startswith(".")], "staging left behind"


def test_first_install_flower() -> None:
    with site() as s:
        _assert_first_install(s, s.install(s.kit("flower")), "flower")
        net = s.fl_kit / "net-1"
        assert (net / "certificates" / "ca.crt").read_bytes() == b"flower ca"
        assert (net / "keys" / "supernode_credentials_2").read_bytes() == b"flower private key"
        _assert_owned(net / "certificates")
        _assert_owned(net / "keys")


def test_first_install_from_a_hub_between_releases_says_the_tags_need_setting() -> None:
    with site() as s:
        shared = {**HUB_SHARED, "FL_BACKEND": "nvflare", "DOCKER_TAG": "sha-0123abc"}
        result = s.install(s.kit("nvflare", kit__hub_shared=shared))
        assert result.returncode == 0, result.stderr
        assert "Set DOCKER_TAG / DOCKER_FL_TAG" in result.stdout, result.stdout
    with site() as s:
        assert "Set DOCKER_TAG" not in s.install(s.kit("nvflare")).stdout


def test_update_keeps_keys_tags_and_site_lines() -> None:
    with site() as s:
        assert s.install(s.kit("nvflare")).returncode == 0
        # What an operator and the upgrade verb do to a live kit file.
        text = s.kit_file.read_text().replace("PACS_HOST=orthanc", "PACS_HOST=pacs.site.example")
        s.kit_file.write_text(text.replace("DOCKER_TAG=v0.16.0", "DOCKER_TAG=v0.15.0"))
        stale = s.fl_kit / "net-1" / "services" / "Trust_2" / "startup" / "old-rootCA.pem"
        stale.write_text("from the previous kit")
        before = s.values()

        files = {**FL_FILES["nvflare"], "fl-kit/net-1/services/Trust_2/startup/client.key": b"new flare key"}
        update = s.kit(
            "nvflare",
            "update",
            files=files,
            kit__hub_shared={
                **HUB_SHARED,
                "FL_BACKEND": "nvflare",
                "FLARE_KIT_DATE": "20261201",
                "DOCKER_TAG": "v0.17.0",
            },
        )
        result = s.install(update)
        assert result.returncode == 0, result.stderr
        after = s.values()

        assert after["FLARE_KIT_DATE"] == "20261201"
        assert after["DOCKER_TAG"] == "v0.15.0", "the installed tag is the upgrade verb's"
        assert after["DOCKER_FL_TAG"] == before["DOCKER_FL_TAG"]
        assert after["PACS_HOST"] == "pacs.site.example"
        changed = {key for key in after if after[key] != before.get(key)}
        assert changed == {"FLARE_KIT_DATE"}, changed
        assert stale.parent.joinpath("client.key").read_bytes() == b"new flare key"
        assert not stale.exists(), "the previous kit's files are replaced, not merged"
        assert "Set: FLARE_KIT_DATE" in result.stdout
        assert "Kept: DOCKER_TAG" in result.stdout
        assert "make upgrade-onprem-trust KIT=DEMO YES=1" in result.stdout
        assert "make up-onprem-trust" not in result.stdout, "a first-install verb, for a live trust"
        assert not list(s.tmp.iterdir())


def test_full_kit_over_an_installed_one_replaces_the_keys() -> None:
    with site() as s:
        assert s.install(s.kit("flower")).returncode == 0
        before = s.values()
        result = s.install(
            s.kit(
                "flower",
                kit__trust_api_key="reissued-api-key",  # pragma: allowlist secret
                kit__trust_internal_service_key="reissued-internal-key",  # pragma: allowlist secret
            ),
            "--delete-file",
        )
        assert result.returncode == 0, result.stderr
        after = s.values()
        assert (after["TRUST_API_KEY"], after["TRUST_INTERNAL_SERVICE_KEY"]) == (
            "reissued-api-key",
            "reissued-internal-key",
        )
        assert after["ORTHANC_PASSWORD"] == before["ORTHANC_PASSWORD"]
        assert after["XNAT_DATASOURCE_PASSWORD"] == before["XNAT_DATASOURCE_PASSWORD"]
        assert "keys were replaced" in result.stdout
        assert "reissued" not in result.stdout + result.stderr
        assert not (s.download / "kit.enc").exists(), "--delete-file"


def test_refusals_change_nothing() -> None:
    fl = FL_FILES["nvflare"]
    key = "fl-kit/net-1/services/Trust_2/startup/client.key"
    cases: dict[str, tuple[dict, list[str], str]] = {
        "wrong passphrase": ({}, [], "wrong passphrase"),
        "digest mismatch": ({"listed": {**fl, key: b"something else"}}, [], "checksum"),
        "unlisted member": ({"listed": {k: v for k, v in fl.items() if k != key}}, [], "file list"),
        "path traversal": ({"files": {**fl, "fl-kit/../../escaped": b"x"}}, [], "must not"),
        "absolute path": ({"files": {**fl, "/tmp/escaped": b"x"}}, [], "must not"),
        "member outside fl-kit": ({"files": {**fl, "trust/.env.DEMO.prod": b"x"}}, [], "must not"),
        "symlink": ({"links": {"fl-kit/net-1/services/Trust_2/startup/link": "/etc/passwd"}}, [], "link"),
        "another slot's files": (
            {"files": {"fl-kit/net-1/services/Trust_9/startup/client.key": b"x"}},
            [],
            "not an FL file",
        ),
        "a file where a directory is": ({"files": {"fl-kit": b"x", **fl}}, [], "also holds a directory"),
        "update kit carrying keys": ({"kind": "update"}, [], "trust keys"),
        "full kit missing a key": ({"kit__trust_internal_service_key": None}, [], "trust keys"),
        "unknown schema": ({"schema": 2}, [], "schema"),
        "unknown hub-shared key": ({"kit__hub_shared": {"ORTHANC_PASSWORD": "x"}}, [], "does not know"),
        "multi-line value": (
            {"kit__trust_api_key": "a\nORTHANC_PASSWORD=x"},  # pragma: allowlist secret
            [],
            "not valid",
        ),
        "make expansion in a value": (
            {"kit__hub_shared": {"CENTRAL_HUB_API_URL": "https://hub.example/$(shell id)"}},
            [],
            "not valid",
        ),
        "KIT names another trust": ({}, ["--kit", "OTHER"], "not KIT=OTHER"),
    }
    for label, (changes, args, message) in cases.items():
        with site() as s:
            passphrase = "not the passphrase" if label == "wrong passphrase" else PASSPHRASE
            result = s.install(s.kit("nvflare", **changes), *args, passphrase=passphrase)
            assert result.returncode == 1, f"{label}: exit {result.returncode}"
            assert message in result.stderr, f"{label}: {result.stderr}"
            assert result.stderr.startswith("❌"), f"{label}: {result.stderr}"
            assert "Traceback" not in result.stderr, f"{label}: {result.stderr}"
            assert PASSPHRASE not in result.stdout + result.stderr, label
            assert not s.kit_file.exists(), f"{label}: a kit file was written"
            assert not s.fl_kit.exists(), f"{label}: FL files were staged"
            assert not list(s.tmp.iterdir()), f"{label}: temp dir left behind"


def test_an_openssl_failure_that_is_the_hosts_is_not_called_a_wrong_passphrase() -> None:
    import install_kit

    with site() as s:
        message = ""
        try:
            install_kit.decrypt(s.kit("nvflare"), s.tmp / "no-such-dir" / "kit.tar.gz", PASSPHRASE)
        except install_kit.KitError as error:
            message = str(error)
        assert "openssl could not decrypt" in message, message
        assert "no-such-dir" in message, message
        assert PASSPHRASE not in message


def test_refusals_against_an_installed_kit() -> None:
    cases = {
        "another trust id": ("EXPECTED_TRUST_ID=" + TRUST_ID, "EXPECTED_TRUST_ID=another-id", "another trust id"),
        "another trust code": ("TRUST_CODE=DEMO", "TRUST_CODE=OTHER", "is trust OTHER's"),
    }
    for label, (old, new, message) in cases.items():
        with site() as s:
            assert s.install(s.kit("nvflare")).returncode == 0
            s.kit_file.write_text(s.kit_file.read_text().replace(old, new))
            before = s.kit_file.read_text()
            result = s.install(s.kit("nvflare", "update"))
            assert result.returncode == 1, label
            assert message in result.stderr, f"{label}: {result.stderr}"
            assert s.kit_file.read_text() == before, f"{label}: the kit file changed"
            assert not list(s.tmp.iterdir())


def test_update_kit_needs_installed_keys() -> None:
    with site() as s:
        result = s.install(s.kit("nvflare", "update"))
        assert result.returncode == 1
        assert "issue new keys" in result.stderr, result.stderr
        assert not s.kit_file.exists()
        assert not s.fl_kit.exists()


def test_fl_kit_the_client_could_not_read_is_refused() -> None:
    if os.getuid() in (0, 49999):
        return  # root can chown, and uid 49999 already is the SuperNode's
    with site(own_as_me=False) as s:
        result = s.install(s.kit("flower"))
        assert result.returncode == 1
        assert "uid 49999:49999" in result.stderr, result.stderr
        assert "FLIP_UID" in result.stderr
        assert not (s.fl_kit / "net-1" / "certificates").exists(), "FL files staged under the wrong owner"
        assert not [p for p in s.fl_kit.rglob(".*")], "staging left behind"
        # The kit file is there to take FLIP_UID, and the same kit then installs.
        s.kit_file.write_text(s.kit_file.read_text() + f"FLIP_UID={os.getuid()}\nFLIP_GID={os.getgid()}\n")
        _assert_first_install(s, s.install(s.kit("flower")), "flower")


def test_old_kit_name_is_not_doubled() -> None:
    with site() as s:
        (s.trust / ".env.DEMO.production").write_text("TRUST_CODE=DEMO\n")
        result = s.install(s.kit("nvflare"))
        assert result.returncode == 1
        assert "old name" in result.stderr, result.stderr
        assert not s.kit_file.exists()


def test_passphrase_reaches_openssl_on_stdin_only() -> None:
    import install_kit

    calls = []
    real_run = subprocess.run

    def recording_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return real_run(argv, **kwargs)

    with site() as s:
        kit = s.kit("nvflare")
        install_kit.subprocess.run = recording_run
        try:
            install_kit.decrypt(kit, s.tmp / "kit.tar.gz", PASSPHRASE)
        finally:
            install_kit.subprocess.run = real_run
        (argv, kwargs), *rest = calls
        assert not rest
        assert argv[0] == "openssl"
        assert not [arg for arg in argv if PASSPHRASE in arg], "passphrase in argv"
        assert "env" not in kwargs, "passphrase could be in the environment"
        assert kwargs["input"] == PASSPHRASE.encode() + b"\n"
        assert tarfile.is_tarfile(s.tmp / "kit.tar.gz")


def test_xnat_keys_are_the_generators() -> None:
    import install_kit

    generator = (REPO_ROOT / "flip-api/src/flip_api/scripts/generate_xnat_credentials.py").read_text()
    listed = re.search(r"PASSWORD_VARS = \((.*?)\)", generator, re.S)
    assert listed
    assert tuple(re.findall(r'"(\w+)"', listed.group(1))) == install_kit.XNAT_PASSWORD_KEYS
    assert "secrets.token_urlsafe(32)" in generator


def main() -> None:
    failed = 0
    for name, test in list(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            test()
            print(f"  ✅ {name}")
        except AssertionError as error:
            failed += 1
            print(f"  ❌ {name}\n    {error}")
    print("—")
    print(f"FAIL={failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
