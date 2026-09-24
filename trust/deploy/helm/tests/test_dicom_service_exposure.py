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
``test_chart_template_invariants.py`` do: **the pytest job that runs this file has no helm**, so a
rendered-output check here would error rather than assert. (It is not that XNAT is off in the kind
values — ``helm template --set`` renders XNAT perfectly well; that was the earlier, wrong reason
given here.) The rendered counterpart lives in ``tests/templates/test_dicom_service_render.py``,
which the chart workflow's helm-template job runs and which asserts per rendered Service document
what these can only assert about template text.
"""

import re
from pathlib import Path

CHART_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = CHART_DIR / "templates"
XNAT_WEB_TEMPLATE = TEMPLATES_DIR / "xnat-web.yaml"
ORTHANC_TEMPLATE = TEMPLATES_DIR / "orthanc.yaml"
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

    The partial does also read ``service.type``, in a separate check that refuses an externally
    exposed web console (see ``test_real_pacs_refuses_an_externally_exposed_web_console``). So
    this asserts the *reachability* refusals specifically — the ClusterIP one and the NodePort
    one — rather than the field's absence from the whole partial, which would forbid that check.
    """
    text = HELPERS_TEMPLATE.read_text()
    define_start = text.index('{{- define "flip-trust.validatePacsReachable" -}}')
    define_end = text.index("{{- end }}\n{{- end }}\n{{- end }}\n{{- end }}", define_start)
    body = text[define_start:define_end]

    assert ".Values.xnat.web.dicomService.type" in body

    reachability_conditions = [
        line
        for line in body.splitlines()
        if line.lstrip().startswith("{{- if")
        and ("ClusterIP" in line or "NodePort" in line)
        and "dicomService" in line
    ]
    assert reachability_conditions, (
        "no reachability condition keys off dicomService.type — the guard is checking the wrong "
        "field for whether the DICOM receiver is reachable"
    )
    for line in body.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("{{- if"):
            continue
        if ".Values.xnat.web.service.type" in stripped and "dicomService" not in stripped:
            # The one legitimate use: the web-console exposure refusal, which is about the console
            # and says so. Anything else is a reachability check reading the wrong field.
            assert 'ne .Values.xnat.web.service.type "ClusterIP"' in stripped, (
                f"validatePacsReachable branches on the web console's service.type in {stripped!r} "
                "— DICOM reachability is controlled by dicomService.type, not this field"
            )


def test_validate_pacs_reachable_rejects_wide_open_ingress_cidr() -> None:
    """A ``0.0.0.0/0`` ingress CIDR for the DICOM port must fail the chart at render time.

    Scoping the PACS ingress rule to the whole internet rather than the PACS's own address was
    found live on a real trust (the chart offered no narrower option at the time), directly
    contradicting NETWORK-POLICY.md's own "scope to the PACS itself, never the whole trust
    network" guidance. This must be caught before it can happen again.

    The check is an exact-literal tripwire, not a CIDR validator — Helm cannot evaluate a CIDR, so
    0.0.0.0/1 and ::/0 deliberately pass it. What it must not do is pretend otherwise, hence the
    assertions on the fail message below.
    """
    text = HELPERS_TEMPLATE.read_text()

    assert '"0.0.0.0/0"' in text, (
        "validatePacsReachable has no guard against a wide-open (0.0.0.0/0) DICOM ingress CIDR"
    )
    assert re.search(r'eq \(trim \.\) "0\.0\.0\.0/0"', text), (
        "the 0.0.0.0/0 comparison must trim whitespace first — matched exactly, a trailing space "
        "('0.0.0.0/0 ') walks straight past the guard"
    )

    # Bind the literal to an actual `fail`, not to one appearing within N characters of it: the
    # earlier proximity form passed on any chart where the word happened to fall nearby, including
    # one where the comparison set a variable that nothing ever acted on. Take the span from the
    # comparison to the end of the partial and require a fail inside it whose message names the
    # value being refused.
    comparison_at = text.index('eq (trim .) "0.0.0.0/0"')
    tail = text[comparison_at:]

    assert "{{- fail (printf" in tail, (
        "the 0.0.0.0/0 comparison is not followed by a fail — a guard that computes a verdict and "
        "renders anyway is decorative"
    )
    fail_message = tail[tail.index("{{- fail (printf"):]
    assert "allowedIngressCIDRsWithPorts contains 0.0.0.0/0" in fail_message, (
        "the fail that follows the 0.0.0.0/0 comparison does not name it — an operator cannot act "
        "on a refusal that does not say what was refused"
    )
    assert "0.0.0.0/1" in fail_message[:1200], (
        "the 0.0.0.0/0 fail message no longer names a case it does not catch. It is an "
        "exact-literal tripwire, not a wide-CIDR validator, and the message must not read as the "
        "latter — an operator would take the render passing as the chart having checked their CIDR."
    )


def test_real_pacs_refuses_an_externally_exposed_web_console() -> None:
    """A real PACS plus a non-ClusterIP ``xnat.web.service.type`` must fail the render.

    Before the Service split that one field carried both ports, so ``NodePort`` on it was how an
    operator exposed DICOM. The value survives a `helm upgrade`, where it now exposes the Tomcat
    console — admin login and archive metadata on a node address — and does nothing for retrieval.
    Documenting the console as "always ClusterIP" did not make it so; this is the check that does.
    The rendered proof is in ``tests/templates/test_dicom_service_render.py``.
    """
    text = HELPERS_TEMPLATE.read_text()
    define_start = text.index('{{- define "flip-trust.validatePacsReachable" -}}')
    body = text[define_start:]

    assert 'if ne .Values.xnat.web.service.type "ClusterIP"' in body, (
        "validatePacsReachable does not refuse an externally-typed xnat.web.service.type for a "
        "real PACS — an upgrade of an install that set it to reach DICOM silently moves that "
        "exposure onto the web console"
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


def _orthanc_xnat_modality() -> tuple[str, str]:
    """Pull the host and port out of Orthanc's ``ORTHANC__DICOM_MODALITIES`` XNAT entry.

    Returns:
        tuple[str, str]: ``(host, port_expression)`` — the host as the literal Service name it
        dials, and the port left as its raw template expression so a caller can compare it against
        the Service the chart actually renders.
    """
    orthanc = ORTHANC_TEMPLATE.read_text()
    modality = re.search(r'"XNAT":\s*\{.*?"Host":\s*"([^"]+)".*?"Port":\s*(\{\{.*?\}\})', orthanc)
    assert modality, "could not find the XNAT entry in orthanc.yaml's ORTHANC__DICOM_MODALITIES"

    return modality.group(1), modality.group(2)


def test_orthanc_modality_dials_the_service_carrying_dicom_scp() -> None:
    """Orthanc's XNAT modality must resolve to a Service that actually carries ``dicom-scp``.

    Splitting the DICOM SCP onto ``xnat-web-dicom`` takes the DICOM port off ``xnat-web``, so the
    C-STORE destination XNAT hands the PACS has to move with it. Nothing else in this file would
    notice if it did not: every Service above can be shaped exactly right while Orthanc dials one
    carrying only ``tomcat``, which breaks image retrieval on every default K8s install and the
    ``smoke-cstore`` target that drives a C-STORE through this same modality entry.
    """
    host, _ = _orthanc_xnat_modality()
    services = _service_blocks()

    assert host in services, (
        f"orthanc.yaml's XNAT modality dials Service {host!r}, which the chart never renders — the "
        f"C-STORE destination must be one of {sorted(services)}"
    )
    assert "name: dicom-scp" in services[host], (
        f"orthanc.yaml's XNAT modality dials Service {host!r}, which does not carry the dicom-scp "
        "port. The DICOM SCP is on xnat-web-dicom; after the split xnat-web carries tomcat only, so "
        "a C-STORE aimed there never reaches XNAT."
    )


def test_orthanc_modality_port_matches_the_dicom_scp_port() -> None:
    """The modality's port and the Service's ``port:`` must be the same expression, not two literals.

    Both sides read ``xnat.web.dicomPort``; asserting they agree keeps a future edit to one of them
    from silently aiming C-STORE at a port the receiver is not listening on.
    """
    _, modality_port = _orthanc_xnat_modality()
    dicom = _service_blocks()["xnat-web-dicom"]
    service_port = re.search(r"^\s*- port:\s*(\{\{.*?\}\})\s*$", dicom, re.MULTILINE)

    assert service_port, "xnat-web-dicom declares no templated port: for its dicom-scp port"

    def rendered_value(expression: str) -> str:
        """Reduce a sprig expression to the value it renders, ignoring output formatting.

        ``| quote`` changes how the value is emitted, not what it is, so it is dropped on both
        sides — comparing the raw strings would flag a difference that does not exist.
        """
        parts = [part.strip() for part in expression.strip("{} \t").split("|")]

        return "|".join(part for part in parts if part not in {"quote", "squote"})

    assert rendered_value(modality_port) == rendered_value(service_port.group(1)), (
        f"orthanc.yaml dials port {rendered_value(modality_port)} but xnat-web-dicom listens on "
        f"{rendered_value(service_port.group(1))} — the C-STORE destination must be the port that "
        "Service exposes"
    )
