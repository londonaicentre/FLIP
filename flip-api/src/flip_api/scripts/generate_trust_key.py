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

"""Generate a trust API key and its SHA-256 hash.

Pure utility, used by ``register_trust`` to mint a trust's API key and its
internal-service key at registration time. There is no standalone CLI — keys
are never added by hand; ``register_trust`` is the sole writer of the registry.
"""

import hashlib
import secrets


def generate_trust_key() -> tuple[str, str]:
    """Generate a trust API key and its SHA-256 hash.

    Returns:
        tuple[str, str]: Tuple of (plaintext_key, sha256_hex_hash).
    """
    key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    return key, key_hash
