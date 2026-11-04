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

"""Static guard: the FLIP database must state ``storage_encrypted = true`` (FLIP#1576).

The instance is built through ``terraform-aws-modules/rds/aws``, whose ``storage_encrypted``
defaults to true today. Relying on that default leaves nothing in the tree to fail if a module
bump or a refactor changes it, and checkov cannot catch it either: ``checkov_lint.sh`` runs with
``--skip-download``, so the module's ``aws_db_instance`` is never scanned and CKV_AWS_16 has no
resource to evaluate. Encryption can only be set when an instance is created, so a silent drop
would surface on the next replacement as an unencrypted database.

``kms_key_id`` must not be added as a plain argument change: it forces replacement of the
instance, so the move to ``flip_app_key`` is a snapshot, encrypted copy and restore migration.
"""

import re
from pathlib import Path

from tf_source import hcl_block, strip_comments

MAIN_TF = Path(__file__).resolve().parent.parent / "main.tf"
HEADER = 'module "flip_db"'


def _flip_db_body() -> str:
    return strip_comments(hcl_block(MAIN_TF.read_text(), HEADER))


def test_flip_db_states_storage_encrypted_true() -> None:
    """The module call sets storage_encrypted to the literal true."""
    assert re.search(r"^\s*storage_encrypted\s*=\s*true\s*$", _flip_db_body(), re.MULTILINE), (
        f"{HEADER} in main.tf must set storage_encrypted = true explicitly (FLIP#1576)"
    )


def test_flip_db_does_not_change_kms_key_in_place() -> None:
    """kms_key_id forces replacement, so it may only arrive with a migration, not as an edit."""
    assert not re.search(r"^\s*kms_key_id\s*=", _flip_db_body(), re.MULTILINE), (
        "setting kms_key_id on flip_db replaces the live instance; migrate via snapshot, "
        "encrypted copy and restore first, then update this guard (FLIP#1576)"
    )
