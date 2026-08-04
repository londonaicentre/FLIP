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
"""
Parity tests for the XNAT weak-password guards (FLIP-PT-056).

Four independent layers decide whether an XNAT database password is a real
credential or a known-weak placeholder, in three different languages:

* ``flip-api/src/flip_api/scripts/generate_xnat_credentials.py`` — the minter,
  which overwrites a weak value without ``--force``.
* ``trust/xnat/Makefile`` — the deploy-time guard, which refuses ``up-xnat``.
* ``trust/xnat/xnat/wait-for-postgres.sh`` — xnat-web's entrypoint guard.
* ``trust/xnat/postgres/guard-xnat-db-passwords.sh`` — xnat-db's entrypoint guard.

They cannot share one list at runtime: the XNAT images carry no Python, and
each image's Docker build context is its own leaf directory, so neither can read
a file from ``trust/xnat/``. Duplication is therefore forced — but drift is not.
A value that one layer calls strong and the next calls weak strands the operator
between two tools that disagree (mint reports "skipped", deploy then refuses it),
so these tests pin all four to the same set and fail if any one of them moves.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

GENERATOR = REPO_ROOT / "flip-api/src/flip_api/scripts/generate_xnat_credentials.py"
XNAT_MAKEFILE = REPO_ROOT / "trust/xnat/Makefile"
WAIT_FOR_POSTGRES = REPO_ROOT / "trust/xnat/xnat/wait-for-postgres.sh"
GUARD_XNAT_DB = REPO_ROOT / "trust/xnat/postgres/guard-xnat-db-passwords.sh"
IMAGING_API_CONFIG = REPO_ROOT / "trust/imaging-api/imaging_api/config.py"

PLACEHOLDER = "<run-make-generate-xnat-credentials>"

# The canonical answer to "is this a real credential?". `postgres` is here
# because it is the stock superuser default and XNAT_DATASOURCE_ADMIN_PASSWORD
# is a superuser password; the rest are the literals FLIP-PT-056 removed from
# the committed kit templates and the published image.
CANONICAL_WEAK_VALUES = frozenset({"xnat", "password", "admin", "postgres", PLACEHOLDER})


def _case_arm_literals(source: Path, anchor: str) -> set[str]:
    """Extract the literal patterns from the shell ``case`` arm following an anchor line.

    Args:
        source (Path): File to read.
        anchor (str): Substring identifying the ``case ... in`` line; the arm is
            the next non-blank line after it.

    Returns:
        set[str]: The arm's literal alternatives, with quotes stripped. Dynamic
        alternatives (``"$SOME_VAR"``) are excluded — they are compared against a
        runtime value, not a fixed literal.
    """
    lines = source.read_text().splitlines()
    anchor_index = next(i for i, line in enumerate(lines) if anchor in line)
    arm = next(line for line in lines[anchor_index + 1 :] if line.strip())

    patterns = arm.split(")")[0].strip()
    literals = set()
    for alternative in patterns.split("|"):
        value = alternative.strip().strip('"').strip("'")
        if value.startswith("$"):
            continue
        literals.add(value)
    return literals


def _generator_placeholder_values() -> set[str]:
    """Read ``PLACEHOLDER_VALUES`` out of the generator without importing flip-api.

    Parsing rather than importing keeps this test project free of a dependency on
    the flip-api package, which is not installed in the trust/xnat test env.

    Returns:
        set[str]: The literal values in the generator's ``PLACEHOLDER_VALUES``.
    """
    tree = ast.parse(GENERATOR.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PLACEHOLDER_VALUES" for target in node.targets
        ):
            # frozenset({...}) — the set literal is the sole call argument.
            assert isinstance(node.value, ast.Call), "PLACEHOLDER_VALUES is no longer a frozenset(...) call"
            return {ast.literal_eval(element) for element in node.value.args[0].elts}  # type: ignore[attr-defined]
    pytest.fail(f"PLACEHOLDER_VALUES not found in {GENERATOR}")


def _makefile_variable(name: str) -> str:
    """Return the value of a simple ``NAME := value`` assignment in the XNAT Makefile.

    Args:
        name (str): The Make variable name.

    Returns:
        str: The assigned value, stripped.
    """
    match = re.search(rf"^{re.escape(name)}\s*:=\s*(.+)$", XNAT_MAKEFILE.read_text(), re.MULTILINE)
    assert match is not None, f"{name} not found in {XNAT_MAKEFILE}"
    return match.group(1).strip()


def test_generator_weak_values_match_canonical():
    """The minter must treat exactly the canonical set as overwritable.

    Its extra member is the empty string: unset is "not a real credential" here,
    whereas the three shell/Make guards test emptiness in a separate branch.
    """
    values = _generator_placeholder_values()

    assert "" in values, "unset must remain overwritable without --force"
    assert values - {""} == CANONICAL_WEAK_VALUES


def test_xnat_web_entrypoint_weak_values_match_canonical():
    """xnat-web's entrypoint must refuse exactly the canonical set."""
    assert _case_arm_literals(WAIT_FOR_POSTGRES, 'case "$XNAT_DATASOURCE_PASSWORD" in') == CANONICAL_WEAK_VALUES


def test_xnat_db_entrypoint_weak_values_match_canonical():
    """xnat-db's entrypoint must refuse exactly the canonical set."""
    assert _case_arm_literals(GUARD_XNAT_DB, 'case "$value" in') == CANONICAL_WEAK_VALUES


def test_deploy_guard_weak_values_match_canonical():
    """The deploy-time Make guard must refuse exactly the canonical set.

    It splits the concern: the weak literals live in a ``case`` arm, while the
    placeholder is matched against ``XNAT_CREDENTIAL_PLACEHOLDER`` (it applies to
    every required password, not just the two minted ones).
    """
    weak_literals = _case_arm_literals(XNAT_MAKEFILE, 'case "$$(printenv $$v 2>/dev/null)" in')
    placeholder = _makefile_variable("XNAT_CREDENTIAL_PLACEHOLDER")

    assert weak_literals | {placeholder} == CANONICAL_WEAK_VALUES


@pytest.mark.parametrize(
    ("source", "pattern"),
    [
        (GENERATOR, rf'"{re.escape(PLACEHOLDER)}"'),
        (XNAT_MAKEFILE, rf"XNAT_CREDENTIAL_PLACEHOLDER\s*:=\s*{re.escape(PLACEHOLDER)}"),
        (WAIT_FOR_POSTGRES, rf'"{re.escape(PLACEHOLDER)}"'),
        (GUARD_XNAT_DB, rf'"{re.escape(PLACEHOLDER)}"'),
        (IMAGING_API_CONFIG, rf'_XNAT_CREDENTIAL_PLACEHOLDER = "{re.escape(PLACEHOLDER)}"'),
    ],
    ids=["generator", "makefile", "xnat-web", "xnat-db", "imaging-api"],
)
def test_placeholder_token_is_identical_everywhere(source: Path, pattern: str):
    """Every layer must spell the kit-template placeholder identically.

    A typo in one copy silently downgrades that layer to "this looks like a real
    password", which is the failure this token exists to prevent.
    """
    assert re.search(pattern, source.read_text()) is not None, f"{PLACEHOLDER} not found as expected in {source}"


def test_username_equality_is_guarded_in_both_entrypoints():
    """Both entrypoints must reject password == username.

    This one is not expressible as a shared literal (it depends on runtime env),
    so it is asserted structurally instead — xnat-web folds it into the ``case``
    as a dynamic arm, xnat-db uses a separate comparison.
    """
    assert '"$XNAT_DATASOURCE_USERNAME"' in WAIT_FOR_POSTGRES.read_text()
    assert '"${XNAT_DATASOURCE_USERNAME:-xnat}"' in GUARD_XNAT_DB.read_text()


def test_xnat_db_entrypoint_rejects_reused_superuser_password():
    """xnat-db must refuse POSTGRES_PASSWORD == XNAT_DATASOURCE_PASSWORD.

    The superuser and the xnat application role are separate credentials; wiring
    both to one secret collapses that separation and makes the app role's
    password superuser-equivalent, with nothing downstream failing to reveal it.
    """
    assert '[ "$POSTGRES_PASSWORD" = "$XNAT_DATASOURCE_PASSWORD" ]' in GUARD_XNAT_DB.read_text()
