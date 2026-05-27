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

"""Register a single trust on the hub and emit its kit (deploy-time CLI).

Invoked once per trust by the deploy Makefile's ``register-trust-<n>`` targets
(dev: ``docker compose exec``; prod: a one-off ECS task). The trust's
name / code / region are passed as arguments — the hub carries no trust list
of its own; the deploy tooling decides what to register.

Idempotent: if a trust with ``--name`` already exists it is skipped.

Contract:

- **stdout** — a JSON array: one kit object for a new registration, ``[]`` when
  the trust already existed. The array shape keeps ``distribute-trust-kits.sh``
  uniform whether or not anything was registered.
- **stderr** — human-readable logging.
- Exit code 0 on success or skip; 1 on a registration failure.
"""

import argparse
import json
import os
import sys
from typing import Any

from sqlmodel import Session, select

from flip_api.db.database import engine
from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.trusts_services.services.register_trust import (
    TrustRegistrationError,
    register_trust,
)
from flip_api.utils.logger import logger

# Keep this list in sync with scripts/distribute-trust-kits.sh and
# scripts/sync-trust-kits.sh — those scripts upsert exactly these keys.
HUB_SHARED_ENV_KEYS = (
    "AES_KEY_BASE64",
    "CENTRAL_HUB_API_URL",
    "TRUST_API_KEY_HEADER",
    "FL_BACKEND",
    "FLOWER_KIT_DATE",
    "FLARE_KIT_DATE",
    "DOCKER_TAG",
    "DOCKER_FL_TAG",
    "DOCKER_FL_REGISTRY",
    "DOCKER_FL_CLIENT_NAME",
)


def _hub_shared_from_env() -> dict[str, str]:
    """Read hub-shared values from os.environ. Unset keys are omitted (no empty strings)."""
    return {key: os.environ[key] for key in HUB_SHARED_ENV_KEYS if key in os.environ}


def register_one_trust(
    name: str,
    code: str | None,
    region: str | None,
    session: Session,
) -> list[dict[str, Any]]:
    """Register one trust if it does not already exist.

    Args:
        name (str): Trust display name.
        code (str | None): Optional short code.
        region (str | None): Optional NHS region.
        session (Session): SQLModel session.

    Returns:
        list[dict[str, Any]]: ``[kit]`` always — one full kit dict (including
        credentials) for a new registration, or one metadata-only dict (no
        credentials) when the trust already existed (idempotent skip). Both
        shapes include a ``hub_shared`` key populated from os.environ so the
        deploy distributor can sync shared values without rotating credentials.

    Raises:
        TrustRegistrationError: If registration of a new trust fails.
    """
    name = name.strip()
    existing = session.exec(select(Trust).where(Trust.name == name)).first()
    if existing is not None:
        logger.info("Trust %r already registered — emitting hub-shared block only.", name)
        slot = session.exec(select(FLKitSlot).where(FLKitSlot.assigned_to_trust_id == existing.id)).first()
        return [
            {
                "trust_id": str(existing.id),
                "trust_name": existing.name,
                "fl_kit_slot": slot.slot_name if slot else None,
                "fl_kit_slot_number": slot.slot_number if slot else None,
                "hub_shared": _hub_shared_from_env(),
            }
        ]

    kit = register_trust(name=name, code=code, region=region, session=session)
    logger.info(
        "Registered trust %r (id=%s) with kit slot %r.",
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
    """CLI entry point: register one trust, emit its kit JSON to stdout."""
    parser = argparse.ArgumentParser(description="Register one trust on the hub.")
    parser.add_argument("--name", required=True, help="Trust display name.")
    parser.add_argument("--code", default=None, help="Optional short code (e.g. GSTT).")
    parser.add_argument("--region", default=None, help="Optional NHS region (e.g. London).")
    args = parser.parse_args()

    with Session(engine) as session:
        try:
            kits = register_one_trust(args.name, args.code, args.region, session)
        except TrustRegistrationError as e:
            logger.error("Failed to register trust %r: %s", args.name, e)
            session.rollback()
            print("[]")
            sys.exit(1)

    # JSON array on stdout — the deploy distributor writes it to the per-trust
    # kit file. Logging went to stderr.
    print(json.dumps(kits))


if __name__ == "__main__":
    main()
