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

import ast
import inspect
import os
import re
import stat
from pathlib import Path
from unittest.mock import patch

from flip_api.scripts import env_utils
from flip_api.scripts.env_utils import get_json_value, write_env_file


class TestGetJsonValue:
    def test_returns_value_for_existing_key(self):
        assert get_json_value('{"Trust_1": "abc123"}', "Trust_1") == "abc123"

    def test_returns_empty_string_for_missing_key(self):
        assert get_json_value('{"Trust_1": "abc123"}', "Trust_2") == ""

    def test_returns_empty_string_for_empty_json(self):
        assert get_json_value("", "Trust_1") == ""

    def test_returns_empty_string_for_empty_dict(self):
        assert get_json_value("{}", "Trust_1") == ""


class TestWriteEnvFile:
    def test_writes_lines_with_a_trailing_newline(self, tmp_path):
        target = tmp_path / ".env.test"

        write_env_file(target, ["A=1", "B=2"])

        assert target.read_text() == "A=1\nB=2\n"

    def test_empty_lines_write_an_empty_file_not_a_leading_blank_line(self, tmp_path):
        """An empty list must not seed a lone newline — parity with trust_kit_lib.write_secure."""
        target = tmp_path / ".env.test"

        write_env_file(target, [])

        assert target.read_text() == ""

    def test_restricts_a_new_file_to_owner_read_write(self, tmp_path):
        target = tmp_path / ".env.test"

        write_env_file(target, ["SECRET=value"])

        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_tightens_an_existing_world_readable_file_before_replacing_its_contents(self, tmp_path):
        """Fresh credentials must not be written while the old permissive mode still applies."""
        target = tmp_path / ".env.test"
        existing_contents = "EXISTING=1\n"
        target.write_text(existing_contents)
        target.chmod(0o644)

        real_fchmod = os.fchmod

        def assert_mode_is_tightened_before_write(fd, mode):
            # ftruncate and the new write happen after fchmod, so this must still be the old
            # content. Reversing the order would expose SECRET=value through mode 0644.
            assert target.read_text() == existing_contents
            real_fchmod(fd, mode)

        with patch("flip_api.scripts.env_utils.os.fchmod", side_effect=assert_mode_is_tightened_before_write):
            write_env_file(target, ["EXISTING=1", "SECRET=value"])

        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def _normalised_body(source: str, func_name: str, renames: dict[str, str]) -> str:
    """Return a function's body as canonical source, ignoring its docstring, comments and local names.

    Args:
        source (str): Python source containing the function.
        func_name (str): Name of the function to extract.
        renames (dict[str, str]): Identifier substitutions applied to the rendered body, so two
            copies that differ only in their parameter and file-handle names compare equal.

    Returns:
        str: The rendered body, one statement per line.
    """
    func = next(
        node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef) and node.name == func_name
    )
    body = func.body
    if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]

    # ast.unparse discards comments and normalises formatting, so only the statements are compared.
    rendered = "\n".join(ast.unparse(statement) for statement in body)
    for old, new in renames.items():
        rendered = re.sub(rf"\b{old}\b", new, rendered)

    return rendered


def test_write_env_file_stays_in_lockstep_with_trust_kit_lib_write_secure():
    """``write_env_file`` and ``scripts/trust_kit_lib.write_secure`` must stay byte-equivalent.

    Both docstrings declare the two to be the same routine, and both write live credentials, but
    nothing enforced it — the pair had already drifted twice (one caught the empty-list case, the
    other an ``except Exception``/``except OSError`` split) before this guard existed. They cannot
    be shared: ``trust_kit_lib`` sits at the repository root and is imported by the plain
    ``make register-trust`` scripts, while this module ships inside flip-api's package and virtual
    environment, so neither can import the other. Compare them instead, the same way
    ``test_register_trust_cli.test_hub_shared_keys_in_lockstep`` compares the hub-shared key set.
    """
    repo_root = Path(__file__).resolve()
    while repo_root.name and not (repo_root / "flip-api").is_dir():
        repo_root = repo_root.parent
    assert repo_root.name, "Could not locate FLIP repo root from test file"

    lib_path = repo_root / "scripts" / "trust_kit_lib.py"
    theirs = _normalised_body(lib_path.read_text(), "write_secure", {"target": "path", "handle": "env_file"})
    ours = _normalised_body(inspect.getsource(env_utils), "write_env_file", {})

    assert ours == theirs, (
        f"flip_api.scripts.env_utils.write_env_file and {lib_path}:write_secure have drifted:\n"
        f"  write_env_file:\n{ours}\n"
        f"  write_secure:\n{theirs}"
    )
