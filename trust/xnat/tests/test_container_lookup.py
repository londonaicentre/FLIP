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

"""Tests for how ``trust/xnat/Makefile`` finds the running xnat-web container (FLIP#1210).

``xnat-configure`` and ``xnat-shell`` need the container of *their* stack's ``xnat-web``
service to ``docker exec`` into. Docker's ``--filter name=`` is a regular-expression search,
not an exact match, so on a host that also runs a ``FLIP_INSTANCE``-prefixed stack
(``lzastag-xnat1`` beside ``xnat1``) the unprefixed lookup returns both containers — the
prefixed name contains the unprefixed one — and ``docker exec $CONTAINER bash -c …`` becomes
``docker exec <id1> <id2> bash -c …``, failing with ``exec: "<id2>": executable file not
found``. The stack has deployed, but is never configured.

These tests run the recipes' own shell (extracted with ``make -n``, as
``test_data_dir_ownership`` does) against a ``docker`` shim on ``PATH`` that answers ``docker
ps`` from a fixture of running containers, modelling the real filters — a regex search for
``name=`` and an exact match for the swarm labels — and records every ``docker exec``. The
decoy is listed *first*, as a ``docker ps`` ordering would not save a lookup that merely took
the first line. Neither Docker nor a swarm is needed.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

XNAT_DIR = Path(__file__).resolve().parents[1]
MAKEFILE = XNAT_DIR / "Makefile"

TIMEOUT_SECONDS = 60

STACK = "xnat1"
WANTED_ID = "d3f08e4beed6"
DECOY_ID = "cc132be1ad0b"

# id | name | com.docker.stack.namespace | com.docker.swarm.service.name — the labels a swarm
# task container really carries (checked against a live stack).
PREFIXED = f"lzastag-{STACK}"
TWO_STACKS = [
    (DECOY_ID, f"{PREFIXED}_xnat-web.1.9s6l73xdfh7mzb3er056h2lb1", PREFIXED, f"{PREFIXED}_xnat-web"),
    (WANTED_ID, f"{STACK}_xnat-web.1.19bnryjiwsumaovt1shq9rxa6", STACK, f"{STACK}_xnat-web"),
]
# Two tasks of the *same* service at once (a rolling update still tearing the old one down).
ROLLING_UPDATE = [
    ("aaaaaaaaaaaa", f"{STACK}_xnat-web.1.oldtaskoldtaskoldtask", STACK, f"{STACK}_xnat-web"),
    ("bbbbbbbbbbbb", f"{STACK}_xnat-web.1.newtasknewtasknewtask", STACK, f"{STACK}_xnat-web"),
]

DOCKER_SHIM = r"""#!/bin/bash
# `docker` stand-in: `ps` answers from $FAKE_DOCKER_PS (id|name|namespace|service per line) with
# Docker's own filter semantics — `name=` is a regex search, `label=k=v` an exact match — and
# `exec` appends its argv (tab-separated) to $FAKE_DOCKER_EXEC_LOG instead of running anything.
set -u
case "${1:-}" in
  ps)
    shift
    filters=()
    while [ $# -gt 0 ]; do
      case "$1" in
        --filter) filters+=("$2"); shift 2 ;;
        --filter=*) filters+=("${1#--filter=}"); shift ;;
        *) shift ;;
      esac
    done
    while IFS='|' read -r id name ns svc; do
      ok=1
      for f in "${filters[@]}"; do
        case "$f" in
          name=*) printf '%s\n' "$name" | grep -Eq -- "${f#name=}" || ok=0 ;;
          label=com.docker.stack.namespace=*) [ "${f#label=com.docker.stack.namespace=}" = "$ns" ] || ok=0 ;;
          label=com.docker.swarm.service.name=*) [ "${f#label=com.docker.swarm.service.name=}" = "$svc" ] || ok=0 ;;
          *) ok=0 ;;
        esac
      done
      [ "$ok" = 1 ] && printf '%s\n' "$id"
    done < "$FAKE_DOCKER_PS"
    ;;
  exec)
    shift
    printf '%s\t' "$@" >> "$FAKE_DOCKER_EXEC_LOG"
    printf '\n' >> "$FAKE_DOCKER_EXEC_LOG"
    ;;
  *) echo "docker shim: unexpected command: $*" >&2; exit 2 ;;
esac
"""


def _recipe(target: str, *variables: str) -> str:
    """The shell ``make`` would run for ``target``, exactly as the Makefile generates it."""
    result = subprocess.run(
        ["make", "-n", "--no-print-directory", target, "KIT=GSTT", *variables],
        cwd=XNAT_DIR,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, f"make -n {target} failed:\n{result.stderr}"
    return result.stdout


class _Host:
    """A fake docker host: the shim on PATH plus the fixture it answers from."""

    def __init__(self, root: Path, containers: list[tuple[str, str, str, str]]) -> None:
        bin_dir = root / "bin"
        bin_dir.mkdir()
        shim = bin_dir / "docker"
        shim.write_text(DOCKER_SHIM)
        shim.chmod(0o755)
        self.ps = root / "ps.txt"
        self.ps.write_text("".join("|".join(row) + "\n" for row in containers))
        self.exec_log = root / "exec.log"
        self.exec_log.touch()
        self.env = dict(
            os.environ,
            PATH=f"{bin_dir}:{os.environ['PATH']}",
            FAKE_DOCKER_PS=str(self.ps),
            FAKE_DOCKER_EXEC_LOG=str(self.exec_log),
        )

    def run(self, shell: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", shell], capture_output=True, text=True, timeout=TIMEOUT_SECONDS, env=self.env
        )

    def execs(self) -> list[list[str]]:
        """argv of every ``docker exec`` the recipe issued, without the leading ``exec``."""
        return [line.split("\t")[:-1] for line in self.exec_log.read_text().splitlines()]


class TestConfigureFindsItsOwnStack:
    def test_execs_into_the_unprefixed_stack_only(self, tmp_path: Path) -> None:
        host = _Host(tmp_path, TWO_STACKS)
        result = host.run(_recipe("xnat-configure", f"XNAT_PROJECT={STACK}"))

        assert result.returncode == 0, f"xnat-configure failed:\n{result.stdout}{result.stderr}"
        execs = host.execs()
        # One exec per configuration script, in the order the recipe chains them; setup-datatypes.sh
        # registers the slide-microscopy data type after the viewer and converter are configured.
        expected = ["configure-xnat.sh", "configure-dcm2niix.sh", "setup-datatypes.sh"]
        ran = [script for argv in execs for script in expected if f"bash {script}" in argv[-1]]
        assert ran == expected, f"expected {expected} in order, got {execs}"
        for argv in execs:
            # `docker exec <container> bash -c …` — one container, then the program.
            assert argv[0] == WANTED_ID, f"exec'd into {argv[0]!r}, not {STACK}_xnat-web ({WANTED_ID})"
            assert argv[1] == "bash", f"a second container id reached docker exec: {argv[:3]}"

    def test_refuses_an_ambiguous_match(self, tmp_path: Path) -> None:
        """Two containers of the wanted service is not a lookup to resolve — it is an error to name."""
        host = _Host(tmp_path, ROLLING_UPDATE)
        result = host.run(_recipe("xnat-configure", f"XNAT_PROJECT={STACK}"))

        assert result.returncode != 0, "xnat-configure went ahead with two candidate containers"
        assert host.execs() == [], f"docker exec ran despite the ambiguity: {host.execs()}"
        assert "aaaaaaaaaaaa" in result.stdout + result.stderr, "the error does not name the candidates"
        assert "bbbbbbbbbbbb" in result.stdout + result.stderr, "the error does not name the candidates"


class TestShellFindsItsOwnStack:
    def test_opens_the_unprefixed_stack_only(self, tmp_path: Path) -> None:
        host = _Host(tmp_path, TWO_STACKS)
        # xnat-shell derives XNAT_STACK from the kit's slot number; no kit file is present here.
        result = host.run(_recipe("xnat-shell", "FL_KIT_SLOT_NUMBER=1"))

        assert result.returncode == 0, f"xnat-shell failed:\n{result.stdout}{result.stderr}"
        execs = host.execs()
        assert len(execs) == 1, f"expected one docker exec, got {execs}"
        argv = [arg for arg in execs[0] if arg != "-it"]
        assert argv[0] == WANTED_ID, f"exec'd into {argv[0]!r}, not {STACK}_xnat-web ({WANTED_ID})"
        assert argv[1] == "bash", f"a second container id reached docker exec: {argv[:3]}"


class TestNoSubstringLookupsRemain:
    def test_makefile_has_no_name_filter(self) -> None:
        """Every container lookup must match exactly (the swarm labels), never by name substring."""
        stray = [
            line.strip()
            for line in MAKEFILE.read_text().splitlines()
            if not line.lstrip().startswith("#") and re.search(r"""--filter[= ]["']?name=""", line)
        ]
        assert not stray, "docker ps --filter name= is a substring match; use the stack/service labels:\n" + "\n".join(
            stray
        )


@pytest.mark.parametrize("target", ["xnat-configure", "xnat-shell"])
def test_recipes_still_extract(target: str) -> None:
    """Guard the guard: the recipes the classes above run must still contain a container lookup."""
    variables = [f"XNAT_PROJECT={STACK}"] if target == "xnat-configure" else ["FL_KIT_SLOT_NUMBER=1"]
    assert "docker ps" in _recipe(target, *variables)
