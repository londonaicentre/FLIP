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

from imaging_api.config import get_settings
from imaging_api.utils.xnat_token import XnatTokenFactory

# Initialize XNAT Token Factory with credentials from settings
xnat_token_factory = XnatTokenFactory(
    url=get_settings().XNAT_URL,
    username=get_settings().XNAT_SERVICE_USER,
    password=get_settings().XNAT_SERVICE_PASSWORD,
)


def get_xnat_auth_headers() -> dict[str, str]:
    """
    Returns a dictionary of headers for authenticating with XNAT.
    """
    token = xnat_token_factory.get_xnat_cookie()
    return {"Cookie": f"JSESSIONID={token}", "Content-Type": "application/json"}
