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

"""Provision the demo users used by the demo-video recorder.

Creates (idempotently) the demo researcher and demo admin in the configured
identity provider (``AUTH_BACKEND``: the local Keycloak realm or the Cognito
user pool) and sets their permanent passwords, so the scripted demo
(tests/demo_video.py) can sign them in through the real login form. Their
FLIP role grants are seeded at flip-api boot by db/seed/main_users.py —
restart flip-api after running this so the grants land.

Passwords come exclusively from the environment (DEMO_RESEARCHER_PASSWORD /
DEMO_ADMIN_PASSWORD) and are never stored anywhere else; they must satisfy
the provider's password policy. Under Cognito this needs live AWS credentials
with admin permissions on the pool (aws sso login --sso-session FLIP).

Before writing anything the script names the exact target (pool + region +
AWS account, or Keycloak realm + URL) and asks for an explicit interactive
"yes" — a stale AWS_COGNITO_USER_POOL_ID or a wrong SSO account must not
silently plant a known-password admin account in an unintended (e.g.
production) directory.

Usage:
    make demo-users            # from the repo root
    make -C flip-api create_demo_users
"""

import os
import sys

from flip_api.auth.identity import IdentityProvider, IdentityProviderError, UserAlreadyExistsError
from flip_api.auth.identity.factory import build_identity_provider
from flip_api.config import get_settings
from flip_api.utils.constants import DEMO_ADMIN_EMAIL, DEMO_RESEARCHER_EMAIL


def _describe_target(idp: IdentityProvider) -> str:
    description = idp.describe_target()
    if idp.backend == "cognito":
        import boto3

        account = boto3.client("sts", region_name=get_settings().AWS_REGION).get_caller_identity()["Account"]
        description += f"\n   account: {account}"
    return description


def confirm_target(idp: IdentityProvider) -> bool:
    """Show exactly which directory is about to be written to and require explicit confirmation.

    Creating a user with a permanent password (and the ADMIN role granted at
    the next flip-api boot) is a dangerous thing to point at the wrong
    directory, and the coordinates come straight from the environment — so
    make the operator confirm the resolved target before any write.

    Args:
        idp: The provider the users will be written to.

    Returns:
        bool: True when the operator typed "yes"; False to abort.
    """
    print(
        "🎯 About to create/reset demo users with PERMANENT passwords in:\n"
        f"   {_describe_target(idp)}\n"
        "   The demo admin is granted the ADMIN role by flip-api boot seeding — make sure this is a dev directory."
    )
    try:
        answer = input("   Type 'yes' to confirm this target: ")
    except EOFError:
        print("❌ No interactive confirmation possible (stdin closed) — aborting.", file=sys.stderr)
        return False
    if answer.strip().lower() != "yes":
        print("❌ Aborted — nothing was created or changed.", file=sys.stderr)
        return False
    return True


def ensure_user(idp: IdentityProvider, email: str, password: str) -> None:
    """Create the user if missing (no invitation), then (re)set its permanent password.

    Args:
        idp: The identity provider to write to.
        email (str): The user's email, also used as the username.
        password (str): Permanent password; must satisfy the provider's policy.
    """
    try:
        idp.create_user(email, suppress_invite=True)
        print(f"✅ Created {idp.backend} user {email}")
    except UserAlreadyExistsError:
        print(f"ℹ️  {idp.backend} user {email} already exists — resetting its password")

    idp.set_password(email, password)
    print(f"✅ Permanent password set for {email}")


def main() -> int:
    wanted = [
        (DEMO_RESEARCHER_EMAIL, os.environ.get("DEMO_RESEARCHER_PASSWORD", "")),
        (DEMO_ADMIN_EMAIL, os.environ.get("DEMO_ADMIN_PASSWORD", "")),
    ]
    missing = [email for email, password in wanted if not password]
    if missing:
        print(
            "❌ Set DEMO_RESEARCHER_PASSWORD and DEMO_ADMIN_PASSWORD in the environment "
            f"(missing for: {', '.join(missing)}). Passwords are env-only — never commit them.",
            file=sys.stderr,
        )
        return 1

    idp = build_identity_provider()
    try:
        if not confirm_target(idp):
            return 1
        for email, password in wanted:
            ensure_user(idp, email, password)
    except IdentityProviderError as exc:
        print(f"❌ {idp.backend} call failed: {exc}", file=sys.stderr)
        if idp.backend == "cognito":
            print("   Check your AWS session (aws sso login --sso-session FLIP) and the pool id.", file=sys.stderr)
        else:
            print(
                "   Check that the keycloak service is up (make central-hub) and KEYCLOAK_* match it.",
                file=sys.stderr,
            )
        return 1

    # Through compose, naming the SERVICE: dev containers carry no container_name, so they are
    # named by the project (deploy-flip-api-1) and a literal `docker restart flip-api` matches
    # nothing. The project is FLIP_INSTANCE's, mirroring COMPOSE_PROJECT in deploy/instance.mk.
    instance = os.environ.get("FLIP_INSTANCE", "").strip()
    project = f"{instance}-deploy" if instance else "deploy"
    print(f"\n🔁 Restart flip-api (docker compose -p {project} restart flip-api) so boot seeding grants the roles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
