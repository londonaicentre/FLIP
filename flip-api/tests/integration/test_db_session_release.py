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

"""Regression tests for the ``get_session`` connection leak (FLIP#773).

FastAPI finalises a yield-dependency by throwing the endpoint's exception into
the generator at the ``yield`` (endpoint raised) or by closing it
(``GeneratorExit`` — client disconnect / task cancellation). If ``get_session``
does not guard the ``yield`` with a ``with`` block (or ``try/finally``),
``session.close()`` is skipped on those paths and the checked-out connection is
stranded ``idle in transaction`` until the process restarts. Under a Flower
training-metrics burst this exhausted the whole pool (5 + 10 overflow) and took
the hub down — see FLIP#773 for the incident evidence.

These tests drive the generator exactly the way FastAPI's dependency teardown
does and assert the pool's checked-out count returns to baseline. The leaked
``Session`` is deliberately kept referenced so CPython refcounting cannot mask
a leak by collecting it early.

The suite's shared engine uses ``StaticPool`` (one shared connection), which
cannot observe checkout leaks — so these tests install a ``QueuePool`` engine
(the pool class dev and prod actually run) on the same throwaway database for
their duration.
"""

from collections.abc import Generator

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool

from flip_api.db import database as db_module
from flip_api.db.database import get_session


@pytest.fixture
def queue_pool_engine(integration_engine) -> Generator[Engine, None, None]:
    """Install a QueuePool engine on the throwaway DB as flip-api's engine.

    Args:
        integration_engine: The suite's shared StaticPool engine (provides the
            throwaway database URL).

    Yields:
        Engine: A QueuePool-backed engine that ``get_session`` will use.
    """
    engine = create_engine(integration_engine.url, poolclass=QueuePool)
    previous = db_module._engine
    db_module._engine = engine
    yield engine
    db_module._engine = previous
    engine.dispose()


def test_get_session_releases_connection_when_endpoint_raises(queue_pool_engine: Engine) -> None:
    """A request that raises after querying must not strand its connection.

    FastAPI throws the endpoint's exception into the dependency generator at the
    ``yield``; the session must still be closed so its connection returns to the
    pool (FLIP#773).
    """
    pool = queue_pool_engine.pool
    assert isinstance(pool, QueuePool)
    baseline = pool.checkedout()
    gen = get_session()
    db = next(gen)
    db.execute(text("SELECT 1"))  # autobegin: the session now holds a connection
    assert pool.checkedout() == baseline + 1

    with pytest.raises(HTTPException):
        gen.throw(HTTPException(status_code=400, detail="endpoint error"))

    assert pool.checkedout() == baseline, "session leaked its pooled connection on the exception path"
    assert db is not None  # keep the Session referenced: no refcount rescue


def test_get_session_releases_connection_on_generator_close(queue_pool_engine: Engine) -> None:
    """A cancelled/disconnected request must not strand its connection.

    When a request is torn down without a response (client disconnect,
    cancellation), the dependency generator is closed — ``GeneratorExit`` is
    raised at the ``yield``. The session must still be closed (FLIP#773).
    """
    pool = queue_pool_engine.pool
    assert isinstance(pool, QueuePool)
    baseline = pool.checkedout()
    gen = get_session()
    db = next(gen)
    db.execute(text("SELECT 1"))
    assert pool.checkedout() == baseline + 1

    gen.close()

    assert pool.checkedout() == baseline, "session leaked its pooled connection on the GeneratorExit path"
    assert db is not None  # keep the Session referenced: no refcount rescue
