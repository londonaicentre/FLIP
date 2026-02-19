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

# Application: upload, monitor

from fastapi import APIRouter, Depends, status

from fl_api.core.dependencies import get_session
from fl_api.utils.flip_session import FLIP_Session
from fl_api.utils.schemas import UploadAppRequest
from fl_api.utils.upload import upload_application

router = APIRouter()


@router.post("/upload_app/{model_id}", status_code=status.HTTP_200_OK)
def upload_app(model_id: str, body: UploadAppRequest, session: FLIP_Session = Depends(get_session)) -> dict[str, str]:
    """
    Upload an application to the server.

    Args:
        model_id (str): The ID of the model to associate the application with.
        body (UploadAppRequest): The request body containing the application details.
        session (FLIP_Session): The NVFlare session instance.

    Returns:
        dict[str, str]: A dictionary containing the status of the upload.
    """
    return upload_application(model_id, body, upload_dir=session.upload_dir)


@router.get("/get_available_apps_to_upload", status_code=status.HTTP_200_OK, response_model=list[str])
def get_available_apps_to_upload(session: FLIP_Session = Depends(get_session)) -> list[str]:
    """
    Get list of available apps to upload (list the contents of the upload directory that are directories).

    Args:
        session (FLIP_Session): The NVFlare session instance.

    Returns:
        list[str]: list of available directories to upload.
    """
    return session.get_available_apps_to_upload()
