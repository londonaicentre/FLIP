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
import subprocess
import tarfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

AWS_STUB = """#!/bin/bash
# s3 sync <src> <dst>: record the source, drop one file in the destination.
echo "$*" >> "$AWS_CALLS"
[ "$1 $2" = "s3 sync" ] && touch "$4/startup.sh"
exit 0
"""


KIT_LINES = ["FL_BACKEND=nvflare", "FLARE_KIT_DATE=20261009", "FL_KIT_SLOT=Trust_3", "FL_KIT_SLOT_NUMBER=3"]


def _pack(tmp_path: Path, kit_lines: list[str]) -> tuple[subprocess.CompletedProcess, Path]:
    """Run the packager with KIT=AZ1 HUB=stag over a kit of ``kit_lines``; returns the run and the aws calls log."""
    scripts = tmp_path / "repo" / "deploy" / "providers" / "AWS" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("package-onprem-trust-kit.sh", "utils.sh"):
        shutil.copy2(SCRIPTS / name, scripts / name)
    kit = tmp_path / "repo" / "trust" / ".env.AZ1.stag"
    kit.parent.mkdir()
    kit.write_text("\n".join([*kit_lines, ""]))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "aws").write_text(AWS_STUB)
    (bin_dir / "aws").chmod(0o755)
    calls = tmp_path / "aws_calls"
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "AWS_CALLS": str(calls),
        "AWS_PROFILE": "probe",
        "AWS_REGION": "eu-west-2",
        "AICENTRE_BUCKET_NAME": "probe-bucket",
        "KIT": "AZ1",
        "HUB": "stag",
        "KIT_FILE": str(kit),
    }
    result = subprocess.run(
        ["bash", str(scripts / "package-onprem-trust-kit.sh")], env=env, capture_output=True, text=True
    )
    return result, calls


def _tarballs(tmp_path: Path) -> list[Path]:
    return list((tmp_path / "repo" / "deploy" / "providers" / "AWS" / "build" / "trust-kits").glob("*.tar.gz"))


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
