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
Helm chart did not, so a trust's [disclosure]/[access]/[fl_privacy.nvflare] rules were enforced on
one deployment shape and dropped on the other, with no error anywhere. The chart renders the
document into a ConfigMap. data-access-api mounts it read-only and points ``ACCESS_POLICY_FILE`` at
it. The NVFLARE fl-client never mounts it in its own container — researcher code runs there — but
in a governance-extract init container that writes the client's section alone to an emptyDir the
client reads. The mounts carry subPath, which makes the ConfigMap's key and each subPath one
contract across three files.

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

#: The opt-in guard the ConfigMap and the data-access-api template carry. Spelled once, so a
#: reworded guard fails loudly rather than letting every fragment below read as unguarded.
GOVERNANCE_GUARD = "{{- if .Values.governance.document }}"
#: The fl-client's guard: a document AND the NVFLARE backend. Nothing on Flower reads a site
#: privacy section, so the Flower pod gets none of the wiring.
FL_CLIENT_GUARD = "{{- if $governed }}"
FL_CLIENT_GUARD_DEFINITION = '{{- $governed := and .Values.governance.document (eq .Values.flBackend "nvflare") }}'
#: Where the fl-client reads its extracted section.
EXTRACT_PATH = "/app/governance/governance.fl_privacy.toml"
#: Where data-access-api and the fl-client's extract step read the document — the path Compose uses.
MOUNT_PATH = "/app/governance.toml"
#: The name both pod templates must reference to hash the ConfigMap they mount.
CONFIGMAP_TEMPLATE_NAME = "governance-configmap.yaml"
#: The volume name shared by both pod templates.
VOLUME_NAME = "governance"
CONFIGMAP_NAME = 'name: {{ include "flip-trust.fullname" . }}-governance'
CHECKSUM_DIRECTIVE = (
    f'checksum/{VOLUME_NAME}: {{{{ include (print $.Template.BasePath "/governance-configmap.yaml") . | sha256sum }}}}'
)
#: The fl-client's checksum: the digest of its own section (sync-kit), else the whole document's.
FL_CLIENT_CHECKSUM_DIRECTIVE = (
    f"checksum/{VOLUME_NAME}: {{{{ .Values.governance.flPrivacyChecksum | default "
    f'(include (print $.Template.BasePath "/governance-configmap.yaml") . | sha256sum) }}}}'
)


def _guarded_regions(text: str, guard: str = GOVERNANCE_GUARD) -> list[str]:
    """Return the text between each governance guard and its closing ``{{- end }}``.

    Directive indentation is the only nesting signal in a Go template read as text. Every
    template in this chart closes a block with ``{{- end }}`` at the same indent as the
    ``{{- if }}`` that opened it, so a same-indent match is the closing directive. A guard
    with no closing directive is an error rather than a skipped region: an unclosed guard
    would swallow the rest of the template and make the containment checks below vacuous.

    Args:
        text (str): Template source.
        guard (str): The guard directive to collect.

    Returns:
        list[str]: One entry per guard, holding that guard's body.
    """
    lines = text.splitlines()
    regions: list[str] = []

    for index, line in enumerate(lines):
        if line.strip() != guard:
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
        list[str]: Fragments, each of which must sit inside that template's governance guard.
    """
    key = _configmap_data_key()
    if template == FL_CLIENT_TEMPLATE:
        return [
            FL_CLIENT_CHECKSUM_DIRECTIVE,
            "- name: governance-extract",
            "site_policy --extract /app/governance.toml",
            f"mountPath: {MOUNT_PATH}",
            f"subPath: {key}",
            "- name: ACCESS_POLICY_FILE",
            f"value: {EXTRACT_PATH}",
            "mountPath: /app/governance",
            "readOnly: true",
            "configMap:",
            CONFIGMAP_NAME,
            "emptyDir:",
        ]
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


def _guard(template: Path) -> str:
    return FL_CLIENT_GUARD if template == FL_CLIENT_TEMPLATE else GOVERNANCE_GUARD


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
    assert governance.get("flPrivacyChecksum") == "", "flPrivacyChecksum is sync-kit's to write; it defaults empty"
    assert set(governance) == {"document", "flPrivacyChecksum"}, (
        f"unexpected keys under governance: {sorted(governance)}"
    )


def test_the_values_schema_declares_the_governance_document() -> None:
    """``helm`` validates values against the schema; an undeclared key is only a warning.

    The schema is the chart's machine-readable interface (``helm lint``, scripts/validate.sh),
    so the document has to be declared there — a string, defaulting to empty.
    """
    schema = json.loads(SCHEMA_FILE.read_text())
    governance = schema["properties"]["governance"]
    document = governance["properties"]["document"]

    assert document["type"] == "string"
    assert document.get("default") == ""
    # A misspelt key (governance.documnet) must fail the install, not render with no policy.
    assert governance["additionalProperties"] is False


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


def test_both_pod_templates_carry_their_wiring_under_the_guard() -> None:
    """data-access-api mounts the document; the NVFLARE fl-client extracts its section.

    Every fragment has to sit INSIDE its template's guard. Present-but-unconditional is the
    defect this suite exists for: on a release with no document configured it would mount a
    ConfigMap that is never rendered, leaving the pod stuck at ContainerCreating, and it would
    also change the pod spec of every existing trust on upgrade.
    """
    for template in (DATA_ACCESS_TEMPLATE, FL_CLIENT_TEMPLATE):
        text = template.read_text()
        regions = _guarded_regions(text, _guard(template))
        guarded = "\n".join(regions)

        assert len(regions) >= 4, (
            f"{template.name}: expected separate guarded blocks, found {len(regions)} governance guard(s)"
        )
        for fragment in _governance_fragments(template):
            assert fragment in text, f"{template.name}: governance wiring lost {fragment!r}"
            assert fragment in guarded, (
                f"{template.name}: {fragment!r} renders outside the {_guard(template)!r} guard — a trust with no "
                "document would get that object, or an env var pointing at a file nothing mounts"
            )

    # Positive controls: fragments the guard must NOT cover, so the containment assertion above
    # cannot pass by the guard swallowing the whole template.
    for template, fragment in (
        (DATA_ACCESS_TEMPLATE, "- name: TRUST_INTERNAL_SERVICE_KEY\n"),
        (FL_CLIENT_TEMPLATE, "- name: FL_SITE_PRIVACY_POLICY"),
    ):
        regions = _guarded_regions(template.read_text(), _guard(template))
        assert not any(fragment in region for region in regions), (
            f"{template.name}: {fragment!r} moved inside the governance guard — the guard now spans unrelated wiring"
        )


def test_the_fl_client_guard_is_the_document_on_nvflare() -> None:
    """Flower reads no site privacy section: wiring it there reported a policy nothing enforced."""
    assert FL_CLIENT_GUARD_DEFINITION in FL_CLIENT_TEMPLATE.read_text()


def test_the_fl_client_container_never_mounts_the_whole_document() -> None:
    """Researcher code runs in the fl-client container as the same user as the client; the whole
    document there hands training code every [access] rule. Only the init container mounts it."""
    text = FL_CLIENT_TEMPLATE.read_text()
    containers = text[text.index("      containers:") :]

    assert f"mountPath: {MOUNT_PATH}" not in containers, "the fl-client container mounts the whole document"
    assert f"mountPath: {MOUNT_PATH}" in text[: text.index("      containers:")], "the init container lost its mount"


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


def test_both_pod_templates_carry_a_rollout_checksum() -> None:
    """Editing what a pod reads must roll it.

    A ConfigMap content change restarts nothing by itself. data-access-api hashes the template
    that renders the ConfigMap. The fl-client takes sync-kit's digest of its own section, so an
    [access]-only edit does not interrupt a running FL job, and falls back to the whole
    document's hash when no digest was supplied.
    """
    data_access = DATA_ACCESS_TEMPLATE.read_text()
    fl_client = FL_CLIENT_TEMPLATE.read_text()

    assert CHECKSUM_DIRECTIVE in data_access, (
        f"{DATA_ACCESS_TEMPLATE.name}: checksum/{VOLUME_NAME} annotation changed shape — verify it still hashes "
        "the rendered governance ConfigMap before updating this assertion"
    )
    assert FL_CLIENT_CHECKSUM_DIRECTIVE in fl_client, (
        f"{FL_CLIENT_TEMPLATE.name}: checksum/{VOLUME_NAME} annotation changed shape"
    )
