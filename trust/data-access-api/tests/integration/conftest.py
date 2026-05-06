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

"""Integration-test scaffolding for data-access-api → omop-db (B3, issue #369).

Mirrors ``trust/trust-api/tests/integration/conftest.py`` — same Compose stack, same
seed file, same key — but the tests here drive the data-access-api ``/cohort`` routes
directly without going through trust-api. This catches schema / SQL-template / auth
regressions that only show up with a real Postgres backend.

The AES key has to be aligned end-to-end: ``test_dataframe_endpoint_returns_seeded_columns``
encrypts a project id in the test process and the container decrypts it, so a mismatch
silently produces a 500. The unit conftest (``tests/conftest.py``) only sets defaults
when the key is unset, but in CI ``.env.development`` is populated from a GitHub Secret
that overrides the test value — pin the env var here unconditionally so the test
process and the container always agree on the key the compose stack uses.
"""

import os

# Pin the AES key BEFORE any data_access_api module loads its Settings singleton.
# Same value as trust/compose.test.yml so encrypt/decrypt round-trip works.
os.environ["AES_KEY_BASE64"] = "QgZ+TBA0lUxcuCiRPLneFe/JjMaUEUJWHACHHGz2gGA="  # pragma: allowlist secret

from collections.abc import Generator  # noqa: E402  — must come after the env override
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from testcontainers.compose import DockerCompose  # noqa: E402

# Resolve the path to ``trust/`` from this file
# (``trust/data-access-api/tests/integration/conftest.py``).
_COMPOSE_DIR = Path(__file__).resolve().parents[3]
_COMPOSE_FILE = "compose.test.yml"
_DATA_ACCESS_SERVICE = "data-access-api-test"
_DATA_ACCESS_INTERNAL_PORT = 8000

# Must match the value baked into ``trust/compose.test.yml``. Keeping this in source
# (rather than reading os.environ) makes it explicit which value the tests need.
TRUST_INTERNAL_KEY = "test-trust-internal-service-key"  # pragma: allowlist secret
AUTH_HEADERS = {"X-Trust-Internal-Service-Key": TRUST_INTERNAL_KEY}


@pytest.fixture(scope="session")
def compose_stack() -> Generator[DockerCompose, None, None]:
    """Bring up the Compose stack for the whole session — see trust-api conftest for rationale."""
    with DockerCompose(
        context=str(_COMPOSE_DIR),
        compose_file_name=_COMPOSE_FILE,
        wait=True,
    ) as compose:
        yield compose


@pytest.fixture(scope="session")
def data_access_api_url(compose_stack: DockerCompose) -> str:
    """Resolve the ephemeral host URL where the data-access-api is reachable."""
    host, port = compose_stack.get_service_host_and_port(
        _DATA_ACCESS_SERVICE, _DATA_ACCESS_INTERNAL_PORT
    )
    return f"http://{host}:{port}"
