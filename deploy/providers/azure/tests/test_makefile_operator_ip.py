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
"""The operator's IP is looked up once per make run, and only an IPv4 answer is kept (FLIP#1390).

The api.ipify.org answer reaches a shell inside double quotes in several recipes, so anything else
in it would run on the operator's machine. These tests run the real targets with az, terraform,
git and curl stubbed on PATH.
"""

from __future__ import annotations

import pytest
from conftest import run_make

HOSTILE = ['203.0.113.7"; touch PWNED; "', "203.0.113.7`touch PWNED`", "$(touch PWNED)"]
LOOKUP = "curl -fsS --max-time 10 https://api.ipify.org"


def _curls(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("curl ")]


def _terraform(calls: list[str], verb: str) -> str:
    (call,) = [c for c in calls if c.startswith(f"terraform -chdir=vm {verb}")]
    return call


def _plan(tmp_path, answers: list[str]) -> list[str]:
    key = tmp_path / "id.pub"
    key.write_text("ssh-ed25519 AAAA test\n")
    result, calls = run_make(tmp_path, "plan", ipify=answers, SSH_PUBLIC_KEY_FILE=str(key), FLIP_REF="0" * 40)
    assert result.returncode == 0, result.stdout + result.stderr
    return calls


def test_one_run_looks_the_ip_up_once_and_every_use_sees_that_answer(tmp_path):
    probe = tmp_path / "probe.mk"
    probe.write_text("probe:\n\t@echo '$(OPERATOR_IP)|$(OPERATOR_IP)'\n")

    result, calls = run_make(tmp_path, "probe", ipify=["203.0.113.7", "198.51.100.1"], makefiles=(probe,))

    assert result.stdout.strip() == "203.0.113.7|203.0.113.7", result.stderr
    assert _curls(calls) == [LOOKUP], "one lookup, over HTTPS, bounded in time"


def test_plan_passes_the_looked_up_ip_to_terraform(tmp_path):
    calls = _plan(tmp_path, ["203.0.113.7"])

    assert _terraform(calls, "plan").endswith(" -var operator_ip=203.0.113.7")


@pytest.mark.parametrize("answer", [*HOSTILE, "<html>busy</html>", "", "999.1.1.1", "203.0.113.256", "1.2.3"])
def test_an_answer_that_is_not_one_ipv4_address_never_reaches_the_shell(tmp_path, answer):
    calls = _plan(tmp_path, [answer.replace("PWNED", str(tmp_path / "PWNED"))])

    assert not (tmp_path / "PWNED").exists(), "the answer was run as a command"
    assert _terraform(calls, "plan").endswith(" -var operator_ip="), "nothing but an IPv4 address is kept"


@pytest.mark.parametrize(("answer", "shown"), [("203.0.113.7", "203.0.113.7"), *[(a, "") for a in HOSTILE]])
def test_a_refused_kit_upload_names_the_ip_and_never_runs_the_answer(tmp_path, answer, shown):
    tarball = tmp_path / "kit.tar.gz"
    tarball.write_bytes(b"kit")

    result, _ = run_make(
        tmp_path,
        "kit-upload",
        ipify=[answer.replace("PWNED", str(tmp_path / "PWNED"))],
        az_fail="storage blob",
        TARBALL=str(tarball),
    )

    assert result.returncode != 0
    assert f"your IP ({shown}) is not the one" in result.stdout + result.stderr
    assert not (tmp_path / "PWNED").exists(), "the answer was run as a command"


def test_destroy_needs_no_ip_lookup(tmp_path):
    key = tmp_path / "id.pub"
    key.write_text("ssh-ed25519 AAAA test\n")

    result, calls = run_make(tmp_path, "destroy", ipify=[""], SSH_PUBLIC_KEY_FILE=str(key))

    assert result.returncode == 0, result.stdout + result.stderr
    assert not _curls(calls), "a failed lookup must not stand between the operator and a teardown"
    assert _terraform(calls, "destroy").endswith(" -var operator_ip=192.0.2.1")
