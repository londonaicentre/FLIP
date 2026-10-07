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

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, make_url

from data_access_api.config import Settings

# Deliberately a literal rather than an import of ``config.DEFAULT_COHORT_QUERY_THRESHOLD``:
# these tests pin the shipped value independently of its source, so importing the constant
# would make ``test_cohort_query_threshold_defaults_to_ten`` tautological.
DEFAULT_COHORT_QUERY_THRESHOLD = 10


def test_cohort_query_threshold_defaults_to_ten():
    """The shipped disclosure floor. Kept explicit so a change to it is a deliberate edit."""
    assert Settings().COHORT_QUERY_THRESHOLD == DEFAULT_COHORT_QUERY_THRESHOLD


@pytest.mark.parametrize("value", ["", None])
def test_cohort_query_threshold_coerces_empty_to_default(value):
    """An empty value must fall back to the default rather than fail validation.

    The service Makefile exports kit-file names with ``sed 's/=.*//'``, stripping the value
    from every line including commented ones — so a commented-out entry reaches the process
    as an empty string. Without coercion pydantic rejects it against ``int`` at import,
    which takes down the service and every test that imports it.
    """
    assert Settings(COHORT_QUERY_THRESHOLD=value).COHORT_QUERY_THRESHOLD == DEFAULT_COHORT_QUERY_THRESHOLD


def test_cohort_query_threshold_empty_coercion_tracks_field_default():
    """The empty-string coercion must yield whatever the field default is, not a copy of it.

    Regression test for the drift the validator used to carry: it returned a hard-coded ``10``
    alongside a field default of ``10``, so changing one silently left the other behind. Both
    now read ``DEFAULT_COHORT_QUERY_THRESHOLD``, and re-introducing a literal fails this.
    """
    assert Settings(COHORT_QUERY_THRESHOLD="").COHORT_QUERY_THRESHOLD == Settings().COHORT_QUERY_THRESHOLD


def test_cohort_query_threshold_accepts_operator_override():
    """A trust may raise its own floor; the value still parses from a string as env vars do."""
    assert Settings(COHORT_QUERY_THRESHOLD="25").COHORT_QUERY_THRESHOLD == 25


@pytest.mark.parametrize("value", [0, -1, "0", "-5"])
def test_cohort_query_threshold_rejects_non_positive(value):
    """A non-positive threshold must fail loudly rather than disable the control.

    ``COHORT_QUERY_THRESHOLD=0`` would turn off every check that reads it at once: both
    row-level gates (``len(df) < 0`` is never true, so ``/cohort/dataframe`` and
    ``/cohort/accession-ids`` would release any cohort, including a single patient) and the
    statistics suppression on ``/cohort``. Settings are built at import, so rejecting here
    means the service refuses to start instead of running with the floor silently removed.
    """
    with pytest.raises(ValidationError):
        Settings(COHORT_QUERY_THRESHOLD=value)


def test_omop_database_url_names_the_psycopg2_driver():
    """A bare ``postgresql://`` lets SQLAlchemy choose the driver, and 2.1 chooses psycopg3.

    This service ships psycopg2 only, so with the bare scheme the engine fails on
    ``ModuleNotFoundError: No module named 'psycopg'``.
    """
    url = Settings().OMOP_DATABASE_URL.get_secret_value()

    assert url.startswith("postgresql+psycopg2://")
    # create_engine imports the driver's DBAPI, so a mismatched pair fails here too.
    assert create_engine(url).dialect.driver == "psycopg2"


@pytest.mark.parametrize("password", ["p@ss/word:with@specials", "colon:only", "slash/slash"])
def test_omop_database_url_escapes_the_operator_password(password):
    """The password is operator-set and lands in a URL, so the URL-breaking characters are escaped."""
    url = Settings(DATA_ACCESS_POSTGRES_PASSWORD=password).OMOP_DATABASE_URL.get_secret_value()

    assert make_url(url).password == password


# ── The governance policy is loaded at import (FLIP#1259) ────────────────────────────────
# config.py loads the document once, when the module is imported, and deliberately lets an
# invalid one raise: uvicorn then exits instead of serving under defaults nobody chose. These
# re-import the module under a given environment and restore it afterwards.


@pytest.fixture
def reload_config(monkeypatch):
    import importlib

    from data_access_api import config

    def _reload(**env: str):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(config)

    yield _reload
    monkeypatch.undo()
    importlib.reload(config)


def test_an_invalid_document_stops_the_import(reload_config, tmp_path):
    """Swallowing the error here would leave the service running on the platform defaults
    while the operator believes their rules are in force."""
    from data_access_api.policy import AccessPolicyError

    document = tmp_path / "governance.toml"
    document.write_text("[disclosure]\nmin_cohort_size = 5\n", encoding="utf-8")

    with pytest.raises(AccessPolicyError, match="below the configured COHORT_QUERY_THRESHOLD of 10"):
        reload_config(ACCESS_POLICY_FILE=str(document), COHORT_QUERY_THRESHOLD="10")


def test_the_loaded_document_is_what_get_policy_returns(reload_config, tmp_path):
    document = tmp_path / "governance.toml"
    document.write_text("[disclosure]\nmin_cohort_size = 30\n", encoding="utf-8")

    config = reload_config(ACCESS_POLICY_FILE=str(document), COHORT_QUERY_THRESHOLD="10")

    policy = config.get_policy()
    assert policy is not None
    assert policy.min_cohort_size == 30


def test_the_floor_the_document_is_held_to_is_the_kits_threshold(reload_config, tmp_path):
    """A document is validated against the configured COHORT_QUERY_THRESHOLD, not a fixed 1 or
    the default: 20 passes a floor of 10 and must fail a kit floor of 25."""
    from data_access_api.policy import AccessPolicyError

    document = tmp_path / "governance.toml"
    document.write_text("[disclosure]\nmin_cohort_size = 20\n", encoding="utf-8")

    with pytest.raises(AccessPolicyError, match="of 25"):
        reload_config(ACCESS_POLICY_FILE=str(document), COHORT_QUERY_THRESHOLD="25")


def test_no_document_means_no_policy(reload_config):
    config = reload_config(ACCESS_POLICY_FILE="")

    assert config.get_policy() is None
