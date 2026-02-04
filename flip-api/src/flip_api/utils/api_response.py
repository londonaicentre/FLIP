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
import logging

from fastapi import status

# Define the standard CORS headers
HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "OPTIONS,POST,GET,PUT,DELETE",
}


def success(status_code=status.HTTP_200_OK, data=None):
    """
    Construct a successful API response with default CORS headers.
    """
    if data is None:
        data = {}

    response = {"statusCode": status_code, "headers": HEADERS, "body": json.dumps(data)}

    return response


def error(status_code, error):
    """
    Construct an error response with logging and error message.
    """
    logging.error(str(error))

    error_message = (
        error.message if hasattr(error, "message") else "There was an internal error. Please try again later."
    )

    response = {"statusCode": status_code, "headers": HEADERS, "body": json.dumps({"error": error_message})}

    return response


def unhandled_error(status_code, error):
    """
    Construct an unhandled error response with logging and generic error message.
    """
    logging.debug("UNHANDLED ERROR")

    if hasattr(error, "message"):
        logging.error(str(error))

    response = {"statusCode": status_code, "headers": HEADERS, "body": json.dumps({"error": "Something went wrong"})}

    return response


def api_response(status_code, body=None):
    """
    Construct a generic API response with default CORS headers.
    """
    if body is None:
        body = {}

    response = {"statusCode": status_code, "headers": HEADERS, "body": json.dumps(body)}

    return response
