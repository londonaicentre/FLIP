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
"""configure-nifti-to-seg.sh run for real against a stub ``curl`` serving XNAT 1.10's responses.

The stub records each subscription the script POSTs, so the tests check what XNAT would be asked to
store (the status, the label filter, the wrapper's action key, who it runs as) as well as the validation of what it
reports back, in XNAT 1.10's shape: the event class under ``event-filter.event-type``.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

CONFIG_DIR = Path(__file__).resolve().parents[1] / "xnat" / "config"
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = CONFIG_DIR / "configure-nifti-to-seg.sh"
PREFIX = "Convert FL job masks to DICOM-SEG"
RESOURCE_EVENT = "org.nrg.xnat.eventservice.events.ResourceEvent"

STUB_CURL = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
url = next(a for a in args if a.startswith("http"))
method = args[args.index("-X") + 1] if "-X" in args else "GET"
if method == "GET" and "/xapi/commands?name=" in url:
    body = "[]"
elif method == "POST" and url.endswith("/xapi/commands"):
    body = "3"
elif method == "GET" and url.endswith("/xapi/commands/3"):
    body = json.dumps({"id": 3, "xnat": [{"id": 7, "name": "nifti_to_seg-resource"}]})
elif url.endswith("/enabled"):
    body = "true" if method == "GET" else ""
elif method == "GET" and url.endswith("/xapi/events/subscriptions"):
    body = open(os.environ["STUB_SUBSCRIPTIONS"]).read()
elif method == "POST" and url.endswith("/xapi/events/subscription"):
    with open(os.environ["STUB_POSTED"], "a") as posted:
        posted.write(sys.stdin.read().replace("\n", " ") + "\n")
    body = ""
else:
    body = ""
sys.stdout.write(body + "\n200")
"""


def _subscription(status: str, event_type: str = RESOURCE_EVENT, **overrides) -> dict:
    sub = {
        "id": 20 if status == "CREATED" else 21,
        "name": f"{PREFIX} ({status})",
        "active": True,
        "action-key": "org.nrg.containers.services.CommandActionProvider:7",
        "event-filter": {"event-type": event_type, "project-ids": [], "status": status, "payload-filter": ""},
        "valid": True,
    }
    sub.update(overrides)
    return sub


def _run(tmp_path: Path, subscriptions: list[dict], **env_overrides) -> tuple[subprocess.CompletedProcess, list]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(STUB_CURL)
    curl.chmod(0o755)
    state, posted = tmp_path / "subscriptions.json", tmp_path / "posted.jsonl"
    state.write_text(json.dumps(subscriptions))
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_SUBSCRIPTIONS": str(state),
        "STUB_POSTED": str(posted),
        "XNAT_ADMIN_USER": "admin",
        "XNAT_ADMIN_PASSWORD": "test-password",  # pragma: allowlist secret
        "TRUST_NETWORK_NAME": "deploy_trust-network-1",
        **env_overrides,
    }
    result = subprocess.run(["bash", str(SCRIPT)], cwd=CONFIG_DIR, env=env, capture_output=True, text=True, timeout=60)
    bodies = [json.loads(line) for line in posted.read_text().splitlines()] if posted.exists() else []
    return result, bodies


def test_updated_alone_is_subscribed_on_the_label_with_the_wrappers_action(tmp_path: Path) -> None:
    # CREATED as well would launch a second container racing the first to the same ROI collection.
    result, posted = _run(tmp_path, [_subscription("UPDATED")])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "nifti_to_seg configuration complete and validated" in result.stdout
    assert [p["event-filter"]["status"] for p in posted] == ["UPDATED"]
    for body in posted:
        status = body["event-filter"]["status"]
        assert body["event-selector"] == f"{RESOURCE_EVENT}:{status}"
        # The wrapper's id, not the command's.
        assert body["action-key"] == "org.nrg.containers.services.CommandActionProvider:7"
        assert body["event-filter"]["payload-filter"] == f'@.label == "{_mask_resource()}"'
        assert body["project-id"] == ""  # site-wide
        # Run as whoever caused the event, not as the admin who registered the subscription.
        assert body["act-as-event-user"] is True


def _mask_resource() -> str:
    """flip-utils' MASK_RESOURCE: the resource flip.add_mask writes, read from its source."""
    source = (REPO_ROOT / "flip-utils" / "flip" / "constants" / "flip_constants.py").read_text()
    match = re.search(r'^MASK_RESOURCE = "([^"]+)"$', source, re.MULTILINE)
    assert match, "no MASK_RESOURCE constant in flip-utils"
    return match.group(1)


def test_the_trigger_label_is_the_one_flip_add_mask_writes() -> None:
    assert f'NIFTI_TO_SEG_RESOURCE="{_mask_resource()}"' in SCRIPT.read_text()


@pytest.mark.parametrize(
    "broken",
    [
        [_subscription("CREATED")],  # UPDATED missing
        [_subscription("UPDATED", event_type="org.nrg.xnat.eventservice.events.Resource")],
        [_subscription("UPDATED", valid=False)],
        [_subscription("UPDATED", active=False)],
    ],
    ids=["missing-status", "unresolvable-event-class", "invalid", "inactive"],
)
def test_a_subscription_that_would_never_fire_fails_the_bring_up(tmp_path: Path, broken: list[dict]) -> None:
    result, _ = _run(tmp_path, broken)
    assert result.returncode != 0
    assert "ERROR" in result.stderr
