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
"""Kit-side rotation of the XNAT admin and service passwords (FLIP#1569).

Stdlib only: CI runs this directory as ``python <file>`` with no pytest installed.
"""

from __future__ import annotations

import stat
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import rotate_xnat_passwords as rotation  # noqa: E402

# Admin and service share one value, as on a trust from before the split.
KIT = """\
TRUST_NAME=Scratch
XNAT_ADMIN_USER=admin
XNAT_ADMIN_INITIAL_PASSWORD=admin
XNAT_ADMIN_PASSWORD=shared-old
XNAT_SERVICE_USER=flipServiceAccount
XNAT_SERVICE_PASSWORD=shared-old
# XNAT_SERVICE_PASSWORD=a-commented-example
TRUST_INTERNAL_SERVICE_KEY=internal-stays
"""


def _values(path: Path) -> dict[str, str]:
    return dict(ln.split("=", 1) for ln in path.read_text().splitlines() if ln and not ln.startswith("#"))


class RotateXnatPasswords(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_path = Path(tmp.name)

    def _kit(self, text: str = KIT) -> Path:
        path = self.tmp_path / ".env.SCR.prod"
        path.write_text(text)
        path.chmod(0o644)
        return path

    def test_both_passwords_change_to_distinct_values_and_the_old_admin_one_is_kept_as_previous(self) -> None:
        kit = self._kit()

        assert rotation.main(["--kit-file", str(kit)]) == 0

        after = _values(kit)
        assert after["XNAT_ADMIN_PREVIOUS_PASSWORD"] == "shared-old"  # pragma: allowlist secret
        assert after["XNAT_ADMIN_PASSWORD"] not in ("", "shared-old")
        assert after["XNAT_SERVICE_PASSWORD"] not in ("", "shared-old")
        assert after["XNAT_ADMIN_PASSWORD"] != after["XNAT_SERVICE_PASSWORD"]
        assert len(after["XNAT_ADMIN_PASSWORD"]) >= 32  # token_urlsafe(24)
        assert after["XNAT_ADMIN_INITIAL_PASSWORD"] == "admin"  # pragma: allowlist secret
        assert after["TRUST_INTERNAL_SERVICE_KEY"] == "internal-stays"  # pragma: allowlist secret
        assert "# XNAT_SERVICE_PASSWORD=a-commented-example" in kit.read_text()

    def test_service_only_leaves_the_admin_password_and_writes_no_previous(self) -> None:
        kit = self._kit()

        assert rotation.main(["--kit-file", str(kit), "--service-only"]) == 0

        after = _values(kit)
        assert after["XNAT_ADMIN_PASSWORD"] == "shared-old"  # pragma: allowlist secret
        assert after["XNAT_SERVICE_PASSWORD"] not in ("", "shared-old")
        assert "XNAT_ADMIN_PREVIOUS_PASSWORD" not in after

    def test_the_kit_ends_up_owner_only(self) -> None:
        kit = self._kit()
        rotation.main(["--kit-file", str(kit)])
        assert stat.S_IMODE(kit.stat().st_mode) == 0o600

    def test_a_pending_admin_rotation_is_refused_untouched(self) -> None:
        """A second run before a deploy would overwrite the only record of the password XNAT holds."""
        kit = self._kit()
        rotation.main(["--kit-file", str(kit)])
        pending = kit.read_text()

        assert rotation.main(["--kit-file", str(kit)]) == 1
        assert kit.read_text() == pending
        # The service password alone can still move: it does not depend on the previous admin value.
        assert rotation.main(["--kit-file", str(kit), "--service-only"]) == 0

    def test_an_empty_previous_line_is_not_a_pending_rotation(self) -> None:
        kit = self._kit(KIT + "XNAT_ADMIN_PREVIOUS_PASSWORD=\n")
        assert rotation.main(["--kit-file", str(kit)]) == 0
        assert _values(kit)["XNAT_ADMIN_PREVIOUS_PASSWORD"] == "shared-old"  # pragma: allowlist secret

    def test_a_kit_without_both_xnat_passwords_is_refused_untouched(self) -> None:
        for text in ("TRUST_NAME=Scratch\n", "XNAT_ADMIN_PASSWORD=a\n# XNAT_SERVICE_PASSWORD=b\n"):
            with self.subTest(text=text):
                kit = self._kit(text)
                assert rotation.main(["--kit-file", str(kit)]) == 1
                assert kit.read_text() == text

    def test_a_missing_kit_is_an_error(self) -> None:
        assert rotation.main(["--kit-file", str(self.tmp_path / "absent")]) == 1


if __name__ == "__main__":
    unittest.main()
