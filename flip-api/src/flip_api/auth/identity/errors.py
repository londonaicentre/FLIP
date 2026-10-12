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

"""Provider-neutral failures of the identity directory (FLIP#919).

Providers raise these instead of ``HTTPException`` so the mapping to status
codes lives in exactly one place (:class:`flip_api.auth.identity.http.HttpIdentityProvider`)
and a provider can be driven from a script or the boot seed without FastAPI
in the loop. The message of each exception is the client-facing detail, so it
must stay generic — never the backend SDK's own text, which can carry request
ids, ARNs or hostnames.
"""


class IdentityProviderError(Exception):
    """The provider could not complete the call (maps to HTTP 500)."""


class IdentityProviderUnavailable(IdentityProviderError):
    """The provider could not be reached or is not ready (maps to HTTP 503)."""


class UserNotFoundError(IdentityProviderError):
    """No user matches the identifier (maps to HTTP 404)."""


class UserAlreadyExistsError(IdentityProviderError):
    """A user with that email already exists (maps to HTTP 400)."""


class InvalidIdentifierError(IdentityProviderError):
    """The caller-supplied email or id is malformed, or neither was given (maps to HTTP 400)."""
