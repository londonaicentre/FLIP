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
"""Seeds a first answer with a misspelt column and a wrong table, then lets the real model fix it from the
schema-check error. Shows the retry loop end to end."""

import json
from types import SimpleNamespace

import helper
from anthropic.types import TextBlock, Usage

BAD = {
    "sql": "SELECT io.person_id, io.accesion_id, io.image_occurrence_date FROM omop.image_occurrence io "
    "JOIN omop.patient p ON p.person_id = io.person_id WHERE io.modality_concept_id = 4300757 "
    "AND p.gender_concept_id = 8532 ORDER BY io.image_occurrence_id LIMIT 1000",
    "style": "novocab",
    "explanation": "",
    "assumptions": [],
    "concepts_used": [],
}
real = helper.client.messages.create
calls = []


def fake_first(**kw):
    calls.append(1)
    if len(calls) == 1:
        return SimpleNamespace(
            stop_reason="end_turn",
            stop_details=None,
            usage=Usage(input_tokens=0, output_tokens=0),
            content=[TextBlock(type="text", text=json.dumps(BAD))],
        )
    return real(**kw)


helper.client.messages.create = fake_first
r = helper.generate("All CT scans of women.")
helper.show(r)
print("rejections:", [(x["stage"], x["error"]) for x in r["rejections"]])
