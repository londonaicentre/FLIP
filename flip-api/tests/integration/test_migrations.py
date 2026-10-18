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


def test_has_imaging_backfills_true_for_pre_existing_projects(empty_db_engine: Engine) -> None:
    """``40f7934c6419`` must turn every project that predates the flag into an imaging project.

    The drift guard does not compare server defaults and the empty-DB upgrade has no rows to backfill,
    so this is the only test that would catch a wrong default stripping the imaging stage from every
    deployed project (FLIP#1071).
    """
    with empty_db_engine.connect() as connection:
        # pragma: allowlist nextline secret
        command.upgrade(make_alembic_config(connection), "46edb903e4d1")
    with empty_db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, name, description, owner_id, deleted, creation_timestamp, status, "
                "dicom_to_nifti) VALUES (gen_random_uuid(), 'legacy', 'predates has_imaging', gen_random_uuid(), "
                "false, now(), 'UNSTAGED', true)"
            )
        )
    with empty_db_engine.connect() as connection:
        command.upgrade(make_alembic_config(connection), "head")

    with empty_db_engine.connect() as connection:
        has_imaging = connection.execute(text("SELECT has_imaging FROM projects WHERE name = 'legacy'")).scalar_one()
        nullable = connection.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'projects' AND column_name = 'has_imaging'"
            )
        ).scalar_one()
    assert has_imaging is True
    assert nullable == "NO"


def _insert_intersect_fixture(connection: Connection) -> None:
    """One project at two trusts: approved at APP, never approved at NOT (pre-#1318 shape)."""
    connection.execute(
        text(
            "INSERT INTO projects (id, name, description, owner_id, deleted, creation_timestamp, status, "
            "dicom_to_nifti, has_imaging) VALUES ('11111111-1111-1111-1111-111111111111', 'legacy', "
            "'predates trust decisions', gen_random_uuid(), false, now(), 'APPROVED', true, true)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO trust (id, name, code, created_at) VALUES "
            "('22222222-2222-2222-2222-222222222222', 'Approved Trust', 'APP', now()), "
            "('33333333-3333-3333-3333-333333333333', 'Not Approved Trust', 'NOT', now())"
        )
    )
    connection.execute(
        text(
            "INSERT INTO project_trust_intersect (id, project_id, trust_id, approved, approved_at) VALUES "
            "(gen_random_uuid(), '11111111-1111-1111-1111-111111111111', "
            "'22222222-2222-2222-2222-222222222222', true, '2026-03-19 10:30:00'), "
            "(gen_random_uuid(), '11111111-1111-1111-1111-111111111111', "
            "'33333333-3333-3333-3333-333333333333', false, NULL)"
        )
    )


def test_trust_decisions_map_approved_to_approved_and_never_to_declined(empty_db_engine: Engine) -> None:
    """The FLIP#1318 revision must turn ``approved = false`` into PENDING, never DECLINED.

    A pre-#1318 ``false`` meant "not approved", which covered both "never looked at" and "left out
    when the others were approved" — there is no way to tell them apart, so none of them may be
    recorded as a refusal. Historical approvals keep their date and have no recorded approver.
    """
    with empty_db_engine.connect() as connection:
        # pragma: allowlist nextline secret
        command.upgrade(make_alembic_config(connection), "40f7934c6419")
    with empty_db_engine.begin() as connection:
        _insert_intersect_fixture(connection)
    with empty_db_engine.connect() as connection:
        # pragma: allowlist nextline secret
        command.upgrade(make_alembic_config(connection), "b7e3a1c95d20")

    with empty_db_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT t.code, i.status, i.decided_by, i.decided_at FROM project_trust_intersect i "
                "JOIN trust t ON t.id = i.trust_id ORDER BY t.code"
            )
        ).all()
    by_code = {row.code: row for row in rows}
    assert by_code["APP"].status == "APPROVED"
    assert by_code["APP"].decided_at.isoformat() == "2026-03-19T10:30:00"
    assert by_code["APP"].decided_by is None
    assert by_code["NOT"].status == "PENDING"
    assert by_code["NOT"].decided_at is None
    assert by_code["NOT"].decided_by is None


def test_trust_decisions_downgrade_restores_the_approved_flag(empty_db_engine: Engine) -> None:
    """Downgrading the FLIP#1318 revision maps APPROVED back to ``approved = true`` and the rest to false."""
    with empty_db_engine.connect() as connection:
        # pragma: allowlist nextline secret
        command.upgrade(make_alembic_config(connection), "40f7934c6419")
    with empty_db_engine.begin() as connection:
        _insert_intersect_fixture(connection)
    with empty_db_engine.connect() as connection:
        command.upgrade(make_alembic_config(connection), "head")
    with empty_db_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE project_trust_intersect SET status = 'DECLINED', decided_at = now() "
                "WHERE trust_id = '33333333-3333-3333-3333-333333333333'"
            )
        )
    with empty_db_engine.connect() as connection:
        # pragma: allowlist nextline secret
        command.downgrade(make_alembic_config(connection), "40f7934c6419")

    with empty_db_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT t.code, i.approved, i.approved_at FROM project_trust_intersect i "
                "JOIN trust t ON t.id = i.trust_id ORDER BY t.code"
            )
        ).all()
    by_code = {row.code: row for row in rows}
    assert by_code["APP"].approved is True
    assert by_code["APP"].approved_at.isoformat() == "2026-03-19T10:30:00"
    assert by_code["NOT"].approved is False
    assert by_code["NOT"].approved_at is None


def test_trust_admin_revision_adds_decision_maker_and_audit_subject(empty_db_engine: Engine) -> None:
    """``e8c4a2f71b36`` records who decided (HUB or SITE) and who a trust audit row is about (FLIP#1258).

    Every decision before it was the hub's — nobody could decide for a single trust — so past decisions are
    backfilled as HUB, and a trust still pending has no decider at all.
    """
    # The #1318 fixture targets the pre-#1318 shape; upgrading through b7e3a1c95d20 maps it to APPROVED/PENDING.
    with empty_db_engine.connect() as connection:
        # pragma: allowlist nextline secret
        command.upgrade(make_alembic_config(connection), "40f7934c6419")
    with empty_db_engine.begin() as connection:
        _insert_intersect_fixture(connection)
    with empty_db_engine.connect() as connection:
        command.upgrade(make_alembic_config(connection), "head")

    with empty_db_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT t.code, i.decided_as FROM project_trust_intersect i "
                "JOIN trust t ON t.id = i.trust_id ORDER BY t.code"
            )
        ).all()
        audit_columns = {
            row.column_name
            for row in connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'trusts_audit'")
            )
        }
        audit_actions = {
            row.enumlabel
            for row in connection.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'trustauditaction'"
                )
            )
        }
    by_code = {row.code: row for row in rows}
    assert by_code["APP"].decided_as == "HUB"
    assert by_code["NOT"].decided_as is None
    assert "subject_user_id" in audit_columns
    assert {"ADMIN_ADDED", "ADMIN_REMOVED"} <= audit_actions


def _insert_staged_project_fixture(connection: Connection) -> None:
    """A STAGED project not yet approved at NOT (pre-#1318 shape): still awaiting its decisions."""
    connection.execute(
        text(
            "INSERT INTO projects (id, name, description, owner_id, deleted, creation_timestamp, status, "
            "dicom_to_nifti, has_imaging) VALUES ('44444444-4444-4444-4444-444444444444', 'staged', "
            "'awaiting decisions', gen_random_uuid(), false, now(), 'STAGED', true, true)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO project_trust_intersect (id, project_id, trust_id, approved, approved_at) VALUES "
            "(gen_random_uuid(), '44444444-4444-4444-4444-444444444444', "
            "'33333333-3333-3333-3333-333333333333', false, NULL)"
        )
    )


def _decisions_by_project_and_code(connection: Connection) -> dict:
    rows = connection.execute(
        text(
            "SELECT p.name, t.code, i.status, i.decided_by, i.decided_at "
            "FROM project_trust_intersect i JOIN trust t ON t.id = i.trust_id JOIN projects p ON p.id = i.project_id"
        )
    ).all()
    return {(row.name, row.code): row for row in rows}


def _upgrade_legacy_fixtures_to_head(engine: Engine) -> None:
    with engine.connect() as connection:
        # pragma: allowlist nextline secret
        command.upgrade(make_alembic_config(connection), "40f7934c6419")
    with engine.begin() as connection:
        _insert_intersect_fixture(connection)
        _insert_staged_project_fixture(connection)
    with engine.connect() as connection:
        command.upgrade(make_alembic_config(connection), "head")


def test_trust_admin_revision_closes_trusts_left_pending_on_approved_projects(empty_db_engine: Engine) -> None:
    """A trust left out of a project approved before trusts decided at their own pace is closed at upgrade.

    From ``e8c4a2f71b36`` a pending trust on an APPROVED project may still approve and join its models, so a
    legacy one left pending would reopen a long-settled project. It is closed as DECLINED with no decider, date
    or ``decided_as`` — the mark that nobody declined it. A STAGED project's trusts are still awaiting a decision.
    """
    _upgrade_legacy_fixtures_to_head(empty_db_engine)

    with empty_db_engine.connect() as connection:
        rows = _decisions_by_project_and_code(connection)
        closed_as = connection.execute(
            text(
                "SELECT decided_as FROM project_trust_intersect WHERE project_id = "
                "'11111111-1111-1111-1111-111111111111' AND trust_id = '33333333-3333-3333-3333-333333333333'"
            )
        ).scalar_one()
    closed = rows[("legacy", "NOT")]
    assert (closed.status, closed.decided_by, closed.decided_at, closed_as) == ("DECLINED", None, None, None)
    assert rows[("legacy", "APP")].status == "APPROVED"
    assert rows[("staged", "NOT")].status == "PENDING"


def test_trust_admin_revision_downgrade_reopens_only_the_trusts_it_closed(empty_db_engine: Engine) -> None:
    """Downgrading returns the closed trusts to PENDING; a decline someone made keeps its decision."""
    _upgrade_legacy_fixtures_to_head(empty_db_engine)
    with empty_db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO trust (id, name, code, created_at) VALUES "
                "('55555555-5555-5555-5555-555555555555', 'Declining Trust', 'DEC', now())"
            )
        )
        connection.execute(
            text(
                "INSERT INTO project_trust_intersect (id, project_id, trust_id, status, decided_at, decided_as) "
                "VALUES (gen_random_uuid(), '11111111-1111-1111-1111-111111111111', "
                "'55555555-5555-5555-5555-555555555555', 'DECLINED', now(), 'SITE')"
            )
        )
    with empty_db_engine.connect() as connection:
        # pragma: allowlist nextline secret
        command.downgrade(make_alembic_config(connection), "c3a7f1eb9402")

    with empty_db_engine.connect() as connection:
        rows = _decisions_by_project_and_code(connection)
    assert rows[("legacy", "NOT")].status == "PENDING"
    assert rows[("legacy", "DEC")].status == "DECLINED"
    assert rows[("staged", "NOT")].status == "PENDING"


def test_trust_admin_revision_downgrades_cleanly(empty_db_engine: Engine) -> None:
    """Downgrading ``e8c4a2f71b36`` drops the decider column and its type."""
    with empty_db_engine.connect() as connection:
        command.upgrade(make_alembic_config(connection), "head")
    with empty_db_engine.connect() as connection:
        # pragma: allowlist nextline secret
        command.downgrade(make_alembic_config(connection), "c3a7f1eb9402")
    with empty_db_engine.connect() as connection:
        columns = {
            row.column_name
            for row in connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'project_trust_intersect'")
            )
        }
        decision_type = connection.execute(text("SELECT 1 FROM pg_type WHERE typname = 'decisionmaker'")).first()
    assert "decided_as" not in columns
    assert decision_type is None
