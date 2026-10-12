#  Copyright 2026 London AI Centre
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""Helpers for the static guards that assert over Terraform source text.

Shared by every test that pins an invariant in a ``.tf`` file (there is no HCL parser in the
test environment, and a regex over a brace-balanced block body is enough for these guards).

Known limitation: the walker counts every ``{``/``}``, including the braces of ``${...}``
interpolations — a header whose block contains an unbalanced brace inside a string is not
supported (none does today).
"""


def strip_comments(source: str) -> str:
    """Drop ``#`` and ``//`` comment lines so no assertion can be satisfied by prose.

    Args:
        source (str): Terraform source text.

    Returns:
        str: The text with whole-line comments removed.
    """
    return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(("#", "//")))


def hcl_block(source: str, header: str) -> str:
    """Extract the brace-balanced body of the first block opened by ``header``.

    Args:
        source (str): Full contents of a Terraform file.
        header (str): The block header to find, e.g. ``resource "aws_lb_target_group" "ecs_flip_api"``.

    Returns:
        str: The block body, without the enclosing braces.
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
