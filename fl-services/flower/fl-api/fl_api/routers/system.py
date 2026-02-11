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

# System and service status functions
from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from fl_api.core.dependencies import get_session
from fl_api.utils.flip_session import FLIP_Session
from fl_api.utils.schemas import ClientInfoModel, ServerInfoModel

router = APIRouter()


@router.get("/check_server_status", response_model=ServerInfoModel)
def check_server_status(session: FLIP_Session = Depends(get_session)) -> ServerInfoModel:
    """
    Checks the status of the server.

    Args:
        session (FLIP_Session): the FLIP session instance.

    Returns:
        ServerInfoModel: status information about the server.
    """
    return session.check_server_status()


@router.get("/check_client_status", response_model=List[ClientInfoModel])
def check_client_status(
    targets: Optional[List[str]] = Query(None),
    session: FLIP_Session = Depends(get_session),
) -> List[ClientInfoModel]:
    """
    Checks the status of specified clients or all clients if no specific targets are provided.

    Args:
        targets (Optional[List[str]]): list of client names to check status for. If not specified, the status of all
        clients will be checked.
        session (FLIP_Session): the FLIP session instance.

    Returns:
        List[ClientInfoModel]: a list of ClientInfoModel objects containing client status information.
    """
    return session.check_client_status(targets)
