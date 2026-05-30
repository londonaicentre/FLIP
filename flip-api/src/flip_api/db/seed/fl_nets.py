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
from flip_api.db.seed.seed_logger import logger


def seed_fl_nets(session: Session) -> list[FLNets]:
    """Upsert FL nets in the database from ``NET_ENDPOINTS``.

    Rows are matched by ``name`` and their ``endpoint`` reconciled to the value
    in ``NET_ENDPOINTS`` on every startup. The previous behaviour was
    insert-only and skipped on name conflict, which silently stranded stale
    endpoints after the EC2-compose → ECS Cloud Map migration (rows seeded
    with the old `http://flip-fl-api-net-1:8000` docker-compose hostname
    survived alongside the new `http://fl-api-net-1.flip.local:8000` service
    discovery URL in the env, breaking `/api/fl/status` with `Name or service
    not known`). NET_ENDPOINTS is the canonical source — operator changes to
    the row's endpoint via SQL would also be overwritten on the next start,
    which is the intended behaviour.

    Args:
        session (Session): The SQLModel session used to read existing FL nets and upsert
            entries from ``NET_ENDPOINTS``.

    Returns:
        list[FLNets]: All FL net rows present after seeding.
    """
    settings = get_settings()
    nets = settings.NET_ENDPOINTS
    backend = settings.FL_BACKEND
    existing_by_name = {net.name: net for net in session.exec(select(FLNets)).all()}

    for name, endpoint in nets.items():
        existing = existing_by_name.get(name)
        if existing is None:
            # Bootstrap fl_backend from the declared FL_BACKEND. The background poll reconciles
            # it to each net's self-reported backend at runtime; we only seed the initial value.
            session.add(FLNets(name=name, endpoint=endpoint, fl_backend=backend))
            logger.info(f"FL Net '{name}' created with endpoint '{endpoint}' and backend '{backend}'.")
        else:
            if existing.endpoint != endpoint:
                logger.info(
                    f"FL Net '{name}' endpoint changed from '{existing.endpoint}' to '{endpoint}'; reconciling."
                )
                existing.endpoint = endpoint
                session.add(existing)
            # Backfill backend for rows created before fl_backend existed. Do NOT overwrite a
            # value that is already set — the self-report poll is the runtime authority for it.
            if existing.fl_backend is None:
                logger.info(f"FL Net '{name}' backfilling backend '{backend}'.")
                existing.fl_backend = backend
                session.add(existing)
            if existing.endpoint == endpoint and existing.fl_backend is not None:
                logger.info(f"FL Net '{name}' already matches NET_ENDPOINTS. Skipping.")
    session.commit()

    return list(session.exec(select(FLNets)).all())


if __name__ == "__main__":
    with Session(engine) as session:
        nets = seed_fl_nets(session)
