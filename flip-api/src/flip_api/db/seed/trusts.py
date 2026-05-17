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


from sqlmodel import Session, col, select

from flip_api.config import get_settings
from flip_api.db.database import engine
from flip_api.db.models.main_models import Trust
from flip_api.db.seed.seed_logger import logger

SEED_REGION = "London"

# Friendly-name overrides for seeded trusts. The env key (left) stays the lookup
# identifier that Makefile uses to fetch the api key / internal key; the override
# (display name, code) is what gets persisted in the DB. Demonstrates that the hub's
# trust identity is independent of the env-side bookkeeping name and proves the
# admin/trusts flow works with arbitrary friendly names.
SEED_NAME_OVERRIDES: dict[str, tuple[str, str]] = {
    "Trust_1": ("(Mock) Guys and St Thomas NHS Trust", "GSTT"),
}


def _derive_code(trust_name: str) -> str:
    """Default display code for a seeded trust.

    Maps the dev-convention `Trust_N` names to the short `TN` form. Any other name (e.g.
    a real NHS trust name like `GSTT`) is passed through as-is — the operator can override
    via the admin UI / direct DB update later.
    """
    return trust_name.replace("Trust_", "T") if trust_name.startswith("Trust_") else trust_name


def seed_trusts(session: Session) -> list[dict[str, str]]:
    """Seed trusts into the database.

    For each name in ``TRUST_NAMES``: inserts a row with ``code`` derived from the name,
    ``region`` set to ``SEED_REGION``, and ``api_key_hash`` copied from
    ``TRUST_API_KEY_HASHES[name]`` when available. For pre-existing rows missing any of
    those, backfills them in-place — this is the one-time migration path that lets
    env-seeded trusts authenticate against the DB now that ``authenticate_trust`` reads
    from there.

    Args:
        session (Session): The SQLModel session used for reads and inserts.

    Returns:
        list[dict[str, str]]: List of ``{"name": <trust_name>}`` dicts for every trust present
        after seeding.
    """

    # Get settings
    stt = get_settings()

    # Trust names to seed — in production these could come from config or secrets
    trust_names: list[str] = stt.TRUST_NAMES
    env_hashes: dict[str, str] = stt.TRUST_API_KEY_HASHES or {}

    for trust_name in trust_names:
        override = SEED_NAME_OVERRIDES.get(trust_name)
        display_name, code = override if override else (trust_name, _derive_code(trust_name))
        env_hash = env_hashes.get(trust_name)

        # Check if trust exists under the display (post-override) name — that's the row
        # that the heartbeat/auth path-param will look up at runtime.
        statement = select(Trust).where(col(Trust.name) == display_name)
        existing_trust = session.exec(statement).first()

        if not existing_trust:
            session.add(
                Trust(name=display_name, code=code, region=SEED_REGION, api_key_hash=env_hash)
            )
        else:
            if existing_trust.code is None:
                existing_trust.code = code
            if existing_trust.region is None:
                existing_trust.region = SEED_REGION
            if existing_trust.api_key_hash is None and env_hash is not None:
                existing_trust.api_key_hash = env_hash
    session.commit()

    # Return trusts
    trusts = session.exec(select(Trust)).all()
    return [{"name": trust.name} for trust in trusts]


if __name__ == "__main__":
    with Session(engine) as session:
        seed_trusts(session)
        logger.info("Trusts seeded successfully.")
        session.commit()
        session.close()
