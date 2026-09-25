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

"""The web/DICOM Service split, asserted per rendered Service document (FLIP#993).

``tests/test_dicom_service_exposure.py`` reads the templates as *text*, which is what lets it run
in the pytest job that has no helm. That leaves one class of fault invisible: a chart can satisfy
every textual assertion and still render a document where the DICOM port sits on the wrong
Service, or where the web console picks up an external type. The CI step that renders a real-PACS
install greps the whole document for ``nodePort: 8104`` — a match anywhere in a 3000-line render
satisfies it, including on the web console's Service.

These assert the render, split into documents and checked per Service, the way the workflow's
imaging-api ConfigMap step does. They need helm, so they live under ``tests/templates/`` with the
other rendered checks and the chart workflow's helm-template job runs them; the pytest job skips
this whole module.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")

# A real PACS, wired the way TROUBLESHOOTING §2.6a describes: DICOM on its own NodePort with the
# port pinned, ingress scoped to the PACS, egress open to its query port.
REAL_PACS = (
    "pacs.host=10.0.0.10",
    "pacs.aeTitle=SECTRA_QR",
    "pacs.qrPort=8059",
    "networkPolicies.allowedIngressCIDRsWithPorts[0].cidrs[0]=10.0.0.10/32",
    "networkPolicies.allowedIngressCIDRsWithPorts[0].port=8104",
    "networkPolicies.allowedEgressCIDRsWithPorts[0].cidrs[0]=10.0.0.10/32",
    "networkPolicies.allowedEgressCIDRsWithPorts[0].port=8059",
)
NODEPORT_DICOM = ("xnat.web.dicomService.type=NodePort", "xnat.web.dicomNodePort=8104")


def _render(*sets: str) -> subprocess.CompletedProcess[str]:
    """``helm template`` the chart with ``sets``, without raising on a refused render.

    Returns:
        subprocess.CompletedProcess[str]: the completed process, so a caller can assert on a
        non-zero return code and the ``fail`` message on stderr as readily as on the output.
    """
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    for item in sets:
        args += ["--set", item]

    return subprocess.run(args, capture_output=True, text=True, timeout=120)


def _services(*sets: str) -> dict[str, dict]:
    """Every rendered ``kind: Service``, keyed by ``metadata.name``."""
    rendered = _render(*sets)
    assert rendered.returncode == 0, f"render failed:\n{rendered.stderr}"

    return {
        doc["metadata"]["name"]: doc
        for doc in yaml.safe_load_all(rendered.stdout)
        if isinstance(doc, dict) and doc.get("kind") == "Service"
    }


def _port_names(service: dict) -> set[str]:
    """The ``name`` of every port on a rendered Service."""
    return {port.get("name") for port in service["spec"].get("ports", [])}


def test_dicom_port_renders_only_on_the_dicom_service() -> None:
    """The rendered DICOM port is on ``xnat-web-dicom`` and on no other Service.

    The workflow's ``grep -q "nodePort: 8104"`` over the whole render passes whichever Service
    carries it. This is the assertion that distinguishes them: if the split regressed and the
    DICOM port went back onto the console's Service, the grep would still match and only this
    would fail.
    """
    services = _services(*REAL_PACS, *NODEPORT_DICOM)

    assert "xnat-web" in services, sorted(services)
    assert "xnat-web-dicom" in services, sorted(services)
    assert "dicom-scp" in _port_names(services["xnat-web-dicom"])
    assert "tomcat" not in _port_names(services["xnat-web-dicom"])

    carrying_dicom = [name for name, svc in services.items() if "dicom-scp" in _port_names(svc)]
    assert carrying_dicom == ["xnat-web-dicom"], (
        f"the DICOM port renders on {carrying_dicom} — it must appear on xnat-web-dicom alone, or "
        "exposing DICOM externally exposes whatever else carries it"
    )

    for name, svc in services.items():
        if name == "xnat-web-dicom":
            continue
        ports = {port.get("port") for port in svc["spec"].get("ports", [])}
        assert 8104 not in ports, f"Service {name} carries port 8104 alongside the DICOM Service"


def test_web_console_stays_internal_while_dicom_is_exposed() -> None:
    """Exposing DICOM externally leaves the console ClusterIP with no node port of its own."""
    services = _services(*REAL_PACS, *NODEPORT_DICOM)
    web, dicom = services["xnat-web"], services["xnat-web-dicom"]

    assert web["spec"]["type"] == "ClusterIP", (
        f"the web console rendered as {web['spec']['type']} while DICOM was exposed — the split "
        "exists precisely so one cannot drag the other out"
    )
    assert not any("nodePort" in port for port in web["spec"]["ports"]), web["spec"]["ports"]
    assert "externalTrafficPolicy" not in web["spec"]

    assert dicom["spec"]["type"] == "NodePort"
    assert [port["nodePort"] for port in dicom["spec"]["ports"]] == [8104]
    assert dicom["spec"]["externalTrafficPolicy"] == "Local"


def test_dicom_service_keeps_local_policy_on_loadbalancer() -> None:
    """``externalTrafficPolicy: Local`` is not a NodePort-only property of the rendered Service.

    The LoadBalancer path is the one a trust that cannot widen the API server's node-port range
    takes, and it is equally subject to the SNAT that breaks the ingress CIDR match.
    """
    services = _services(*REAL_PACS, "xnat.web.dicomService.type=LoadBalancer")
    dicom = services["xnat-web-dicom"]

    assert dicom["spec"]["type"] == "LoadBalancer"
    assert dicom["spec"]["externalTrafficPolicy"] == "Local"
    assert services["xnat-web"]["spec"]["type"] == "ClusterIP"


def test_both_services_select_the_same_xnat_web_pod() -> None:
    """The split is two Services in front of one Deployment, not a second XNAT.

    A selector that drifted on either side would render clean and fail at runtime — the DICOM
    Service would have no endpoints, which looks exactly like the receiver being down.
    """
    services = _services(*REAL_PACS, *NODEPORT_DICOM)

    assert services["xnat-web"]["spec"]["selector"] == services["xnat-web-dicom"]["spec"]["selector"]


def test_orthanc_modality_resolves_to_the_rendered_dicom_service() -> None:
    """The mocked Orthanc's C-STORE destination is a Service that exists and carries ``dicom-scp``.

    The text-level twin of this in ``tests/test_dicom_service_exposure.py`` compares template
    expressions; this one resolves both sides through an actual render of the default (mocked
    Orthanc) install, which is the configuration every default K8s deployment and the
    ``smoke-cstore`` target actually run.
    """
    rendered = _render()
    assert rendered.returncode == 0, rendered.stderr
    docs = [doc for doc in yaml.safe_load_all(rendered.stdout) if isinstance(doc, dict)]

    modalities = next(
        (
            doc["data"]["ORTHANC__DICOM_MODALITIES"]
            for doc in docs
            if doc.get("kind") == "ConfigMap" and "ORTHANC__DICOM_MODALITIES" in (doc.get("data") or {})
        ),
        None,
    )
    assert modalities, "no rendered ConfigMap carries ORTHANC__DICOM_MODALITIES"

    xnat_modality = yaml.safe_load(modalities)["XNAT"]
    host, port = xnat_modality["Host"], xnat_modality["Port"]
    services = {doc["metadata"]["name"]: doc for doc in docs if doc.get("kind") == "Service"}

    assert host in services, f"Orthanc dials {host!r}, which the chart does not render"
    assert "dicom-scp" in _port_names(services[host]), (
        f"Orthanc's C-STORE destination {host!r} carries no dicom-scp port — a C-STORE aimed there "
        "never reaches XNAT, breaking retrieval on every default install and the smoke-cstore target"
    )
    assert int(port) in {p["port"] for p in services[host]["spec"]["ports"]}, (
        f"Orthanc dials port {port} but {host} exposes {[p['port'] for p in services[host]['spec']['ports']]}"
    )


def test_real_pacs_with_an_exposed_web_console_is_refused() -> None:
    """A real PACS plus an externally-typed ``xnat.web.service.type`` must fail the render.

    This is the upgrade path, not a hypothetical: before the split, ``xnat.web.service.type:
    NodePort`` was how an operator exposed DICOM. Carried across the upgrade it exposes the Tomcat
    console instead — the admin login and the archive's metadata on a node address — while doing
    nothing for retrieval. Rendering that silently is the one widening this split exists to make
    impossible.
    """
    for web_type in ("NodePort", "LoadBalancer"):
        rendered = _render(*REAL_PACS, *NODEPORT_DICOM, f"xnat.web.service.type={web_type}")

        assert rendered.returncode != 0, (
            f"a real PACS rendered successfully with xnat.web.service.type={web_type} — the web "
            "console is exposed externally and nothing refused it"
        )
        assert "xnat.web.service.type" in rendered.stderr, (
            f"the render failed for some other reason than the web-console guard:\n{rendered.stderr}"
        )


def test_the_mocked_orthanc_install_may_still_expose_the_console() -> None:
    """The console guard is scoped to a real PACS, as every other check in the partial is.

    A mocked-Orthanc install is a developer's cluster; refusing a NodePort console there would
    break local workflows to protect data that is synthetic.
    """
    rendered = _render("xnat.web.service.type=NodePort")

    assert rendered.returncode == 0, (
        f"the web-console guard fired on a mocked-Orthanc install:\n{rendered.stderr}"
    )
