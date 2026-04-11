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

from flip_api.db.database import engine
from flip_api.db.models.main_models import FLNets, FLScheduler
from flip_api.domain.schemas.status import NetStatus
from flip_api.utils.logger import logger


def seed_fl_scheduler(session: Session, nets: list[FLNets]) -> list[FLScheduler]:
    """Seed FL scheduler entries."""
    logger.debug("Seeding Federated Learning Scheduler")
    for net in nets:
        # Check if scheduler entry exists
        logger.debug(f"Checking scheduler for net: {net.name} ({net.id})")
        statement = select(FLScheduler).where(col(FLScheduler.net_id) == net.id)
        existing_scheduler = session.exec(statement).first()

        if not existing_scheduler:
            new_scheduler = FLScheduler(status=NetStatus.AVAILABLE, netid=net.id)
            session.add(new_scheduler)

    session.commit()

    # Return all scheduler entries
    schedulers = session.exec(select(FLScheduler)).all()
    return list(schedulers)


if __name__ == "__main__":
    with Session(engine) as session:
        nets = session.exec(select(FLNets)).all()
