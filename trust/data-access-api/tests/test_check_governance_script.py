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

"""The check-governance pre-flight's own refusals (FLIP#1259).

The loader it delegates to is unit-tested in ``tests/policy``; this pins the check that
belongs to the script rather than to the loader — the inline document that declares
``[fl_privacy]``. ``flip.nvflare.site_policy`` reads the document from ``ACCESS_POLICY_FILE``
only, so that section would be parsed by data-access-api and then ignored by the one
container meant to enforce it: the failure mode this feature exists to refuse.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_governance.py"
EXAMPLE_DOCUMENT = SCRIPT.parents[2] / "governance.example.toml"

_INLINE_WITH_PRIVACY = """
[disclosure]
min_cohort_size = 25

[fl_privacy]
policy = "percentile"
percentile = 10
"""

_INLINE_DISCLOSURE_ONLY = """
[disclosure]
min_cohort_size = 25
"""


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
    for key in ("ACCESS_POLICY", "ACCESS_POLICY_FILE", "COHORT_QUERY_THRESHOLD"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    code = _load_script().main()
    return code, capsys.readouterr().out


def test_an_inline_document_declaring_fl_privacy_is_refused(monkeypatch, capsys) -> None:
    """Silently ignoring a configured privacy control is the one outcome not allowed."""
    code, out = _run(monkeypatch, capsys, ACCESS_POLICY=_INLINE_WITH_PRIVACY)

    assert code == 1
    assert "[fl_privacy] appears in ACCESS_POLICY" in out
    # The message has to name the way out, or it is just a refusal.
    assert "ACCESS_POLICY_FILE" in out


def test_an_inline_document_without_fl_privacy_is_accepted(monkeypatch, capsys) -> None:
    """The inline source stays usable for the half data-access-api does enforce."""
    code, out = _run(monkeypatch, capsys, ACCESS_POLICY=_INLINE_DISCLOSURE_ONLY)

    assert code == 0
    assert "source: ACCESS_POLICY" in out
    assert "effective min cohort size: 25" in out


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
    assert "access rules: 2" in out


def test_both_sources_set_is_refused(monkeypatch, capsys) -> None:
    """Ambiguity about which policy is in force is refused, not resolved by precedence."""
    code, out = _run(
        monkeypatch,
        capsys,
        ACCESS_POLICY=_INLINE_DISCLOSURE_ONLY,
        ACCESS_POLICY_FILE=str(EXAMPLE_DOCUMENT),
    )

    assert code == 1
    assert "both ACCESS_POLICY and ACCESS_POLICY_FILE are set" in out
