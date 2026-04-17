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

from unittest.mock import patch

import pytest
import requests
from pytest_factoryboy import register

from tests.fixtures import db_fixtures
from tests.fixtures.main_fixtures import (  # noqa: F401
    client,
    mock_db_session,
    mock_db_session_with_exec,
    session,
)
from tests.fixtures.model_fixtures import create_model_data, model_id  # noqa: F401
from tests.fixtures.project_fixture import (  # noqa: F401
    create_project_data,
    mock_project,
    project_id,
    project_with_approved_trusts,
)
from tests.fixtures.user_fixtures import mock_request, user_data, user_email, user_id  # noqa: F401

register(db_fixtures.ProjectFactory)
register(db_fixtures.ModelFactory)
register(db_fixtures.UserFactory)
register(db_fixtures.RoleFactory)
register(db_fixtures.RolesFactory)
register(db_fixtures.UserRoleFactory)
register(db_fixtures.TrustFactory)
register(db_fixtures.ProjectTrustIntersectFactory)
register(db_fixtures.TrustTaskFactory)


@pytest.fixture
def real_client() -> requests.Session:
    """Fixture to provide a requests session for API calls."""
    real_client = requests.Session()
    real_client.headers.update({"Content-Type": "application/json"})
    return real_client


@pytest.fixture(autouse=True)
def mock_pull_required_files(request):
    """
    Automatically mock pull_required_files_json_to_assets across all tests to prevent modifications to the file during
    test runs.

    This fixture is skipped for tests in test_pull_required_files.py which test
    the function directly.
    """
    # Skip mocking for tests that are testing the pull_required_files function itself
    if "test_pull_required_files" in request.node.nodeid:
        yield
    else:
        with patch("flip_api.fl_services.services.pull_required_files.pull_required_files_json_to_assets"):
            with patch("flip_api.model_services.save_model.pull_required_files_json_to_assets"):
                yield


def pytest_addoption(parser):
    parser.addoption(
        "--skip-client",
        action="store_true",
        default=False,
        help="Call api endpoints with the client, which does not if the api is not running",
    )
    parser.addoption(
        "--skip-db",
        action="store_true",
        default=False,
        help="Skip tests that require a database connection",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "skip-client: mark test to run only if --skip-client is given")
    config.addinivalue_line("markers", "skip-db: mark test to run only if --skip-db is given")


def pytest_collection_modifyitems(config, items):
    # Check if the --skip-client flag IS provided
    if config.getoption("--skip-client"):
        # Create the skip marker instance
        skip_marker = pytest.mark.skip(reason="skipped because --skip-client was provided")
        # Iterate through all collected test items
        for item in items:
            # Check if the item is marked with 'skip_client'
            if "skip_client" in item.keywords:
                # Add the skip marker to this item
                item.add_marker(skip_marker)
    # Check if the --skip-db flag IS provided
    if config.getoption("--skip-db"):
        # Create the skip marker instance
        skip_marker = pytest.mark.skip(reason="skipped because --skip-db was provided")
        # Iterate through all collected test items
        for item in items:
            # Check if the item is marked with 'skip_db'
            if "skip_db" in item.keywords:
                # Add the skip marker to this item
                item.add_marker(skip_marker)

    if config.getoption("--skip-client"):
        # --skip-client given in cli: do not skip slow tests
        return
    skip_client = pytest.mark.skip(reason="need --skip-client option to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_client)
