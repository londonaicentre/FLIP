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

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from flip_api.db.database import engine
from flip_api.main import app


@pytest.fixture(scope="module")
def session():
    """Fixture for creating a synchronous database session."""
    with Session(engine) as s:
        yield s


@pytest.fixture
def mock_db_session():
    """Fixture for mocking the database session."""
    # Mock the fluent interface for query execution
    mock_exec = MagicMock()
    mock_exec.one_or_none.return_value = None  # Default: user not found
    session = MagicMock(spec=Session)
    session.add = MagicMock()
    session.commit = MagicMock()
    session.exec.return_value = mock_exec
    session.execute.return_value = MagicMock()
    session.execute.return_value.first.return_value = None
    session.execute.return_value.rowcount = 1
    session.execute.return_value.scalar_one_or_none.return_value = None
    session.rollback = MagicMock()
    return session


@pytest.fixture
def mock_db_session_with_exec():
    """Fixture for mocking the database session."""
    session = MagicMock(spec=Session)
    session.execute.return_value = MagicMock()
    session.execute.return_value.rowcount = 1
    session.execute.return_value.scalar_one_or_none.return_value = None
    session.execute.return_value.first.return_value = None
    # Mock the fluent interface for query execution
    mock_exec = MagicMock()
    session.exec.return_value = mock_exec
    mock_exec.one_or_none.return_value = None  # Default: user not found
    return session, mock_exec


@pytest.fixture
def client():
    """Fixture for creating a test client."""
    with TestClient(app) as c:
        yield c
