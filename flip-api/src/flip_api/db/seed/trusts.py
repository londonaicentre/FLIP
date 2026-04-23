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
from flip_api.utils.logger import logger


def seed_trusts(session: Session) -> list[dict[str, str]]:
    """Seed trusts into the database.

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

    for trust_name in trust_names:
        # Check if trust exists
        statement = select(Trust).where(col(Trust.name) == trust_name)
        existing_trust = session.exec(statement).first()

        if not existing_trust:
            # Create new trust
            new_trust = Trust(name=trust_name)
            session.add(new_trust)
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
