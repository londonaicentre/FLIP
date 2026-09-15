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
REPO_ROOT = CHART_DIR.parents[2]  # deploy/providers/kubernetes -> repo root
CONFIGURE_XNAT = REPO_ROOT / "trust" / "xnat" / "xnat" / "config" / "configure-xnat.sh"

# The activation POST, and the gate that must not precede it. Matched on the calls
# rather than the surrounding prose — the comments name both endpoints in both orders.
#
# These live in configure-xnat.sh, not in the Job template: since FLIP#993 the initContainer
# runs that script instead of carrying its own inline copy, so the ordering is asserted at its
# one remaining source. The Job is checked separately for the delegation that makes it apply.
ACTIVATION_CALL = 'xnat_curl -X POST "$XNAT_URL/xapi/siteConfig"'
PLUGIN_GATE_POLL = "bash wait-for-xnat-plugins.sh"

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


def test_the_init_container_delegates_to_configure_xnat() -> None:
    """The ordering invariant below is only binding if the Job actually runs that script."""
    script = _init_container_script()

    assert "bash configure-xnat.sh" in script, (
        "the xnat-init initContainer no longer runs configure-xnat.sh — if it has gone back to an "
        "inline copy of the configuration, assert the activation ordering against that copy too, "
        "or this suite guards a script the cluster never executes"
    )


def test_the_xnat_site_is_activated_before_the_plugin_route_gate() -> None:
    """Reversed, the gate can never see a 2xx and blames itself for the ordering."""
    script = CONFIGURE_XNAT.read_text()

    assert ACTIVATION_CALL in script, f"no site-activation call {ACTIVATION_CALL!r} in configure-xnat.sh"
    assert PLUGIN_GATE_POLL in script, f"no plugin-route gate {PLUGIN_GATE_POLL!r} in configure-xnat.sh"
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


# Every FLIP-built image the chart runs. Observability images and xnat-dcm2niix carry
# upstream versions of their own and are deliberately absent.
FLIP_IMAGE_VALUES = (
    "trustApi.image.tag",
    "imagingApi.image.tag",
    "dataAccessApi.image.tag",
    "flClient.image.tag",
    "omopDb.image.tag",
    "orthanc.image.tag",
    "xnat.web.image.tag",
    "xnat.db.image.tag",
    "xnat.nginx.image.tag",
)


def test_values_declares_the_release_pin() -> None:
    assert _values_declares("global.image.tag"), "values.yaml does not declare global.image.tag (FLIP#1204)"


@pytest.mark.parametrize("dotted", FLIP_IMAGE_VALUES)
def test_every_flip_image_tag_follows_the_release_pin(dotted: str) -> None:
    """`make upgrade-trust-k8s TAG=vX.Y.Z` sets one value, global.image.tag, and every FLIP-built
    image must follow it (FLIP#1204). A template that still reads `.Values.<svc>.image.tag`
    directly renders cleanly and silently keeps that one service on the old release — the
    mixed-version site the pin exists to prevent."""
    assert _values_declares(dotted), f"values.yaml no longer declares {dotted}"
    direct = f".Values.{dotted}"
    for template in sorted(TEMPLATES_DIR.glob("*.yaml")) + [TEMPLATES_DIR / "_helpers.tpl"]:
        for line in template.read_text().splitlines():
            if direct in line and "flip-trust.imageTag" not in line:
                raise AssertionError(
                    f"{template.name}: reads {direct} directly, bypassing flip-trust.imageTag: {line.strip()}"
                )


#: The compose OMOP_DB_TAG / ORTHANC_TAG / XNAT_TAG opt-outs, as chart values: the image
#: value each pin holds back, and the templates that must consult it.
IMAGE_PINS = (
    ("omopDb.image.pin", ".Values.omopDb.image.tag", ("omop-db.yaml", "omop-db-vocab-load-job.yaml")),
    ("orthanc.image.pin", ".Values.orthanc.image.tag", ("orthanc.yaml",)),
    ("xnat.image.pin", ".Values.xnat.web.image.tag", ("xnat-web.yaml", "xnat-init-job.yaml")),
    ("xnat.image.pin", ".Values.xnat.db.image.tag", ("xnat-db.yaml",)),
    ("xnat.image.pin", ".Values.xnat.nginx.image.tag", ("xnat-nginx.yaml",)),
)


@pytest.mark.parametrize(("pin", "own", "templates"), IMAGE_PINS)
def test_the_path_filtered_images_can_be_held_back_from_the_release_pin(
    pin: str, own: str, templates: tuple[str, ...]
) -> None:
    """omop-db, orthanc and the XNAT trio only rebuild when their own tree changes, so a sha- tag
    of any other commit has no such image; the kit's OMOP_DB_TAG / ORTHANC_TAG / XNAT_TAG hold
    them back from global.image.tag on compose, and `<svc>.image.pin` is the chart twin. Every
    image line that reads the service's own tag must also pass its pin, or `helm upgrade` would
    roll that image to a tag that does not exist and leave the StatefulSet stuck (FLIP#1204)."""
    assert _values_declares(pin), f"values.yaml does not declare {pin}"
    for name in templates:
        lines = [line for line in (TEMPLATES_DIR / name).read_text().splitlines() if own in line]
        assert lines, f"{name}: no image line reads {own}"
        for line in lines:
            assert f'"pin" .Values.{pin}' in line, f"{name}: image line ignores {pin}: {line.strip()}"


def test_the_pin_beats_the_release_pin_in_the_helper() -> None:
    helper = (TEMPLATES_DIR / "_helpers.tpl").read_text()
    assert "{{- .pin | default .global | default .own }}" in helper
