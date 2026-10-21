#!/usr/bin/env python3
#
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

"""A minimal YAML emitter for SPCS service specifications.

The renderer is deliberately stdlib-only: it runs on an operator workstation and on a
deploy host from a bare ``python3``, the same contract ``trust/deploy/helm/sync_k8s_kit.py``
keeps. PyYAML is not guaranteed there, and a service specification is a small, closed
shape (mappings, lists, strings, integers, booleans) that does not need a general emitter.

Everything a specification can legally contain round-trips through ``yaml.safe_load``;
the spec test suite parses every rendered file with PyYAML precisely so that this
claim is checked rather than asserted.
"""

from __future__ import annotations

import re
from typing import Any

#: Scalars that are safe to emit unquoted. Anything else is double-quoted, which is
#: always valid YAML, rather than maintaining a list of YAML's plain-scalar traps
#: (``yes``/``no``/``on``/``off``, leading ``@``/``*``/``&``, ``:`` followed by a space).
_PLAIN_SAFE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")

#: Strings a YAML reader would hand back as a number. They must stay strings: a spec's
#: `permission: "0600"` is rejected outright when it arrives as the integer 600, and an
#: env value such as COHORT_QUERY_THRESHOLD reaching a container as `15` rather than
#: `"15"` is the same class of defect one layer down.
_NUMBER_LIKE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$|^0[xXoObB][0-9a-fA-F_]+$")

#: Words a plain scalar must never be: YAML 1.1 readers turn these into booleans or null,
#: so an AE title of ``NO`` or a tag of ``on`` would stop being a string.
_RESERVED = frozenset(
    {"y", "n", "yes", "no", "true", "false", "on", "off", "null", "none", "~"},
)


def quote(value: str) -> str:
    """Render a string as a YAML scalar, quoting whenever plain form would be ambiguous."""
    if value and _PLAIN_SAFE.match(value) and value.lower() not in _RESERVED and not _NUMBER_LIKE.match(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int | float):
        return str(value)
    return quote(str(value))


def dump(data: Any, indent: int = 0) -> str:
    """Emit ``data`` as YAML text (always ending in a newline).

    Args:
        data: Mappings, lists and scalars only. Empty mappings and lists are omitted by
            the callers rather than emitted as ``{}``/``[]``, so a spec never carries an
            empty block Snowflake would reject.
        indent: Current indentation level, in spaces. Callers pass 0.

    Returns:
        str: The YAML document.
    """
    pad = " " * indent
    if isinstance(data, dict):
        lines = []
        for key, value in data.items():
            if isinstance(value, dict | list) and value:
                lines.append(f"{pad}{key}:")
                lines.append(dump(value, indent + 2).rstrip("\n"))
            elif isinstance(value, dict | list):
                continue  # an empty block is omitted, never emitted as {} or []
            else:
                lines.append(f"{pad}{key}: {_scalar(value)}")
        return "\n".join(lines) + "\n"
    if isinstance(data, list):
        lines = []
        for item in data:
            if isinstance(item, dict):
                rendered = dump(item, indent + 2).rstrip("\n").splitlines()
                first = rendered[0].lstrip()
                lines.append(f"{pad}- {first}")
                lines.extend(rendered[1:])
            else:
                lines.append(f"{pad}- {_scalar(item)}")
        return "\n".join(lines) + "\n"
    return f"{pad}{_scalar(data)}\n"
