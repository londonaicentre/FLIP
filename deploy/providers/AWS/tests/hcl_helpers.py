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

"""Shared helpers for the credential-free tests that read Terraform source as text.

The suite deliberately parses ``.tf`` files with ``read_text()`` + string handling rather than a
``terraform`` binary or an HCL library, so it runs on fork PRs with nothing installed. This module
holds the one piece of that parsing worth sharing: pulling a brace-balanced block out of a file by
its header, which several tests need and used to each carry a private copy of.
"""


def hcl_block(source: str, header: str) -> str:
    """Extract a brace-balanced HCL block by its opening header.

    Brace-matched from the first ``{`` after the header, so a nested block cannot truncate the
    body the way a non-greedy regex to the first ``\\n}`` would.

    Args:
        source (str): Full contents of a Terraform file.
        header (str): The block header to find, e.g. ``resource "aws_iam_role_policy" "trust_ec2_s3"``.

    Returns:
        str: The block body, without the enclosing braces.

    Raises:
        ValueError: If ``header`` does not occur in ``source`` (from ``str.index``).
        AssertionError: If the braces after ``header`` never balance.
    """
    start = source.index(header)
    open_brace = source.index("{", start)
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace + 1 : index]
    raise AssertionError(f"unbalanced braces after {header!r}")
