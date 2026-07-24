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

"""Regression tests for the Trust EC2 CloudWatch agent config template.

The Trust host runs Ubuntu, so the agent must tail Ubuntu log paths. A prior
version shipped RHEL/CentOS paths (``/var/log/messages`` and a non-existent
``/var/log/docker``), so the agent had nothing to read and never created a
log stream (FLIP#397). These tests render the Jinja template and assert the
paths are the Ubuntu ones, guarding against a regression to the RHEL paths.
"""

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE_NAME = "cloudwatch-agent-trust.json.j2"
INSTANCE_ID = "i-0123456789abcdef0"
TRUST_LOG_GROUP = "/aws/ec2/flip-trust"


def _render() -> dict:
    """Render the Trust CloudWatch agent template and parse it as JSON.

    Returns:
        dict: The parsed agent configuration.
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)
    rendered = env.get_template(TEMPLATE_NAME).render(ansible_ec2_instance_id=INSTANCE_ID)
    return json.loads(rendered)


def _file_paths(config: dict) -> list[str]:
    """Extract the ``file_path`` of every collected log file.

    Args:
        config (dict): The parsed agent configuration.

    Returns:
        list[str]: The configured file paths, in declaration order.
    """
    return [entry["file_path"] for entry in config["logs"]["logs_collected"]["files"]["collect_list"]]


def test_template_renders_to_valid_json() -> None:
    """The template must render to a JSON object the agent can parse."""
    assert isinstance(_render(), dict)


def test_collects_ubuntu_log_paths() -> None:
    """The agent tails the Ubuntu system log and the Docker json-file logs."""
    paths = _file_paths(_render())
    assert "/var/log/syslog" in paths
    assert "/var/lib/docker/containers/*/*.log" in paths


def test_does_not_ship_rhel_paths() -> None:
    """Guard against regressing to the RHEL paths that never exist on Ubuntu."""
    paths = _file_paths(_render())
    assert "/var/log/messages" not in paths
    assert "/var/log/docker" not in paths


def test_all_streams_target_the_trust_log_group() -> None:
    """Every collected file writes to the Trust EC2 log group, keyed by instance id."""
    collect_list = _render()["logs"]["logs_collected"]["files"]["collect_list"]
    assert collect_list, "expected at least one collected log file"
    for entry in collect_list:
        assert entry["log_group_name"] == TRUST_LOG_GROUP
        assert entry["log_stream_name"].startswith(f"{INSTANCE_ID}/")
