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

from collections.abc import Generator
from typing import Any
from urllib.parse import quote_plus

import boto3
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from flip_api.config import get_settings

# RDS Proxy drops idle client connections after `idle_client_timeout` (1800s by
# default — see deploy/providers/AWS/rds_proxy.tf). Recycle pooled connections
# comfortably below that so we never hand out a connection the proxy has already
# closed; pool_pre_ping is the belt-and-braces liveness check on top.
_POOL_RECYCLE_SECONDS = 1500

# Lazily-created, reused RDS client for minting IAM auth tokens. boto3 clients
# are not free to construct (service-model load), and the do_connect hook can
# fire on every new physical connection, so we cache one. Typed Any because the
# bare boto3-stubs install has no rds service stub.
_rds_client: Any = None


def _get_rds_client() -> Any:
    """Return a cached boto3 RDS client for the configured region."""
    global _rds_client  # noqa: PLW0603
    if _rds_client is None:
        _rds_client = boto3.session.Session().client(service_name="rds", region_name=get_settings().AWS_REGION)
    return _rds_client


def _generate_db_auth_token() -> str:
    """Mint a short-lived (~15 min) IAM auth token to use as the DB password.

    ``generate_db_auth_token`` is a local SigV4 signing operation (no network
    round-trip), so it is cheap to call on the connection path. A fresh token
    is produced for every new physical connection, so token expiry and RDS
    secret rotation are both handled transparently — the app holds no static
    DB credential.

    Returns:
        str: IAM authentication token for the configured DB user and host.
    """
    stt = get_settings()
    return _get_rds_client().generate_db_auth_token(
        DBHostname=stt.DB_HOST,
        Port=stt.DB_PORT,
        DBUsername=stt.POSTGRES_USER,
        Region=stt.AWS_REGION,
    )


def _do_connect_listener(_dialect: object, _conn_rec: object, _cargs: object, cparams: dict[str, Any]) -> None:
    """SQLAlchemy ``do_connect`` hook: inject a freshly-minted IAM token as the password.

    The token is passed as a connection parameter (not embedded in the engine
    URL), so it never needs URL-encoding and is regenerated on each new
    physical connection.
    """
    cparams["password"] = _generate_db_auth_token()


def _build_engine() -> Engine:
    """Build the SQLAlchemy engine for the active environment.

    Production authenticates to Postgres through RDS Proxy with a per-connection
    IAM token (passwordless URL + a ``do_connect`` hook), so the app holds no
    static credential and RDS secret rotation is a non-event (FLIP#556). Dev
    uses the static ``POSTGRES_PASSWORD`` from the environment.
    """
    stt = get_settings()

    if stt.ENV == "production":
        # No static password in the URL — the do_connect hook supplies a
        # short-lived IAM token per connection. IAM auth mandates TLS.
        db_url = f"postgresql+psycopg2://{stt.POSTGRES_USER}@{stt.DB_HOST}:{stt.DB_PORT}/{stt.POSTGRES_DB}"
        engine = create_engine(
            db_url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=_POOL_RECYCLE_SECONDS,
            connect_args={"sslmode": "require"},
        )
        event.listen(engine, "do_connect", _do_connect_listener)
        return engine

    # Dev: password from env var. URL-encode to handle special characters like
    # @, #, %, etc.; strip in case there is a trailing comment in the env file.
    encoded_password = quote_plus(stt.POSTGRES_PASSWORD.strip())
    db_url = f"postgresql+psycopg2://{stt.POSTGRES_USER}:{encoded_password}@{stt.DB_HOST}:{stt.DB_PORT}/{stt.POSTGRES_DB}"
    return create_engine(db_url, echo=False)


engine = _build_engine()


def get_session() -> Generator[Session, None, None]:
    """
    Create a new SQLModel session.

    Returns:
        Session: A new SQLModel session.
    """
    session = Session(engine)
    yield session
    session.close()
