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

"""The check-governance pre-flight (FLIP#1259).

The loader it delegates to is unit-tested in ``tests/policy``; this pins the script's own
contract: the same loader, the digest the service logs, and a bare interpreter.
"""

import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_governance.py"
SERVICE_ROOT = SCRIPT.parents[1]
EXAMPLE_DOCUMENT = SCRIPT.parents[2] / "governance.example.toml"


def _load_script() -> ModuleType:
    """Import the script by path — it is a pre-flight tool, not part of the package."""
    spec = importlib.util.spec_from_file_location("_check_governance_under_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(monkeypatch, capsys, **env: str) -> tuple[int, str]:
    for key in ("ACCESS_POLICY_FILE", "COHORT_QUERY_THRESHOLD"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    code = _load_script().main()
    return code, capsys.readouterr().out


def test_no_document_configured_is_not_an_error(monkeypatch, capsys) -> None:
    """Absent means the platform defaults apply, exactly as before the feature."""
    code, out = _run(monkeypatch, capsys)

    assert code == 0
    assert "No governance document configured" in out


def test_the_shipped_example_document_validates(monkeypatch, capsys) -> None:
    """The document operators are told to copy must pass the loader they will run it through."""
    code, out = _run(monkeypatch, capsys, ACCESS_POLICY_FILE=str(EXAMPLE_DOCUMENT), COHORT_QUERY_THRESHOLD="10")

    assert code == 0, out
    assert "Governance document is valid" in out


def test_the_digest_printed_is_the_documents(monkeypatch, capsys, tmp_path) -> None:
    """The operator matches this against data-access-api's startup line after a reload."""
    document = tmp_path / "governance.toml"
    document.write_text("[disclosure]\nmin_cohort_size = 30\n", encoding="utf-8")

    code, out = _run(monkeypatch, capsys, ACCESS_POLICY_FILE=str(document))

    assert code == 0, out
    digest = hashlib.sha256(document.read_bytes()).hexdigest()
    assert f"sha256: {digest}" in out


def test_an_invalid_document_fails_with_the_loaders_message(monkeypatch, capsys, tmp_path) -> None:
    document = tmp_path / "governance.toml"
    document.write_text("[disclosure]\nmin_cohort_size = 3\n", encoding="utf-8")

    code, out = _run(monkeypatch, capsys, ACCESS_POLICY_FILE=str(document), COHORT_QUERY_THRESHOLD="10")

    assert code == 1
    assert "below the configured COHORT_QUERY_THRESHOLD" in out


def test_it_runs_on_a_bare_interpreter_without_the_services_dependencies(tmp_path) -> None:
    """check-governance runs this on the trust host with no project sync: nothing it imports
    may need fastapi, pydantic or sqlalchemy. -S -I drops site-packages and the environment,
    so an accidental heavy import fails here rather than on a firewalled host."""
    document = tmp_path / "governance.toml"
    document.write_text("[disclosure]\nmin_cohort_size = 30\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            f"import sys; sys.path.insert(0, {str(SERVICE_ROOT)!r}); "
            f"import runpy; runpy.run_path({str(SCRIPT)!r}, run_name='__main__')",
        ],
        capture_output=True,
        text=True,
        env={"ACCESS_POLICY_FILE": str(document), "PATH": os.environ.get("PATH", "")},
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Governance document is valid" in result.stdout
