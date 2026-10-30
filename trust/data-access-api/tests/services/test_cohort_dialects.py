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

"""``validate_query`` must give the SAME guarantees on every backend (FLIP#13).

The rules are identical; the implementation deliberately is not. So the whole attack corpus
is run against BOTH dialects here, parametrised on the backend rather than duplicated, and
the Snowflake-specific surface (``SYSTEM$…``, ``RESULT_SCAN(LAST_QUERY_ID())``, external
functions, stage references) gets its own cases — reusing the Postgres ``pg_catalog``
allowlist would say nothing true about a warehouse.

CI has no Snowflake and should not gain one: everything here is pure sqlglot plus the
backend objects, so it runs with no account and no network.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError

from data_access_api.db import backend as backend_module
from data_access_api.db.backend import PostgresOmopBackend, SnowflakeOmopBackend
from data_access_api.services import cohort as cohort_service
from data_access_api.services.cohort import validate_query
from tests.db.test_backend import snowflake_settings

BACKENDS = ["postgres", "snowflake"]


@pytest.fixture(params=BACKENDS)
def backend(request):
    """Run the test body once per OMOP backend."""
    if request.param == "postgres":
        with patch.object(cohort_service, "get_backend", return_value=PostgresOmopBackend()):
            yield request.param
        return
    with (
        patch.object(backend_module, "get_settings", return_value=snowflake_settings()),
        patch.object(cohort_service, "get_backend", return_value=SnowflakeOmopBackend()),
    ):
        yield request.param


@pytest.fixture
def snowflake():
    """Run the test body on the Snowflake backend only."""
    with (
        patch.object(backend_module, "get_settings", return_value=snowflake_settings()),
        patch.object(cohort_service, "get_backend", return_value=SnowflakeOmopBackend()),
    ):
        yield


# ── The guarantees, asserted identically on both backends ────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("SELECT person_id FROM omop.person; DROP TABLE omop.person", id="stacked-statements"),
        pytest.param("SELECT 1; ;", id="trailing-semicolon-smuggling"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace-only"),
    ],
)
def test_only_one_statement_is_allowed(backend, query):
    with pytest.raises(HTTPException) as error:
        validate_query(query)
    assert error.value.status_code == 400


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("DELETE FROM omop.person", id="top-level-delete"),
        pytest.param("UPDATE omop.person SET person_id = 1", id="top-level-update"),
        pytest.param("INSERT INTO omop.person (person_id) VALUES (1)", id="top-level-insert"),
        pytest.param(
            "WITH gone AS (DELETE FROM omop.person RETURNING *) SELECT * FROM gone",
            id="writable-cte",
        ),
        pytest.param("SELECT person_id INTO omop.copy FROM omop.person", id="select-into"),
    ],
)
def test_writes_are_refused_anywhere_in_the_tree(backend, query):
    """A writable CTE parses as a top-level SELECT, so the shape check alone is not enough."""
    with pytest.raises(HTTPException, match="are not allowed|are allowed"):
        validate_query(query)


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("SELECT * FROM public.secrets", id="other-schema"),
        pytest.param("SELECT * FROM pg_catalog.pg_class", id="pg-catalog"),
        pytest.param('SELECT * FROM "OMOP".person', id="quoted-other-case-schema"),
    ],
)
def test_only_the_omop_schema_is_reachable(backend, query):
    with pytest.raises(HTTPException, match="is not accessible"):
        validate_query(query)


def test_an_unqualified_table_is_pinned_to_omop(backend):
    """Pinned in the TREE, so the emitted query is a pinned query however the session resolves."""
    emitted = validate_query("SELECT person_id FROM person")

    assert "omop.person" in emitted.lower()


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("SELECT 1 LIMIT CASE WHEN 1 = 1 THEN 1 ELSE 2 END", id="limit-oracle"),
        pytest.param("SELECT 1 OFFSET CASE WHEN 1 = 1 THEN 1 ELSE 2 END", id="offset-oracle"),
    ],
)
def test_limit_and_offset_must_be_literal_integers(backend, query):
    """The blind-extraction technique that reads a value back through the cohort-size message."""
    with pytest.raises(HTTPException, match="literal integer"):
        validate_query(query)


def test_an_unrecognised_function_is_refused(backend):
    """Reject-unknown-by-default is what makes a per-backend allowlist tractable."""
    with pytest.raises(HTTPException, match="is not an allowed function"):
        validate_query("SELECT some_function_nobody_models(1) FROM omop.person")


def test_a_cte_is_not_mistaken_for_a_physical_table(backend):
    emitted = validate_query("WITH cohort AS (SELECT person_id FROM omop.person) SELECT * FROM cohort")

    assert "omop.cohort" not in emitted.lower()


def test_the_query_is_re_emitted_from_the_checked_tree(backend):
    """Trailing semicolons and whitespace normalise away because the string is generated."""
    emitted = validate_query("  SELECT person_id FROM omop.person ;  ")

    assert emitted.strip() == emitted
    assert ";" not in emitted


def test_an_ordinary_cohort_query_survives_on_both_backends(backend):
    """One SQL string authored once at the hub, running on a Postgres trust and a Snowflake one."""
    query = """
    WITH imaging AS (
        SELECT io.person_id, io.accession_id, MAX(CASE WHEN io.modality_concept_id = 1 THEN 1 ELSE 0 END) AS has_ct
        FROM omop.image_occurrence io
        GROUP BY io.person_id, io.accession_id
    )
    SELECT p.person_id, i.accession_id
    FROM omop.person p
    JOIN imaging i ON i.person_id = p.person_id
    WHERE p.gender_concept_id = 8507
    LIMIT 100
    """

    emitted = validate_query(query)

    assert "accession_id" in emitted.lower()
    assert "LIMIT 100" in emitted


# ── Dialect-specific emission ────────────────────────────────────────────────────────


def test_postgres_emits_postgres():
    with patch.object(cohort_service, "get_backend", return_value=PostgresOmopBackend()):
        emitted = validate_query("SELECT person_id FROM omop.person")

    assert emitted == "SELECT person_id FROM omop.person"


def test_snowflake_emission_pins_the_catalog_too(snowflake):
    """``DB.SCHEMA.TABLE`` is Snowflake's ordinary form, and the database is a session property."""
    emitted = validate_query("SELECT person_id FROM person")

    assert "FLIP_TRUST_GSTT.omop.person" in emitted


def test_snowflake_leaves_unquoted_names_unquoted(snowflake):
    """Snowflake upper-folds them itself, so lower-case emission resolves to the OMOP objects."""
    emitted = validate_query("SELECT person_id FROM omop.person")

    assert '"' not in emitted


def test_a_quoted_alias_survives_transpilation(snowflake):
    """A quoted identifier keeps its case on both backends, which is why the folding rule is one rule."""
    emitted = validate_query('SELECT p.person_id AS "Patient ID" FROM omop.person p')

    assert '"Patient ID"' in emitted


# ── The Snowflake dangerous surface ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("SELECT SYSTEM$GET_PRIVATELINK_CONFIG() FROM omop.person", id="system-function"),
        pytest.param("SELECT * FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))", id="result-scan"),
        pytest.param("SELECT my_external_function(person_id) FROM omop.person", id="external-function"),
        pytest.param("SELECT * FROM omop.person WHERE SYSTEM$WHITELIST() IS NOT NULL", id="system-in-where"),
    ],
)
def test_the_snowflake_dangerous_surface_is_refused(snowflake, query):
    """None of these is a ``pg_catalog`` name, and all of them parse perfectly cleanly."""
    with pytest.raises(HTTPException) as error:
        validate_query(query)
    assert error.value.status_code == 400


def test_the_postgres_allowlist_is_not_reused_on_snowflake(snowflake):
    """``age()`` does not exist on Snowflake, so admitting it would trade a 400 for a runtime error."""
    with pytest.raises(HTTPException, match="is not an allowed function"):
        validate_query("SELECT AGE(CURRENT_DATE, p.birth_datetime) FROM omop.person p")


def test_postgres_still_allows_age():
    with patch.object(cohort_service, "get_backend", return_value=PostgresOmopBackend()):
        assert "AGE(" in validate_query("SELECT AGE(CURRENT_DATE, p.birth_datetime) FROM omop.person p").upper()


def test_a_snowflake_cross_database_reference_is_refused(snowflake):
    """Pinned, not rejected: this trust's own database is fine, any other database is not."""
    with pytest.raises(HTTPException, match="Cross-database"):
        validate_query("SELECT * FROM OTHER_TRUST.omop.person")


def test_this_trusts_own_database_may_be_named(snowflake):
    emitted = validate_query("SELECT person_id FROM FLIP_TRUST_GSTT.omop.person")

    assert "FLIP_TRUST_GSTT" in emitted.upper()


def test_postgres_rejects_every_three_part_reference():
    with patch.object(cohort_service, "get_backend", return_value=PostgresOmopBackend()):
        with pytest.raises(HTTPException, match="Cross-database"):
            validate_query("SELECT * FROM otherdb.omop.person")


# ── What get_records hands back ──────────────────────────────────────────────────────


def test_get_records_lower_cases_snowflake_columns(snowflake):
    """``routers/cohort.py`` does ``df[\"accession_id\"]`` outside any try block, and the
    tutorial transform chains index in lower case too — so the fold happens before anything
    downstream, including the cache, sees the frame."""
    frame = pd.DataFrame({"PERSON_ID": [1, 2], "ACCESSION_ID": ["a", "b"]})
    with (
        patch.object(cohort_service, "get_engine", return_value=MagicMock()),
        patch("pandas.read_sql", return_value=frame),
    ):
        result = cohort_service.get_records("SELECT person_id, accession_id FROM omop.person", use_cache=False)

    assert list(result.columns) == ["person_id", "accession_id"]


def test_get_records_reports_a_missing_snowflake_object_as_a_named_400(snowflake):
    """The diagnostic must not degrade to an opaque 500 merely because there is no psycopg2."""
    error = MagicMock()
    error.errno = 2003
    error.__str__ = lambda self: "Object 'OMOP.PERSONN' does not exist or not authorized."
    with (
        patch.object(cohort_service, "get_engine", return_value=MagicMock()),
        patch("pandas.read_sql", side_effect=DBAPIError("statement", "params", error)),
    ):
        with pytest.raises(HTTPException) as raised:
            cohort_service.get_records("SELECT * FROM omop.personn", use_cache=False)

    assert raised.value.status_code == 400
    assert "OMOP.PERSONN" in raised.value.detail
