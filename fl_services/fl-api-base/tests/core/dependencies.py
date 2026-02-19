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

from types import SimpleNamespace

import pytest
from fl_api.core.dependencies import get_session
from fl_api.utils.flip_session import FLIP_Session


class DummySession(FLIP_Session):
    """Minimal subclass to simulate an NVFLARE session."""

    def __init__(self):
        self.name = "dummy"


@pytest.fixture
def fake_request():
    """Create a mock FastAPI Request with app.state.session set."""
    dummy_session = DummySession()
    fake_app = SimpleNamespace(state=SimpleNamespace(session=dummy_session))
    request = SimpleNamespace(app=fake_app)
    return request, dummy_session


def test_get_session_returns_session(fake_request):
    request, dummy_session = fake_request

    result = get_session(request)
    assert isinstance(result, FLIP_Session)
    assert result is dummy_session
    assert result.name == "dummy"
