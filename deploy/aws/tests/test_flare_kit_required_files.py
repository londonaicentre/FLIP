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
"""add_fl_kits.sh uploads a kit's required startup files last, and flip-api counts a kit
only once those files are on every net (FLIP#1720), so the two lists must match."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_script_and_flip_api_require_the_same_startup_files():
    script = (REPO_ROOT / "deploy/providers/AWS/scripts/add_fl_kits.sh").read_text()
    resolver = (REPO_ROOT / "flip-api/src/flip_api/db/seed/fl_kit_slots.py").read_text()

    script_files = re.search(r"^REQUIRED_STARTUP_FILES=\(([^)]*)\)", script, re.M)
    resolver_files = re.search(r"^FLARE_KIT_REQUIRED_FILES = frozenset\(\{([^}]*)\}\)", resolver, re.M)

    assert script_files
    assert resolver_files
    assert set(script_files.group(1).split()) == set(re.findall(r'"([^"]+)"', resolver_files.group(1)))
