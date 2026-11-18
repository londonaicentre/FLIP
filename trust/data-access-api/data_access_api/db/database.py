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

"""The OMOP engine, built on first use rather than at import (FLIP#13).

The engine used to be a module-level global created while this module was being imported,
which made the backend un-choosable per deployment and un-swappable in a test: importing
anything that transitively reached here built a psycopg2 engine, whatever the configuration
said. It is now built by the configured backend on the first call and reused afterwards, so
the connection is a deployment decision rather than an import-time one.

Statement logging stays off on every backend: cohort SQL and its bind parameters carry
patient identifiers, accession IDs and table joins, and ``echo=True`` would write all of it
to stdout and from there into any log aggregator.
"""

from sqlalchemy import Engine

from data_access_api.db.backend import get_backend

_engine: Engine | None = None


def get_engine() -> Engine:
    """The SQLAlchemy engine for the configured OMOP backend, created on first use.

    Returns:
        Engine: The process-wide engine.
    """
    global _engine
    if _engine is None:
        _engine = get_backend().create_engine()
    return _engine


def reset_engine() -> None:
    """Dispose of and forget the cached engine, so the next call rebuilds it.

    Exists for tests and for a configuration reload; nothing on the request path calls it.
    """
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
