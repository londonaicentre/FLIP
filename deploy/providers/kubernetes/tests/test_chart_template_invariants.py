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

"""Ordering and templating invariants that a rendered chart cannot self-report.

Both guards here cover defects whose failure mode is a *plausible-looking* deploy
rather than an error:

* The xnat-init Job activates the site before gating on plugin routes. Reversed,
  every plugin route 302s to /setup, the gate can never see a 2xx, and the Job
  burns its whole budget before failing with "plugin routes did not register" —
  blaming the wait rather than the ordering that caused it. That is exactly how
  the ordering bug went unnoticed the first time, and the inline script is long
  and will be edited again.
* The omop-db probe fields are templated from values rather than hardcoded. A
  half-templated block renders cleanly, silently ignores an operator's override
  and — for a field values.yaml never declares — renders empty, taking the
  Kubernetes default. Two of the four fields have slipped through this block
  twice already.

These parse the templates as text for the same reason ``test_chart_secrets.py``
does: ``helm template`` only reaches the branches its values enable (XNAT is
disabled in the kind CI values), so a rendered-output check would silently skip
most of the chart.
"""

from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = CHART_DIR / "templates"
VALUES_FILE = CHART_DIR / "values.yaml"
XNAT_INIT_JOB = TEMPLATES_DIR / "xnat-init-job.yaml"
OMOP_DB_TEMPLATE = TEMPLATES_DIR / "omop-db.yaml"

# The activation POST, and the gate that must not precede it. Matched on the calls
# rather than the surrounding prose — the comments name both endpoints in both orders.
ACTIVATION_CALL = 'xnat_curl -X POST "${XNAT_URL}/xapi/siteConfig"'
PLUGIN_GATE_POLL = '"${XNAT_URL}/xapi/dqr/settings"'

PROBE_FIELDS = ("initialDelaySeconds", "periodSeconds", "timeoutSeconds", "failureThreshold")


def _init_container_script() -> str:
    """Return the xnat-init Job's ``initContainers`` block with comment lines dropped.

    The same file carries a second, unrelated activation script further down (the
    manual-recovery ConfigMap), so the slice is bounded by ``containers:``.

    Returns:
        str: The initContainers block, comments removed.
    """
    text = XNAT_INIT_JOB.read_text()
    start = text.index("\n      initContainers:")
    end = text.index("\n      containers:", start)
    body = text[start:end]

    return "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))


def _probe_fields(template: Path, probe: str) -> dict[str, str]:
    """Collect the scalar fields of one probe block in a template.

    Args:
        template (Path): Chart template to scan.
        probe (str): Probe key, e.g. ``livenessProbe``.

    Returns:
        dict[str, str]: ``{field name: value as written}`` for every field in PROBE_FIELDS.
    """
    fields: dict[str, str] = {}
    inside = False
    probe_indent = 0

    for line in template.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())

        if stripped == f"{probe}:":
            inside = True
            probe_indent = indent
            continue
        if inside:
            if indent <= probe_indent:
                break
            key, _, value = stripped.partition(":")
            if key in PROBE_FIELDS:
                fields[key] = value.strip()

    return fields


def _values_declares(dotted_path: str) -> bool:
    """Report whether values.yaml declares a dotted key path.

    Walks by indentation rather than parsing: the CI job installs pytest alone, so
    no YAML parser is available.

    Args:
        dotted_path (str): Key path, e.g. ``omopDb.probes.liveness.periodSeconds``.

    Returns:
        bool: True if every segment is nested under the one before it.
    """
    segments = dotted_path.split(".")
    depth = 0
    parent_indent = -1

    for line in VALUES_FILE.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())

        if depth and indent <= parent_indent:
            return False
        if stripped.split(":")[0] == segments[depth] and (depth == 0 or indent > parent_indent):
            depth += 1
            parent_indent = indent
            if depth == len(segments):
                return True

    return False


def test_the_xnat_site_is_activated_before_the_plugin_route_gate() -> None:
    """Reversed, the gate can never see a 2xx and blames itself for the ordering."""
    script = _init_container_script()

    assert ACTIVATION_CALL in script, f"no site-activation call {ACTIVATION_CALL!r} in the initContainer"
    assert PLUGIN_GATE_POLL in script, f"no plugin-route gate {PLUGIN_GATE_POLL!r} in the initContainer"
    assert script.index(ACTIVATION_CALL) < script.index(PLUGIN_GATE_POLL), (
        "the plugin-route gate runs before the site is activated: every plugin route 302s to "
        "/setup until activation, so the gate burns its whole budget and then reports "
        "'plugin routes did not register' rather than the ordering that caused it"
    )


@pytest.mark.parametrize("probe", ["livenessProbe", "readinessProbe"])
def test_every_omop_db_probe_field_is_templated_from_values(probe: str) -> None:
    """A hardcoded field ignores an operator's override without rendering any differently."""
    fields = _probe_fields(OMOP_DB_TEMPLATE, probe)
    values_key = probe[: -len("Probe")]

    assert set(fields) == set(PROBE_FIELDS), (
        f"omop-db {probe} declares {sorted(fields)}, expected all of {sorted(PROBE_FIELDS)} — "
        "the vocabulary-load hook needs every one of them raised above the Kubernetes defaults"
    )
    for field, written in fields.items():
        expected = f"{{{{ .Values.omopDb.probes.{values_key}.{field} }}}}"
        assert written == expected, f"omop-db {probe}.{field} is {written!r}, expected {expected!r}"


@pytest.mark.parametrize("probe", ["liveness", "readiness"])
def test_values_declares_every_omop_db_probe_field(probe: str) -> None:
    """A field the template reads but values.yaml omits renders empty, not as an error."""
    for field in PROBE_FIELDS:
        path = f"omopDb.probes.{probe}.{field}"
        assert _values_declares(path), f"values.yaml does not declare {path}; the rendered probe field would be empty"
