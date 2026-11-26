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
"""trust/seed_trust.sh seeds only an empty store, unless RESEED=true (FLIP#2111).

The Helm chart's trust-seed hook runs the script on every install and upgrade, and the EC2 play on
every seed, so a seeded trust must come out of it untouched. The script runs for real here, against
a local HTTP server standing in for Orthanc (and giving the OMOP wait a port to reach) and a stub
`uv` that answers the OMOP probe and records every loader it is asked to run.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "seed_trust.sh"
VOCAB = "vocab_dicom_test"


def _orthanc(studies: int, status: int = 200) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — the stdlib's hook name
            body = json.dumps({"CountStudies": studies}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _seed(
    tmp_path: Path, omop_holds: bool | str, studies: int, status: int = 200, ok: bool = True, **env: str
) -> tuple[str, str]:
    """Run the script; return what the stub uv was asked to run, and the script's output.

    ``omop_holds`` is the OMOP probe's answer (True/False for yes/no, or any other text it might print);
    ``status`` what Orthanc answers /statistics with; ``ok`` whether the script must succeed.
    """
    answer = ("yes" if omop_holds else "no") if isinstance(omop_holds, bool) else omop_holds
    calls = tmp_path / "uv.log"
    stub = tmp_path / "bin" / "uv"
    stub.parent.mkdir()
    stub.write_text(
        f'#!/bin/sh\necho "uv $*" >> "{calls}"\ncase "$*" in *--holds-data*) echo {answer};; esac\nexit 0\n'
    )
    stub.chmod(0o755)
    (tmp_path / "work" / VOCAB).mkdir(parents=True)  # present, so nothing is downloaded
    server = _orthanc(studies, status)
    port = str(server.server_address[1])
    try:
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            env={
                **os.environ,
                "PATH": f"{stub.parent}:{os.environ['PATH']}",
                "WORK_DIR": str(tmp_path / "work"),
                "TRUST_DATA_VERSION": "20261125",
                "SOURCE_TRUST": "2",
                "PROJECTS": "cxr_project",
                "VOCAB_DICOM_BUNDLE": VOCAB,
                "SEED_OMOP": "true",
                "SEED_ORTHANC": "true",
                "OMOP_DB_HOST": "127.0.0.1",
                "OMOP_DB_PORT": port,
                "ORTHANC_URL": f"http://127.0.0.1:{port}",
                "ORTHANC_USERNAME": "o",
                "ORTHANC_PASSWORD": "p",
                "TOOLS_SPEC": "omop-db-tools",
                "SEEDER": "seed_orthanc.py",
                **env,
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        server.shutdown()
    assert (result.returncode == 0) == ok, result.stdout + result.stderr
    return (calls.read_text() if calls.exists() else ""), result.stdout + result.stderr


LOAD = "omop_db_tools.import_tables --trust-index 2 --num-trusts"
FETCH = "omop_db_tools.dataset fetch"
PACS = "seed_orthanc.py --trust-index 2"


def test_empty_stores_are_seeded(tmp_path):
    calls, _ = _seed(tmp_path, omop_holds=False, studies=0)
    assert FETCH in calls, calls
    assert LOAD in calls, calls
    assert PACS in calls, calls
    assert "--clear-projects" not in calls, "an empty PACS has nothing to clear"


def test_stores_that_hold_data_are_left_alone(tmp_path):
    """What every Helm upgrade of a seeded trust now does: ask, then fetch and load nothing."""
    calls, out = _seed(tmp_path, omop_holds=True, studies=12)
    assert "omop_db_tools.import_tables --trust-index 2 --holds-data" in calls, calls
    assert FETCH not in calls, calls
    assert LOAD not in calls, calls
    assert PACS not in calls, calls
    assert "OMOP already holds data" in out, out
    assert "Orthanc already holds 12 studies" in out, out


def test_each_store_is_judged_on_its_own(tmp_path):
    calls, _ = _seed(tmp_path, omop_holds=True, studies=0)
    assert LOAD not in calls, calls
    assert PACS in calls, calls


def test_reseed_loads_both_and_clears_the_pacs_first(tmp_path):
    """How a trust moves to a new data version: Orthanc never overwrites an instance it holds."""
    calls, _ = _seed(tmp_path, omop_holds=True, studies=12, RESEED="true")
    assert LOAD in calls, calls
    assert f"{PACS} --projects cxr_project" in calls, calls
    assert "--clear-projects" in calls, calls


@pytest.mark.parametrize("seeded", [True, False])
def test_forcing_the_dicom_vocabulary_still_reaches_it(tmp_path, seeded):
    """FORCE_DICOM_VOCAB=1 repairs a half-loaded vocabulary, which only a database holding rows can have."""
    calls, _ = _seed(tmp_path, omop_holds=seeded, studies=0, FORCE_DICOM_VOCAB="1")
    assert "omop_db_tools.load_dicom_vocab --vocab-dir" in calls, calls
    assert "--force" in calls, calls


def test_forcing_the_vocabulary_on_a_seeded_database_reloads_nothing_else(tmp_path):
    """The repair must not quietly move the rows to the current pin while Orthanc stays where it was."""
    calls, _ = _seed(tmp_path, omop_holds=True, studies=12, FORCE_DICOM_VOCAB="1")
    assert FETCH not in calls, calls
    assert LOAD not in calls, calls


def test_a_probe_answer_that_is_neither_yes_nor_no_stops_the_seed(tmp_path):
    """Stray output must never read as "empty" and reload a store that holds data."""
    calls, out = _seed(tmp_path, omop_holds="warning: something", studies=12, ok=False)
    assert "not yes or no" in out, out
    assert LOAD not in calls, calls


def test_a_refused_orthanc_login_fails_at_once(tmp_path):
    """A wrong login is not an Orthanc still starting: no five minutes of retries ending in "not reachable"."""
    _, out = _seed(tmp_path, omop_holds=True, studies=0, status=401, ok=False)
    assert "refused the login (401)" in out, out
