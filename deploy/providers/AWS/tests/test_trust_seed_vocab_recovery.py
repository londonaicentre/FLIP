# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The EC2 seed can reload a half-loaded DICOM vocabulary (FLIP#1190 review).

``load_dicom_vocab`` decides "already loaded" on one scaffolding concept, and that concept is
committed before the concepts and relationships it precedes. A run killed in between therefore
leaves a database that reports itself loaded while holding an incomplete vocabulary, and every
later run skips straight past it. The dev path reaches ``--force`` through
``FORCE_DICOM_VOCAB=1`` in ``trust/omop-db/Makefile``; without the same route here, recovery on
an EC2 trust is a hand-run psql against the trust's cluster.

Asserted over the source because nothing in CI runs the Makefile or the play against a host.
"""

import re
from pathlib import Path

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
MAKEFILE = AWS_PROVIDER_DIR / "Makefile"
SITE_YML = AWS_PROVIDER_DIR / "site.yml"
SEED_SCRIPT = AWS_PROVIDER_DIR.parents[2] / "trust" / "seed_trust.sh"


def test_the_seed_procedure_honours_the_variable():
    """The one procedure all three surfaces run: without this the chart and the play cannot reach --force."""
    script = SEED_SCRIPT.read_text()
    assert 'if [ "${FORCE_DICOM_VOCAB:-}" = "1" ]; then VOCAB_MODE=--force; else VOCAB_MODE=--skip-if-loaded; fi' in script
    assert 'load_dicom_vocab --vocab-dir "${VOCAB}" "${VOCAB_MODE}"' in script


def test_the_play_passes_the_variable_into_the_seed_container():
    text = SITE_YML.read_text()
    assert re.search(r"-e FORCE_DICOM_VOCAB=\{\{ '1' if seed_force_dicom_vocab \| bool else '' \}\}", text)


def test_the_play_var_does_not_shadow_its_own_extra_var():
    """`x: "{{ x | default(...) }}"` recurses unless an extra-var of that name is always passed;
    this one is passed only when the operator asks for it, so the two names must differ."""
    text = SITE_YML.read_text()
    assert 'seed_force_dicom_vocab: "{{ force_dicom_vocab | default(false) }}"' in text


def test_the_make_target_forwards_the_variable():
    text = MAKEFILE.read_text()
    assert "$(if $(filter 1,$(FORCE_DICOM_VOCAB)),-e force_dicom_vocab=true)" in text
