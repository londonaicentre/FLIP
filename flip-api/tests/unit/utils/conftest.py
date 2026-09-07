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

"""Shared fixtures for `flip_api.utils` unit tests."""

import pytest


@pytest.fixture(autouse=True)
def _reset_cognito_client_cache():
    """Clear the `_cognito_client` lru_cache around every test in this directory.

    `_cognito_client` is `@lru_cache(maxsize=1)`, so a boto3 client is built once per
    process and the cache object is *shared* — `cors.py` binds the same function, and
    `test_cognito_helpers.py` and `test_cors.py` both exercise it. Tests patch
    `boto3.client` and assume each starts fresh, so without this a mock warmed by one
    test is silently served to the next, and the second test's patch is never consulted.

    It lives here rather than in the individual modules so a new test file that touches
    `_cognito_client` inherits the guard structurally instead of having to remember to
    copy it (FLIP#1087). Clearing on both setup and teardown closes the leak in both
    directions, so no module depends on another's teardown discipline.
    """
    from flip_api.utils.cognito_helpers import _cognito_client

    _cognito_client.cache_clear()
    yield
    _cognito_client.cache_clear()
