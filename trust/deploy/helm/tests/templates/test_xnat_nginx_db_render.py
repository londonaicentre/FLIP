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

"""xnat-nginx's config and xnat-db's Postgres settings, rendered (FLIP#1880).

The xnat-nginx image's nginx.conf includes nothing from conf.d, so the chart's config only takes
effect mounted over /etc/nginx/nginx.conf. xnat-db carries Compose's max_connections and
shared_buffers (FLIP#662). Skipped without helm.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _render(template: str, *sets: str) -> list[dict]:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    for item in sets:
        args += ["--set", item]
    rendered = subprocess.run(
        [*args, "--show-only", template], capture_output=True, text=True, check=True, timeout=120
    ).stdout
    return [doc for doc in yaml.safe_load_all(rendered) if doc]


def _container(docs: list[dict], kind: str) -> dict:
    (workload,) = [d for d in docs if d["kind"] == kind]
    (container,) = workload["spec"]["template"]["spec"]["containers"]
    return container


def test_nginx_config_replaces_the_image_nginx_conf():
    docs = _render("templates/xnat-nginx.yaml")
    (configmap,) = [d for d in docs if d["kind"] == "ConfigMap"]
    mount = next(m for m in _container(docs, "Deployment")["volumeMounts"] if m["name"] == "config")

    assert mount == {"name": "config", "mountPath": "/etc/nginx/nginx.conf", "subPath": "nginx.conf"}
    conf = configmap["data"][mount["subPath"]]
    # A whole nginx.conf: a bare server block at the top level would stop nginx starting.
    assert conf.lstrip().startswith("worker_processes") and "events {" in conf and "http {" in conf, conf
    assert "proxy_read_timeout 300s;" in conf


def test_nginx_pod_restarts_on_a_config_change():
    docs = _render("templates/xnat-nginx.yaml")
    (deployment,) = [d for d in docs if d["kind"] == "Deployment"]

    assert "checksum/config" in deployment["spec"]["template"]["metadata"].get("annotations", {})


@pytest.mark.parametrize(
    ("sets", "expected"),
    [
        ((), ["postgres", "-c", "max_connections=300", "-c", "shared_buffers=512MB"]),
        (
            ("xnat.db.maxConnections=500", "xnat.db.sharedBuffers=1GB"),
            ["postgres", "-c", "max_connections=500", "-c", "shared_buffers=1GB"],
        ),
    ],
)
def test_xnat_db_runs_with_compose_postgres_settings(sets: tuple[str, ...], expected: list[str]):
    postgres = _container(_render("templates/xnat-db.yaml", *sets), "StatefulSet")

    # args, not command: the image's guard entrypoint must still run.
    assert "command" not in postgres
    assert postgres["args"] == expected
