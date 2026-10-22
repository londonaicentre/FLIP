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
"""The Makefile checks every argument before it goes into a Run Command script (FLIP#1390).

Run Command hands its script to a shell on the node, so a value is parsed as shell there before
flip-node's own checks run. These tests run the real targets with `az` stubbed on PATH and read
back what would have been sent to the node.
"""

from __future__ import annotations

import pytest
from conftest import run_make


def _sent(calls: list[str]) -> list[str]:
    return [c.split("--scripts ", 1)[1].split(" --query", 1)[0] for c in calls if c.startswith("vm run-command")]


@pytest.mark.parametrize(
    ("target", "variables", "sent"),
    [
        ("reprovision", {"REF": "v0.12.0"}, "flip-node reprovision v0.12.0"),
        ("reprovision", {"REF": "0123456789abcdef0123456789abcdef01234567"}, None),  # pragma: allowlist secret
        ("reprovision", {}, "flip-node reprovision "),
        ("selftest", {"FL_BACKEND": "flower"}, "flip-node selftest flower"),
        ("join", {"NAME": "flip-trust-kit-AZ1.tar.gz", "KIT": "AZ1", "PROD": "lza-stag"}, None),
        ("logs", {}, "flip-node logs flip-provision 80"),
        ("report", {"FL_BACKEND": "nvflare"}, "flip-node report nvflare"),
    ],
)
def test_a_valid_argument_reaches_the_node(tmp_path, target, variables, sent):
    result, calls = run_make(tmp_path, target, **variables)

    assert result.returncode == 0, result.stdout + result.stderr
    scripts = _sent(calls)
    assert len(scripts) == 1, calls
    if sent is not None:
        assert scripts[0] == sent


@pytest.mark.parametrize(
    ("target", "variables", "bad"),
    [
        ("reprovision", {"REF": "v0.12.0; touch PWNED"}, "REF"),
        ("reprovision", {"REF": "main"}, "REF"),
        ("selftest", {"FL_BACKEND": "nvflare`touch PWNED`"}, "FL_BACKEND"),
        ("join", {"NAME": "kit.tar.gz; touch PWNED", "KIT": "AZ1", "PROD": "lza-stag"}, "NAME"),
        ("join", {"NAME": "kit.tar.gz", "KIT": "AZ1 x", "PROD": "lza-stag"}, "KIT"),
        ("join", {"NAME": "kit.tar.gz", "KIT": "AZ1", "PROD": "lza-stag'; touch PWNED; '"}, "PROD"),
        ("logs", {"UNIT": "flip-provision; touch PWNED"}, "UNIT"),
        ("logs", {"LINES": "80 && touch PWNED"}, "LINES"),
        ("report", {"FL_BACKEND": "both"}, "FL_BACKEND"),
        ("selftest-kit", {"FL_BACKEND": "nvflare; touch PWNED"}, "FL_BACKEND"),
    ],
)
def test_an_unsafe_argument_never_reaches_the_node(tmp_path, target, variables, bad):
    sentinel = str(tmp_path / "PWNED")
    result, calls = run_make(tmp_path, target, **{k: v.replace("PWNED", sentinel) for k, v in variables.items()})

    assert result.returncode != 0
    assert f"{bad}=" in result.stdout + result.stderr, "the refusal names the argument"
    assert not _sent(calls), "nothing was sent to the node"
    assert not (tmp_path / "PWNED").exists(), "the value was never run as a command locally either"


@pytest.mark.parametrize("name", ["kit.tar.gz; touch PWNED", "../kit.tar.gz", "kit.zip"])
def test_kit_upload_checks_the_blob_name_before_azure_sees_it(tmp_path, name):
    tarball = tmp_path / "kit.tar.gz"
    tarball.write_bytes(b"kit")
    sentinel = str(tmp_path / "PWNED")

    result, calls = run_make(tmp_path, "kit-upload", TARBALL=str(tarball), NAME=name.replace("PWNED", sentinel))

    assert result.returncode != 0
    assert "NAME=" in result.stdout + result.stderr, "the refusal names the argument"
    assert not [c for c in calls if c.startswith("storage blob")], "nothing was uploaded"
    assert not (tmp_path / "PWNED").exists()


def test_kit_upload_names_the_blob_after_the_tarball_by_default(tmp_path):
    tarball = tmp_path / "flip-trust-kit-AZ1.tar.gz"
    tarball.write_bytes(b"kit")

    result, calls = run_make(tmp_path, "kit-upload", TARBALL=str(tarball))

    assert result.returncode == 0, result.stdout + result.stderr
    (upload,) = [c for c in calls if c.startswith("storage blob upload")]
    assert "/flip-trust-kit-AZ1.tar.gz " in upload
