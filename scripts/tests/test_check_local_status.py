#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
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
"""Unit tests for scripts/check_local_status.py kit discovery and instance naming.

discover_trust_kits() globs trust/.env.<CODE>.<env> kit files and reads each
trust's host-facing ports + assigned FL slot, so the status checker reflects
the CODE-named kit architecture instead of fixed Trust_1 / Trust_2 names.

compose_project() and instance_prefix() split the two things FLIP_INSTANCE
(FLIP#957) renames: containers are found via the compose project label, while
the hub-shared networks really do carry a name prefix. Getting either wrong
reports a healthy stack as wholly missing.

parse_compose_containers() turns the `docker ps` rows into that service-keyed
map — the part that would otherwise need a live daemon to exercise.

Usage:
    uv run --no-config scripts/tests/test_check_local_status.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from check_local_status import (  # noqa: E402
    TrustKit,
    compose_project,
    discover_trust_kits,
    instance_prefix,
    parse_compose_containers,
)

PASS = 0
FAIL = 0


def _assert(condition: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}")
        PASS += 1
    else:
        print(f"  ❌ {label}")
        if detail:
            print(f"    {detail}")
        FAIL += 1


def _write_kit(trust_dir: Path, code: str, env: str, **kv: object) -> None:
    body = "\n".join(f"{k}={v}" for k, v in kv.items())
    (trust_dir / f".env.{code}.{env}").write_text(body + "\n")


def _trust_dir() -> Path:
    trust = Path(tempfile.mkdtemp()) / "trust"
    trust.mkdir()
    return trust


def test_discovers_code_kits() -> None:
    trust = _trust_dir()
    _write_kit(
        trust,
        "KCH",
        "development",
        TRUST_NAME="KCH",
        FL_KIT_SLOT_NUMBER=1,
        XNAT_PORT=8106,
        PACS_UI_PORT=8044,
        TRUST_API_PORT=8020,
        IMAGING_API_PORT=8001,
        DATA_ACCESS_API_PORT=8010,
    )
    _write_kit(
        trust, "GSTT", "development", TRUST_NAME="GSTT", FL_KIT_SLOT_NUMBER=2, XNAT_PORT=8104, PACS_UI_PORT=8042
    )
    kits = discover_trust_kits(trust, "development")
    _assert(len(kits) == 2, "discovers both CODE kits", f"got {len(kits)}")
    by_code = {k.code: k for k in kits}
    _assert("KCH" in by_code and "GSTT" in by_code, "keyed by CODE", f"got {sorted(by_code)}")
    kch = by_code.get("KCH", TrustKit("", "", None, None, None, None, None, None))
    _assert(kch.slot_number == 1, "reads FL_KIT_SLOT_NUMBER", f"got {kch.slot_number!r}")
    _assert(kch.xnat_web_port == "8106" and kch.pacs_ui_port == "8044", "reads XNAT/PACS ports")
    _assert(kch.trust_api_port == "8020", "reads TRUST_API_PORT", f"got {kch.trust_api_port!r}")


def test_web_port_prefers_xnat_web_port() -> None:
    """The web UI moved off XNAT_PORT in FLIP#993; probing the DICOM port would report it down."""
    trust = _trust_dir()
    _write_kit(trust, "GSTT", "development", TRUST_NAME="GSTT", XNAT_PORT=8104, XNAT_WEB_PORT=8080)
    kits = discover_trust_kits(trust, "development")
    _assert(kits[0].xnat_web_port == "8080", "XNAT_WEB_PORT wins over XNAT_PORT", f"got {kits[0].xnat_web_port!r}")


def test_web_port_falls_back_to_xnat_port() -> None:
    """A kit predating the split sets only XNAT_PORT, and still publishes the web UI there."""
    trust = _trust_dir()
    _write_kit(trust, "GSTT", "development", TRUST_NAME="GSTT", XNAT_PORT=8104)
    kits = discover_trust_kits(trust, "development")
    _assert(kits[0].xnat_web_port == "8104", "falls back to XNAT_PORT", f"got {kits[0].xnat_web_port!r}")


def test_ignores_examples_and_other_envs() -> None:
    trust = _trust_dir()
    _write_kit(trust, "KCH", "development", TRUST_NAME="KCH", FL_KIT_SLOT_NUMBER=1)
    (trust / ".env.KCH.development.example").write_text("TRUST_NAME=SHOULD_IGNORE\n")
    _write_kit(trust, "PROD1", "production", TRUST_NAME="PROD1", FL_KIT_SLOT_NUMBER=1)
    kits = discover_trust_kits(trust, "development")
    _assert(
        len(kits) == 1 and kits[0].code == "KCH",
        "ignores .example + other-env kits",
        f"got {[k.code for k in kits]}",
    )


def test_missing_slot_and_name_fallback() -> None:
    trust = _trust_dir()
    _write_kit(trust, "KCL", "development", XNAT_PORT=8108)  # no TRUST_NAME, no slot
    kits = discover_trust_kits(trust, "development")
    _assert(len(kits) == 1, "discovers kit without name/slot", f"got {len(kits)}")
    _assert(kits[0].slot_number is None, "slot_number None when absent", f"got {kits[0].slot_number!r}")
    _assert(kits[0].name == "KCL", "name falls back to CODE", f"got {kits[0].name!r}")


def test_missing_dir_returns_empty() -> None:
    kits = discover_trust_kits(Path(tempfile.mkdtemp()) / "nope", "development")
    _assert(kits == [], "missing trust dir -> empty list", f"got {kits!r}")


def test_instance_prefix() -> None:
    """The network prefix must match `${FLIP_INSTANCE:+$FLIP_INSTANCE-}`, environment winning.

    Getting the default-stack case wrong is the expensive one: a stray prefix would report every
    hub network missing on the single-stack setup that nearly every run inspects.
    """
    original = os.environ.pop("FLIP_INSTANCE", None)
    try:
        _assert(instance_prefix({}) == "", "unset -> no prefix", f"got {instance_prefix({})!r}")
        _assert(
            instance_prefix({"FLIP_INSTANCE": ""}) == "",
            "empty env-file value -> no prefix",
            f"got {instance_prefix({'FLIP_INSTANCE': ''})!r}",
        )
        _assert(
            instance_prefix({"FLIP_INSTANCE": "  "}) == "",
            "whitespace-only value -> no prefix",
            f"got {instance_prefix({'FLIP_INSTANCE': '  '})!r}",
        )
        _assert(
            instance_prefix({"FLIP_INSTANCE": "b"}) == "b-",
            "env-file value -> '<value>-'",
            f"got {instance_prefix({'FLIP_INSTANCE': 'b'})!r}",
        )
        os.environ["FLIP_INSTANCE"] = "shell"
        _assert(
            instance_prefix({"FLIP_INSTANCE": "envfile"}) == "shell-",
            "process environment beats the env file",
            f"got {instance_prefix({'FLIP_INSTANCE': 'envfile'})!r}",
        )
    finally:
        os.environ.pop("FLIP_INSTANCE", None)
        if original is not None:
            os.environ["FLIP_INSTANCE"] = original


def test_compose_project() -> None:
    """Must mirror COMPOSE_PROJECT in deploy/instance.mk, bare `deploy` default included.

    The default must stay exactly `deploy` — that is the value compose derives implicitly from the
    directory of the first `-f` file, so anything else stops matching the containers `make up`
    created, on the single-stack setup that nearly every run inspects.
    """
    original = os.environ.pop("FLIP_INSTANCE", None)
    try:
        _assert(compose_project({}) == "deploy", "unset -> 'deploy'", f"got {compose_project({})!r}")
        _assert(
            compose_project({"FLIP_INSTANCE": "  "}) == "deploy",
            "whitespace-only value -> 'deploy'",
            f"got {compose_project({'FLIP_INSTANCE': '  '})!r}",
        )
        _assert(
            compose_project({"FLIP_INSTANCE": "b"}) == "b-deploy",
            "env-file value -> '<value>-deploy'",
            f"got {compose_project({'FLIP_INSTANCE': 'b'})!r}",
        )
        os.environ["FLIP_INSTANCE"] = "shell"
        _assert(
            compose_project({"FLIP_INSTANCE": "envfile"}) == "shell-deploy",
            "process environment beats the env file",
            f"got {compose_project({'FLIP_INSTANCE': 'envfile'})!r}",
        )
    finally:
        os.environ.pop("FLIP_INSTANCE", None)
        if original is not None:
            os.environ["FLIP_INSTANCE"] = original


def test_parse_compose_containers() -> None:
    """Rows are keyed by compose service, not by the project-assembled container name.

    The service name is what the rest of the script asks for, and it is identical in both stacks —
    the container name it maps to is not, which is the whole point of resolving instead of
    spelling one out.
    """
    rows = "\n".join((
        "flip-api\tb-deploy-flip-api-1\tUp 3 hours (healthy)",
        "fl-api-net-1\tb-deploy-fl-api-net-1-1\tUp 3 hours",
        "fl-server-net-2\tb-deploy-fl-server-net-2-1\tExited (1) 2 minutes ago",
    ))
    parsed = parse_compose_containers(rows)
    _assert(sorted(parsed) == ["fl-api-net-1", "fl-server-net-2", "flip-api"], "keyed by service name")
    _assert(
        parsed["fl-api-net-1"].name == "b-deploy-fl-api-net-1-1",
        "maps the service to the project-assembled container name",
        f"got {parsed['fl-api-net-1'].name!r}",
    )
    _assert(parsed["flip-api"].running, "'Up ...' -> running")
    _assert(not parsed["fl-server-net-2"].running, "'Exited ...' -> not running")

    # A container started outside compose carries no service label; keying an empty string would
    # shadow a real service and report it healthy.
    stray = parse_compose_containers("\tsome-stray-container\tUp 1 hour")
    _assert(stray == {}, "row with no service label is dropped", f"got {stray!r}")
    _assert(parse_compose_containers("") == {}, "empty output -> empty map")


def main() -> None:
    # Discovered rather than listed: the hand-maintained tuple this replaced meant a new test
    # function ran nowhere and the suite still reported green.
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    if not tests:
        print("no tests discovered")
        sys.exit(1)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
