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

from flip_api.auth.identity import (
    IdentityProvider,
    IdentityProviderError,
    InvalidIdentifierError,
    UserNotFoundError,
    build_identity_provider,
)
from flip_api.db.models.user_models import RoleRef, UserProfile, UserRole
from flip_api.db.seed.seed_logger import logger
from flip_api.utils.constants import (
    ADMIN_EMAIL_1,
    ADMIN_EMAIL_2,
    ADMIN_EMAIL_3,
    DEMO_ADMIN_EMAIL,
    DEMO_RESEARCHER_EMAIL,
    RESEARCHER_EMAIL,
    VIEWER_EMAIL,
)

MAIN_USER_PROFILES = {
    ADMIN_EMAIL_1: ("AI Centre Admin", "London AI Centre"),
    ADMIN_EMAIL_2: ("Alexandre Triay Bagur", "King's College London"),
    ADMIN_EMAIL_3: ("Rafael Garcia-Dias", "King's College London"),
    RESEARCHER_EMAIL: ("Rafael Garcia-Dias", "King's College London"),
    VIEWER_EMAIL: ("Alexandre Triay", "London AI Centre"),
    # Demo-video identities (flip_api/scripts/create_demo_users.py). Seeding
    # skips them with a warning when they don't exist in the identity
    # provider, so stacks that never run the demo are unaffected.
    DEMO_RESEARCHER_EMAIL: ("Demo Researcher", "London AI Centre"),
    DEMO_ADMIN_EMAIL: ("Demo Admin", "London AI Centre"),
}


def ensure_user_and_role(
    idp: IdentityProvider,
    email: str,
    role_ref: RoleRef,
    session: Session,
    name: str,
    organisation: str,
) -> None:
    """Look up the identity-provider user for ``email`` and seed their FLIP profile/role.

    The identity provider is the source of truth for user identity, so this
    function does not create an auth user locally. It stores FLIP-owned
    profile fields in ``user_profile`` and ensures the ``user_role`` grant
    exists for the provider sub corresponding to the given email.

    Args:
        idp (IdentityProvider): The configured identity provider.
        email (str): The user's email, used to look up the corresponding provider user.
        role_ref (RoleRef): The role to assign to the user if they don't already have it.
        session (Session): The SQLModel session used for DB reads and writes.
        name (str): The user's seeded display name.
        organisation (str): The user's seeded organisation.
    """
    # 1️⃣ Try to get the user from the identity provider
    try:
        user = idp.get_user(email=email)
    except UserNotFoundError:
        logger.warning(
            "Skipping seed user %s with role %s because the user does not exist in the identity provider.",
            email,
            role_ref.name,
        )
        return
    user_id = user.id
    logger.debug(f"Found identity-provider user {email} with sub {user_id}")

    profile = session.get(UserProfile, user_id)
    if profile is None:
        logger.info(f"Creating profile for {email}")
        session.add(UserProfile(user_id=user_id, name=name, organisation=organisation))
        session.commit()
    elif profile.name != name or profile.organisation != organisation:
        logger.info(f"Updating profile for {email}")
        profile.name = name
        profile.organisation = organisation
        session.add(profile)
        session.commit()

    # 2️⃣ Bootstrap the user's role only on a fresh DB.
    # We assign the seeded role only when the user has NO role at all, not
    # just when this specific role is missing. Otherwise every flip-api boot
    # would re-grant the hardcoded seed role and silently undo any admin-UI
    # role change for these well-known emails (the dev flip-db has no volume,
    # so a `make down && make up` wipes the DB and re-runs this seed).
    has_any_role = session.exec(
        select(UserRole).where(UserRole.user_id == user_id)
    ).first()

    if not has_any_role:
        logger.info(f"Assigning role {role_ref.name} to {email}")
        session.add(UserRole(user_id=user_id, role_id=role_ref.value))
        session.commit()
    else:
        logger.debug(f"{email} already has a role; leaving role grants unchanged")


def _ensure_user_and_role_resilient(
    idp: IdentityProvider,
    email: str,
    role_ref: RoleRef,
    session: Session,
    name: str,
    organisation: str,
) -> None:
    """Run ``ensure_user_and_role`` but tolerate transient provider-side failures.

    Seeding reads from the identity provider on every boot. A blip mid-deploy
    would otherwise couple flip-api liveness to the provider's read
    availability — log the skip loudly and continue with the remaining users
    instead.

    Definitive failures (``InvalidIdentifierError``: a malformed well-known
    email) still propagate: those are config / programming errors that should
    fail boot loudly rather than producing a platform with quietly missing
    grants.

    Args:
        idp (IdentityProvider): The configured identity provider.
        email (str): The user's email used to look up the corresponding provider user.
        role_ref (RoleRef): The role to grant if missing.
        session (Session): The SQLModel session used for DB reads and writes.
        name (str): The user's seeded display name.
        organisation (str): The user's seeded organisation.
    """
    try:
        ensure_user_and_role(idp, email, role_ref, session, name, organisation)
    except InvalidIdentifierError:
        raise
    except IdentityProviderError as exc:
        logger.warning(
            "Skipping seed for %s with role %s due to an identity-provider read failure (%s); "
            "platform will boot without this grant — investigate if it persists.",
            email,
            role_ref.name,
            exc,
        )


def seed_main_users(session: Session) -> None:
    """
    Seed role grants for the well-known admin/researcher/viewer emails.

    Resolves each email to its identity-provider sub and ensures the
    corresponding ``user_role`` row exists. No local users-table state is created.

    Args:
        session (Session): The SQLModel session used for DB reads and writes.
    """
    logger.debug("Seeding main users...")

    # One raw provider for the whole seed: this runs in the entrypoint before
    # uvicorn, outside FastAPI, so there is no dependency to inject.
    idp = build_identity_provider()

    # Ensure the Admin role grant for each well-known admin email.
    _ensure_user_and_role_resilient(idp, ADMIN_EMAIL_1, RoleRef.ADMIN, session, *MAIN_USER_PROFILES[ADMIN_EMAIL_1])
    _ensure_user_and_role_resilient(idp, ADMIN_EMAIL_2, RoleRef.ADMIN, session, *MAIN_USER_PROFILES[ADMIN_EMAIL_2])
    _ensure_user_and_role_resilient(idp, ADMIN_EMAIL_3, RoleRef.ADMIN, session, *MAIN_USER_PROFILES[ADMIN_EMAIL_3])

    # Ensure the Researcher role grant.
    _ensure_user_and_role_resilient(
        idp, RESEARCHER_EMAIL, RoleRef.RESEARCHER, session, *MAIN_USER_PROFILES[RESEARCHER_EMAIL]
    )

    # Ensure the Viewer role grant.
    _ensure_user_and_role_resilient(idp, VIEWER_EMAIL, RoleRef.VIEWER, session, *MAIN_USER_PROFILES[VIEWER_EMAIL])

    # Demo-video identities — no-ops (with a warning) until the users are
    # provisioned via flip_api/scripts/create_demo_users.py. Deliberately on
    # the same universal every-boot path as the grants above (no env gate
    # needed): the grant only materialises if the demo user exists in that
    # environment's identity provider, which only the dev-stack provisioning
    # script creates.
    _ensure_user_and_role_resilient(
        idp, DEMO_RESEARCHER_EMAIL, RoleRef.RESEARCHER, session, *MAIN_USER_PROFILES[DEMO_RESEARCHER_EMAIL]
    )
    _ensure_user_and_role_resilient(
        idp, DEMO_ADMIN_EMAIL, RoleRef.ADMIN, session, *MAIN_USER_PROFILES[DEMO_ADMIN_EMAIL]
    )

    logger.info("✅ Finished seeding main users.")
