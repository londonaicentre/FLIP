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

"""Tests for the Alembic migration chain.

Covers a clean ``upgrade head`` on an empty DB, the **drift guard** (the
migrations must reproduce ``SQLModel.metadata`` exactly), and a
``downgrade base`` → ``upgrade head`` round-trip (which would fail with a stale
native PG enum type if the baseline's hand-added ``DROP TYPE`` cleanup were lost).

Each test gets a guaranteed-empty database (the ``public`` schema is dropped and
recreated per test) from a module-scoped throwaway Postgres, kept separate from
the seeded session DB used by the rest of the integration suite.
"""

from collections.abc import Generator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, text
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

# Load-bearing: importing the models registers every table on SQLModel.metadata,
# which the drift comparison below diffs the migrated DB against.
import flip_api.db.models.main_models  # noqa: F401
import flip_api.db.models.user_models  # noqa: F401
from tests.integration.conftest import make_alembic_config


@pytest.fixture(scope="module")
def _migrations_pg() -> Generator[PostgresContainer, None, None]:
    """A throwaway Postgres for the migration tests, isolated from the seeded session DB."""
    with PostgresContainer("postgres:16-alpine", driver="psycopg2") as pg:
        yield pg


@pytest.fixture
def empty_db_engine(_migrations_pg: PostgresContainer) -> Generator[Engine, None, None]:
    """Yield an engine onto a guaranteed-empty database.

    Dropping and recreating the ``public`` schema between tests clears tables, the
    native ENUM types and the ``alembic_version`` table, so each test starts from
    a true blank slate without paying for a fresh container boot.
    """
    engine = create_engine(_migrations_pg.get_connection_url(), echo=False)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    yield engine
    engine.dispose()


def _script_head(connection: Connection) -> str:
    """Return the head revision id recorded in the migrations directory."""
    head = ScriptDirectory.from_config(make_alembic_config(connection)).get_current_head()
    assert head is not None, "migrations directory has no head revision"
    return head


def test_upgrade_head_on_empty_db_reaches_head(empty_db_engine: Engine) -> None:
    """``alembic upgrade head`` on an empty DB succeeds and stamps the head revision."""
    with empty_db_engine.begin() as connection:
        command.upgrade(make_alembic_config(connection), "head")

    with empty_db_engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
        assert current == _script_head(connection)


def test_no_drift_between_models_and_migrations(empty_db_engine: Engine) -> None:
    """Drift guard: the migrations reproduce ``SQLModel.metadata`` with no diff.

    Any schema-affecting change to ``db/models/*.py`` shipped WITHOUT a matching
    revision makes ``compare_metadata`` report diffs and fails here. The baseline
    was verified to produce zero diffs (including the native PG enums), so this
    needs no enum-noise filtering — a non-empty diff is a real drift.
    """
    with empty_db_engine.begin() as connection:
        command.upgrade(make_alembic_config(connection), "head")

    with empty_db_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        diffs = compare_metadata(context, SQLModel.metadata)

    assert diffs == [], f"Models and migrations are out of sync — run `make migration MESSAGE=...`. Diffs: {diffs}"


def test_downgrade_base_then_upgrade_head_round_trips(empty_db_engine: Engine) -> None:
    """``upgrade head`` → ``downgrade base`` → ``upgrade head`` is clean.

    The re-upgrade only succeeds because ``downgrade`` drops the native PG ENUM
    types; without that cleanup the second upgrade fails with "type already exists".
    """
    with empty_db_engine.begin() as connection:
        command.upgrade(make_alembic_config(connection), "head")
    with empty_db_engine.begin() as connection:
        command.downgrade(make_alembic_config(connection), "base")
    with empty_db_engine.begin() as connection:
        command.upgrade(make_alembic_config(connection), "head")

    with empty_db_engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
        assert current == _script_head(connection)
