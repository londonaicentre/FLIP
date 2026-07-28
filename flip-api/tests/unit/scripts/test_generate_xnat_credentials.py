# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

from unittest.mock import patch

import pytest

from flip_api.scripts.generate_xnat_credentials import PASSWORD_VARS, main

ENV_TEMPLATE = """\
SOME_VAR=foo
XNAT_DATASOURCE_USERNAME=xnat
XNAT_ACTIVEMQ_USERNAME=write
OTHER_VAR=bar
"""


def _parse_env(content: str) -> dict[str, str]:
    """Parse env file content into a dict (skipping blank/comment lines)."""
    return {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in content.strip().splitlines()
        if "=" in line and not line.startswith("#")
    }


class TestGenerateXnatCredentials:
    def test_generates_all_passwords(self, tmp_path):
        """main() should write a non-empty value for every PASSWORD_VARS entry."""
        env_file = tmp_path / ".env.test"
        env_file.write_text(ENV_TEMPLATE)

        with patch("sys.argv", ["generate_xnat_credentials", "--env-file", str(env_file)]):
            main()

        env = _parse_env(env_file.read_text())

        assert env["SOME_VAR"] == "foo"
        assert env["OTHER_VAR"] == "bar"
        for var in PASSWORD_VARS:
            assert var in env
            assert env[var] not in {"", "xnat", "password", "admin"}
            assert len(env[var]) >= 32

    def test_passwords_differ(self, tmp_path):
        """Each generated password must be unique to limit blast radius if one leaks."""
        env_file = tmp_path / ".env.test"
        env_file.write_text(ENV_TEMPLATE)

        with patch("sys.argv", ["generate_xnat_credentials", "--env-file", str(env_file)]):
            main()

        env = _parse_env(env_file.read_text())
        values = [env[v] for v in PASSWORD_VARS]
        assert len(set(values)) == len(values)

    def test_skips_existing_strong_password(self, tmp_path):
        """An existing non-placeholder value must be preserved on re-run without --force."""
        existing = "EkdK8x_strongRandomPassword_already_present_xyz123"
        env_file = tmp_path / ".env.test"
        env_file.write_text(ENV_TEMPLATE + f"XNAT_DATASOURCE_PASSWORD={existing}\n")

        with patch("sys.argv", ["generate_xnat_credentials", "--env-file", str(env_file)]):
            main()

        env = _parse_env(env_file.read_text())
        assert env["XNAT_DATASOURCE_PASSWORD"] == existing
        assert env["XNAT_DATASOURCE_ADMIN_PASSWORD"] != existing
        assert env["XNAT_ACTIVEMQ_PASSWORD"] != existing

    @pytest.mark.parametrize(
        "weak_value",
        ["xnat", "password", "admin", "", "<run-make-generate-xnat-credentials>"],
    )
    def test_overwrites_known_weak_default(self, tmp_path, weak_value):
        """Known weak/placeholder values must always be regenerated, even without --force."""
        env_file = tmp_path / ".env.test"
        env_file.write_text(ENV_TEMPLATE + f"XNAT_DATASOURCE_PASSWORD={weak_value}\n")

        with patch("sys.argv", ["generate_xnat_credentials", "--env-file", str(env_file)]):
            main()

        env = _parse_env(env_file.read_text())
        assert env["XNAT_DATASOURCE_PASSWORD"] != weak_value
        assert len(env["XNAT_DATASOURCE_PASSWORD"]) >= 32

    def test_force_regenerates_existing(self, tmp_path):
        """--force should overwrite a previously-generated strong password."""
        existing = "EkdK8x_strongRandomPassword_already_present_xyz123"
        env_file = tmp_path / ".env.test"
        env_file.write_text(ENV_TEMPLATE + f"XNAT_DATASOURCE_PASSWORD={existing}\n")

        with patch("sys.argv", ["generate_xnat_credentials", "--env-file", str(env_file), "--force"]):
            main()

        env = _parse_env(env_file.read_text())
        assert env["XNAT_DATASOURCE_PASSWORD"] != existing

    def test_force_rotation_warns_about_initialised_db(self, tmp_path, capsys):
        """Rotating a previously-real value must warn that an initialised xnat-db keeps the old password."""
        existing = "EkdK8x_strongRandomPassword_already_present_xyz123"
        env_file = tmp_path / ".env.test"
        env_file.write_text(ENV_TEMPLATE + f"XNAT_DATASOURCE_PASSWORD={existing}\n")

        with patch("sys.argv", ["generate_xnat_credentials", "--env-file", str(env_file), "--force"]):
            main()

        out = capsys.readouterr().out
        assert "XNAT_DATASOURCE_PASSWORD: rotated" in out
        assert "WARNING" in out
        assert "ALTER ROLE" in out

    def test_fresh_generation_does_not_warn(self, tmp_path, capsys):
        """Filling placeholders (even with --force) is not a rotation — no warning expected."""
        env_file = tmp_path / ".env.test"
        env_file.write_text(ENV_TEMPLATE + "XNAT_DATASOURCE_PASSWORD=<run-make-generate-xnat-credentials>\n")

        with patch("sys.argv", ["generate_xnat_credentials", "--env-file", str(env_file), "--force"]):
            main()

        out = capsys.readouterr().out
        assert "XNAT_DATASOURCE_PASSWORD: generated" in out
        assert "WARNING" not in out

    def test_skip_does_not_warn(self, tmp_path, capsys):
        """Preserving an existing value without --force must not emit the rotation warning."""
        existing = "EkdK8x_strongRandomPassword_already_present_xyz123"
        env_file = tmp_path / ".env.test"
        env_file.write_text(ENV_TEMPLATE + f"XNAT_DATASOURCE_PASSWORD={existing}\n")

        with patch("sys.argv", ["generate_xnat_credentials", "--env-file", str(env_file)]):
            main()

        out = capsys.readouterr().out
        assert "XNAT_DATASOURCE_PASSWORD: skipped" in out
        assert "WARNING" not in out

    def test_exits_when_env_file_missing(self, tmp_path):
        """main() should exit non-zero when the env file does not exist."""
        env_file = tmp_path / ".env.nonexistent"

        with (
            patch("sys.argv", ["generate_xnat_credentials", "--env-file", str(env_file)]),
            pytest.raises(SystemExit, match="1"),
        ):
            main()

    def test_does_not_write_extra_files(self, tmp_path):
        """main() must touch only the env file."""
        env_file = tmp_path / ".env.test"
        env_file.write_text(ENV_TEMPLATE)

        with patch("sys.argv", ["generate_xnat_credentials", "--env-file", str(env_file)]):
            main()

        # The only file under tmp_path is the env file we created.
        assert [p.name for p in tmp_path.iterdir()] == [env_file.name]
