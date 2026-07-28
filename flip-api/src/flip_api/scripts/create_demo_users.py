# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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
"""Provision the demo Cognito users used by the demo-video recorder.

Creates (idempotently) the demo researcher and demo admin in the Cognito
user pool and sets their permanent passwords, so the scripted demo
(tests/demo_video.py) can sign them in through the real login form. Their
FLIP role grants are seeded at flip-api boot by db/seed/main_users.py —
restart flip-api after running this so the grants land.

Passwords come exclusively from the environment (DEMO_RESEARCHER_PASSWORD /
DEMO_ADMIN_PASSWORD) and are never stored anywhere else; they must satisfy
the user pool's password policy. Requires live AWS credentials with Cognito
admin permissions on the pool (aws sso login --sso-session FLIP).

Before touching Cognito the script resolves the target pool's name, region,
and AWS account and asks for an explicit interactive "yes" — a stale
AWS_COGNITO_USER_POOL_ID or a wrong SSO account must not silently plant a
known-password admin account in an unintended (e.g. production) pool.

Usage:
    make demo-users            # from the repo root
    make -C flip-api create_demo_users
"""

import os
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from flip_api.utils.constants import DEMO_ADMIN_EMAIL, DEMO_RESEARCHER_EMAIL


def confirm_target_pool(client, user_pool_id: str, region: str) -> bool:
    """Show exactly which pool/account is about to be written to and require explicit confirmation.

    AdminCreateUser + a permanent password (with the ADMIN role granted at next flip-api boot) is
    a dangerous thing to point at the wrong pool, and the pool id comes straight from the
    environment — so make the operator confirm the resolved target before any write.

    Args:
        client: boto3 cognito-idp client.
        user_pool_id (str): Pool id resolved from the environment.
        region (str): AWS region the client targets.

    Returns:
        bool: True when the operator typed "yes"; False to abort.
    """
    pool_name = client.describe_user_pool(UserPoolId=user_pool_id)["UserPool"]["Name"]
    account = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    print(
        "🎯 About to create/reset demo users with PERMANENT passwords in:\n"
        f"   pool:    {pool_name} ({user_pool_id})\n"
        f"   region:  {region}\n"
        f"   account: {account}\n"
        "   The demo admin is granted the ADMIN role by flip-api boot seeding — make sure this is a dev pool."
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


def ensure_user(client, user_pool_id: str, email: str, password: str) -> None:
    """Create the Cognito user if missing, then (re)set its permanent password.

    Args:
        client: boto3 cognito-idp client.
        user_pool_id (str): Target user pool.
        email (str): The user's email, also used as the username.
        password (str): Permanent password; must satisfy the pool policy.
    """
    try:
        client.admin_create_user(
            UserPoolId=user_pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            MessageAction="SUPPRESS",
        )
        print(f"✅ Created Cognito user {email}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "UsernameExistsException":
            raise
        print(f"ℹ️  Cognito user {email} already exists — resetting its password")

    client.admin_set_user_password(
        UserPoolId=user_pool_id,
        Username=email,
        Password=password,
        Permanent=True,
    )
    print(f"✅ Permanent password set for {email}")


def main() -> int:
    user_pool_id = os.environ.get("AWS_COGNITO_USER_POOL_ID", "")
    if not user_pool_id:
        print("❌ AWS_COGNITO_USER_POOL_ID is not set", file=sys.stderr)
        return 1

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

    region = os.environ.get("AWS_REGION", "eu-west-2")
    client = boto3.client("cognito-idp", region_name=region)
    try:
        if not confirm_target_pool(client, user_pool_id, region):
            return 1
        for email, password in wanted:
            ensure_user(client, user_pool_id, email, password)
    except (ClientError, BotoCoreError) as exc:
        print(f"❌ Cognito call failed: {exc}", file=sys.stderr)
        print("   Check your AWS session (aws sso login --sso-session FLIP) and the pool id.", file=sys.stderr)
        return 1

    print("\n🔁 Restart flip-api (docker restart flip-api) so boot seeding grants the demo roles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
