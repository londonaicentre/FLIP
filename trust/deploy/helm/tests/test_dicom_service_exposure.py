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

"""Guards for splitting the DICOM SCP onto its own externally-exposable Service.

The chart used to render one ``xnat-web`` Service carrying both the Tomcat/web-console port and
the DICOM SCP port, with a single ``xnat.web.service.type``. That meant the only way to expose
DICOM to an external PACS (NodePort or LoadBalancer) also exposed the web console on the same
external address — and a real trust's DICOM Service was found running unmanaged outside Helm
with an ingress NetworkPolicy scoped to ``0.0.0.0/0`` (the whole internet) rather than the PACS,
because there was no chart-native way to do it narrowly.

The DICOM SCP now gets its own Service (``xnat-web-dicom``), driven by its own
``xnat.web.dicomService.type``, so exposing it externally can never expose the web console with
it. These parse the templates as text for the same reason ``test_chart_secrets.py`` and
``test_chart_template_invariants.py`` do: ``helm template`` only reaches the branches its values
enable, and XNAT is disabled in the kind CI values, so a rendered-output check would silently
skip this file entirely.
"""

import re
from pathlib import Path

CHART_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = CHART_DIR / "templates"
XNAT_WEB_TEMPLATE = TEMPLATES_DIR / "xnat-web.yaml"
HELPERS_TEMPLATE = TEMPLATES_DIR / "_helpers.tpl"
NETWORK_POLICY_TEMPLATE = TEMPLATES_DIR / "network-policy.yaml"
VALUES_FILE = CHART_DIR / "values.yaml"


def _service_blocks() -> dict[str, str]:
    """Split ``xnat-web.yaml`` into its ``kind: Service`` documents, keyed by ``metadata.name``.

    Returns:
        dict[str, str]: ``{service name: full YAML document text}``.
    """
    text = XNAT_WEB_TEMPLATE.read_text()
    blocks: dict[str, str] = {}

    for doc in text.split("\n---\n"):
        if "kind: Service" not in doc:
            continue
        name_match = re.search(r"^\s*name:\s*(\S+)\s*$", doc, re.MULTILINE)
        assert name_match, f"a Service document has no metadata.name:\n{doc}"
        blocks[name_match.group(1)] = doc

    return blocks


def test_dicom_and_web_services_are_split() -> None:
    """The web console and the DICOM SCP are two Services, not one shared by both ports."""
    blocks = _service_blocks()

    assert "xnat-web" in blocks, "the Tomcat/web-console Service (xnat-web) is missing"
    assert "xnat-web-dicom" in blocks, "the DICOM SCP Service (xnat-web-dicom) is missing"

    web, dicom = blocks["xnat-web"], blocks["xnat-web-dicom"]

    assert "name: tomcat" in web, "xnat-web must still carry the tomcat port"
    assert "name: dicom-scp" not in web, (
        "xnat-web (the web console Service) carries the dicom-scp port — exposing this Service "
        "externally would expose the web console alongside DICOM, the exact bug this split fixes"
    )
    assert "name: dicom-scp" in dicom, "xnat-web-dicom must carry the dicom-scp port"
    assert "name: tomcat" not in dicom, (
        "xnat-web-dicom carries the tomcat port — it must expose DICOM only, never the web console"
    )


def test_web_service_type_is_independent_of_dicom_service_type() -> None:
    """Each Service's ``type:`` is driven by its own values field, not a shared one."""
    blocks = _service_blocks()

    assert "type: {{ .Values.xnat.web.service.type }}" in blocks["xnat-web"], (
        "xnat-web's type must come from xnat.web.service.type"
    )
    assert "type: {{ .Values.xnat.web.dicomService.type }}" in blocks["xnat-web-dicom"], (
        "xnat-web-dicom's type must come from xnat.web.dicomService.type, not the shared "
        "xnat.web.service.type — otherwise setting one to LoadBalancer/NodePort for DICOM also "
        "changes the web console's exposure"
    )


def test_dicom_service_gets_local_traffic_policy_for_every_external_type() -> None:
    """``externalTrafficPolicy: Local`` applies to NodePort AND LoadBalancer, not NodePort only.

    Under the default `Cluster` policy, kube-proxy SNATs external traffic to the node address
    before it reaches the pod, so an ingress NetworkPolicy scoped to the PACS's real CIDR never
    matches and C-STORE retrievals silently time out (FLIP#993). That applies identically whether
    the Service is NodePort or LoadBalancer — gating this on ``eq ... "NodePort"`` alone would
    leave the LoadBalancer path with the exact bug the guard exists to prevent.
    """
    dicom = _service_blocks()["xnat-web-dicom"]

    assert re.search(r'\{\{-\s*if ne \.Values\.xnat\.web\.dicomService\.type "ClusterIP"\s*\}\}', dicom), (
        "externalTrafficPolicy: Local must be gated on dicomService.type != ClusterIP (i.e. cover "
        "both NodePort and LoadBalancer), not narrowly on NodePort alone"
    )
    assert "externalTrafficPolicy: Local" in dicom

    # It must sit inside that guard (not rendered unconditionally for a ClusterIP/Orthanc install):
    # the guard opens right after `type: {{ ... }}` and closes before the `ports:` block, so the
    # line must fall inside that span rather than after the closing {{- end }}.
    guard_open = dicom.index('{{- if ne .Values.xnat.web.dicomService.type "ClusterIP" }}')
    guard_close = dicom.index("{{- end }}", guard_open)
    local_line_idx = dicom.index("externalTrafficPolicy: Local")
    assert guard_open < local_line_idx < guard_close, (
        "externalTrafficPolicy: Local falls outside its ClusterIP guard — it must be conditional, "
        "not rendered unconditionally"
    )


def test_dicom_node_port_pin_is_scoped_to_nodeport_type_only() -> None:
    """The ``nodePort:`` pin only makes sense — and must only render — for ``type: NodePort``."""
    dicom = _service_blocks()["xnat-web-dicom"]

    pin_guard = re.compile(
        r'\{\{-\s*if and \.Values\.xnat\.web\.dicomNodePort'
        r' \(eq \.Values\.xnat\.web\.dicomService\.type "NodePort"\)\s*\}\}'
    )
    assert pin_guard.search(dicom), (
        "the nodePort: pin must be gated on dicomService.type == NodePort (and dicomNodePort being set)"
    )
    assert "nodePort: {{ .Values.xnat.web.dicomNodePort }}" in dicom


def test_validate_pacs_reachable_uses_dicom_service_type() -> None:
    """The reachability guard must key off ``dicomService.type``, not the shared ``service.type``.

    Keying this off the web console's Service type would validate the wrong field: a real PACS
    install with the web console on ClusterIP and DICOM properly exposed via dicomService would
    fail this check for no reason, and the inverse misconfiguration (DICOM stuck on ClusterIP)
    would pass it.
    """
    text = HELPERS_TEMPLATE.read_text()
    define_start = text.index('{{- define "flip-trust.validatePacsReachable" -}}')
    define_end = text.index("{{- end }}\n{{- end }}\n{{- end }}\n{{- end }}", define_start)
    body = text[define_start:define_end]

    assert ".Values.xnat.web.dicomService.type" in body
    assert ".Values.xnat.web.service.type" not in body, (
        "validatePacsReachable still references the web console's service.type — it must check "
        "dicomService.type, the field that actually controls DICOM reachability"
    )


def test_validate_pacs_reachable_rejects_wide_open_ingress_cidr() -> None:
    """A ``0.0.0.0/0`` ingress CIDR for the DICOM port must fail the chart at render time.

    Scoping the PACS ingress rule to the whole internet rather than the PACS's own address was
    found live on a real trust (the chart offered no narrower option at the time), directly
    contradicting NETWORK-POLICY.md's own "scope to the PACS itself, never the whole trust
    network" guidance. This must be caught before it can happen again.
    """
    text = HELPERS_TEMPLATE.read_text()

    assert '"0.0.0.0/0"' in text, (
        "validatePacsReachable has no guard against a wide-open (0.0.0.0/0) DICOM ingress CIDR"
    )
    assert "fail" in text[text.index('"0.0.0.0/0"'):text.index('"0.0.0.0/0"') + 400], (
        "the 0.0.0.0/0 check does not appear to fail the render"
    )


def test_network_policy_failure_message_names_dicom_service_type() -> None:
    """The missing-ingress-rule guard in network-policy.yaml must name the current field."""
    text = NETWORK_POLICY_TEMPLATE.read_text()

    assert "xnat.web.dicomService.type" in text, (
        "network-policy.yaml's fail message still tells the operator to set xnat.web.service.type "
        "for DICOM reachability — that field no longer controls it"
    )


def test_values_yaml_declares_dicom_service_with_clusterip_default() -> None:
    """``xnat.web.dicomService.type`` exists and defaults to ClusterIP (off, like the old field did)."""
    text = VALUES_FILE.read_text()

    assert re.search(r"dicomService:\s*\n\s*type:\s*ClusterIP", text), (
        "values.yaml must declare xnat.web.dicomService.type defaulting to ClusterIP"
    )
