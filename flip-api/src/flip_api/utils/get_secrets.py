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

from flip_api.config import get_settings


def get_secrets(secret_name: str = "", region_name: str = "") -> dict:
    """
    Retrieve secrets from AWS Secrets Manager.

    Args:
        secret_name (str): The name of the secret to retrieve.
        region_name (str): The AWS region where the secret is stored.

    Returns:
        dict: The retrieved secret as a dictionary.
    """
    secret_name = get_settings().AWS_SECRET_NAME if not secret_name else secret_name
    region_name = get_settings().AWS_REGION if not region_name else region_name

    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(
        service_name="secretsmanager",
        region_name=region_name,
    )

    try:
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        # For a list of exceptions thrown, see
        # https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
        raise e

    secret_string = get_secret_value_response.get("SecretString")

    if secret_string is None:
        raise ValueError(
            f"Secret '{secret_name}' does not contain 'SecretString'. "
            'Expected a JSON object string, e.g. \'{"aes_key":"..."}\'.'
        )

    # Cast json string to dict and validate structure.
    try:
        secrets = json.loads(secret_string)
    except json.JSONDecodeError as e:
        preview = secret_string[:200]
        raise ValueError(f"Secret '{secret_name}' has invalid JSON in SecretString: {e}. Preview: {preview!r}") from e

    if not isinstance(secrets, dict):
        raise ValueError(f"Secret '{secret_name}' must be a JSON object, got {type(secrets).__name__}.")

    return secrets


def get_secret(secret_key: str, secret_name: str = "", region_name: str = "") -> str:
    """
    Retrieve a specific secret value from an AWS Secrets Manager secret.

    Args:
        secret_key (str): The key of the secret to retrieve.
        secret_name (str): The name of the secret to retrieve.
        region_name (str): The AWS region where the secret is stored.

    Returns:
        str: The value of the requested secret.
    """
    secret_name = get_settings().AWS_SECRET_NAME if not secret_name else secret_name
    region_name = get_settings().AWS_REGION if not region_name else region_name

    secrets = get_secrets(secret_name=secret_name, region_name=region_name)

    try:
        return secrets[secret_key]
    except KeyError:
        raise KeyError(f"Secret key '{secret_key}' not found in secret '{secret_name}'")
