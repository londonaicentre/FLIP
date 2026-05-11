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

"""Integration-test scaffolding.

Boots a throwaway Postgres via Testcontainers once per session and rewires
flip-api's module-level engine references at it so:

* the existing ``session`` fixture from ``tests.fixtures.main_fixtures`` (and
  every test depending on it) reads/writes the throwaway DB;
* ``Depends(get_session)`` does the same when tests hit endpoints through the
  FastAPI ``client`` fixture.

Each test gets its own function-scoped ``session`` (overrides the module-scoped
one in main_fixtures) and tables are truncated between tests so test order is
irrelevant. This keeps integration test files free of bring-up boilerplate —
they just request ``session`` (and/or ``client``) and write SQL-shaped
assertions.
"""

import os
from collections.abc import Generator
from unittest.mock import patch
from uuid import UUID, uuid4

import boto3
import pytest
from moto import mock_aws
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool
from testcontainers.postgres import PostgresContainer

import flip_api.db.database as db_module

# Importing models is what populates SQLModel.metadata so create_all() builds the
# full schema. The imports look unused but are load-bearing — don't drop them.
import flip_api.db.models.main_models  # noqa: F401
import flip_api.db.models.user_models  # noqa: F401
import tests.fixtures.main_fixtures as main_fixtures
from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.user_models import RoleRef, UserRole
from flip_api.db.seed.permissions import seed_permissions
from flip_api.db.seed.role_permissions import seed_role_permissions
from flip_api.db.seed.roles import seed_roles
from flip_api.db.seed.trusts import seed_trusts
from flip_api.main import app


@pytest.fixture(scope="session", autouse=True)
def aws_mock() -> Generator[None, None, None]:
    """In-process moto fake for S3, Cognito and SES (B2 — issue #368).

    moto intercepts ``boto3.client(...)`` calls at the botocore layer, so the
    flip-api production code paths in ``utils/s3_client.py``,
    ``utils/cognito_helpers.py`` and the inline ``boto3.client("sesv2", ...)``
    constructions in ``user_services/access_request.py`` and
    ``private_services/imaging_notifications.py`` all hit the moto fake with no
    test-only branches in source.

    Why moto and not LocalStack: ``cognito-idp`` and ``sesv2`` are Pro-only on
    LocalStack (the free tier rejects ``CreateUserPool`` /
    ``CreateEmailIdentity`` outright). moto covers all three in OSS, runs
    in-process so there's no container boot, and the per-region message lists
    on the SES backend (``moto.ses.ses_backends``) make assertion-after-send
    cheap.

    Fake credentials and a stable region are pinned in the environment up
    front: boto3 still demands creds before it'll sign a request, and a
    stable region keeps moto's per-region backends predictable across tests.
    Real-AWS env vars from a developer's shell are intentionally clobbered
    here so a leak from a real account is impossible while the fixture is
    active.
    """
    prior = {
        k: os.environ.get(k)
        for k in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_SECURITY_TOKEN",
            "AWS_DEFAULT_REGION",
            "AWS_PROFILE",
        )
    }
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_SESSION_TOKEN"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_SECURITY_TOKEN"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    os.environ.pop("AWS_PROFILE", None)

    # Production code reads ``AWS_REGION`` off ``Settings`` and threads it into
    # ``boto3.client(region_name=...)``. moto's per-region backends mean a
    # client built for region X cannot see a pool/bucket created in region Y.
    # Pin ``Settings.AWS_REGION`` to the same region the env vars above
    # advertise so both the test setup and the code-under-test land in the
    # same moto backend.
    settings = get_settings()
    prior_region = settings.AWS_REGION
    settings.AWS_REGION = "us-east-1"

    with mock_aws():
        try:
            yield
        finally:
            settings.AWS_REGION = prior_region
            for k, v in prior.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


@pytest.fixture(scope="session")
def pg_container() -> Generator[PostgresContainer, None, None]:
    """Throwaway Postgres for the whole test session.

    Pinned to a specific minor (``postgres:16-alpine``) so a base-image bump on
    Docker Hub can't make CI suddenly red. Match the major to whatever RDS
    runs in stag/prod when you upgrade.
    """
    with PostgresContainer("postgres:16-alpine", driver="psycopg2") as container:
        yield container


@pytest.fixture(scope="session")
def integration_engine(pg_container: PostgresContainer):
    """Build the engine, create the schema, seed essentials, redirect flip-api at it.

    The seed steps that don't need ``Settings`` (permissions, roles,
    role-permissions) run unconditionally. ``seed_trusts`` reads
    ``settings.TRUST_NAMES`` so it only runs if that's set; tests that need
    specific trusts insert their own rows via the ``trust_factory``.
    """
    engine = create_engine(
        pg_container.get_connection_url(),
        echo=False,
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as s:
        seed_permissions(s)
        seed_roles(s)
        seed_role_permissions(s)
        try:
            seed_trusts(s)
        except Exception:
            # TRUST_NAMES may be absent in a CI env that hasn't copied the
            # full .env.development; tests that need trusts add them inline.
            s.rollback()

    # Redirect every reference to the prod-bound engine at the throwaway DB.
    # Both modules captured ``engine`` at import time, so we rebind both names.
    main_fixtures.engine = engine
    db_module.engine = engine

    yield engine

    engine.dispose()


@pytest.fixture(autouse=True)
def _override_get_session(integration_engine):
    """Point ``Depends(get_session)`` at the throwaway DB for every integration test."""

    def _get_test_session() -> Generator[Session, None, None]:
        with Session(integration_engine) as s:
            yield s

    app.dependency_overrides[get_session] = _get_test_session
    yield
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture(autouse=True)
def _stub_cors_lookup():
    """Neutralise FastAPI lifespan side-effects for integration tests.

    ``TestClient.__enter__`` runs the production lifespan, which (a) hits
    Cognito to build the CORS allowlist and (b) starts APScheduler. Cognito
    has no creds in CI; the scheduler is a module-level singleton and a
    second ``start()`` raises ``SchedulerAlreadyRunningError``. Both are
    irrelevant for these tests, so stub them.
    """
    with (
        patch("flip_api.main.get_cors_allowed_origins", return_value=[]),
        patch("flip_api.main.start_scheduler"),
    ):
        yield


# Tables to keep across tests (seeded once per session). Everything else is
# truncated between tests so a leftover row never poisons the next test.
_PRESERVED_TABLES = {"permission", "roles", "role_permission", "trust"}


@pytest.fixture(autouse=True)
def _truncate_tables(integration_engine):
    """Wipe per-test tables before each test runs.

    Uses ``TRUNCATE ... RESTART IDENTITY CASCADE`` in one statement so the
    order of dependent FKs doesn't matter. Cheaper than dropping/recreating
    the schema for every test.
    """
    yield
    table_names = [
        f'"{t.name}"'
        for t in SQLModel.metadata.sorted_tables
        if t.name not in _PRESERVED_TABLES
    ]
    if not table_names:
        return
    with integration_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(table_names)} RESTART IDENTITY CASCADE"))


@pytest.fixture
def session(integration_engine) -> Generator[Session, None, None]:
    """Function-scoped DB session for integration tests.

    Overrides the module-scoped ``session`` from
    ``tests.fixtures.main_fixtures`` (pytest picks the closest conftest), so
    each test gets a fresh session that committed rows from another test
    can't leak into. Each Session() call uses the same engine, so the test
    sees rows committed via the API path through ``client`` too.
    """
    with Session(integration_engine) as s:
        yield s


# Bucket settings that ``s3_buckets`` rebinds to deterministic test values.
# Each entry pairs the ``Settings`` attribute with the prefix the production
# code expects under that bucket — keep these in sync with
# ``.env.development.example`` so the parsed (bucket, prefix) tuples that
# tests construct via ``urlparse`` match the production layout.
_TEST_BUCKET_SETTINGS: dict[str, tuple[str, str]] = {
    "UPLOADED_MODEL_FILES_BUCKET": ("flip-test-assets", "model_files/uploaded"),
    "SCANNED_MODEL_FILES_BUCKET": ("flip-test-assets", "model_files/scanned"),
    "UPLOADED_FEDERATED_DATA_BUCKET": ("flip-test-assets", "uploaded_federated_data"),
    "FL_APP_BASE_BUCKET": ("flip-test-assets", "base-application/nvflare"),
    "FL_APP_DESTINATION_BUCKET": ("flip-test-assets", "app_destination_bucket"),
}


@pytest.fixture
def s3_buckets(aws_mock, monkeypatch) -> dict[str, str]:
    """Rebind bucket Settings to deterministic test values and create the buckets in moto.

    The bucket setting values in ``.env.development.example`` are unfilled
    placeholders (``s3://<your-s3-bucket-name-for-flip-assets>/...``) — when
    CI copies that file in unmodified, ``urlparse(...).netloc`` yields a
    string that S3 rejects as an invalid bucket name. Real env files have
    real names, so the production code path is untouched: this fixture only
    intervenes for the integration suite, where every bucket-touching test
    requests the fixture and ends up reading the rebound settings.

    Returns the mapping of setting-name → bucket-name so individual tests
    can address objects without re-parsing the setting value. Idempotent
    creation: ``BucketAlreadyOwnedByYou`` is swallowed so re-running the
    fixture across tests in the same session doesn't error.
    """
    settings = get_settings()
    bucket_names: dict[str, str] = {}
    for setting_name, (bucket, prefix) in _TEST_BUCKET_SETTINGS.items():
        monkeypatch.setattr(settings, setting_name, f"s3://{bucket}/{prefix}")
        bucket_names[setting_name] = bucket

    s3 = boto3.client("s3")
    for bucket in set(bucket_names.values()):
        try:
            s3.create_bucket(Bucket=bucket)
        except s3.exceptions.BucketAlreadyOwnedByYou:
            pass
    return bucket_names


@pytest.fixture
def cognito_user_pool(aws_mock, monkeypatch) -> Generator[dict[str, str], None, None]:
    """Create a moto user pool + app client and rebind ``Settings`` at them.

    Cognito IDs in ``Settings`` are environment-specific (a real prod pool
    ID); production-code helpers in ``utils/cognito_helpers.py`` read those
    IDs at call time via ``get_settings()``. Patching the module-level
    ``_settings`` singleton is sufficient — pydantic-settings doesn't
    re-read the env, so a ``monkeypatch.setattr`` sticks for the test and
    is rolled back automatically afterwards.

    The ``_cognito_client`` lru_cache is also cleared so the next call
    reaches into moto with the freshly-pinned pool ID rather than serving
    a stale client built before the override.
    """
    from flip_api.utils.cognito_helpers import _cognito_client

    cognito = boto3.client("cognito-idp")
    pool = cognito.create_user_pool(PoolName="b2-pool")
    pool_id = pool["UserPool"]["Id"]
    client_resp = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName="b2-client",
        CallbackURLs=["https://app.example.com/cb"],
        GenerateSecret=False,
    )
    client_id = client_resp["UserPoolClient"]["ClientId"]

    settings = get_settings()
    monkeypatch.setattr(settings, "AWS_COGNITO_USER_POOL_ID", pool_id)
    monkeypatch.setattr(settings, "AWS_COGNITO_APP_CLIENT_ID", client_id)
    _cognito_client.cache_clear()
    yield {"pool_id": pool_id, "client_id": client_id}
    _cognito_client.cache_clear()


@pytest.fixture
def ses_send_email_recorder(aws_mock, monkeypatch) -> list[dict]:
    """Capture every ``sesv2.send_email`` call made during a test.

    Why a recorder instead of letting moto handle the call:
    moto v5's sesv2 backend explicitly raises ``NotImplementedError("Template
    functionality not ready")`` when ``send_email`` is invoked with
    ``Content.Template`` — and every flip-api SES caller (``access_request``,
    ``imaging_notifications``) uses templated content. Patching ``send_email``
    on the boto3 ``sesv2`` client lets the production code path execute
    end-to-end up to the SDK boundary; we then assert the SDK was called with
    the expected shape (``FromEmailAddress``, ``Destination.ToAddresses``,
    template name, template data). It is the highest-fidelity assertion that
    moto's coverage allows today, and the closest approximation to a real
    SES round-trip.

    Switching to a real moto round-trip is a one-line fixture change once
    moto implements sesv2 templates upstream
    (https://github.com/getmoto/moto/issues — search ``sesv2 template``).
    """
    recorded: list[dict] = []
    real_client_factory = boto3.client

    def _send_email_stub(self, **kwargs):  # noqa: ANN001 - boto3 client method shape
        recorded.append(kwargs)
        return {"MessageId": f"stub-{len(recorded)}"}

    def _patched_client(service_name, *args, **kwargs):
        client = real_client_factory(service_name, *args, **kwargs)
        if service_name == "sesv2":
            # Bind the stub onto this specific client instance so cognito /
            # s3 / etc. clients keep their normal moto behaviour.
            import types

            client.send_email = types.MethodType(_send_email_stub, client)
        return client

    monkeypatch.setattr(boto3, "client", _patched_client)
    return recorded


def admin_user(session: Session) -> UUID:
    """Persist an Admin role grant for a fresh Cognito-sub-shaped UUID and return it.

    Admin grants every permission via the seed contract verified in
    ``test_auth_permissions_db_flow``, so any ``can_*`` check short-circuits
    True for this user without per-resource access rows. Cognito is the
    source of truth for user identity (no local users table), so the
    returned UUID stands in for a Cognito ``sub``.
    """
    user_id = uuid4()
    session.add(UserRole(user_id=user_id, role_id=RoleRef.ADMIN.value))
    session.commit()
    return user_id


def override_verify_token_as(user_id: UUID) -> None:
    """Inject ``user_id`` as the authenticated principal for the next request."""
    app.dependency_overrides[verify_token] = lambda: user_id


@pytest.fixture(autouse=True)
def _reset_dependency_overrides_after_each_test():
    """Clear the per-test ``verify_token`` override on teardown.

    No-op for tests that never set the override — ``dict.pop(..., None)``
    swallows the missing key — so widening this to autouse across the whole
    integration suite is free.
    """
    yield
    app.dependency_overrides.pop(verify_token, None)
