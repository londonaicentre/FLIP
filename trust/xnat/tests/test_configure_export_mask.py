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
"""configure-export-mask.sh run for real against a stub ``curl`` serving XNAT 1.10's responses.

The subscription bodies are XNAT 1.10.0's actual shape (captured from a live trust): the event class
is reported under ``event-filter.event-type`` and there is no ``event-selector`` key at all, so a
validation reading ``event-selector`` fails every bring-up of a MONAI Label trust.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

CONFIG_DIR = Path(__file__).resolve().parents[1] / "xnat" / "config"
SCRIPT = CONFIG_DIR / "configure-export-mask.sh"
NAME = "Convert exported OHIF masks to NIfTI"

STUB_CURL = r"""#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
url = next(a for a in args if a.startswith("http"))
method = args[args.index("-X") + 1] if "-X" in args else "GET"
if method == "GET" and "/xapi/commands?name=" in url:
    body = "[]"
elif method == "POST" and url.endswith("/xapi/commands"):
    body = "3"
elif url.endswith("/enabled"):
    body = "true" if method == "GET" else ""
elif method == "GET" and url.endswith("/xapi/events/subscriptions"):
    body = open(os.environ["STUB_SUBSCRIPTIONS"]).read()
else:
    body = ""
sys.stdout.write(body + "\n200")
"""


def _subscription(event_type: str = "org.nrg.xnat.eventservice.events.ImageAssessorEvent", **overrides) -> dict:
    sub = {
        "id": 15,
        "name": NAME,
        "active": True,
        "action-key": "org.nrg.containers.services.CommandActionProvider:3",
        "attributes": {},
        "event-filter": {"event-type": event_type, "project-ids": [], "status": "CREATED", "payload-filter": ""},
        "act-as-event-user": False,
        "subscription-owner": "admin",
        "valid": True,
    }
    sub.update(overrides)
    return sub


def _run(tmp_path: Path, subscriptions: list[dict]) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(STUB_CURL)
    curl.chmod(0o755)
    state = tmp_path / "subscriptions.json"
    state.write_text(json.dumps(subscriptions))
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_SUBSCRIPTIONS": str(state),
        "XNAT_ADMIN_USER": "admin",
        "XNAT_ADMIN_PASSWORD": "test-password",  # pragma: allowlist secret
        "TRUST_NETWORK_NAME": "deploy_trust-network-1",
    }
    return subprocess.run(["bash", str(SCRIPT)], cwd=CONFIG_DIR, env=env, capture_output=True, text=True, timeout=60)


def test_a_subscription_as_xnat_1_10_stores_it_validates(tmp_path: Path) -> None:
    result = _run(tmp_path, [_subscription()])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "export_mask configuration complete and validated" in result.stdout


@pytest.mark.parametrize(
    "broken",
    [
        _subscription(event_type="org.nrg.xnat.eventservice.events.ImageAssessor"),  # class without its suffix
        _subscription(valid=False),
        _subscription(active=False),
    ],
    ids=["unresolvable-event-class", "invalid", "inactive"],
)
def test_a_subscription_that_would_never_fire_fails_the_bring_up(tmp_path: Path, broken: dict) -> None:
    result = _run(tmp_path, [broken])
    assert result.returncode != 0
    assert "ERROR" in result.stderr
