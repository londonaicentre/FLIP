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

"""Postgres data in a subdirectory of its volume, rendered (FLIP#1390).

A freshly formatted cloud disk (Azure Disk, EBS) has lost+found at its root, and postgres's initdb
refuses a non-empty data directory, so omop-db and xnat-db never start on one. `pgdataSubdir`
points PGDATA one level down. Off by default: an existing volume keeps its data at the root, and
moving PGDATA under it would start an empty cluster beside the real one. Skipped without helm.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2]
STATEFULSETS = {"omop-db": "omopDb.persistence.pgdataSubdir", "xnat-db": "xnat.db.persistence.pgdataSubdir"}

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def _pgdata(*sets: str) -> dict[str, str | None]:
    args = ["helm", "template", "trust-release", str(CHART_DIR), "--set", "flClient.kitHostPath=/opt/flip/fl-kit"]
    for item in sets:
        args += ["--set", item]
    rendered = subprocess.run(args, capture_output=True, text=True, check=True, timeout=120).stdout
    found: dict[str, str | None] = {}
    for doc in yaml.safe_load_all(rendered):
        if not doc or doc.get("kind") != "StatefulSet":
            continue
        for name in STATEFULSETS:
            if doc["metadata"]["name"].endswith(name):
                container = doc["spec"]["template"]["spec"]["containers"][0]
                env = {e["name"]: e.get("value") for e in container.get("env", [])}
                found[name] = env.get("PGDATA")
    return found


def test_pgdata_stays_at_the_volume_root_by_default():
    assert _pgdata() == {"omop-db": None, "xnat-db": None}


def test_pgdata_moves_into_the_named_subdirectory():
    found = _pgdata(*(f"{key}=pgdata" for key in STATEFULSETS.values()))
    assert found == {name: "/var/lib/postgresql/data/pgdata" for name in STATEFULSETS}
