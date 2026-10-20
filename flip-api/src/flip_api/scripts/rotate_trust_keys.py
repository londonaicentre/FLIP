# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Reissue one trust's keys on the hub and emit its kit (deploy-time CLI).

The rotation counterpart of ``register_trust.py``, invoked by ``make rotate-trust-keys
KIT=<CODE>`` (dev) and by ``deploy/providers/AWS/scripts/register-trusts.sh`` with
``ROTATE_KEYS=1`` (stag / prod, as a one-off ECS task).

Why this exists: re-registration cannot rotate a key. ``register_trust`` refuses a duplicate
name, and the idempotent skip path emits a metadata-only kit because the hub keeps only the
api key's SHA-256 and cannot re-emit plaintext. Deleting first instead drops the trust's
tasks, metrics and FL kit slot assignment. ``rotate_trust_keys`` changes only
``api_key_hash`` and writes a ROTATED audit row; the trust id, code and slot stay as they are.

Transports mirror ``register_trust.py`` exactly:

- *Default (dev pipe / docker compose exec)*: the kit JSON array prints on **stdout**, and
  ``scripts/distribute_trust_kits.py --target`` writes it back into the kit file.
- *``--out-ssm-parameter <name>`` (stag / prod ECS one-off task)*: the kit JSON goes to an SSM
  Parameter Store SecureString and **stdout** receives only that parameter name, keeping the
  plaintext keys out of CloudWatch. The deploy script reads and deletes it immediately.

The kit is always a full one here (unlike registration, there is no skip path): the caller is
asking for new credentials, so a kit without them is a failure.

The kit must still reach the site out of band. The hub swaps the accepted hash as soon as this
commits, so the trust authenticates with the new key once it restarts from the new kit, and the
old key stops working at that point. Ship the kit before assuming the old one is dead.

- **stderr**: human-readable logging (no kit body).
- Exit code 0 on success, 1 on failure.
"""

import argparse
import json
import sys
from typing import Any

from sqlmodel import Session

from flip_api.db.database import get_engine
from flip_api.scripts.register_trust import _hub_shared_from_env, _write_kit_to_ssm
from flip_api.trusts_services.services.register_trust import (
    TrustRegistrationError,
    rotate_trust_keys,
)
from flip_api.utils.logger import logger


def rotate_one_trust(
    name: str | None,
    code: str | None,
    session: Session,
) -> list[dict[str, Any]]:
    """Rotate one trust and return its full kit as a single-element list.

    Args:
        name (str | None): Trust display name, when the caller has it.
        code (str | None): Trust code, used to find the kit and to look the trust up otherwise.
        session (Session): SQLModel session.

    Returns:
        list[dict[str, Any]]: ``[kit]`` with ``trust_api_key``, ``trust_internal_service_key``,
        ``fl_kit_slot``, ``fl_kit_slot_number`` and the ``hub_shared`` block.

    Raises:
        TrustRegistrationError: No trust matched, the code is ambiguous, or it has no kit slot.
    """
    kit = rotate_trust_keys(session, name=name, code=code)
    logger.info(
        "Rotated keys for trust %r (id=%s), FL kit slot %r.",
        kit.trust.name,
        kit.trust.id,
        kit.fl_kit_slot.slot_name,
    )
    return [
        {
            "trust_id": str(kit.trust.id),
            "trust_name": kit.trust.name,
            "trust_api_key": kit.trust_api_key,
            "trust_internal_service_key": kit.trust_internal_service_key,
            "fl_kit_slot": kit.fl_kit_slot.slot_name,
            "fl_kit_slot_number": kit.fl_kit_slot.slot_number,
            "hub_shared": _hub_shared_from_env(),
        }
    ]


def main() -> None:
    """CLI entry point: rotate one trust's keys and emit its kit to the chosen output."""
    parser = argparse.ArgumentParser(
        description=(
            "Reissue a registered trust's api and internal-service keys in place. "
            "Distribute the returned kit to the site out of band; the old key keeps working until it restarts."
        )
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--name", default=None, help="Trust display name.")
    target.add_argument("--code", default=None, help="Trust code (e.g. GSTT).")
    parser.add_argument(
        "--out-ssm-parameter",
        default=None,
        metavar="NAME",
        help=(
            "If set, write the kit JSON to this SSM Parameter Store SecureString name "
            "(must start with '/') and print only the parameter name on stdout, keeping the "
            "plaintext keys out of CloudWatch."
        ),
    )
    args = parser.parse_args()

    with Session(get_engine()) as session:
        try:
            kits = rotate_one_trust(args.name, args.code, session)
        except TrustRegistrationError as e:
            logger.error("Failed to rotate keys for %r: %s", args.name or args.code, e)
            session.rollback()
            if args.out_ssm_parameter:
                # Empty array, same signal as register_trust: no kit, whatever the transport.
                _write_kit_to_ssm([], args.out_ssm_parameter)
                print(args.out_ssm_parameter)
            else:
                print("[]")
            sys.exit(1)

    if args.out_ssm_parameter:
        _write_kit_to_ssm(kits, args.out_ssm_parameter)
        print(args.out_ssm_parameter)
        return

    print(json.dumps(kits))


if __name__ == "__main__":
    main()
