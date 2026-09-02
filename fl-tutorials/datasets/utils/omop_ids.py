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

"""Per-project surrogate-key blocks, shared by the dataset converters (FLIP#1092 task 9).

Both spleen_project and cxr_project load into the same trust database, and prostate_project will be
a third. Surrogate keys (visit_occurrence_id, procedure_occurrence_id, image_occurrence_id,
image_feature_id) must not collide across projects, so each project gets a reserved 1,000,000-wide
block.

``person_id`` is deliberately NOT covered here: it comes from ``nhs_number_to_integer(PatientID)``,
the first nine digits of a random NHS number, so it is outside our control and scatters across the
whole 9-digit range. Do not assume a person_id band is reserved — it is not.
"""

PROJECT_ID_BLOCKS = {
    "cxr_project": 1_000_000,
    "spleen_project": 2_000_000,
    "prostate_project": 3_000_000,
}
BLOCK_SIZE = 1_000_000


def surrogate_ids(project: str, count: int) -> range:
    """Allocate ``count`` surrogate keys inside ``project``'s reserved block.

    Args:
        project: Key of PROJECT_ID_BLOCKS.
        count: How many consecutive ids are needed.

    Returns:
        range: ``count`` ids starting one above the project's block base.

    Raises:
        KeyError: If the project has no allocated block — allocate one rather than reusing another
            project's, since they share a database.
        ValueError: If ``count`` would overflow the block into the next project's range.
    """
    base = PROJECT_ID_BLOCKS[project]
    if count > BLOCK_SIZE:
        raise ValueError(
            f"{project}: {count} surrogate ids requested but its block only holds {BLOCK_SIZE} — "
            "would overflow into the next project's range"
        )
    return range(base + 1, base + 1 + count)
