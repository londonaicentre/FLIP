# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for scripts/ssh_config.py: one managed entry per node, nothing else touched."""

import stat
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ssh_config.py"
HUB_BLOCK = "# Managed by FLIP - SSH over SSM Session Manager\nHost flip-trust\n    HostName i-0hub0trust0\n"


def run(config: Path, instance_id: str = "i-0123456789abcdef0", *extra: str) -> subprocess.CompletedProcess[str]:
    args = ["--alias", "flip-stag-gstt", "--instance-id", instance_id, "--region", "eu-west-2"]
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--key", "~/.ssh/host-aws", "--config", str(config), *extra],
        capture_output=True,
        text=True,
    )


def test_writes_an_entry_reaching_the_node_over_ssm(tmp_path):
    config = tmp_path / "config"
    assert run(config, "i-0123456789abcdef0", "--profile", "lza-stag").returncode == 0
    text = config.read_text()
    assert "Host flip-stag-gstt\n    HostName i-0123456789abcdef0\n" in text
    assert "--document-name AWS-StartSSHSession" in text
    assert "--profile lza-stag" in text
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_a_rerun_replaces_the_entry_and_keeps_the_others(tmp_path):
    config = tmp_path / "config"
    config.write_text("Host other\n    HostName example.org\n\n" + HUB_BLOCK)
    run(config, "i-0aaaaaaaaaaaaaaa1")
    run(config, "i-0bbbbbbbbbbbbbbb2")
    text = config.read_text()
    assert text.count("Host flip-stag-gstt\n") == 1
    assert "i-0bbbbbbbbbbbbbbb2" in text
    assert "i-0aaaaaaaaaaaaaaa1" not in text
    assert "Host other\n    HostName example.org\n" in text
    assert HUB_BLOCK in text


def test_refuses_a_value_that_is_not_an_instance_id(tmp_path):
    config = tmp_path / "config"
    result = run(config, "")
    assert result.returncode != 0
    assert "not an EC2 instance id" in result.stderr
    assert not config.exists()
