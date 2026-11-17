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

"""Unit tests for the OMOP SQLAlchemy engine configuration.

The data-access-api executes user-supplied cohort SQL against the OMOP
database — bind parameters carry patient identifiers, accession IDs and
table joins. SQLAlchemy's ``echo=True`` writes every executed statement
(plus its bound values) to stdout via the ``sqlalchemy.engine`` logger,
which would persist that PII in container logs and any log aggregator
collecting stdout. The engine must be constructed with statement logging
disabled.

Since FLIP#13 the engine is also built on FIRST USE rather than at import, so which backend
it talks to is a deployment decision rather than an import-time one.
"""

from unittest.mock import MagicMock, patch

from sqlalchemy.engine import Engine

from data_access_api.db import database
from data_access_api.db.database import get_engine, reset_engine


def test_engine_is_sqlalchemy_engine():
    """Sanity check — the module hands back a real SQLAlchemy Engine."""
    assert isinstance(get_engine(), Engine)


def test_engine_echo_disabled():
    """Statement logging must be off so cohort SQL and bind params are not written to stdout."""
    assert get_engine().echo is False


def test_engine_is_not_built_at_import():
    """Importing the module must not open a connection pool.

    The engine used to be a module-level global created during import, which made the backend
    un-choosable per deployment and un-swappable in a test: anything that transitively imported
    this module built a psycopg2 engine whatever the configuration said (FLIP#13).
    """
    reset_engine()
    assert database._engine is None


def test_engine_is_built_once_and_reused():
    """A second call must reuse the pool rather than open another one."""
    reset_engine()
    assert get_engine() is get_engine()


def test_the_engine_comes_from_the_configured_backend():
    """The backend owns engine construction, which is what makes the seam a seam."""
    reset_engine()
    sentinel = MagicMock()
    try:
        with patch("data_access_api.db.database.get_backend") as get_backend:
            get_backend.return_value.create_engine.return_value = sentinel
            assert get_engine() is sentinel
    finally:
        # The cached engine is process-wide, so a stand-in left behind would be handed to
        # every later test in the session.
        reset_engine()
