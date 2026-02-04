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

from fastapi import Request

from flip_api.utils.logger import logger


def get_user_id_from_request(request: Request) -> str:
    """
    Extract the user ID from the request.

    Args:
        request: FastAPI request object

    Returns:
        User ID
    """
    # Get from Authorization JWT token (this would need to be customized based on your auth setup)
    user_id = request.headers.get("X-User-ID")

    if not user_id:
        # Alternative: get from the JWT token if you're using that
        # Assuming you have middleware that extracts the user ID from JWT
        user_id = request.state.user_id if hasattr(request.state, "user_id") else None

    if not user_id:
        # FIXME: ON PRODUCTION this should be checked
        # raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing user ID")
        logger.error("Skipping Authorization check for user ID")
        user_id = "admin"
    return user_id
