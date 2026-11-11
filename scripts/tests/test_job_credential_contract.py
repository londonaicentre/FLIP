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

"""The per-job hub credential keeps one wire contract across four packages.

flip-api mints it and names the request field and the callback header; each FL API reads the field and
writes the credential to ``JOB_CREDENTIAL_DIR``; flip-utils reads it there and sends the header. No suite
imports more than one of them, so a rename on one side would pass every test and leave each job's
callbacks refused. Every hub FL service must also be told the directory, the same one on both sides of a
net.

Stdlib-only and executable as a plain script, like the rest of ``scripts/tests``.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HUB_AUTH = REPO / "flip-api/src/flip_api/auth/job_callback.py"
HUB_BODY = REPO / "flip-api/src/flip_api/domain/interfaces/fl.py"
FLIP_UTILS = REPO / "flip-utils/flip/core/standard.py"
FL_API_SCHEMAS = (
    REPO / "fl-services/nvflare/fl-api-base/fl_api/utils/schemas.py",
    REPO / "fl-services/flower/fl-api-flower/fl_api/schemas.py",
)
COMPOSES = {
    "nvflare": "/app/server-checkpoints/.job-credentials",
    "flower": "/app/src/.job-credentials",
}


def _header(path: Path) -> str:
    match = re.search(r'^JOB_CREDENTIAL_HEADER = "([^"]+)"$', path.read_text(), re.M)
    assert match, f"JOB_CREDENTIAL_HEADER not found in {path}"
    return match.group(1)


class JobCredentialContract(unittest.TestCase):
    def test_flip_utils_sends_the_header_the_hub_reads(self):
        assert _header(FLIP_UTILS) == _header(HUB_AUTH)

    def test_each_fl_api_reads_the_field_the_hub_sends(self):
        assert re.search(r"^    job_credential: str\b", HUB_BODY.read_text(), re.M)
        for schema in FL_API_SCHEMAS:
            assert re.search(r"^    job_credential: str \| None\b", schema.read_text(), re.M), schema

    def test_every_hub_fl_service_is_given_the_same_directory(self):
        for backend, directory in COMPOSES.items():
            for env in ("development", "prod"):
                compose = (REPO / f"deploy/compose.{env}.{backend}.yml").read_text()
                services = re.findall(r"^  (fl-(?:api|server)-net-\d+):\n(.*?)(?=^  \S|\Z)", compose, re.M | re.S)
                assert services, f"no FL service in compose.{env}.{backend}.yml"
                for name, block in services:
                    assert f"- JOB_CREDENTIAL_DIR={directory}\n" in block, f"{name} in compose.{env}.{backend}.yml"


if __name__ == "__main__":
    unittest.main()
