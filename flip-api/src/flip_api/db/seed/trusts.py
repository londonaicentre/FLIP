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

from flip_api.auth.access_manager import _bootstrap_trust_hashes
from flip_api.config import get_settings
from flip_api.db.database import engine
from flip_api.db.models.main_models import Trust
from flip_api.db.seed.seed_logger import logger

SEED_REGION = "London"


def seed_trusts(session: Session) -> list[dict[str, str]]:
    """Seed trusts into the database.

    For each env-slot name in ``TRUST_NAMES``, the persisted ``Trust.name`` is the
    per-environment display name from ``TRUST_DISPLAY_NAMES`` (the env-slot name itself
    when the slot is not listed there). ``api_key_hash`` comes from the same source
    ``authenticate_trust`` uses — the settings dict in dev, AWS Secrets Manager in prod
    — keyed by the env-slot name, so a seeded trust authenticates under its display
    name rather than its env-slot name. Pre-existing rows are backfilled in place.

    The display name lives only in the env file (``TRUST_DISPLAY_NAMES``), read by both
    this seed and ``trust/Makefile``, so it is never hardcoded.

    Args:
        session (Session): The SQLModel session used for reads and inserts.

    Returns:
        list[dict[str, str]]: ``{"name": <trust_name>}`` for every trust after seeding.
    """

    stt = get_settings()

    trust_names: list[str] = stt.TRUST_NAMES
    display_names: dict[str, str] = stt.TRUST_DISPLAY_NAMES
    # Same hash source as authenticate_trust (_collect_trust_hashes): the settings
    # dict in dev, Secrets Manager in prod. Keyed by env-slot name — so seeded rows
    # carry api_key_hash even in prod, where the hashes are not a settings field.
    env_hashes: dict[str, str] = _bootstrap_trust_hashes()

    for trust_name in trust_names:
        # Display name persisted as Trust.name; env-slot name is the runtime lookup
        # key the trust-api authenticates with.
        display_name = display_names.get(trust_name, trust_name)
        env_hash = env_hashes.get(trust_name)

        existing_trust = session.exec(select(Trust).where(col(Trust.name) == display_name)).first()

        if not existing_trust:
            session.add(Trust(name=display_name, region=SEED_REGION, api_key_hash=env_hash))
        else:
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
