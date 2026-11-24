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
"""The Cognito user pool admits admin-created users only.

Every FLIP user is invited by an admin (``register_user`` → ``admin_create_user``) and the
hub's authorisation model assumes that: ``verify_token`` trusts any token the pool signed.
Cognito's default is the other way round (public ``SignUp`` enabled), and the Terraform
argument that turns it off is easy to drop from the block without a plan diff that reads
as a security change. Static guard over the module source, like the callback-URL test.
"""

from pathlib import Path

from tf_source import hcl_block, strip_comments

COGNITO_MAIN_TF = Path(__file__).resolve().parent.parent / "modules" / "cognito" / "main.tf"


def test_user_pool_allows_admin_created_users_only() -> None:
    pool = hcl_block(strip_comments(COGNITO_MAIN_TF.read_text()), 'resource "aws_cognito_user_pool" "flip_user_pool"')
    admin_create = hcl_block(pool, "admin_create_user_config")
    assignments = {
        line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
        for line in admin_create.splitlines()
        if "=" in line and "{" not in line
    }
    assert assignments.get("allow_admin_create_user_only") == "true", (
        "admin_create_user_config must set allow_admin_create_user_only = true; "
        "without it the pool accepts public SignUp and verify_token admits the result"
    )
