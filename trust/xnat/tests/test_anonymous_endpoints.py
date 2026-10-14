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

"""The XNAT paths FLIP requests with no credentials, pinned to the XNAT version they were checked at.

XNAT decides which paths an anonymous request may reach in
``WEB-INF/classes/META-INF/xnat/security/configured-urls.yaml`` inside its WAR. Only a path under
``openUrls`` is anonymous. The neighbouring ``initUrls`` list is a different thing and does not open
anything: ``/xapi/siteConfig/initialized`` sits there, and an anonymous kubelet-style GET of it got a
401, both on a running trust and on a fresh Kubernetes install. Any other XAPI path needs a login.

These tests cannot read the WAR (they download nothing), so this is a tripwire: bumping
``XNAT_VERSION`` fails it until someone has read the new WAR's list and recorded the version below.
To check a version::

    make -C trust/xnat xnat-war-download
    unzip -p trust/xnat/xnat/build-artifacts/xnat-web-<version>.war \\
        WEB-INF/classes/META-INF/xnat/security/configured-urls.yaml

Every path in ``RELIED_ON`` must be listed under ``openUrls``. If one is not, change the consumer
named beside it before bumping. A readiness probe that 401s leaves a Kubernetes trust's xnat-web
NotReady, and its deploy fails at the xnat-init hook.

The test reads ``trust/xnat/.env``, which ``test_trust_xnat.yml`` does not list in its paths. It
still runs on every XNAT bump, because the bump also changes the WAR line of ``artifacts.manifest``,
which that workflow does list (``test_artifacts.py`` pins the two together).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
XNAT_ENV = REPO_ROOT / "trust" / "xnat" / ".env"

#: Each path FLIP requests anonymously, and who requests it.
RELIED_ON = {
    "/xapi/siteConfig/buildInfo": (
        "the readiness probe in trust/deploy/helm/templates/xnat-web.yaml, and trust-api's XNAT health "
        "check (trust/trust-api/trust_api/services/health_collector.py)"
    ),
    "/app/template/Login.vm": (
        "where XNAT redirects the liveness probe's anonymous `/` in trust/deploy/helm/templates/xnat-web.yaml"
    ),
}

#: Each XNAT version whose configured-urls.yaml someone has read, and the RELIED_ON paths it lists
#: under ``openUrls``.
OPEN_URLS_CHECKED = {
    # 2026-10-14, FLIP#1456 (item H5): read from the 1.10.0 webapp (build 246) running on dev trust
    # 1, where both paths sit under openUrls and an anonymous kube-probe GET of buildInfo got a 200.
    "1.10.0": frozenset({"/xapi/siteConfig/buildInfo", "/app/template/Login.vm"}),
}


def _xnat_version() -> str:
    """Return the ``XNAT_VERSION`` pinned in ``trust/xnat/.env``, the version every FLIP XNAT image is built at."""
    match = re.search(r"^XNAT_VERSION=(\S+)$", XNAT_ENV.read_text(), re.MULTILINE)
    assert match is not None, f"no XNAT_VERSION in {XNAT_ENV}"
    return match.group(1)


def test_the_pinned_xnat_version_has_been_checked_for_its_anonymous_paths() -> None:
    """A version nobody has checked may have closed a path a probe or health check relies on."""
    version = _xnat_version()

    assert version in OPEN_URLS_CHECKED, (
        f"XNAT_VERSION is {version}, which nobody has checked for the paths FLIP requests anonymously: "
        f"{sorted(RELIED_ON)}. Read configured-urls.yaml from the new WAR (see this module's docstring) "
        f"and add {version!r} to OPEN_URLS_CHECKED with the paths it lists under openUrls"
    )


def test_every_relied_on_path_is_open_at_the_pinned_version() -> None:
    """Recording a version without one of the paths means a consumer must change first."""
    version = _xnat_version()
    open_paths = OPEN_URLS_CHECKED.get(version, frozenset())

    closed = {path: who for path, who in RELIED_ON.items() if path not in open_paths}
    assert not closed, (
        f"at XNAT {version} these paths are not recorded as openUrls, so an anonymous request gets a 401: "
        + "; ".join(f"{path} (used by {who})" for path, who in sorted(closed.items()))
    )
