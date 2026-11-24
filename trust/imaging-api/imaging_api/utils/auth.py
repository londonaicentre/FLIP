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

from fastapi import HTTPException, status

from imaging_api.config import get_settings
from imaging_api.utils.xnat_token import XnatTokenFactory

# imaging-api's two XNAT identities (FLIP#2011). The provisioning account is a site administrator and serves every
# route except download and upload. The data account is a Member of FLIP projects only, and serves those two
# routers alone, so a flaw on a route the fl-client reaches is bounded by what a project member can do.
xnat_token_factory = XnatTokenFactory(
    url=get_settings().XNAT_URL,
    username=get_settings().XNAT_SERVICE_USER,
    password=get_settings().XNAT_SERVICE_PASSWORD,
)
xnat_data_token_factory = XnatTokenFactory(
    url=get_settings().XNAT_URL,
    username=get_settings().XNAT_DATA_USER,
    password=get_settings().XNAT_DATA_PASSWORD,
)


def get_xnat_auth_headers() -> dict[str, str]:
    """
    Retrieves the XNAT authentication token and formats it into the appropriate headers for API requests.

    Returns:
        dict[str, str]: A dictionary containing XNAT authentication headers.
    """
    token = xnat_token_factory.get_xnat_cookie()
    return {"Cookie": f"JSESSIONID={token}", "Content-Type": "application/json"}


def get_xnat_data_auth_headers() -> dict[str, str]:
    """
    The data account's XNAT headers, for the download and upload routers only (FLIP#2011).

    Returns:
        dict[str, str]: A dictionary containing XNAT authentication headers.

    Raises:
        HTTPException: 503 naming the account and what to do, when it is not configured or XNAT refuses it; a
            download then says why instead of failing with XNAT's 401.
    """
    from imaging_api.services import data_account

    if data_account.state() == data_account.DataAccountState.NOT_CONFIGURED:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=data_account.remedy())
    try:
        token = xnat_data_token_factory.get_xnat_cookie()
    except HTTPException as e:
        if e.status_code not in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
            raise
        data_account.set_state(data_account.DataAccountState.MISSING)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=data_account.remedy()) from None
    return {"Cookie": f"JSESSIONID={token}", "Content-Type": "application/json"}
