# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Integration-test scaffolding.

Boots a throwaway Postgres via Testcontainers once per session and rewires
flip-api's module-level engine references at it so:

* the existing ``session`` fixture from ``tests.fixtures.main_fixtures`` (and
  every test depending on it) reads/writes the throwaway DB;
* ``Depends(get_session)`` does the same when tests hit endpoints through the
  FastAPI ``client`` fixture.

Each test gets its own function-scoped ``session`` (overrides the module-scoped
one in main_fixtures) and tables are truncated between tests so test order is
irrelevant. This keeps integration test files free of bring-up boilerplate —
they just request ``session`` (and/or ``client``) and write SQL-shaped
assertions.
"""

from collections.abc import Generator
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool
from testcontainers.postgres import PostgresContainer

import flip_api.db.database as db_module

# Importing models is what populates SQLModel.metadata so create_all() builds the
# full schema. The imports look unused but are load-bearing — don't drop them.
import flip_api.db.models.main_models  # noqa: F401
import flip_api.db.models.user_models  # noqa: F401
import tests.fixtures.main_fixtures as main_fixtures
from flip_api.db.database import get_session
from flip_api.db.seed.permissions import seed_permissions
from flip_api.db.seed.role_permissions import seed_role_permissions
from flip_api.db.seed.roles import seed_roles
from flip_api.db.seed.trusts import seed_trusts
from flip_api.main import app


@pytest.fixture(scope="session")
def pg_container() -> Generator[PostgresContainer, None, None]:
    """Throwaway Postgres for the whole test session.

    Pinned to a specific minor (``postgres:16-alpine``) so a base-image bump on
    Docker Hub can't make CI suddenly red. Match the major to whatever RDS
    runs in stag/prod when you upgrade.
    """
    with PostgresContainer("postgres:16-alpine", driver="psycopg2") as container:
        yield container


@pytest.fixture(scope="session")
def integration_engine(pg_container: PostgresContainer):
    """Build the engine, create the schema, seed essentials, redirect flip-api at it.

    The seed steps that don't need ``Settings`` (permissions, roles,
    role-permissions) run unconditionally. ``seed_trusts`` reads
    ``settings.TRUST_NAMES`` so it only runs if that's set; tests that need
    specific trusts insert their own rows via the ``trust_factory``.
    """
    engine = create_engine(
        pg_container.get_connection_url(),
        echo=False,
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as s:
        seed_permissions(s)
        seed_roles(s)
        seed_role_permissions(s)
        try:
            seed_trusts(s)
        except Exception:
            # TRUST_NAMES may be absent in a CI env that hasn't copied the
            # full .env.development; tests that need trusts add them inline.
            s.rollback()

    # Redirect every reference to the prod-bound engine at the throwaway DB.
    # Both modules captured ``engine`` at import time, so we rebind both names.
    main_fixtures.engine = engine
    db_module.engine = engine

    yield engine

    engine.dispose()


@pytest.fixture(autouse=True)
def _override_get_session(integration_engine):
    """Point ``Depends(get_session)`` at the throwaway DB for every integration test."""

    def _get_test_session() -> Generator[Session, None, None]:
        with Session(integration_engine) as s:
            yield s

    app.dependency_overrides[get_session] = _get_test_session
    yield
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture(autouse=True)
def _stub_cors_lookup():
    """Neutralise FastAPI lifespan side-effects for integration tests.

    ``TestClient.__enter__`` runs the production lifespan, which (a) hits
    Cognito to build the CORS allowlist and (b) starts APScheduler. Cognito
    has no creds in CI; the scheduler is a module-level singleton and a
    second ``start()`` raises ``SchedulerAlreadyRunningError``. Both are
    irrelevant for these tests, so stub them.
    """
    with (
        patch("flip_api.main.get_cors_allowed_origins", return_value=[]),
        patch("flip_api.main.start_scheduler"),
    ):
        yield


# Tables to keep across tests (seeded once per session). Everything else is
# truncated between tests so a leftover row never poisons the next test.
_PRESERVED_TABLES = {"permission", "roles", "role_permission", "trust"}


@pytest.fixture(autouse=True)
def _truncate_tables(integration_engine):
    """Wipe per-test tables before each test runs.

    Uses ``TRUNCATE ... RESTART IDENTITY CASCADE`` in one statement so the
    order of dependent FKs doesn't matter. Cheaper than dropping/recreating
    the schema for every test.
    """
    yield
    table_names = [
        f'"{t.name}"'
        for t in SQLModel.metadata.sorted_tables
        if t.name not in _PRESERVED_TABLES
    ]
    if not table_names:
        return
    with integration_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(table_names)} RESTART IDENTITY CASCADE"))


@pytest.fixture
def session(integration_engine) -> Generator[Session, None, None]:
    """Function-scoped DB session for integration tests.

    Overrides the module-scoped ``session`` from
    ``tests.fixtures.main_fixtures`` (pytest picks the closest conftest), so
    each test gets a fresh session that committed rows from another test
    can't leak into. Each Session() call uses the same engine, so the test
    sees rows committed via the API path through ``client`` too.
    """
    with Session(integration_engine) as s:
        yield s
