#!/usr/bin/env python3
#
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
#

"""Shared fixtures: one trust kit, rendered once, parsed with a real YAML parser.

The kit's credential values are deliberate sentinels (``SENTINEL-...``). Several guards
below are searches for them across every rendered byte, and a sentinel is the only way
that search means anything: a realistic-looking password could coincide with a word the
renderer emits for another reason, and the test would pass while leaking.

PyYAML, not the repo's own emitter, parses the output. The renderer is stdlib-only by
design, so "it round-trips" is a claim that has to be checked by something else.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

SNOWFLAKE_DIR = Path(__file__).resolve().parents[1]
if str(SNOWFLAKE_DIR) not in sys.path:
    sys.path.insert(0, str(SNOWFLAKE_DIR))

from render_specs import render  # noqa: E402

#: Every secret value in the fixture kit. No rendered byte may contain any of them.
SENTINELS = {
    "TRUST_API_KEY": "SENTINEL-trust-api-key-9f3a",  # pragma: allowlist secret
    "TRUST_INTERNAL_SERVICE_KEY": "SENTINEL-internal-key-41bd",  # pragma: allowlist secret
    "AES_KEY_BASE64": "SENTINEL-aes-key-7c12",  # pragma: allowlist secret
    "XNAT_ADMIN_PASSWORD": "SENTINEL-xnat-admin-2e55",  # pragma: allowlist secret
    "XNAT_SERVICE_PASSWORD": "SENTINEL-xnat-service-88ab",  # pragma: allowlist secret
    "XNAT_DATASOURCE_PASSWORD": "SENTINEL-xnat-db-03de",  # pragma: allowlist secret
    "ORTHANC_PASSWORD": "SENTINEL-orthanc-6b70",  # pragma: allowlist secret
}

CODE = "GSTT"
ENV = "stag"


@pytest.fixture(scope="session")
def kit() -> dict[str, str]:
    """A fully registered trust kit, FLARE backend, one FL network."""
    values = {
        "TRUST_NAME": "Example Trust",
        "TRUST_CODE": CODE,
        "CENTRAL_HUB_API_URL": "https://hub.example.org",
        "TRUST_API_KEY_HEADER": "Authorization",  # pragma: allowlist secret
        "TRUST_INTERNAL_SERVICE_KEY_HEADER": "X-Trust-Internal-Service-Key",  # pragma: allowlist secret
        "POLL_INTERVAL_SECONDS": "5",
        "TRUST_LOG_LEVEL": "INFO",
        "COHORT_QUERY_THRESHOLD": "15",
        "DOCKER_TAG": "v1.2.3",
        "DOCKER_FL_TAG": "v1.2.3",
        "FL_BACKEND": "nvflare",
        "FL_KIT_SLOT": "Trust_1",
        "FL_KIT_SLOT_NUMBER": "1",
        "FL_KIT_DIR": "/opt/flip/fl-kit",
        "NUM_AVAILABLE_GPUS": "0",
        "MEMORY_PER_GPU_IN_GIB": "0",
        "XNAT_PORT": "8104",
        "XNAT_AETITLE": "XNAT",
        "XNAT_ADMIN_USER": "admin",
        "XNAT_SERVICE_USER": "flipServiceAccount",
        "XNAT_DATASOURCE_NAME": "xnat",
        "XNAT_DATASOURCE_USERNAME": "xnat",
        "ORTHANC_USERNAME": "admin",
        "PACS_AETITLE": "ORTHANC",
        "PACS_QR_PORT": "4242",
    }
    values.update(SENTINELS)
    return values


@pytest.fixture(scope="session")
def kit_file(tmp_path_factory: pytest.TempPathFactory, kit: dict[str, str]) -> Path:
    path = tmp_path_factory.mktemp("kit") / f".env.{CODE}.{ENV}"
    path.write_text("".join(f"{key}={value}\n" for key, value in kit.items()))
    path.chmod(0o600)
    return path


@pytest.fixture(scope="session")
def rendered(tmp_path_factory: pytest.TempPathFactory, kit: dict[str, str]) -> Path:
    out = tmp_path_factory.mktemp("rendered")
    render(kit, CODE, ENV, out, digests={})
    return out


@pytest.fixture(scope="session")
def specs(rendered: Path) -> dict[str, dict]:
    """Service name -> the rendered spec, as PyYAML reads it."""
    return {path.stem: yaml.safe_load(path.read_text()) for path in sorted(rendered.glob("*.yaml"))}


@pytest.fixture(scope="session")
def manifest(rendered: Path) -> dict:
    import json

    return json.loads((rendered / "manifest.json").read_text())


def containers(spec: dict) -> list[dict]:
    return spec["spec"]["containers"]
