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

"""XNAT's two admin passwords on the rendered chart (FLIP#1692).

configure-xnat.sh signs in with XNAT_ADMIN_INITIAL_PASSWORD while XNAT still accepts it and changes it
to XNAT_ADMIN_PASSWORD. Both used to come from ``xnat-admin-password``, so a trust's own password was
tried as the initial one against XNAT's factory ``admin`` and the first install failed. The initial key
is optional, defaulting to ``admin``, so a Secret made before it existed keeps working.
"""

import base64
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _render(template: str, *sets: str) -> list[dict]:
    args = ["--set", "flClient.kitHostPath=/opt/flip/fl-kit", "--show-only", f"templates/{template}"]
    for item in sets:
        args += ["--set", item]
    rendered = subprocess.run(
        ["helm", "template", "trust-release", str(CHART_DIR), *args], capture_output=True, text=True, timeout=120
    )
    assert rendered.returncode == 0, rendered.stderr
    return [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]


def _pod_container(template: str, kind: str, field: str, name: str) -> dict:
    (doc,) = [d for d in _render(template) if d["kind"] == kind]
    return next(c for c in doc["spec"]["template"]["spec"][field] if c["name"] == name)


def _key_ref(container: dict, variable: str) -> dict:
    (entry,) = [e for e in container["env"] if e["name"] == variable]
    return entry["valueFrom"]["secretKeyRef"]


CONTAINERS = {
    "xnat-init": ("xnat-init-job.yaml", "Job", "initContainers", "configure-xnat-web"),
    "xnat-web": ("xnat-web.yaml", "Deployment", "containers", "tomcat"),
}


@pytest.mark.parametrize("where", CONTAINERS)
def test_the_initial_password_has_its_own_optional_key(where: str) -> None:
    container = _pod_container(*CONTAINERS[where])
    initial, configured = _key_ref(container, "XNAT_ADMIN_INITIAL_PASSWORD"), _key_ref(container, "XNAT_ADMIN_PASSWORD")

    assert (initial["key"], initial.get("optional")) == ("xnat-admin-initial-password", True)
    # The trust's own password must never be optional: the run changes XNAT's password to it.
    assert (configured["key"], configured.get("optional", False)) == ("xnat-admin-password", False)
    assert initial["name"] == configured["name"]


def _run_init_command(tmp_path: Path, env: dict[str, str]) -> str:
    """Runs the rendered xnat-init command against a stand-in configure-xnat.sh that prints what it gets."""
    (script,) = [arg for arg in _pod_container(*CONTAINERS["xnat-init"])["command"] if "configure-xnat.sh" in arg]
    (tmp_path / "configure-xnat.sh").write_text(
        'printf "%s|%s" "${XNAT_ADMIN_INITIAL_PASSWORD-<unset>}" "$XNAT_ADMIN_PASSWORD"\n'
    )
    base = {k: v for k, v in os.environ.items() if k != "XNAT_ADMIN_INITIAL_PASSWORD"}
    result = subprocess.run(
        ["bash", "-c", script.replace("/data/xnat/config", str(tmp_path))],
        env={**base, "XNAT_ADMIN_PASSWORD": "own", **env},  # pragma: allowlist secret
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize(
    ("env", "received"),
    [
        ({}, "admin|own"),  # a Secret without the key: XNAT's factory password
        ({"XNAT_ADMIN_INITIAL_PASSWORD": "factory"}, "factory|own"),  # pragma: allowlist secret
        # Empty stays empty, so configure-xnat.sh refuses it rather than guessing.
        ({"XNAT_ADMIN_INITIAL_PASSWORD": ""}, "|own"),
    ],
)
def test_the_init_job_defaults_only_an_absent_initial_password(tmp_path: Path, env: dict, received: str) -> None:
    assert _run_init_command(tmp_path, env) == received


def test_the_chart_secret_carries_both_passwords() -> None:
    sets = (
        "secrets.create=true",
        "secrets.data.xnat-admin-initial-password=factory",
        "secrets.data.xnat-admin-password=own",
    )
    (secret,) = _render("secrets.yaml", *sets)
    data = {key: base64.b64decode(value).decode() for key, value in secret["data"].items()}

    assert data["xnat-admin-initial-password"] == "factory"  # pragma: allowlist secret
    assert data["xnat-admin-password"] == "own"  # pragma: allowlist secret
