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

Output transports — two contracts depending on ``--out-ssm-parameter``:

- *Default (dev pipe / docker compose exec)* — the kit JSON array prints on
  **stdout**. Consumed locally by ``scripts/distribute_trust_kits.py``. The
  pipe is local: no remote storage involved.

- *``--out-ssm-parameter <name>`` (prod ECS one-off task)* — the kit JSON is
  written to an SSM Parameter Store SecureString at ``<name>``; **stdout**
  receives only that parameter name (one ASCII line, safe to capture in
  CloudWatch). The deploy script then ``ssm:GetParameter --with-decryption``
  reads the kit out-of-band and ``ssm:DeleteParameter`` immediately deletes
  it. This is the S-1 fix: it keeps plaintext API keys, internal-service
  keys, and ``hub_shared.AES_KEY_BASE64`` out of CloudWatch entirely (the
  awslogs driver only sees the parameter name, never the kit body).

Kit shape (both transports):

- *New registration*: full kit (``trust_id``, ``trust_name``,
  ``trust_api_key``, ``trust_internal_service_key``, ``fl_kit_slot``,
  ``fl_kit_slot_number``, ``hub_shared``).
- *Idempotent skip* (trust already existed): metadata-only kit (same keys
  minus ``trust_api_key`` / ``trust_internal_service_key``). The hub stores
  only the SHA-256 hashes of those keys and cannot re-emit plaintext, so
  credentials are intentionally absent on the skip path.

Both shapes include ``hub_shared`` so the deploy distributor can sync shared
env values without rotating credentials.

- **stderr** — human-readable logging (does not contain the kit body).
- Exit code 0 on success or skip; 1 on a registration or transport failure.
"""

import argparse
import json
import os
import sys
from typing import Any

import boto3
from sqlmodel import Session, select

from flip_api.config import get_settings
from flip_api.db.database import engine
from flip_api.db.models.main_models import FLKitSlot, Trust
from flip_api.trusts_services.services.register_trust import (
    TrustRegistrationError,
    register_trust,
)
from flip_api.utils.logger import logger

# Keep this list in lockstep with scripts/trust_kit_lib.py:HUB_SHARED_KEYS —
# the single operator-side definition (scripts/sync_trust_kit.py and
# scripts/distribute_trust_kits.py both import it; register-trusts.sh delegates
# kit writing to the distributor). This CLI runs inside the flip-api container
# and cannot import the root scripts/ package, so the key set lives in two
# places. The lockstep is asserted by
# tests/unit/scripts/test_register_trust_cli.py::test_hub_shared_keys_in_lockstep.
#
# WARNING — exfil whitelist. Every key listed here is:
#   1. Forwarded to every trust host (written into trust/.env.<slot>).
#   2. Captured by the awslogs driver into CloudWatch /ecs/flip-api when
#      this CLI runs as a one-off ECS task (see S-1 in CLAUDE.md; the
#      `--out-ssm-parameter` flag closes that path but stderr / stdout
#      logger output can still leak if a value lands there by mistake).
# NEVER add a secret here without first proving the trust needs it AND
# choosing a non-CloudWatch transport for it. `AES_KEY_BASE64` is the
# pre-existing exception: it MUST be on the trust to encrypt payloads, but
# its presence is exactly why register_trust supports `--out-ssm-parameter`.
HUB_SHARED_ENV_KEYS = (
    "AES_KEY_BASE64",
    "CENTRAL_HUB_API_URL",
    "TRUST_API_KEY_HEADER",
    "FL_BACKEND",
    "FLOWER_KIT_DATE",
    "FLARE_KIT_DATE",
    "DOCKER_TAG",
    "DOCKER_REGISTRY",
    "DOCKER_FL_TAG",
    "DOCKER_FL_REGISTRY",
    "NLB_SUBDOMAIN",
    "FL_SERVER_PORT",
)


def _hub_shared_from_env() -> dict[str, str]:
    """Read hub-shared values from os.environ. Unset keys are omitted (no empty strings)."""
    return {key: os.environ[key] for key in HUB_SHARED_ENV_KEYS if key in os.environ}


def register_one_trust(
    name: str,
    code: str | None,
    region: str | None,
    session: Session,
    require_existing: bool = False,
) -> list[dict[str, Any]]:
    """Register one trust if it does not already exist.

    Args:
        name (str): Trust display name.
        code (str | None): Short code (e.g. ``GSTT``). Required when registering a new
            trust — ``register_trust`` raises ``EmptyTrustCodeError`` on a blank code.
            Unused on the idempotent-skip / ``require_existing`` paths.
        region (str | None): Optional NHS region.
        session (Session): SQLModel session.
        require_existing (bool): When True, exit with an error if the trust has not yet
            been registered. Prevents ``sync-trust-kit-N`` from silently minting and
            discarding credentials for an unregistered trust.

    Returns:
        list[dict[str, Any]]: ``[kit]`` always — one full kit dict (including
        credentials) for a new registration, or one metadata-only dict (no
        credentials) when the trust already existed (idempotent skip). Both
        shapes include a ``hub_shared`` key populated from os.environ so the
        deploy distributor can sync shared values without rotating credentials.

    Raises:
        TrustRegistrationError: If registration of a new trust fails.
        SystemExit: If ``require_existing`` is True and the trust does not exist.
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

    if require_existing:
        logger.error(
            "Trust %r has not been registered yet. Run 'make register-trusts' first. "
            "Refusing to mint credentials that would be discarded by sync-trust-kits.",
            name,
        )
        sys.exit(1)

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


def _write_kit_to_ssm(kits: list[dict[str, Any]], parameter_name: str) -> None:
    """Write the kit JSON to an SSM Parameter Store SecureString.

    The deploy script (``register-trusts.sh``) mints the parameter name before
    spawning this CLI as a one-off ECS task, then reads + deletes it after the
    task completes. The ECS task role only needs ``ssm:PutParameter`` on this
    path; SecureString encryption uses the AWS-managed ``alias/aws/ssm`` so
    no extra KMS permissions are required.

    ``Overwrite=True`` is intentional — re-runs of ``register-trusts.sh`` may
    reuse a parameter name on retry. Idempotent registration emits the same
    metadata-only kit, so overwriting is safe.

    Args:
        kits (list[dict[str, Any]]): The kit list to persist (always length 1).
        parameter_name (str): SSM parameter name (must start with ``/``).

    Raises:
        SystemExit: If the SSM put fails — the deploy script must NOT proceed
            on a partial write, since the credentials block in ``kits`` is
            unrecoverable from the hub side once the session is committed.
    """
    region = get_settings().AWS_REGION
    client = boto3.session.Session().client("ssm", region_name=region)
    try:
        client.put_parameter(
            Name=parameter_name,
            Value=json.dumps(kits),
            Type="SecureString",
            Overwrite=True,
            Description="Ephemeral trust-kit handoff; deleted by register-trusts.sh after read.",
        )
    except Exception as e:  # noqa: BLE001 — boto3 surfaces many ClientError subclasses; uniform fail-loud
        logger.error("Failed to write kit to SSM parameter %r: %s", parameter_name, e)
        sys.exit(1)


def main() -> None:
    """CLI entry point: register one trust, emit its kit JSON to the chosen output."""
    parser = argparse.ArgumentParser(description="Register one trust on the hub.")
    parser.add_argument("--name", required=True, help="Trust display name.")
    parser.add_argument(
        "--code",
        default=None,
        help=(
            "Short code (e.g. GSTT). Required to register a NEW trust — the service "
            "rejects a blank code. Not needed on the idempotent-skip / --require-existing "
            "paths (the trust already exists)."
        ),
    )
    parser.add_argument("--region", default=None, help="Optional NHS region (e.g. London).")
    parser.add_argument(
        "--require-existing",
        action="store_true",
        default=False,
        help=(
            "Fail with exit code 1 if the trust has not yet been registered. "
            "Used by sync-trust-kit-N to prevent silently minting credentials on an unregistered trust."
        ),
    )
    parser.add_argument(
        "--out-ssm-parameter",
        default=None,
        metavar="NAME",
        help=(
            "If set, write the kit JSON to this SSM Parameter Store SecureString name "
            "(must start with '/') and print only the parameter name on stdout. Use this "
            "in the prod ECS one-off task path to keep plaintext credentials out of "
            "CloudWatch — see S-1 in flip-api/src/flip_api/scripts/register_trust.py."
        ),
    )
    args = parser.parse_args()

    with Session(engine) as session:
        try:
            kits = register_one_trust(
                args.name, args.code, args.region, session, require_existing=args.require_existing
            )
        except TrustRegistrationError as e:
            logger.error("Failed to register trust %r: %s", args.name, e)
            session.rollback()
            if args.out_ssm_parameter:
                # Empty array signals "no kit" to the deploy script regardless of
                # transport — keep behaviour symmetric.
                _write_kit_to_ssm([], args.out_ssm_parameter)
                print(args.out_ssm_parameter)
            else:
                print("[]")
            sys.exit(1)

    if args.out_ssm_parameter:
        # SSM path: stdout gets only the parameter name; the kit body never
        # touches CloudWatch. Logger output on stderr is unaffected and still
        # captured by awslogs — that's intentional (operator visibility into
        # registration outcome) and contains no credentials.
        _write_kit_to_ssm(kits, args.out_ssm_parameter)
        print(args.out_ssm_parameter)
        return

    # Dev pipe: JSON array on stdout for ``scripts/distribute_trust_kits.py``.
    print(json.dumps(kits))


if __name__ == "__main__":
    main()
