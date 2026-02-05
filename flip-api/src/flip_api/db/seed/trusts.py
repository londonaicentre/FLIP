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

from typing import Dict, List

from sqlmodel import Session, col, select

from flip_api.db.database import engine
from flip_api.db.models.main_models import Trust
from flip_api.utils.get_secrets import get_secret


def seed_trusts(session: Session) -> List[Dict[str, str]]:
    """Seed trusts into the database."""
    # TODO Replace with dynamic trust names and endpoints as needed
    trust_names = [
        "Trust_1",
        "Trust_2",
    ]

    # Get endpoints from secrets manager
    trust_endpoints = [get_secret(f"{name}-endpoint") for name in trust_names]

    for trust_name, endpoint in zip(trust_names, trust_endpoints):
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
        except Exception as e:
            # If the endpoint is not found in secrets, skip this trust
            print(f"Endpoint for {trust_name} not found in secrets. Skipping. Error: {e}")
            continue
    session.commit()

    # Return trusts
    trusts = session.exec(select(Trust)).all()
    return [{"name": trust.name, "endpoint": trust.endpoint} for trust in trusts]


if __name__ == "__main__":
    with Session(engine) as session:
        seed_trusts(session)
        print("Trusts seeded successfully.")
        session.commit()
        session.close()
