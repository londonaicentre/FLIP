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

"""The trust governance document's chart wiring (FLIP#1259), read off the templates.

A Kubernetes trust must not be able to deploy cleanly while silently ignoring the governance
document the operator configured. The Compose stack mounts it; until this wiring existed the
Helm chart did not, so a trust's [disclosure]/[access]/[fl_privacy] rules were enforced on one
deployment shape and dropped on the other, with no error anywhere. The document is one file
read by two services, so the chart renders it into a ConfigMap that BOTH pod templates mount
read-only and point ``ACCESS_POLICY_FILE`` at — and the mounts carry subPath, which makes the
ConfigMap's key and each container's subPath one contract across three files.

These assertions parse the templates as text, for the reason ``test_chart_secrets.py`` gives:
they hold for every branch, and the feature is gated on a value whose default renders it away.
The rendered half lives beside the image-tag tests in ``tests/templates/test_governance_render.py``
(that is the CI job with helm). What text parsing adds is the scoping itself: a fragment that is
present but OUTSIDE the guard is the same defect as a missing fragment, and only the text says
which of the two it is.
"""

import json
import re
from pathlib import Path

import yaml

CHART_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = CHART_DIR / "templates"
VALUES_FILE = CHART_DIR / "values.yaml"
SCHEMA_FILE = CHART_DIR / "values.schema.json"
CONFIGMAP_TEMPLATE = TEMPLATES_DIR / "governance-configmap.yaml"
DATA_ACCESS_TEMPLATE = TEMPLATES_DIR / "data-access-api.yaml"
FL_CLIENT_TEMPLATE = TEMPLATES_DIR / "fl-client.yaml"

#: The opt-in guard the ConfigMap and both pod templates carry. Spelled once, so a reworded
#: guard fails loudly rather than letting every fragment below read as unguarded.
GOVERNANCE_GUARD = "{{- if .Values.governance.document }}"
#: The path both services read — the same one Compose mounts at /app/governance.toml.
MOUNT_PATH = "/app/governance.toml"
#: The name both pod templates must reference to hash the ConfigMap they mount.
CONFIGMAP_TEMPLATE_NAME = "governance-configmap.yaml"
#: The volume name shared by both pod templates.
VOLUME_NAME = "governance"
CONFIGMAP_NAME = 'name: {{ include "flip-trust.fullname" . }}-governance'
CHECKSUM_DIRECTIVE = (
    f'checksum/{VOLUME_NAME}: '
    '{{ include (print $.Template.BasePath "/governance-configmap.yaml") . | sha256sum }}'
)


def _guarded_regions(text: str) -> list[str]:
    """Return the text between each governance guard and its closing ``{{- end }}``.

    Directive indentation is the only nesting signal in a Go template read as text. Every
    template in this chart closes a block with ``{{- end }}`` at the same indent as the
    ``{{- if }}`` that opened it, so a same-indent match is the closing directive. A guard
    with no closing directive is an error rather than a skipped region: an unclosed guard
    would swallow the rest of the template and make the containment checks below vacuous.

    Args:
        text (str): Template source.

    Returns:
        list[str]: One entry per guard, holding that guard's body.
    """
    lines = text.splitlines()
    regions: list[str] = []

    for index, line in enumerate(lines):
        if line.strip() != GOVERNANCE_GUARD:
            continue
        guard_indent = len(line) - len(line.lstrip())
        for offset in range(index + 1, len(lines)):
            candidate = lines[offset]
            if candidate.strip() == "{{- end }}" and len(candidate) - len(candidate.lstrip()) == guard_indent:
                regions.append("\n".join(lines[index + 1 : offset]))
                break
        else:
            raise AssertionError(f"line {index + 1}: a governance guard is never closed at its own indent")

    return regions


def _configmap_data_key() -> str:
    """The single data key templates/governance-configmap.yaml publishes.

    Returns:
        str: The key, e.g. ``governance.toml``.
    """
    match = re.search(r"^  ([A-Za-z0-9._-]+): \|$", CONFIGMAP_TEMPLATE.read_text(), re.M)
    assert match, "governance-configmap.yaml no longer publishes a block-scalar data key"
    return match.group(1)


def _governance_fragments(template: Path) -> list[str]:
    """The fragments that make up the wiring in one pod template, with the key resolved.

    The subPath asserted for each mount is read from the ConfigMap's own template rather than
    typed here, so a rename on either side cannot leave both fragments matching while the
    mount resolves to nothing.

    Args:
        template (Path): Pod template to collect fragments for.

    Returns:
        list[str]: Fragments, each of which must sit inside a governance guard.
    """
    key = _configmap_data_key()
    return [
        CHECKSUM_DIRECTIVE,
        "- name: ACCESS_POLICY_FILE",
        f"value: {MOUNT_PATH}",
        f"- name: {VOLUME_NAME}",
        f"mountPath: {MOUNT_PATH}",
        f"subPath: {key}",
        "readOnly: true",
        "configMap:",
        CONFIGMAP_NAME,
    ]


def _values() -> dict:
    """values.yaml as YAML.

    Returns:
        dict: The loaded default values.
    """
    return yaml.safe_load(VALUES_FILE.read_text())


def test_values_declare_the_governance_document_and_default_it_off() -> None:
    """The document is operator-facing config, so it belongs in values.yaml — and empty.

    A default carrying any document would change every existing install's posture on upgrade;
    the feature is opt-in, so the default is the absence of the document.
    """
    governance = _values().get("governance")

    assert isinstance(governance, dict), "values.yaml does not declare a top-level governance: block"
    assert governance.get("document") == "", "governance.document must default to the empty string (opt-in)"
    assert set(governance) == {"document"}, f"unexpected keys under governance: {sorted(governance)}"


def test_the_values_schema_declares_the_governance_document() -> None:
    """``helm`` validates values against the schema; an undeclared key is only a warning.

    The schema is the chart's machine-readable interface (``helm lint``, scripts/validate.sh),
    so the document has to be declared there — a string, defaulting to empty.
    """
    schema = json.loads(SCHEMA_FILE.read_text())
    document = schema["properties"]["governance"]["properties"]["document"]

    assert document["type"] == "string"
    assert document.get("default") == ""


def test_the_configmap_is_rendered_only_with_a_document() -> None:
    """The ConfigMap exists exactly when there is a document to carry.

    Gated on the same value the pod templates mount. Rendered unconditionally it would deploy
    an empty document to a trust that never configured one, and nothing distinguishes that
    from a policy that is simply permissive.
    """
    text = CONFIGMAP_TEMPLATE.read_text()

    assert GOVERNANCE_GUARD in text, f"governance-configmap.yaml lost its guard {GOVERNANCE_GUARD!r}"
    assert text.index(GOVERNANCE_GUARD) < text.index("kind: ConfigMap"), (
        "the ConfigMap is declared outside its guard — an install with no document would get one anyway"
    )
    assert "{{ .Values.governance.document | indent 4 }}" in text, (
        "the document is no longer rendered into the ConfigMap body"
    )
    assert text.count("{{- end }}") == 1, "governance-configmap.yaml gained a second block; the guard is unclear"


def test_both_pod_templates_mount_the_document_read_only_under_the_guard() -> None:
    """data-access-api and the fl-client each read the same file, so each must mount it.

    Every fragment — the checksum annotation, the env var, the volumeMount, the volume — has
    to sit INSIDE the guard. Present-but-unconditional is the defect this suite exists for: on
    a release with no document configured it would mount a ConfigMap that is never rendered,
    leaving the pod stuck at ContainerCreating, and it would also change the pod spec of every
    existing trust on upgrade, which is what the opt-in default promises not to do.
    """
    for template in (DATA_ACCESS_TEMPLATE, FL_CLIENT_TEMPLATE):
        text = template.read_text()
        regions = _guarded_regions(text)
        guarded = "\n".join(regions)

        assert len(regions) >= 4, (
            f"{template.name}: expected separate guarded annotation/env/volumeMount/volume blocks, found "
            f"{len(regions)} governance guard(s)"
        )
        for fragment in _governance_fragments(template):
            assert fragment in text, f"{template.name}: governance wiring lost {fragment!r}"
            assert fragment in guarded, (
                f"{template.name}: {fragment!r} renders outside the {GOVERNANCE_GUARD!r} guard — a trust with no "
                "document would get that object, or an env var pointing at a file nothing mounts"
            )

    # Positive controls: fragments the governance guard must NOT cover, so the containment
    # assertion above cannot pass by the guard swallowing the whole template.
    for template, fragment in (
        (DATA_ACCESS_TEMPLATE, "- name: TRUST_INTERNAL_SERVICE_KEY\n"),
        (FL_CLIENT_TEMPLATE, "- name: FL_SITE_PRIVACY_POLICY"),
    ):
        regions = _guarded_regions(template.read_text())
        assert not any(fragment in region for region in regions), (
            f"{template.name}: {fragment!r} moved inside the governance guard — the guard now spans unrelated wiring"
        )


def test_the_configmap_key_the_mount_subpath_and_the_volume_name_are_one_contract() -> None:
    """Three files have to agree: the ConfigMap's key, each subPath, and the volume's name.

    The subPath is what makes the mount a single file beside the image's own tree rather than a
    volume over it. A key typo on either side renders a pod that starts happily with an empty
    /app/governance.toml — the service then refuses to boot, but the operator reads a policy
    error rather than a chart error.
    """
    key = _configmap_data_key()

    assert key == "governance.toml", "the mounted filename must stay the one Compose uses at /app/governance.toml"

    for template in (DATA_ACCESS_TEMPLATE, FL_CLIENT_TEMPLATE):
        text = template.read_text()
        assert f"subPath: {key}" in text, f"{template.name}: does not mount the ConfigMap's {key!r} key"
        assert CONFIGMAP_NAME in text, (
            f"{template.name}: the {VOLUME_NAME} volume no longer points at the chart's governance ConfigMap"
        )


def test_both_pod_templates_hash_the_governance_configmap_into_a_rollout_checksum() -> None:
    """Editing the document must roll both pods, and only the governance ConfigMap's template.

    A ConfigMap content change restarts nothing by itself. The annotation has to hash the
    template that renders the ConfigMap — hashing anything else (or dropping the annotation)
    leaves the old rules in force until something else happens to restart the pod, which is
    exactly the silent-staleness class templates/fl-client.yaml documents for FL_SITE_PRIVACY_*.
    """
    for template in (DATA_ACCESS_TEMPLATE, FL_CLIENT_TEMPLATE):
        text = template.read_text()

        assert f"/{CONFIGMAP_TEMPLATE_NAME}" in text, (
            f"{template.name}: the checksum annotation no longer hashes {CONFIGMAP_TEMPLATE_NAME}"
        )
        assert CHECKSUM_DIRECTIVE in text, (
            f"{template.name}: checksum/{VOLUME_NAME} annotation changed shape — verify it still hashes the "
            "rendered governance ConfigMap before updating this assertion"
        )
