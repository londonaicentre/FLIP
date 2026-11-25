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

"""Static guards: every hub EFS file system carries a restrictive file system policy.

EFS applies a default policy when none is declared, and that default grants full access —
including root — to any client that can reach a mount target. The guards here pin the
replacement (FLIP-SR-075, #1743):

* every ``aws_efs_file_system`` in the root module has an ``aws_efs_file_system_policy``
  bound to it;
* the policy denies NFS connections that are not encrypted in transit;
* the policy grants ``ClientMount``/``ClientWrite`` only to the hub's own FL task roles and
  only through an access point, and grants ``ClientRootAccess`` to nobody;
* every ECS volume that mounts the file system sets ``transit_encryption = "ENABLED"`` and
  ``authorization_config { iam = "ENABLED" }`` — the policy's Allow is keyed on the task
  identity, which only an IAM-authorized mount presents, so dropping either would deny the
  mount at the next task start.

Read straight from the ``.tf`` text (no terraform binary, no state, no credentials) like the
rest of this suite.
"""

import re
from pathlib import Path

from tf_source import strip_comments

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
EFS_TF = AWS_PROVIDER_DIR / "efs.tf"

POLICY_HEADER = 'resource "aws_efs_file_system_policy" "flip_fl"'
POLICY_RESOURCE_PATTERN = r'resource\s+"aws_efs_file_system_policy"\s+"[A-Za-z0-9_-]+"'

#: The task roles allowed to mount, as Terraform references. fl-api and fl-server mount the
#: runtime volumes; the kit-provision role runs the one-shot that seeds them.
ALLOWED_ROLE_REFS = (
    "aws_iam_role.ecs_fl_api_task.arn",
    "aws_iam_role.ecs_fl_server_task.arn",
    "aws_iam_role.ecs_fl_kit_provision_task.arn",
)


def _tf_sources() -> dict[Path, str]:
    """Return every root-module ``.tf`` file's text with comment lines stripped.

    Returns:
        dict[Path, str]: Path -> source text without whole-line comments.
    """
    sources = {path: strip_comments(path.read_text()) for path in sorted(AWS_PROVIDER_DIR.glob("*.tf"))}
    assert sources, f"found no .tf files under {AWS_PROVIDER_DIR} — the inventory is broken, not the policy"
    return sources


def _blocks(source: str, header_pattern: str) -> list[str]:
    """Return the brace-balanced body of every block whose header matches ``header_pattern``.

    ``tf_source.hcl_block`` returns only the first occurrence; several files declare more
    than one ``efs_volume_configuration``.

    Args:
        source (str): Terraform source text.
        header_pattern (str): Regex matching the block header, e.g. ``efs_volume_configuration``.

    Returns:
        list[str]: One body per occurrence, without the enclosing braces.
    """
    bodies = []
    for match in re.finditer(header_pattern + r"\s*\{", source):
        open_brace = source.index("{", match.start())
        depth = 0
        for index in range(open_brace, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(source[open_brace + 1 : index])
                    break
        else:
            raise AssertionError(f"unbalanced braces after {header_pattern!r}")
    return bodies


def _policy_body() -> str:
    """Return the body of the EFS file system policy resource."""
    bodies = _blocks(strip_comments(EFS_TF.read_text()), re.escape(POLICY_HEADER))
    assert bodies, f"{POLICY_HEADER} is missing from efs.tf — the EFS default policy grants every client full access"
    return bodies[0]


def _statements(body: str) -> list[str]:
    """Split a policy body into one chunk per statement, keyed off each ``Sid``."""
    return [chunk for chunk in re.split(r"(?=Sid\s*=)", body) if re.match(r"Sid\s*=", chunk)]


def test_every_efs_file_system_has_a_file_system_policy() -> None:
    """Each aws_efs_file_system in the root module is bound to a policy resource."""
    file_systems = {
        name
        for source in _tf_sources().values()
        for name in re.findall(r'^resource\s+"aws_efs_file_system"\s+"([A-Za-z0-9_-]+)"', source, re.MULTILINE)
    }
    assert file_systems, "no aws_efs_file_system found — the inventory is broken"

    policied = set()
    for source in _tf_sources().values():
        for body in _blocks(source, POLICY_RESOURCE_PATTERN):
            policied.update(re.findall(r"aws_efs_file_system\.([A-Za-z0-9_-]+)", body))

    assert file_systems <= policied, (
        f"EFS file systems without an aws_efs_file_system_policy: {sorted(file_systems - policied)}. "
        "Without one, EFS applies its default policy, which grants full access (including root) to "
        "any client that can reach a mount target (#1743)"
    )


def test_policy_denies_connections_that_are_not_encrypted_in_transit() -> None:
    """A Deny statement covers the client actions when aws:SecureTransport is false."""
    deny = [chunk for chunk in _statements(_policy_body()) if re.search(r'Effect\s*=\s*"Deny"', chunk)]
    assert deny, "the EFS file system policy has no Deny statement"
    tls_denies = [
        chunk
        for chunk in deny
        if re.search(r'"aws:SecureTransport"\s*=\s*"false"', chunk)
        and "elasticfilesystem:ClientMount" in chunk
        and "elasticfilesystem:ClientWrite" in chunk
    ]
    assert tls_denies, (
        "the EFS file system policy must deny ClientMount/ClientWrite when "
        '"aws:SecureTransport" = "false" so an unencrypted NFS mount cannot read the FL workspaces'
    )


def test_policy_allows_only_the_hub_fl_task_roles_through_access_points() -> None:
    """The Allow is scoped to the three FL task roles and to the declared access points."""
    allows = [chunk for chunk in _statements(_policy_body()) if re.search(r'Effect\s*=\s*"Allow"', chunk)]
    assert allows, "the EFS file system policy has no Allow statement — every mount would be denied"

    for chunk in allows:
        principal_arns = set(re.findall(r"aws_iam_role\.[A-Za-z0-9_]+\.arn", chunk))
        assert '"aws:PrincipalArn"' in chunk and principal_arns == set(ALLOWED_ROLE_REFS), (
            "the Allow statement must be scoped with aws:PrincipalArn to exactly "
            f"{sorted(ALLOWED_ROLE_REFS)}, found {sorted(principal_arns)}"
        )
        assert '"elasticfilesystem:AccessPointArn"' in chunk and "aws_efs_access_point.flip_fl" in chunk, (
            "the Allow statement must require elasticfilesystem:AccessPointArn to be one of the "
            "declared access points, so a mount of the file system root is refused"
        )


def test_policy_grants_root_access_to_nobody() -> None:
    """ClientRootAccess never appears in an Allow: the access points pin every client to 1001."""
    for chunk in _statements(_policy_body()):
        if re.search(r'Effect\s*=\s*"Allow"', chunk):
            assert "elasticfilesystem:ClientRootAccess" not in chunk, (
                "no hub task mounts EFS as root — the access points pin uid/gid 1001, so "
                "ClientRootAccess must not be granted"
            )


def test_every_efs_mount_uses_tls_and_iam_authorization() -> None:
    """Every ECS EFS volume sets transit encryption and IAM authorization.

    The policy's Allow is keyed on the mounting task's identity, which ECS only presents when
    ``iam = "ENABLED"``; an anonymous mount would match no statement and be denied.
    """
    checked = 0
    for path, source in _tf_sources().items():
        for body in _blocks(source, "efs_volume_configuration"):
            checked += 1
            assert re.search(r'transit_encryption\s*=\s*"ENABLED"', body), (
                f"an efs_volume_configuration in {path.name} does not set transit_encryption = \"ENABLED\"; "
                "the file system policy denies connections that are not encrypted in transit"
            )
            authorization = _blocks(body, "authorization_config")
            assert authorization and re.search(r'iam\s*=\s*"ENABLED"', authorization[0]), (
                f"an efs_volume_configuration in {path.name} does not set "
                'authorization_config { iam = "ENABLED" }; without it the mount is anonymous and the '
                "file system policy's principal-scoped Allow cannot match it"
            )
    assert checked >= 4, f"expected every FL task's EFS volume to be checked, found only {checked}"
