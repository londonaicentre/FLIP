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
from uuid import uuid4

import pytest

from flip_api.db.models.main_models import Trust
from flip_api.domain.interfaces.trust import ITrust
from flip_api.trusts_services.services.trust import get_trusts


def create_mock_trust(id=None, name="Test Trust", endpoint="https://test.endpoint"):
    return Trust(
        id=id or uuid4(),
        name=name,
        endpoint=endpoint,
    )


def test_get_trusts_with_ids():
    mock_session = MagicMock()
    trust_id = uuid4()
    mock_trust = create_mock_trust(id=trust_id)

    # Mocking the session.exec().all() call
    mock_session.exec.return_value.all.return_value = [mock_trust]

    result = get_trusts(mock_session, ids=[trust_id])

    assert len(result) == 1
    assert isinstance(result[0], ITrust)
    assert result[0].id == trust_id
    mock_session.exec.assert_called()


def test_get_trusts_without_ids():
    mock_session = MagicMock()
    mock_trusts = [create_mock_trust(), create_mock_trust()]
    mock_session.exec.return_value.all.return_value = mock_trusts

    result = get_trusts(mock_session)

    assert len(result) == 2
    assert all(isinstance(trust, ITrust) for trust in result)
    mock_session.exec.assert_called()


def test_get_trusts_no_results():
    mock_session = MagicMock()
    mock_session.exec.return_value.all.return_value = []

    with pytest.raises(ValueError, match="No database response returned"):
        get_trusts(mock_session)
