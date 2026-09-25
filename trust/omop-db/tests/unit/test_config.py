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
"""The loader connection URL, and the driver it commits to.

The URL is built by the tools, not configured, so this is where the database driver is
decided for every consumer: the dev/EC2 seeding on a host, and the Kubernetes seed hook Job
(which installs these tools un-locked at run time from the source archive).
"""

import pytest
from sqlalchemy import create_engine, make_url

from omop_db_tools.config import Settings

_ENV = {
    "OMOP_DB_HOST": "omop-db",
    "OMOP_DB_PORT": "5432",
    "OMOP_POSTGRES_USER": "omop",
    "OMOP_POSTGRES_PASSWORD": "not-a-real-password",  # pragma: allowlist secret
    "OMOP_POSTGRES_DB": "omop",
}


def _settings(**overrides: str) -> Settings:
    return Settings(**{**_ENV, **overrides})  # type: ignore[arg-type]


def test_url_names_the_psycopg2_driver():
    """A bare ``postgresql://`` lets SQLAlchemy choose the driver, and 2.1 chooses psycopg3.

    This project depends on psycopg2-binary, so that choice has to be spelled out or the
    loaders fail with ``ModuleNotFoundError: No module named 'psycopg'`` — which is how the
    Helm seed hook broke, with no commit behind it: the tools are installed un-locked.
    """
    url = _settings().OMOP_DATABASE_URL.get_secret_value()

    assert url.startswith("postgresql+psycopg2://")
    # create_engine imports the driver's DBAPI, so this fails on a mismatched pair too.
    assert create_engine(url).dialect.driver == "psycopg2"


def test_url_targets_the_configured_database():
    """The components are assembled, not passed through: host, port, user and database."""
    parsed = make_url(_settings(OMOP_POSTGRES_DB="omop_trust_2").OMOP_DATABASE_URL.get_secret_value())

    assert (parsed.host, parsed.port, parsed.username, parsed.database) == ("omop-db", 5432, "omop", "omop_trust_2")


@pytest.mark.parametrize("password", ["p@ss/word:with@specials", "colon:only", "slash/slash", "at@at"])
def test_credentials_survive_characters_that_would_break_the_url(password: str):
    """The password is escaped, not interpolated — a seeded trust's password is operator-set."""
    url = _settings(OMOP_POSTGRES_PASSWORD=password).OMOP_DATABASE_URL.get_secret_value()

    assert make_url(url).password == password
