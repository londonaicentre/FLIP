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

from unittest.mock import MagicMock, patch
from uuid import uuid4

from flip_api.auth.dependencies import verify_token
from flip_api.domain.schemas.types import FLBackend
from flip_api.model_services.get_job_types import get_job_types_endpoint, router


def test_get_job_types_endpoint_returns_manifest_for_resolved_backend():
    # The endpoint resolves the active backend from the DB and returns that backend's manifest.
    db = MagicMock()
    manifest = {"standard": ["trainer.py", "config.json"], "evaluation": ["evaluator.py"]}
    with (
        patch("flip_api.model_services.get_job_types.resolve_backend", return_value=FLBackend.FLOWER) as mock_resolve,
        patch(
            "flip_api.model_services.get_job_types.JobRequiredFiles.get_all_job_types_with_files",
            return_value=manifest,
        ) as mock_get_all,
    ):
        result = get_job_types_endpoint(db, uuid4())

    assert result == manifest
    mock_resolve.assert_called_once_with(db)
    mock_get_all.assert_called_once_with(FLBackend.FLOWER)


def test_get_job_types_route_requires_authentication():
    """The route is a token-gated read, like every other route in the /model namespace.

    Asserted on the route rather than through a client call because the dependency *is* the whole
    access control here — the handler ignores the caller — so dropping it would leave every test
    that calls the function directly still passing.
    """
    route = next(r for r in router.routes if getattr(r, "path", "") == "/model/job-types")

    assert verify_token in {dependency.call for dependency in route.dependant.dependencies}
