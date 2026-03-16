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
from flip_api.utils.get_secrets import get_secret
from flip_api.utils.logger import logger


def seed_trusts(session: Session) -> list[dict[str, str]]:
    """Seed trusts into the database."""

    # Get settings
    stt = get_settings()

    # In production, get trust endpoints from AWS Secrets Manager; in dev, use env variables or defaults
    if stt.ENV == "production":
        trust_endpoints: dict[str, str] = get_secret("trust_endpoints")  # type: ignore

    else:
        # In dev, use environment variables or defaults
        trust_endpoints = stt.TRUST_ENDPOINTS

    for trust_name, endpoint in trust_endpoints.items():
        try:
            # Check if trust exists
            statement = select(Trust).where(col(Trust.name) == trust_name)
            existing_trust = session.exec(statement).first()

            if existing_trust:
                # Update existing trust
                existing_trust.endpoint = endpoint
            else:
                # Create new trust
                new_trust = Trust(name=trust_name, endpoint=endpoint)
                session.add(new_trust)
        except Exception:
            # If the endpoint is not found in secrets, skip this trust
            logger.info("Endpoint not found in secrets for one of the trusts. Skipping.")
            continue
    session.commit()

    # Return trusts
    trusts = session.exec(select(Trust)).all()
    return [{"name": trust.name, "endpoint": trust.endpoint} for trust in trusts]


if __name__ == "__main__":
    with Session(engine) as session:
        seed_trusts(session)
        logger.info("Trusts seeded successfully.")
        session.commit()
        session.close()
