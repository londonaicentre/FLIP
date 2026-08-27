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

"""Consistency tests for the chart's Secret plumbing (FLIP-PT-056).

A secret key has to line up across three files that nothing else ties together:
the consumer's ``secretKeyRef``, the ``secrets.yaml`` template that materialises
the key, and ``generate_values.py``'s ``SECRET_VAR_MAP``, which is what actually
fills it from a trust kit. Each of those can be edited alone, and a mismatch is
invisible until deploy time — ``secrets.yaml`` enumerates its keys by hand and
guards each with ``if index``, so a key it does not know about is silently
skipped rather than failing the render, and the pod then dies with
``CreateContainerConfigError``. A key missing only from ``SECRET_VAR_MAP`` is
quieter still: the chart renders and deploys, with an empty credential.

These parse the templates as text rather than rendering them: ``helm template``
only reaches the branches its values enable (XNAT is disabled in the kind CI
values, for one), so a rendered-output check would silently skip most of the
chart. Text parsing sees every branch.
"""

import importlib.util
import re
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = CHART_DIR / "templates"
SECRETS_TEMPLATE = TEMPLATES_DIR / "secrets.yaml"
XNAT_DB_TEMPLATE = TEMPLATES_DIR / "xnat-db.yaml"

_SCRIPT = CHART_DIR / "scripts" / "generate_values.py"
_spec = importlib.util.spec_from_file_location("generate_values", _SCRIPT)
generate_values = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generate_values)


def _secret_key_refs(template: Path) -> dict[str, str]:
    """Map each env var name in a template to the Secret key it reads.

    Args:
        template (Path): Chart template to scan.

    Returns:
        dict[str, str]: ``{env var name: secret key}`` for every ``secretKeyRef``.
    """
    lines = template.read_text().splitlines()
    refs: dict[str, str] = {}
    current_name: str | None = None

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- name: "):
            current_name = stripped.removeprefix("- name: ").strip()
        elif stripped == "secretKeyRef:":
            # The key sits within the next few lines, alongside the secret name.
            for following in lines[index + 1 : index + 5]:
                match = re.match(r"\s*key:\s*([a-z0-9-]+)\s*$", following)
                if match and current_name:
                    refs[current_name] = match.group(1)
                    break
    return refs


def _all_referenced_keys() -> dict[str, set[str]]:
    """Collect every Secret key referenced by any template.

    Returns:
        dict[str, set[str]]: ``{secret key: {template file names that use it}}``.
    """
    referenced: dict[str, set[str]] = {}
    for template in sorted(TEMPLATES_DIR.glob("*.yaml")):
        for key in _secret_key_refs(template).values():
            referenced.setdefault(key, set()).add(template.name)
    return referenced


def _rendered_keys() -> set[str]:
    """Return the Secret keys ``secrets.yaml`` can emit.

    Returns:
        set[str]: Keys with an emit line in the Secret template.
    """
    pattern = re.compile(r"^\s{2}([a-z0-9-]+):\s*\{\{\s*index\s+\.Values\.secrets\.data", re.MULTILINE)
    return set(pattern.findall(SECRETS_TEMPLATE.read_text()))


def test_every_referenced_secret_key_is_rendered():
    """A key a workload mounts must be one the Secret template can emit.

    Otherwise the chart templates and installs cleanly and the pod fails at
    container creation with a missing-key error.
    """
    referenced = _all_referenced_keys()
    missing = {key: sorted(users) for key, users in referenced.items() if key not in _rendered_keys()}

    assert missing == {}, f"secretKeyRef keys absent from templates/secrets.yaml: {missing}"


def test_every_rendered_secret_key_can_be_populated():
    """Every key the Secret can carry must be fillable from a trust kit.

    ``generate_values.py`` is the only path from kit file to values file, so a
    key it does not map cannot be populated and deploys empty.
    """
    unmapped = _rendered_keys() - set(generate_values.SECRET_VAR_MAP.values())

    assert unmapped == set(), f"Secret keys with no SECRET_VAR_MAP source: {sorted(unmapped)}"


def test_secret_var_map_targets_exist_in_the_secret_template():
    """The reverse direction: a mapped key that nothing renders is dead config."""
    orphaned = set(generate_values.SECRET_VAR_MAP.values()) - _rendered_keys()

    assert orphaned == set(), f"SECRET_VAR_MAP entries with no key in templates/secrets.yaml: {sorted(orphaned)}"


def test_xnat_db_superuser_and_app_role_use_distinct_secret_keys():
    """xnat-db's two passwords must not resolve to the same Secret key.

    ``POSTGRES_PASSWORD`` is the Postgres superuser; ``XNAT_DATASOURCE_PASSWORD``
    is the `xnat` application role that xnat-web, imaging-api and the import
    worker authenticate with. Pointing both at one key makes the app role's
    password superuser-equivalent and quietly discards the separately minted
    admin credential (FLIP-PT-056).
    """
    refs = _secret_key_refs(XNAT_DB_TEMPLATE)

    assert refs["POSTGRES_PASSWORD"] == "xnat-datasource-admin-password"
    assert refs["XNAT_DATASOURCE_PASSWORD"] == "xnat-datasource-password"
    assert refs["POSTGRES_PASSWORD"] != refs["XNAT_DATASOURCE_PASSWORD"]


def test_xnat_db_does_not_shadow_the_image_init_directory():
    """Nothing may mount over ``/docker-entrypoint-initdb.d`` without a subPath.

    A directory-mounted volume replaces the directory, hiding the image's baked
    ``XNAT.sql`` — the only thing that creates the `xnat` role as a separate,
    non-superuser role. The failure is silent: Postgres starts, and the two
    roles have collapsed into one.
    """
    lines = XNAT_DB_TEMPLATE.read_text().splitlines()

    for index, line in enumerate(lines):
        if "mountPath:" not in line or "/docker-entrypoint-initdb.d" not in line:
            continue
        following = " ".join(lines[index + 1 : index + 3])
        assert "subPath:" in following, "mount over /docker-entrypoint-initdb.d must use subPath (it hides XNAT.sql)"


def test_xnat_db_does_not_rename_the_superuser():
    """``POSTGRES_USER`` must stay unset so the superuser is not the `xnat` role.

    Setting it makes initdb create the superuser under that name, so `XNAT.sql`'s
    ``create role xnat`` has nothing separate to create and the deployment ends
    up with a single superuser role — the same collapse from the other direction.
    """
    assert not re.search(r"^\s*POSTGRES_USER:", XNAT_DB_TEMPLATE.read_text(), re.MULTILINE)


def test_default_values_declare_every_rendered_key():
    """``values.yaml`` must declare every key the Secret template can emit.

    It is the chart's operator-facing schema: a key absent from ``secrets.data``
    here is one an operator has no way to discover short of reading the
    templates. Only the default values file is held to this — ``values-secrets``
    and the CI fixture are per-environment and legitimately partial, since each
    key is optional at render time (``secrets.yaml`` guards every one with
    ``if index``).
    """
    content = (CHART_DIR / "values.yaml").read_text()
    missing = {key for key in _rendered_keys() if not re.search(rf"^\s+{re.escape(key)}:", content, re.MULTILINE)}

    assert missing == set(), f"values.yaml secrets.data is missing keys: {sorted(missing)}"
