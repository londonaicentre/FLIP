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
"""The NVFLARE dev FL API can read its admin key through the host group (FLIP#1384).

NVFLARE writes ``client.key`` 0600 and the dev FL API runs as the image's user, so three pieces
must agree: ``make provision`` opens exactly the key the compose mounts to the provisioning user's
group, the compose makes the FL API join the host group (with no guessed default), and the root
``make up`` refuses a workspace whose key the FL API could not open.

Stdlib-only and executable as a plain script, like the rest of ``scripts/tests``.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "deploy" / "compose.development.nvflare.yml"
GUARD = REPO / "scripts" / "check-fl-provisioned.sh"


def service_block(name: str) -> str:
    """The text of one top-level service in the dev NVFLARE compose."""
    text = COMPOSE.read_text()
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  \S|\Z)", text, re.M | re.S)
    assert match, f"{name} not found in {COMPOSE}"
    return match.group(1)


class ComposeAndProvision(unittest.TestCase):
    def test_each_fl_api_joins_the_host_group_with_no_default(self):
        for net in (1, 2):
            block = service_block(f"fl-api-net-{net}")
            assert re.search(r'group_add: \["\$\{DOCKER_GID:\?[^}]*\}"\]', block), f"fl-api-net-{net}"
            assert "user:" not in block, f"fl-api-net-{net} must keep the image's user (it writes /app/admin)"

    def test_provision_opens_the_key_the_compose_mounts(self):
        for net in (1, 2):
            mount = f"${{FL_PROVISIONED_DIR}}/net-{net}/services/flip-fl-api-net-{net}/startup:/app/admin/startup"
            assert mount in service_block(f"fl-api-net-{net}")
            recipe = subprocess.run(
                ["make", "-n", "-s", "-C", str(REPO / "fl-services" / "nvflare"), "provision", f"NET_NUMBER={net}"],
                capture_output=True,
                text=True,
                timeout=60,
            ).stdout
            key = f"net-{net}/services/flip-fl-api-net-{net}/startup/client.key"
            assert re.search(rf"chgrp .+ \S*{re.escape(key)}", recipe)
            assert re.search(rf"chmod 640 \S*{re.escape(key)}", recipe)


class MakeUpGuard(unittest.TestCase):
    def guard(self, mode: int, key_gid: str | None = None):
        """Run check-fl-provisioned.sh on a scratch net-1 workspace whose FL API key has ``mode``."""
        with tempfile.TemporaryDirectory() as tmp:
            startup = Path(tmp, "net-1", "services")
            (startup / "fl-server-net-1" / "startup").mkdir(parents=True)
            (startup / "fl-server-net-1" / "startup" / "start.sh").write_text("")
            key = startup / "flip-fl-api-net-1" / "startup" / "client.key"
            key.parent.mkdir(parents=True)
            key.write_text("key")
            key.chmod(mode)
            env = {
                **os.environ,
                "FL_BACKEND": "nvflare",
                "NET_ENDPOINTS": '{"net-1": "http://fl-api-net-1:8000"}',
                "FL_PROVISIONED_DIR": tmp,
                "FL_API_KEY_GID": str(os.getgid()) if key_gid is None else key_gid,
            }
            return subprocess.run(["bash", str(GUARD)], capture_output=True, text=True, env=env, timeout=60)

    def test_a_group_readable_key_passes(self):
        result = self.guard(0o640)
        assert result.returncode == 0, result.stderr

    def test_an_owner_only_key_is_refused_with_the_fix(self):
        result = self.guard(0o600)
        assert result.returncode == 1, result.stderr
        assert "cannot read its admin key" in result.stderr
        assert "chmod 640" in result.stderr

    def test_a_key_owned_by_another_group_is_refused(self):
        result = self.guard(0o640, key_gid=str(os.getgid() + 1))
        assert result.returncode == 1, result.stderr

    def test_outside_development_the_key_is_not_checked(self):
        result = self.guard(0o600, key_gid="")
        assert result.returncode == 0, result.stderr


if __name__ == "__main__":
    unittest.main()
