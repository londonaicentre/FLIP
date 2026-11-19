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
"""Site-local rotation of TRUST_INTERNAL_SERVICE_KEY (FLIP#1714)."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import rotate_internal_service_key as rotation  # noqa: E402

KIT = """\
TRUST_NAME=Scratch
TRUST_API_KEY=api-key-stays
# TRUST_INTERNAL_SERVICE_KEY=a-commented-example
TRUST_INTERNAL_SERVICE_KEY=old-internal
FL_KIT_SLOT=Trust_1
"""


def _kit(tmp_path: Path, text: str = KIT) -> Path:
    path = tmp_path / ".env.SCR.prod"
    path.write_text(text)
    path.chmod(0o644)
    return path


def _values(path: Path) -> dict[str, str]:
    return dict(ln.split("=", 1) for ln in path.read_text().splitlines() if ln and not ln.startswith("#"))


def test_only_the_internal_key_changes(tmp_path: Path) -> None:
    kit = _kit(tmp_path)

    assert rotation.main(["--kit-file", str(kit)]) == 0

    after = _values(kit)
    assert after["TRUST_INTERNAL_SERVICE_KEY"] not in ("", "old-internal")
    assert len(after["TRUST_INTERNAL_SERVICE_KEY"]) >= 43  # token_urlsafe(32)
    assert after["TRUST_API_KEY"] == "api-key-stays"  # pragma: allowlist secret
    assert after["FL_KIT_SLOT"] == "Trust_1"
    assert "# TRUST_INTERNAL_SERVICE_KEY=a-commented-example" in kit.read_text()


def test_the_kit_ends_up_owner_only(tmp_path: Path) -> None:
    kit = _kit(tmp_path)
    rotation.main(["--kit-file", str(kit)])
    assert stat.S_IMODE(kit.stat().st_mode) == 0o600


def test_two_rotations_give_two_different_keys(tmp_path: Path) -> None:
    kit = _kit(tmp_path)
    rotation.main(["--kit-file", str(kit)])
    first = _values(kit)["TRUST_INTERNAL_SERVICE_KEY"]
    rotation.main(["--kit-file", str(kit)])
    assert _values(kit)["TRUST_INTERNAL_SERVICE_KEY"] != first


@pytest.mark.parametrize("text", ["TRUST_NAME=Scratch\n", "# TRUST_INTERNAL_SERVICE_KEY=commented\n"])
def test_a_kit_with_no_internal_key_is_refused_untouched(tmp_path: Path, text: str) -> None:
    kit = _kit(tmp_path, text)
    assert rotation.main(["--kit-file", str(kit)]) == 1
    assert kit.read_text() == text


def test_a_missing_kit_is_an_error(tmp_path: Path) -> None:
    assert rotation.main(["--kit-file", str(tmp_path / "absent")]) == 1
