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
"""package-onprem-trust-kit.sh packs a CODE-named kit with the FL slice of its FL_KIT_SLOT (FLIP#1419).

Runs the real script from a throwaway repo skeleton with ``aws`` stubbed on PATH.
"""

import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

AWS_STUB = """#!/bin/bash
# s3 sync <src> <dst>: record the source and drop files in the destination; s3 cp <src> <dir>/: copy one file in.
# Modes are set explicitly, as an S3 client might, so the packager's modes cannot come from the umask alone.
echo "$*" >> "$AWS_CALLS"
case "$1 $2" in
  "s3 sync")
    touch "$4/startup.sh"
    case "$3" in
      */services/*) mkdir -p "$4/startup" && echo key > "$4/startup/client.key" && chmod 755 "$4/startup" ;;
      */certificates/) echo cert > "$4/ca.crt" ;;
    esac
    chmod -R a+rX "$4" ;;
  "s3 cp") echo key > "$4/${3##*/}" && chmod 644 "$4/${3##*/}" ;;
esac
exit 0
"""

# Records how tar was called, then runs the real one.
TAR_SHIM = """#!/bin/bash
echo "COPYFILE_DISABLE=${COPYFILE_DISABLE:-} $*" >> "$TAR_CALLS"
exec "$REAL_TAR" "$@"
"""


KIT_LINES = ["FL_BACKEND=nvflare", "FLARE_KIT_DATE=20261009", "FL_KIT_SLOT=Trust_3", "FL_KIT_SLOT_NUMBER=3"]


def _pack(tmp_path: Path, kit_lines: list[str]) -> tuple[subprocess.CompletedProcess, Path]:
    """Run the packager with KIT=AZ1 HUB=stag over a kit of ``kit_lines``; returns the run and the aws calls log."""
    scripts = tmp_path / "repo" / "deploy" / "aws" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("package-onprem-trust-kit.sh", "utils.sh"):
        shutil.copy2(SCRIPTS / name, scripts / name)
    kit = tmp_path / "repo" / "trust" / ".env.AZ1.stag"
    kit.parent.mkdir()
    kit.write_text("\n".join([*kit_lines, ""]))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "aws").write_text(AWS_STUB)
    (bin_dir / "tar").write_text(TAR_SHIM)
    for name in ("aws", "tar"):
        (bin_dir / name).chmod(0o755)
    calls = tmp_path / "aws_calls"
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "AWS_CALLS": str(calls),
        "TAR_CALLS": str(tmp_path / "tar_calls"),
        "REAL_TAR": shutil.which("tar") or "/usr/bin/tar",
        "AWS_PROFILE": "probe",
        "AWS_REGION": "eu-west-2",
        "AICENTRE_BUCKET_NAME": "probe-bucket",
        "KIT": "AZ1",
        "HUB": "stag",
        "KIT_FILE": str(kit),
    }
    result = subprocess.run(
        ["bash", "-c", f"umask 022; exec bash {scripts / 'package-onprem-trust-kit.sh'}"],
        env=env,
        capture_output=True,
        text=True,
    )
    return result, calls


def _tarballs(tmp_path: Path) -> list[Path]:
    return list((tmp_path / "repo" / "deploy" / "aws" / "build" / "trust-kits").glob("*.tar.gz"))


def test_a_code_named_kit_packs_its_slot(tmp_path: Path) -> None:
    result, calls = _pack(tmp_path, ["TRUST_CODE=AZ1", *KIT_LINES])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "/net-1/services/Trust_3/" in calls.read_text()
    assert "make up-onprem-trust KIT=AZ1 HUB=stag" in result.stdout
    (tarball,) = _tarballs(tmp_path)
    with tarfile.open(tarball) as tar:
        names = tar.getnames()
    assert ".env.AZ1.stag" in names
    assert "fl-kit/net-1/services/Trust_3/startup.sh" in names


def test_another_trusts_kit_is_refused(tmp_path: Path) -> None:
    """KIT names the kit in the tarball, so KIT_FILE= pointing at another trust's kit must not ship under it."""
    result, _ = _pack(tmp_path, ["TRUST_CODE=KCH", *KIT_LINES])

    assert result.returncode != 0
    assert "TRUST_CODE=KCH" in result.stdout + result.stderr
    assert not _tarballs(tmp_path)


def test_a_kit_without_a_code_ships_with_a_warning(tmp_path: Path) -> None:
    """Nothing can check a kit with no TRUST_CODE against KIT, so the name it ships under is said out loud."""
    result, _ = _pack(tmp_path, KIT_LINES)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "has no TRUST_CODE; shipping it as .env.AZ1.stag" in result.stdout + result.stderr
    assert _tarballs(tmp_path)


def test_a_kit_without_a_slot_is_refused(tmp_path: Path) -> None:
    result, calls = _pack(
        tmp_path, ["TRUST_CODE=AZ1", *(line for line in KIT_LINES if not line.startswith("FL_KIT_SLOT="))]
    )

    assert result.returncode != 0
    assert "FL_KIT_SLOT is empty" in result.stdout + result.stderr
    assert not calls.exists()


FLOWER_KIT_LINES = ["FL_BACKEND=flower", "FLOWER_KIT_DATE=20261009", "FL_KIT_SLOT=Trust_3", "FL_KIT_SLOT_NUMBER=3"]


@pytest.mark.parametrize(
    ("kit_lines", "key"),
    [
        (KIT_LINES, "fl-kit/net-1/services/Trust_3/startup/client.key"),
        (FLOWER_KIT_LINES, "fl-kit/net-1/keys/supernode_credentials_3"),
    ],
    ids=["nvflare", "flower"],
)
def test_the_kit_ships_private_to_whoever_extracts_it(tmp_path: Path, kit_lines: list[str], key: str) -> None:
    """FLIP#1964: the private key was 0644 in the stage dir, the tarball and so on the operator's host."""
    result, _ = _pack(tmp_path, ["TRUST_CODE=AZ1", *kit_lines])

    assert result.returncode == 0, result.stdout + result.stderr
    (tarball,) = _tarballs(tmp_path)
    with tarfile.open(tarball) as tar:
        members = {m.name: m for m in tar.getmembers()}
    assert stat.S_IMODE(members[key].mode) == 0o600
    for member in members.values():
        assert stat.S_IMODE(member.mode) & 0o077 == 0, f"{member.name} is {oct(stat.S_IMODE(member.mode))}"
        assert member.mode & 0o700 == (0o700 if member.isdir() else 0o600), member.name
    stage = next(tarball.parent.glob("Trust_3-*"))
    for path in [stage, *stage.rglob("*")]:
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, f"{path} is left readable in the build dir"


def test_the_tarball_carries_no_mac_metadata(tmp_path: Path) -> None:
    """bsdtar on macOS adds ._* AppleDouble files and xattr headers unless told not to; GNU tar takes the same flag."""
    result, _ = _pack(tmp_path, ["TRUST_CODE=AZ1", *KIT_LINES])

    assert result.returncode == 0, result.stdout + result.stderr
    (call,) = (tmp_path / "tar_calls").read_text().splitlines()
    assert call.startswith("COPYFILE_DISABLE=1 ")
    assert "--no-xattrs" in call.split()
    (tarball,) = _tarballs(tmp_path)
    with tarfile.open(tarball) as tar:
        assert not [m.name for m in tar.getmembers() if Path(m.name).name.startswith("._")]
        assert not [m.name for m in tar.getmembers() if any("xattr" in k for k in m.pax_headers)]


@pytest.mark.parametrize(
    ("kit_lines", "chown"),
    [
        (KIT_LINES, "sudo chown -R 1000:1000 $HOME/flip-trust-kit/fl-kit/net-1/services/Trust_3"),
        (FLOWER_KIT_LINES, "sudo chown -R 49999:49999 $HOME/flip-trust-kit/fl-kit/net-1"),
        (
            [*KIT_LINES, "FLIP_UID=1234", "FLIP_GID=1234"],
            "sudo chown -R 1234:1234 $HOME/flip-trust-kit/fl-kit/net-1/services/Trust_3",
        ),
    ],
    ids=["nvflare", "flower", "flip-uid"],
)
def test_the_operator_is_told_to_give_the_kit_to_the_fl_clients_uid(
    tmp_path: Path, kit_lines: list[str], chown: str
) -> None:
    """Private modes mean only its owner can read the kit, so the FL client's uid must own it (FLARE 1000, Flower
    49999, or the kit's FLIP_UID), as the Ansible role and Helm stage-kit already arrange."""
    result, _ = _pack(tmp_path, ["TRUST_CODE=AZ1", *kit_lines])

    assert result.returncode == 0, result.stdout + result.stderr
    assert chown in result.stdout
