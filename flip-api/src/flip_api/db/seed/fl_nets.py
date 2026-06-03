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
    """Upsert FL nets in the database from ``NET_ENDPOINTS`` and ``FL_BACKEND``.

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

    ``fl_backend`` is likewise canonical: every row is set to the current
    ``FL_BACKEND`` on every startup. There is no runtime reconciliation — the
    way to switch frameworks is ``make restart-fl FL_BACKEND=...``, which
    recreates flip-api so this seeding re-runs and overwrites the backend.

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
            # Seed fl_backend from the declared FL_BACKEND. This value is canonical and never
            # reconciled at runtime; re-seeding (make restart-fl) is the only way it changes.
            session.add(FLNets(name=name, endpoint=endpoint, fl_backend=backend))
            logger.info(f"FL Net '{name}' created with endpoint '{endpoint}' and backend '{backend}'.")
        else:
            changed = False
            if existing.endpoint != endpoint:
                logger.info(
                    f"FL Net '{name}' endpoint changed from '{existing.endpoint}' to '{endpoint}'; reconciling."
                )
                existing.endpoint = endpoint
                changed = True
            # FL_BACKEND is authoritative: always overwrite so `make restart-fl FL_BACKEND=...`
            # (which recreates flip-api) re-applies the declared backend onto every net.
            if existing.fl_backend != backend:
                logger.info(f"FL Net '{name}' backend changed from '{existing.fl_backend}' to '{backend}'.")
                existing.fl_backend = backend
                changed = True
            if changed:
                session.add(existing)
            else:
                logger.info(f"FL Net '{name}' already matches NET_ENDPOINTS and FL_BACKEND. Skipping.")
    session.commit()

    result = list(session.exec(select(FLNets)).all())
    logger.info(f"Seeded {len(result)} FL net(s); declared backend = '{backend}'.")
    return result


if __name__ == "__main__":
    with Session(engine) as session:
        nets = seed_fl_nets(session)
