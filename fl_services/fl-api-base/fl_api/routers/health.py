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

# Health

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def index():
    """
    A welcome message for the FLIP FL API.

    Returns:
        dict[str, str]: A dictionary containing a welcome message for the FLIP FL API.
    """
    return {"message": "Welcome to the FLIP FL API!"}


@router.get("/health/")
def health():
    """
    Updates of whether the FL API service is healthy.

    Returns:
        dict[str, str]: A dictionary containing the health status of the service.
    """
    return {"status": "This service is healthy ✅"}
