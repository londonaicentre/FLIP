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

"""Tripwire: FLIP requests some XNAT paths with no credentials (see trust/deploy/helm/templates/xnat-web.yaml).

Those paths must be under ``openUrls`` in the WAR's configured-urls.yaml. These tests cannot read the WAR,
so they record the XNAT versions someone has checked, and fail on an XNAT_VERSION bump until the new one is.
"""

import re
from pathlib import Path

XNAT_ENV = Path(__file__).resolve().parents[3] / "trust" / "xnat" / ".env"

# 1.10.0: read from the running webapp on dev trust 1, 2026-10-14 (FLIP#1456, item H5).
CHECKED_XNAT_VERSIONS = {"1.10.0"}


def test_the_pinned_xnat_version_was_checked_for_its_anonymous_paths() -> None:
    match = re.search(r"^XNAT_VERSION=(\S+)$", XNAT_ENV.read_text(), re.MULTILINE)
    assert match is not None, f"no XNAT_VERSION in {XNAT_ENV}"
    version = match.group(1)

    assert version in CHECKED_XNAT_VERSIONS, (
        f"XNAT {version} has not been checked. Run `make -C trust/xnat xnat-war-download && unzip -p "
        f"trust/xnat/xnat/build-artifacts/xnat-web-{version}.war "
        "WEB-INF/classes/META-INF/xnat/security/configured-urls.yaml`, confirm /xapi/siteConfig/buildInfo and "
        "/app/template/Login.vm are under openUrls, then add the version to CHECKED_XNAT_VERSIONS"
    )
