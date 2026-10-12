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

"""Every value in a make-included env example reaches make exactly as written (FLIP#1402).

The root Makefile ``include``s ``.env.development`` (copied from ``.env.development.example``) and
``trust/Makefile`` ``-include``s the trust kit (copied from ``trust/.env*.example``), so these files are
parsed as Makefile syntax, not dotenv. Make keeps the whitespace before an inline ``#`` as part of the
value, and treats any ``#`` in a value as the start of a comment. An inline comment after
``AWS_REGION=eu-west-2`` therefore handed flip-api ``"eu-west-2   "``, botocore refused it, and every
model-file presign on a fresh dev hub returned 500. CI's own smoke never saw it, because
``local_auth_smoke.yml`` appends a clean ``AWS_REGION`` line to its copy.

Two checks per file:

- **through make:** a throwaway makefile ``include``s the example and prints ``$(value KEY)`` for
  every key, and no value may end in whitespace. ``$(value)`` is the raw, unexpanded text, so
  ``$(shell …)`` and ``${OTHER}`` values are compared as written. GNU make 3.81 (macOS) and 4.x
  behave the same here.
- **text:** no assignment line carries a ``#`` after its ``=``. Make would cut the value there even
  without the leading whitespace (``KEY=a#b`` reads as ``a``).

Standard library only and run as a plain script (``python <file>``), like every test in this
directory; ``test_trust_kit_scripts.yml`` runs it.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The env examples a Makefile includes once copied into place: the hub env file, and the trust kits.
ENV_EXAMPLES = (
    ".env.development.example",
    "trust/.env.example",
    "trust/.env.GSTT.development.example",
    "trust/.env.KCH.development.example",
)

# A make variable assignment: optional `export`, a name, an optional `:`/`?`/`+`/`!` modifier, `=`.
ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[:?+!]?=(.*)$")

PASS = 0
FAIL = 0


def _assert(condition: bool, what: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {what}")
    else:
        FAIL += 1
        print(f"  ❌ {what}" + (f" — {detail}" if detail else ""))


def _assignments(path: Path) -> list[tuple[int, str, str]]:
    """``(line number, name, text after '=')`` for every assignment line, comments skipped."""
    found = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        match = ASSIGNMENT.match(line)
        if match:
            found.append((n, match.group(1), match.group(2)))
    return found


def _values_through_make(path: Path, names: list[str]) -> dict[str, str]:
    """The raw value make holds for each name after ``include``-ing ``path``."""
    lines = [f"include {path}"]
    lines += [f"$(info {name}=[$(value {name})])" for name in names]
    lines += ["all: ;"]
    env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL")}
    with tempfile.TemporaryDirectory() as tmp:
        makefile = Path(tmp) / "print-values.mk"
        makefile.write_text("\n".join(lines) + "\n")
        run = subprocess.run(
            ["make", "-s", "-f", str(makefile), "all"],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    if run.returncode != 0:
        raise RuntimeError(f"make could not read {path}: {run.stderr.strip()[-400:]}")
    # `$(info)` writes one line per name; a value with a newline cannot occur in an include.
    return dict(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)=\[(.*)\]$", run.stdout, re.M))


def test_example(rel: str) -> None:
    print(f"test_example({rel})")
    path = REPO_ROOT / rel
    assignments = _assignments(path)
    _assert(bool(assignments), f"{rel} has assignments (parser sanity)", detail="none found — file moved?")
    if not assignments:
        return

    for n, name, text in assignments:
        if "#" in text:
            _assert(False, f"{rel}:{n} {name} carries no `#`", detail="move the comment to its own line above")

    names = sorted({name for _, name, _ in assignments})
    values = _values_through_make(path, names)
    _assert(
        set(values) == set(names), f"make printed a value for every key in {rel}", detail=str(set(names) - set(values))
    )
    padded = {name: value for name, value in values.items() if value != value.rstrip()}
    _assert(
        not padded,
        f"no value in {rel} ends in whitespace as make reads it",
        detail=", ".join(f"{name}={value!r}" for name, value in sorted(padded.items())),
    )


def test_guard_catches_an_inline_comment() -> None:
    """The make-side check sees the padding this test exists for (it would pass vacuously otherwise)."""
    print("test_guard_catches_an_inline_comment")
    with tempfile.TemporaryDirectory() as tmp:
        sample = Path(tmp) / "sample.env"
        sample.write_text("AWS_REGION=eu-west-2   # a comment\nCLEAN=value\n")
        values = _values_through_make(sample, ["AWS_REGION", "CLEAN"])
    _assert(values.get("AWS_REGION") == "eu-west-2   ", "make keeps the spaces before an inline #", detail=repr(values))
    _assert(values.get("CLEAN") == "value", "a clean line reads back exactly", detail=repr(values))


def main() -> None:
    test_guard_catches_an_inline_comment()
    for rel in ENV_EXAMPLES:
        test_example(rel)
    print("—")
    print(f"PASS={PASS}  FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
