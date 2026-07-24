# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import threading
from collections.abc import Generator
from typing import Any
from urllib.parse import quote

import boto3
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from flip_api.config import get_settings
from flip_api.db.db_auth_logger import logger

# RDS Proxy drops idle client connections after `idle_client_timeout` (1800s by
# default — see deploy/providers/AWS/rds_proxy.tf). Recycle pooled connections
# comfortably below that so we never hand out a connection the proxy has already
# closed; pool_pre_ping is the belt-and-braces liveness check on top.
_POOL_RECYCLE_SECONDS = 1500

# Lazily-created, reused RDS client for minting IAM auth tokens. boto3 clients
# are not free to construct (service-model load), and the do_connect hook can
# fire on every new physical connection, so we cache one. Typed Any because the
# bare boto3-stubs install has no rds service stub. The lock guards the
# first-warmup race: the do_connect hook can fire from several pool threads at
# once, and without it each would construct (and discard all-but-one) client.
_rds_client: Any = None
_rds_client_lock = threading.Lock()


def _get_rds_client() -> Any:
    """Return a cached boto3 RDS client for the configured region."""
    global _rds_client  # noqa: PLW0603
    if _rds_client is None:
        with _rds_client_lock:
            # Re-check inside the lock: another thread may have built it while
            # this one waited (double-checked locking).
            if _rds_client is None:
                _rds_client = boto3.session.Session().client(
                    service_name="rds", region_name=get_settings().AWS_REGION
                )
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

    Raises:
        Exception: re-raises any error from minting the token (after logging it),
            so the failure still surfaces loudly on the connection path.
    """
    stt = get_settings()
    try:
        return _get_rds_client().generate_db_auth_token(
            DBHostname=stt.DB_HOST,
            Port=stt.DB_PORT,
            DBUsername=stt.POSTGRES_USER,
            Region=stt.AWS_REGION,
        )
    except Exception:
        # Log-and-re-raise: a mint failure (missing rds-db:connect grant, bad
        # AWS_REGION, no resolvable task-role credentials) otherwise reaches the
        # operator as an opaque SQLAlchemy connection error indistinguishable
        # from a DB outage. Re-raise unchanged so the pool's pre-ping/invalidate
        # logic still sees the native exception. Never log the token or a secret
        # — only the non-sensitive host/user/region, plus the original exception
        # via exc_info (a mint failure's traceback carries no token/secret).
        logger.warning(
            "Failed to mint RDS IAM auth token for DB connection (host=%s, user=%s, region=%s); "
            "check the flip-api task role's rds-db:connect grant and AWS_REGION.",
            stt.DB_HOST,
            stt.POSTGRES_USER,
            stt.AWS_REGION,
            exc_info=True,
        )
        raise


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
    # @, #, %, /, etc.; strip in case there is a trailing comment in the env file.
    # quote(safe="") — not quote_plus — so a space encodes as %20 (which the
    # SQLAlchemy URL parser decodes back to a space) rather than '+' (which it
    # would leave as a literal '+', corrupting the password). safe="" keeps '/'
    # encoded, which the default quote(safe="/") would not.
    encoded_password = quote(stt.POSTGRES_PASSWORD.strip(), safe="")
    db_url = f"postgresql+psycopg2://{stt.POSTGRES_USER}:{encoded_password}@{stt.DB_HOST}:{stt.DB_PORT}/{stt.POSTGRES_DB}"
    return create_engine(db_url, echo=False)


# Lazily-created, reused engine. Built on first use rather than at import time
# so importing this module never reads settings or constructs the engine as a
# side effect (FLIP#583) — tests, scripts, and tooling can import it without a
# configured environment. The lock guards the first-build race (the same
# double-checked locking pattern as ``_get_rds_client`` above): ``get_engine``
# is called from FastAPI's request threadpool and APScheduler job threads.
_engine: Engine | None = None
_engine_lock = threading.Lock()


def get_engine() -> Engine:
    """Return the shared SQLAlchemy engine, building it on first use.

    Returns:
        Engine: The lazily-built, module-cached engine for the active environment.
    """
    global _engine  # noqa: PLW0603
    if _engine is None:
        with _engine_lock:
            # Re-check inside the lock: another thread may have built it while
            # this one waited (double-checked locking).
            if _engine is None:
                _engine = _build_engine()
    return _engine


def get_session() -> Generator[Session, None, None]:
    """
    Create a new SQLModel session.

    The ``with`` block is load-bearing: FastAPI finalises this dependency by
    throwing the endpoint's exception into the generator at the ``yield`` (or
    closing it on disconnect/cancellation), so a bare ``yield`` followed by
    ``session.close()`` never closes the session on those paths — the
    checked-out connection is stranded ``idle in transaction`` until the
    process restarts, and enough of them exhaust the pool and take the whole
    API down (FLIP#773).

    Yields:
        Session: A new SQLModel session.
    """
    with Session(get_engine()) as session:
        yield session
