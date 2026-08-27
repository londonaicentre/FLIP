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
migrations must reproduce ``SQLModel.metadata`` exactly), a dedicated
**enum-value drift guard** (``compare_metadata`` is blind to native PG enum
value changes, so they are checked separately against ``pg_enum``), and a
``downgrade base`` → ``upgrade head`` round-trip (which would fail with a stale
native PG enum type if the baseline's hand-added ``DROP TYPE`` cleanup were lost).

Migrations are run on a plain ``connect()`` (never ``begin()``) so Alembic owns
the transaction — the same path the entrypoint/CLI use — otherwise a migration
using ``op.get_context().autocommit_block()`` would raise ``AssertionError``.

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
from sqlalchemy import Connection, Engine, Enum, create_engine, text
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
    with empty_db_engine.connect() as connection:
        command.upgrade(make_alembic_config(connection), "head")

    with empty_db_engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
        assert current == _script_head(connection)


def test_running_migrations_does_not_disable_application_logging(empty_db_engine: Engine) -> None:
    """Running Alembic in-process must leave the app's logger enabled.

    ``migrations/env.py`` calls ``logging.config.fileConfig``, whose default
    ``disable_existing_loggers=True`` sets ``disabled = True`` on every logger
    absent from ``alembic.ini`` — including the "uvicorn" logger the app logs
    through. Production is unaffected (the entrypoint runs ``alembic upgrade
    head`` as its own process), but in-process runs poison logging for the rest
    of the interpreter: the logger emits nothing, so every later ``caplog``
    assertion sees an empty log and fails for reasons unrelated to its subject.

    The regression pinned here is ``logger.disabled`` — that flag alone is what
    ``disable_existing_loggers`` moves, and reverting the fix fails on it. The
    ``logger.propagate`` assertion is a general safety check, not part of this
    regression: ``fileConfig`` never touches ``propagate``, but detaching the
    logger from root would blind ``caplog`` just as completely, so both routes
    to an empty ``caplog`` are covered rather than only the one we hit.
    """
    from flip_api.utils.logger import logger

    with empty_db_engine.connect() as connection:
        command.upgrade(make_alembic_config(connection), "head")

    assert logger.disabled is False, "alembic's fileConfig disabled the application logger"
    assert logger.propagate is True, "the application logger no longer propagates to root (caplog cannot see it)"


def test_no_drift_between_models_and_migrations(empty_db_engine: Engine) -> None:
    """Drift guard: the migrations reproduce ``SQLModel.metadata`` with no diff.

    Any schema-affecting change to ``db/models/*.py`` shipped WITHOUT a matching
    revision makes ``compare_metadata`` report diffs and fails here, for tables,
    columns, types, nullability, FKs, indexes and unique constraints.

    Caveat: ``compare_metadata`` does NOT detect value changes on an existing
    native PG enum (it only diffs the column's type *name*); that gap is covered
    separately by ``test_no_enum_value_drift`` below.
    """
    with empty_db_engine.connect() as connection:
        command.upgrade(make_alembic_config(connection), "head")

    with empty_db_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        diffs = compare_metadata(context, SQLModel.metadata)

    assert diffs == [], f"Models and migrations are out of sync — run `make migration MESSAGE=...`. Diffs: {diffs}"


def test_no_enum_value_drift(empty_db_engine: Engine) -> None:
    """Enum-value drift guard: native PG enum labels must match the models.

    ``compare_metadata`` (used by ``test_no_drift_*``) only diffs a column's type
    *name*, so adding, removing, or reordering a value on an existing native PG enum
    — e.g. a new ``ModelStatus`` member — slips through with no diff. Such a change
    needs a hand-written ``ALTER TYPE ... ADD VALUE`` migration (see the flip-api
    README); without this guard it would ship with no revision, pass CI, and only
    fail at runtime with ``invalid input value for enum ...`` on the first write.

    Compares the ordered labels of every native enum type the models declare against
    those present in the migrated DB's ``pg_enum`` catalog (current schema only).
    """
    with empty_db_engine.connect() as connection:
        command.upgrade(make_alembic_config(connection), "head")

    # The enum labels the models expect, keyed by native PG type name (ordered).
    expected: dict[str, list[str]] = {}
    for table in SQLModel.metadata.tables.values():
        for column in table.columns:
            col_type = column.type
            if isinstance(col_type, Enum) and col_type.native_enum and col_type.name:
                expected[col_type.name] = list(col_type.enums)

    assert expected, "no native enum types discovered in the models — introspection broke"

    # The enum labels actually created in the migrated database, in sort order so a
    # reordering (which changes the native enum's order, not just membership) is caught
    # too. Scope to the current schema so a same-named type elsewhere can't merge in.
    with empty_db_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT t.typname, e.enumlabel "
                "FROM pg_enum e "
                "JOIN pg_type t ON t.oid = e.enumtypid "
                "JOIN pg_namespace n ON n.oid = t.typnamespace "
                "WHERE n.nspname = current_schema() "
                "ORDER BY t.typname, e.enumsortorder"
            )
        ).all()
    actual: dict[str, list[str]] = {}
    for row in rows:
        actual.setdefault(row.typname, []).append(row.enumlabel)

    drift = {
        name: {"models": labels, "database": actual.get(name, [])}
        for name, labels in expected.items()
        if labels != actual.get(name, [])
    }
    assert not drift, (
        "Native PG enum values drifted from the models — add/reorder via a migration "
        f"(`ALTER TYPE ... ADD VALUE`; see flip-api/README.md). Drift: {drift}"
    )


def test_downgrade_base_then_upgrade_head_round_trips(empty_db_engine: Engine) -> None:
    """``upgrade head`` → ``downgrade base`` → ``upgrade head`` is clean.

    The re-upgrade only succeeds because ``downgrade`` drops the native PG ENUM
    types; without that cleanup the second upgrade fails with "type already exists".
    """
    with empty_db_engine.connect() as connection:
        command.upgrade(make_alembic_config(connection), "head")
    with empty_db_engine.connect() as connection:
        command.downgrade(make_alembic_config(connection), "base")
    with empty_db_engine.connect() as connection:
        command.upgrade(make_alembic_config(connection), "head")

    with empty_db_engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
        assert current == _script_head(connection)
