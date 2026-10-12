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
"""Run a Makefile for real and read derived values back.

The probe is a throwaway wrapper Makefile fed on stdin: it sets its own default goal
BEFORE including the real one (otherwise the include's first rule would run), then
prints each requested item NUL-separated so a value may contain any character.
"""

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

_PROBE = """\
.DEFAULT_GOAL := __probe
include Makefile
.PHONY: __probe
__probe:
\t@printf '%s\\0' {items}
"""


def probe_make(
    cwd: Path,
    items: Sequence[str],
    variables: Mapping[str, str] = (),
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Evaluate ``items`` in the recipe context of the Makefile in ``cwd``.

    Args:
        cwd (Path): Directory holding the ``Makefile`` to include.
        items (Sequence[str]): Recipe-level expressions to print, e.g. ``"$(ENV)"`` for a make
            variable or ``"$${NAME-UNSET}"`` for what a child process would see in its environment.
        variables (Mapping[str, str]): Command-line ``NAME=value`` overrides (``PROD``, ``KIT``, …).
        env (Mapping[str, str] | None): Process environment; defaults to the caller's.

    Returns:
        list[str]: One string per item, in order.
    """
    probe = _PROBE.format(items=" ".join(f'"{item}"' for item in items))
    result = subprocess.run(
        ["make", "-s", "-f", "-", "__probe", *(f"{k}={v}" for k, v in dict(variables).items())],
        input=probe,
        text=True,
        capture_output=True,
        cwd=cwd,
        env=dict(os.environ if env is None else env),
        check=True,
    )
    values = result.stdout.split("\0")
    assert values[-1] == "", "probe output did not end with the NUL terminator"
    return values[:-1]
