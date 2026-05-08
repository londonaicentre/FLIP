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
"""

from sqlalchemy.engine import Engine

from data_access_api.db.database import engine


def test_engine_is_sqlalchemy_engine():
    """Sanity check — the module exports a real SQLAlchemy Engine."""
    assert isinstance(engine, Engine)


def test_engine_echo_disabled():
    """Statement logging must be off so cohort SQL and bind params are not written to stdout."""
    assert engine.echo is False
