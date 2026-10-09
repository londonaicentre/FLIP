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

"""scripts/kit_secret.py: a slot's FL kit directory as the Secret flClient.kit.source=secret reads (FLIP#1390).

Run for real against kit trees laid out the way each backend's slot directory is shipped.
"""

import base64
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[1]
SCRIPT = CHART_DIR / "scripts" / "kit_secret.py"


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


NVFLARE = {
    "startup/fed_client.json": "{}",
    "startup/client.key": "key",
    "startup/start.sh": "#!/bin/sh",
    "local/resources.json.default": "{}",
    "readme.txt": "not mounted",
}
FLOWER = {"certificates/ca.crt": "ca", "keys/supernode_credentials_3": "secret"}


def _run(kit: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--kit-src",
            str(kit),
            "--name",
            "trust-release-flip-trust-fl-kit",
            "--namespace",
            "flip-trust",
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize(
    ("files", "keys"),
    [
        (
            NVFLARE,
            {"startup__fed_client.json", "startup__client.key", "startup__start.sh", "local__resources.json.default"},
        ),
        (FLOWER, {"certificates__ca.crt", "keys__supernode_credentials_3"}),
    ],
)
def test_each_mounted_file_becomes_one_key(tmp_path, files, keys):
    result = _run(_tree(tmp_path / "kit", files))
    assert result.returncode == 0, result.stderr
    secret = yaml.safe_load(result.stdout)
    assert secret["kind"] == "Secret"
    assert secret["metadata"]["name"] == "trust-release-flip-trust-fl-kit"
    assert secret["metadata"]["namespace"] == "flip-trust"
    assert set(secret["data"]) == keys, "root-level files are not mounted and stay out"
    first = sorted(keys)[0]
    rel = first.replace("__", "/", 1)
    assert base64.b64decode(secret["data"][first]).decode() == files[rel]


def test_a_kit_missing_its_backends_files_is_refused(tmp_path):
    result = _run(_tree(tmp_path / "kit", {"startup/client.key": "key"}))
    assert result.returncode != 0
    assert "fed_client.json" in result.stderr or "ca.crt" in result.stderr


def test_a_nested_directory_is_refused(tmp_path):
    result = _run(_tree(tmp_path / "kit", {**FLOWER, "keys/extra/deep.key": "x"}))
    assert result.returncode != 0
    assert "deep.key" in result.stderr


def test_a_kit_over_the_secret_size_limit_is_refused(tmp_path):
    result = _run(_tree(tmp_path / "kit", {**FLOWER, "keys/huge.bin": "x" * 1_100_000}))
    assert result.returncode != 0
    assert "1 MiB" in result.stderr


def test_make_kit_secret_pipes_the_secret_to_kubectl():
    """Dry-run: the target builds the Secret the chart's default secretName expects and applies it."""
    import shutil

    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    out = subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), "kit-secret", "KIT_SRC=/tmp/kit", "KUBE_CONTEXT=aks-x"],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    assert "scripts/kit_secret.py --kit-src /tmp/kit" in out
    assert "--name trust-release-flip-trust-fl-kit" in out
    assert "kubectl --context aks-x apply -f -" in out
