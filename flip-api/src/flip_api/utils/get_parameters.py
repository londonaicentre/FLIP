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

"""Read-side counterpart to :mod:`flip_api.utils.get_secrets` for SSM Parameter Store.

Non-secret runtime configuration lives in SSM under the ``/flip/<key>`` convention
(``deploy/providers/AWS/parameter_store.tf``); secrets stay in Secrets Manager. Use
this for plain config the hub must re-read at runtime without a redeploy — e.g. the
FL kit-slot pool names (``/flip/fl_kit_slot_names``).
"""

import boto3

from flip_api.config import get_settings


def get_parameter(name: str, region_name: str = "") -> str:
    """Retrieve a plain-text parameter value from AWS SSM Parameter Store.

    Args:
        name (str): The full parameter name (e.g. ``/flip/fl_kit_slot_names``).
        region_name (str): The AWS region holding the parameter. Defaults to the
            configured ``AWS_REGION``.

    Returns:
        str: The parameter's string value.

    Raises:
        ClientError: If the SSM API call fails — including the ``ParameterNotFound``
            error code when the parameter does not exist yet.
    """
    region_name = get_settings().AWS_REGION if not region_name else region_name

    client = boto3.session.Session().client(service_name="ssm", region_name=region_name)
    # WithDecryption is a no-op for String parameters but keeps this util correct if a
    # parameter is ever hardened to SecureString (without it, GetParameter returns
    # ciphertext and every caller's parse breaks behind a malformed-value error).
    return client.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
