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

import logging
import sys

# Configure root logging BEFORE importing flip_api.utils.logger (transitively
# pulled in by every seed sub-module). The shared logger is
# `logging.getLogger("uvicorn")` — under FastAPI/uvicorn that logger has
# handlers, but this script runs standalone via entrypoint.sh before uvicorn
# boots, so without root-level handlers every `logger.info` / `logger.debug`
# call in the seed phase is silently dropped (Python's `lastResort` only
# emits WARNING+). basicConfig adds a StreamHandler to root so the
# "uvicorn" logger's records propagate and reach stderr → CloudWatch.
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from sqlmodel import Session, SQLModel  # noqa: E402

from flip_api.db.database import engine  # noqa: E402
from flip_api.db.seed.banner import seed_banner  # noqa: E402
from flip_api.db.seed.fl_nets import seed_fl_nets  # noqa: E402
from flip_api.db.seed.fl_scheduler import seed_fl_scheduler  # noqa: E402
from flip_api.db.seed.main_users import seed_main_users  # noqa: E402
from flip_api.db.seed.permissions import seed_permissions  # noqa: E402
from flip_api.db.seed.role_permissions import seed_role_permissions  # noqa: E402
from flip_api.db.seed.roles import seed_roles  # noqa: E402
from flip_api.db.seed.site_config import seed_config  # noqa: E402
from flip_api.db.seed.trusts import seed_trusts  # noqa: E402
from flip_api.utils.logger import logger  # noqa: E402


def main() -> None:
    """Main seeding function.

    Raises:
        Exception: Any error encountered during seeding is re-raised after rolling back the
            session.
    """
    logger.debug("Creating database tables...")
    SQLModel.metadata.create_all(engine)
    logger.debug("About to seed the database...")

    with Session(engine) as session:
        try:
            # Clear existing data
            logger.debug("Creating Roles")
            roles = seed_roles(session)
            logger.debug("Creating Permissions")
            permissions = seed_permissions(session)
            logger.debug("Creating Role Permissions")
            seed_role_permissions(session)
            logger.debug("Creating Users")
            seed_main_users(session)
            logger.debug("Creating Trusts")
            trusts = seed_trusts(session)
            logger.debug("Creating Banner")
            seed_banner(session)
            logger.debug("Seeding Site Config")
            seed_config(session)
            logger.debug("Seeding Federated Learning Networks")
            fl_nets = seed_fl_nets(session)
            logger.debug("Seeding Federated Learning Scheduler")
            fl_scheduler = seed_fl_scheduler(session, fl_nets)

            logger.debug("Seeding completed successfully")
            logger.debug({
                "roles": roles,
                "permissions": permissions,
                "trusts": trusts,
                "fl_nets": fl_nets,
                "fl_scheduler": fl_scheduler,
            })
            session.commit()
            session.flush()

        except Exception as e:
            session.rollback()
            print(f"Error during seeding: {e}")
            raise


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Seeding failed: {e}")
        exit(1)
