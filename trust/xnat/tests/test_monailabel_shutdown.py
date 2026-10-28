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
"""Exercise PID 1 signal forwarding before readiness and during registration."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "monailabel/entrypoint.sh"


@pytest.mark.parametrize("phase", ["readiness", "registration"])
def test_startup_termination_reaps_server(tmp_path: Path, phase: str) -> None:
    server = tmp_path / "monailabel"
    server.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, sys, time\n"
        "from pathlib import Path\n"
        "root = Path(os.environ['TEST_ROOT'])\n"
        "def stop(*args):\n"
        "    (root / 'stopped').touch()\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "(root / 'started').touch()\n"
        "while True: time.sleep(0.02)\n"
    )
    curl = tmp_path / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "root = Path(os.environ['TEST_ROOT'])\n"
        "args = ' '.join(sys.argv)\n"
        "if 'openapi.json' in args or 'ohifaiaa' in args:\n"
        "    while not (root / 'started').exists(): time.sleep(0.02)\n"
        "    phase = 'readiness' if 'openapi.json' in args else 'registration'\n"
        "    if phase == os.environ['TEST_PHASE']:\n"
        "        (root / 'phase').touch()\n"
        "        time.sleep(0.2)\n"
        "        sys.exit(1)\n"
        "if 'ohifaiaa' in args: print('200')\n"
    )
    for executable in (server, curl):
        executable.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{tmp_path}:{os.environ['PATH']}",
        TEST_ROOT=str(tmp_path),
        TEST_PHASE=phase,
        MONAI_LABEL_PORT="8030",
        MONAI_LABEL_DATASTORE_URL="http://xnat.test",
        MONAI_LABEL_DATASTORE_USERNAME="test",
        MONAI_LABEL_DATASTORE_PASSWORD="test",  # pragma: allowlist secret — test fixture only
        MONAI_LABEL_MODELS="deepedit",
        MONAI_LABEL_PUBLIC_URL="https://labels.test",
    )
    process = subprocess.Popen(
        ["bash", str(ENTRYPOINT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "phase").exists():
            assert process.poll() is None, process.communicate()[0]
            assert time.monotonic() < deadline, "startup phase was never reached"
            time.sleep(0.02)
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=10)
        assert process.returncode == 143, output
        assert (tmp_path / "stopped").exists(), output
        assert "Received termination signal" in output
        assert "Registered." not in output
    finally:
        # Clean up the isolated process group even if the regression returns.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
