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


def test_a_code_named_kit_packs_its_slot(tmp_path: Path) -> None:
    scripts = tmp_path / "repo" / "deploy" / "providers" / "AWS" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("package-onprem-trust-kit.sh", "utils.sh"):
        shutil.copy2(SCRIPTS / name, scripts / name)
    kit = tmp_path / "repo" / "trust" / ".env.AZ1.stag"
    kit.parent.mkdir()
    kit.write_text(
        "\n".join(["FL_BACKEND=nvflare", "FLARE_KIT_DATE=20261009", "FL_KIT_SLOT=Trust_3", "FL_KIT_SLOT_NUMBER=3", ""])
    )
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

    assert result.returncode == 0, result.stdout + result.stderr
    assert "/net-1/services/Trust_3/" in calls.read_text()
    assert "make up-onprem-trust KIT=AZ1 HUB=stag" in result.stdout
    (tarball,) = (tmp_path / "repo" / "deploy" / "providers" / "AWS" / "build" / "trust-kits").glob("*.tar.gz")
    with tarfile.open(tarball) as tar:
        names = tar.getnames()
    assert ".env.AZ1.stag" in names
    assert "fl-kit/net-1/services/Trust_3/startup.sh" in names
