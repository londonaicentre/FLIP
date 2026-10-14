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

"""xnat-web's liveness and readiness probes, asserted on the rendered Deployment (FLIP#1456, item H5).

Both probes used to GET ``/xnat/``. XNAT is deployed as Tomcat's ROOT webapp, so that path is not a
context. It got the same anonymous redirect to the login page as any other path, and kubelet counts
the redirect as a success. So neither probe checked more than that Tomcat could render the login
page. Now each probe asks its own question, and the reasons are in the template:

* readiness asks for ``/xapi/siteConfig/buildInfo``, which needs XNAT's XAPI REST layer to be serving;
* liveness asks for ``/``, which can never answer 401, so a closed path cannot start a restart loop.

Neither may carry credentials. A failing probe repeats every period, and XNAT locks an account after
about 20 failed logins. That XNAT really serves buildInfo anonymously at the pinned version is
pinned in ``trust/xnat/tests/test_anonymous_endpoints.py``.

The checks parse the rendered pod spec rather than grepping the template. A grep for the path would
still pass if the path moved to the other probe, if a probe were dropped, or if the probe had
``httpHeaders`` or an ``exec`` added. The tests need helm, so they live under ``tests/templates/``
and run in the chart workflow's helm-template job.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
DEPLOYMENT = "trust-release-flip-trust-xnat-web"
CONTAINER = "tomcat"
#: The container port both probes dial, by name.
PORT = "tomcat"

READINESS_PATH = "/xapi/siteConfig/buildInfo"
LIVENESS_PATH = "/"

#: The probe fields that only set timing. Anything else in a probe changes what it asks.
TIMING_FIELDS = {"initialDelaySeconds", "periodSeconds", "timeoutSeconds", "failureThreshold", "successThreshold"}

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


@pytest.fixture(scope="module")
def tomcat() -> dict:
    """The rendered xnat-web Deployment's ``tomcat`` container, at the chart defaults."""
    rendered = subprocess.run(
        [
            "helm",
            "template",
            "trust-release",
            str(CHART_DIR),
            "--set",
            "flClient.kitHostPath=/opt/flip/fl-kit",
            "--show-only",
            "templates/xnat-web.yaml",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert rendered.returncode == 0, f"render failed:\n{rendered.stderr}"

    deployments = [
        doc
        for doc in yaml.safe_load_all(rendered.stdout)
        if isinstance(doc, dict) and doc.get("kind") == "Deployment" and doc["metadata"]["name"] == DEPLOYMENT
    ]
    assert len(deployments) == 1, f"expected one {DEPLOYMENT} Deployment, got {len(deployments)}"
    containers = {c["name"]: c for c in deployments[0]["spec"]["template"]["spec"]["containers"]}
    assert CONTAINER in containers, f"no {CONTAINER!r} container in {DEPLOYMENT}: {sorted(containers)}"

    return containers[CONTAINER]


def _http_get(container: dict, probe: str) -> dict:
    """Return a probe's ``httpGet``, after checking the probe is an httpGet and nothing else.

    Args:
        container (dict): The rendered container spec.
        probe (str): ``livenessProbe`` or ``readinessProbe``.

    Returns:
        dict: The probe's ``httpGet`` block.
    """
    assert probe in container, f"xnat-web renders no {probe}"
    handler = set(container[probe]) - TIMING_FIELDS
    assert handler == {"httpGet"}, (
        f"xnat-web's {probe} is {sorted(handler)}, not a plain httpGet. An exec, tcpSocket or grpc probe "
        "asks a different question, and an exec probe is where credentials would be passed"
    )

    return container[probe]["httpGet"]


def test_readiness_probe_gets_build_info_on_tomcat(tomcat: dict) -> None:
    """Ready means XAPI answers, which imaging-api, trust-api and the xnat-init Job all call."""
    assert _http_get(tomcat, "readinessProbe") == {"path": READINESS_PATH, "port": PORT}, (
        f"xnat-web's readiness probe must be a plain GET {READINESS_PATH} on the tomcat port. The login "
        "page (/, or the old /xnat/) only proves Tomcat can render one page; buildInfo needs XAPI"
    )


def test_liveness_probe_stays_on_a_path_that_cannot_answer_401(tomcat: dict) -> None:
    """If an XNAT release or a site closed the liveness path, every period would restart the pod."""
    assert _http_get(tomcat, "livenessProbe") == {"path": LIVENESS_PATH, "port": PORT}, (
        f"xnat-web's liveness probe must be a plain GET {LIVENESS_PATH} on the tomcat port. Anonymous `/` "
        "is redirected to the login page, which cannot 401. On buildInfo, a release that closed it would "
        "turn each failed probe into a restart, mid-import"
    )


@pytest.mark.parametrize("probe", ["livenessProbe", "readinessProbe"])
def test_no_probe_carries_credentials(tomcat: dict, probe: str) -> None:
    """A probe repeats every period, and about 20 failed logins lock the XNAT account."""
    http_get = _http_get(tomcat, probe)

    assert "httpHeaders" not in http_get, (
        f"xnat-web's {probe} sends headers {http_get['httpHeaders']!r}. No probe may authenticate: a "
        "failing one repeats every period, and XNAT locks the account after about 20 failed logins"
    )
    assert http_get.get("scheme", "HTTP") == "HTTP", f"xnat-web's {probe} uses {http_get['scheme']}; Tomcat serves HTTP"


def test_the_probe_port_is_the_port_tomcat_listens_on(tomcat: dict) -> None:
    """The named port is what kubelet dials, so it must be XNAT's 8080 and not, say, the DICOM receiver."""
    ports = {p["name"]: p["containerPort"] for p in tomcat.get("ports", [])}

    assert ports.get(PORT) == 8080, f"the {PORT!r} port is {ports.get(PORT)}, not Tomcat's 8080"
