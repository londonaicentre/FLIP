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

import json

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, status

from flip_api.config import get_settings
from flip.domain.interfaces.shared import IAccessRequest
from flip.utils.constants import ACCESS_REQUEST_TEMPLATE_NAME
from flip.utils.logger import logger

router = APIRouter(prefix="/users", tags=["user_services"])


# [#114] ✅
@router.post("/access", status_code=status.HTTP_204_NO_CONTENT)
def request_access(request: IAccessRequest):
    """
    Send an access request email to administrators.

    Args:
        request: The access request details

    Returns:
        204 No Content on success
    """
    logger.info(request)
    try:
        admin_email = get_settings().AWS_SES_ADMIN_EMAIL_ADDRESS
        sender_email = get_settings().AWS_SES_SENDER_EMAIL_ADDRESS
        region_name = get_settings().AWS_REGION

        logger.debug(f"Admin email: {admin_email}, Sender email: {sender_email}, Region: {region_name}")

        sesv2 = boto3.client("sesv2", region_name=region_name)

        template_data = {
            "email": request.email,
            "name": request.full_name,
            "purpose": request.reason_for_access,
        }

        logger.info(f"Sending access request to admin email: {template_data}")

        result = sesv2.send_email(
            FromEmailAddress=sender_email,
            Destination={"ToAddresses": [admin_email]},
            Content={
                "Template": {
                    "TemplateName": ACCESS_REQUEST_TEMPLATE_NAME,
                    "TemplateData": json.dumps(template_data),
                }
            },
        )
        logger.info(f"SES v2 send_email response: {result}")
        logger.info(f"Access request sent to {admin_email} from {sender_email}")

    except ClientError as e:
        error_message = f"Error sending SES templated email: {str(e)}"
        logger.error(error_message)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=error_message)
    except Exception as e:
        error_message = f"Internal server error: {str(e)}"
        logger.error(error_message)
        raise HTTPException(
            status_code=e.status_code if hasattr(e, "status_code") else status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_message,
        )
