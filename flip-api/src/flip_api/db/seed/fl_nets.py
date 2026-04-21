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


from sqlmodel import Session, select

from flip_api.config import get_settings
from flip_api.db.database import engine
from flip_api.db.models.main_models import FLNets
from flip_api.utils.logger import logger


def seed_fl_nets(session: Session) -> list[FLNets]:
    """Seed FL nets into the database.

    Args:
        session (Session): The SQLModel session used to read existing FL nets and insert missing
            ones from ``NET_ENDPOINTS``.

    Returns:
        list[FLNets]: All FL net rows present after seeding.
    """
    nets = get_settings().NET_ENDPOINTS

    fl_nets = session.exec(select(FLNets)).all()

    for name, endpoint in nets.items():
        if any(net.name == name for net in fl_nets):
            logger.info(f"FL Net '{name}' already exists. Skipping.")
            continue
        new_net = FLNets(name=name, endpoint=endpoint)
        session.add(new_net)
        session.commit()

    # Return all nets
    fl_nets = session.exec(select(FLNets)).all()
    return list(fl_nets)


if __name__ == "__main__":
    with Session(engine) as session:
        nets = seed_fl_nets(session)
