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

"""What the hub said about itself on the last heartbeat reply (FLIP#1204).

The hub embeds two things in every heartbeat reply beside the identity block:

- ``hub_version`` — the build the hub runs. A site upgrades *to the hub's release*
  (``make upgrade-onprem-trust`` defaults to it), so this is what the on-prem
  readiness checklist compares the kit's ``DOCKER_TAG`` against.
- ``aes_key_fingerprint`` — a short digest of the hub's AES key (48 bits of a SHA-256 of a
  random 256-bit key, so it identifies the key without disclosing it). Compared with a digest
  of this trust's own key: a kit whose key was rotated under it otherwise shows up only as
  every task failing to decrypt. The hub's digest is kept as well, so the readiness checklist
  can tell a stale kit from a refreshed kit that trust-api has not been recreated on yet.

Both are optional on the wire — a pre-FLIP#1204 hub sends neither, which is recorded
as "unknown", never as a mismatch. The status is only as fresh as the last heartbeat the
hub accepted: a rejected or failed heartbeat forgets it, so a hub that has since rotated
its key is never reported as a match. The poller records; ``/health`` reports.
"""

from trust_api.utils.encryption import aes_key_fingerprint
from trust_api.utils.logger import logger

_UNKNOWN: dict[str, str | bool | None] = {"hub_version": None, "hub_key_match": None, "hub_key_fingerprint": None}
_status: dict[str, str | bool | None] = dict(_UNKNOWN)
_mismatch_logged = False


def record(body: dict) -> None:
    """Update the cached hub status from a heartbeat reply body.

    Args:
        body (dict): Parsed JSON reply from ``POST /trust/heartbeat``. Fields the hub does
            not send leave the corresponding entry ``None``.
    """
    global _mismatch_logged  # noqa: PLW0603
    hub_version = body.get("hub_version")
    _status["hub_version"] = hub_version if isinstance(hub_version, str) else None

    hub_fingerprint = body.get("aes_key_fingerprint")
    if not isinstance(hub_fingerprint, str):
        _status["hub_key_match"] = None
        _status["hub_key_fingerprint"] = None
        return
    _status["hub_key_fingerprint"] = hub_fingerprint
    try:
        own_fingerprint = aes_key_fingerprint()
    except Exception as e:
        # An unloadable own key is a separate, already-loud fault (every task fails at
        # decrypt time); do not dress it up as a hub mismatch.
        logger.error(f"Cannot fingerprint this trust's AES key: {type(e).__name__}: {e}")
        _status["hub_key_match"] = None
        return

    match = own_fingerprint == hub_fingerprint
    _status["hub_key_match"] = match
    if not match and not _mismatch_logged:
        logger.warning(
            "This trust's AES key differs from the hub's — every task payload will fail to decrypt. "
            "The kit's Hub-shared block is stale: ask the FLIP admin for a refreshed kit "
            "(make sync-trust-kit → make package-onprem-trust-kit) and replace the Hub-shared block."
        )
        _mismatch_logged = True
    elif match:
        _mismatch_logged = False


def forget() -> None:
    """Drop what the hub last said — called when a heartbeat is rejected or fails.

    Whether a mismatch was already logged is kept, so a flapping connection does not repeat
    the warning.
    """
    _status.update(_UNKNOWN)


def current() -> dict[str, str | bool | None]:
    """Return a copy of ``{"hub_version", "hub_key_match", "hub_key_fingerprint"}`` as of the last reply."""
    return dict(_status)


def reset() -> None:
    """Forget everything, including whether a mismatch was already logged (tests)."""
    global _mismatch_logged  # noqa: PLW0603
    forget()
    _mismatch_logged = False
