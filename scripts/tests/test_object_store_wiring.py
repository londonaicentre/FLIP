# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""The dev object store's wiring (FLIP#1291): the Makefile guards and the compose invariants.

Two things nothing else pins. ``make clean-object-store`` runs ``rm -rf`` on ``OBJECT_STORE_DIR``, so the
Makefile must refuse a value that resolves to the checkout (an empty or ``.`` value would, through
``abs_or_relative_to``). And the origin flip-api presigns bundle URLs against (its ``AWS_ENDPOINT_URL_S3``)
must be exactly the origin every fl-api admits (``BUNDLE_URL_ALLOWED_ORIGINS``, scheme-and-port exact) and
the endpoint every fl-server uploads to — a change to one file alone 400s every bundle download, which only
a live training run would show. The Terraform side has the same guard in
``deploy/providers/AWS/tests/test_fl_api_bundle_allow_list.py``.

Standard library only and run as a plain script (``python <file>``), like every test in this directory.
The Makefile is driven with ``make -n`` under a scrubbed environment (``MAKEFLAGS`` from an outer make
would otherwise leak in) and a throwaway env file, so nothing touches the checkout.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PASS = 0
FAIL = 0


def _assert(condition: bool, what: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {what}")
    else:
        FAIL += 1
        print(f"  ❌ {what}" + (f" — {detail}" if detail else ""))


def _make(*args: str, env_file: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL")}
    return subprocess.run(
        ["make", "-n", f"MAIN_ENV_FILE={env_file.name}", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _scratch_env(buckets: dict[str, str]) -> Path:
    """A minimal env file in the checkout (MAIN_ENV_FILE must be a bare filename), removed by the caller."""
    lines = ["UI_PORT=443", "API_PORT=8080", 'NET_ENDPOINTS={"net-1": "http://fl-api-net-1:8000"}']
    lines += [f"{k}={v}" for k, v in buckets.items()]
    path = REPO_ROOT / ".env.test-object-store-wiring.development"
    path.write_text("\n".join(lines) + "\n")
    return path


GOOD_BUCKETS = {
    "FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME": "flip-model-files",
    "FLIP_FL_RESULTS_BUCKET_NAME": "flip-fl-results",
    "FLIP_APP_BUNDLES_BUCKET_NAME": "flip-app-bundles",
}


def test_clean_object_store_never_targets_the_checkout() -> None:
    print("test_clean_object_store_never_targets_the_checkout")
    env_file = _scratch_env(GOOD_BUCKETS)
    try:
        default = _make("clean-object-store", env_file=env_file)
        _assert(default.returncode == 0, "default OBJECT_STORE_DIR is accepted", detail=default.stderr[-300:])
        _assert(
            f'rm -rf "{REPO_ROOT}/object-store"' in default.stdout,
            "default removes <checkout>/object-store",
            detail=default.stdout[-300:],
        )
        for value in ("", ".", str(REPO_ROOT), str(REPO_ROOT.parent), "/"):
            run = _make("clean-object-store", f"OBJECT_STORE_DIR={value}", env_file=env_file)
            _assert(
                run.returncode != 0, f"OBJECT_STORE_DIR={value!r} is refused at parse time", detail=run.stdout[-200:]
            )
            _assert("rm -rf" not in run.stdout, f"no rm -rf is printed for OBJECT_STORE_DIR={value!r}")
        elsewhere = _make("clean-object-store", "OBJECT_STORE_DIR=/tmp/flip-store", env_file=env_file)
        _assert(
            elsewhere.returncode == 0 and 'rm -rf "/tmp/flip-store"' in elsewhere.stdout,
            "an absolute directory of its own is accepted",
        )
    finally:
        env_file.unlink()


def test_ensure_object_store_dir_guards_the_bucket_names() -> None:
    print("test_ensure_object_store_dir_guards_the_bucket_names")
    env_file = _scratch_env(GOOD_BUCKETS)
    try:
        run = _make("_ensure-object-store-dir", env_file=env_file)
        _assert(run.returncode == 0, "three good names are accepted", detail=run.stderr[-300:])
        # -n prints the recipe; the loop body names all three variables.
        _assert(all(name in run.stdout for name in GOOD_BUCKETS), "the recipe covers every *_BUCKET_NAME")
    finally:
        env_file.unlink()


def _compose_env(path: Path, service: str, key: str) -> list[str]:
    """Values of ``- KEY=value`` lines inside one service block of a compose file (comments skipped)."""
    text = path.read_text()
    match = re.search(rf"^  {re.escape(service)}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|^[a-z]|\Z)", text, re.M | re.S)
    assert match, (path, service)
    return re.findall(rf"^\s+- {re.escape(key)}=(.+?)\s*(?:#.*)?$", match.group(1), re.M)


def test_dev_presign_origin_matches_every_fl_api_and_fl_server() -> None:
    print("test_dev_presign_origin_matches_every_fl_api_and_fl_server")
    hub = _compose_env(REPO_ROOT / "deploy/compose.development.yml", "flip-api", "AWS_ENDPOINT_URL_S3")
    _assert(
        len(hub) == 1, "flip-api sets AWS_ENDPOINT_URL_S3 exactly once (a second line silently wins)", detail=str(hub)
    )
    origin = hub[0]
    for backend in ("nvflare", "flower"):
        path = REPO_ROOT / f"deploy/compose.development.{backend}.yml"
        for net in ("1", "2"):
            allowed = _compose_env(path, f"fl-api-net-{net}", "BUNDLE_URL_ALLOWED_ORIGINS")
            _assert(
                allowed == [origin],
                f"{backend} fl-api-net-{net} admits exactly the presign origin",
                detail=str(allowed),
            )
            endpoint = _compose_env(path, f"fl-server-net-{net}", "AWS_ENDPOINT_URL_S3")
            _assert(
                endpoint == [origin],
                f"{backend} fl-server-net-{net} uploads to the same endpoint",
                detail=str(endpoint),
            )


def test_prod_presign_origin_matches_every_fl_api() -> None:
    print("test_prod_presign_origin_matches_every_fl_api")
    hub = _compose_env(REPO_ROOT / "deploy/compose.production.yml", "flip-api", "AWS_ENDPOINT_URL_S3")
    _assert(len(hub) == 1, "prod flip-api sets AWS_ENDPOINT_URL_S3 exactly once", detail=str(hub))
    for backend in ("nvflare", "flower"):
        allowed = _compose_env(
            REPO_ROOT / f"deploy/compose.production.{backend}.yml", "fl-api-net-1", "BUNDLE_URL_ALLOWED_ORIGINS"
        )
        _assert(allowed == hub, f"prod {backend} fl-api-net-1 admits exactly the presign origin", detail=str(allowed))


def main() -> None:
    test_clean_object_store_never_targets_the_checkout()
    test_ensure_object_store_dir_guards_the_bucket_names()
    test_dev_presign_origin_matches_every_fl_api_and_fl_server()
    test_prod_presign_origin_matches_every_fl_api()
    print("—")
    print(f"PASS={PASS}  FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
