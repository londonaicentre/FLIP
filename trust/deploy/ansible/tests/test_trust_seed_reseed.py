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
"""The EC2 seed reaches RESEED, the one way to move a seeded node to a new data version (FLIP#2111).

trust/seed_trust.sh seeds only an empty store, so without `make seed RESEED=1` reaching the seed
container a .data_version bump could never reach an EC2 trust. What the script does with it is
tested in trust/tests/test_seed_trust.py; here only the route, over the source, since nothing in CI
runs the Makefile or the play against a host.
"""

import re
from pathlib import Path

ANSIBLE_DIR = Path(__file__).resolve().parent.parent
MAKEFILE = ANSIBLE_DIR.parent / "terraform/aws/ec2/Makefile"
SEED_ROLE = ANSIBLE_DIR / "roles/flip_trust_seed"


def test_the_make_target_forwards_the_variable():
    assert "$(if $(filter 1,$(RESEED)),-e seed_reseed=true)" in MAKEFILE.read_text()


def test_the_play_var_does_not_shadow_its_own_extra_var():
    """As with force_dicom_vocab: `x: "{{ x | default(...) }}"` recurses, so the two names differ."""
    text = (SEED_ROLE / "defaults/main.yml").read_text()
    assert 'seed_reseed_enabled: "{{ seed_reseed | default(false) }}"' in text


def test_the_play_passes_the_variable_into_the_seed_container():
    text = (SEED_ROLE / "tasks/main.yml").read_text()
    assert re.search(r"-e RESEED=\{\{ 'true' if seed_reseed_enabled \| bool else 'false' \}\}", text)
