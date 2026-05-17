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
"""Seed five medical-imaging-themed projects, each with a cohort query.

Run from `flip-api/`:

    env DB_HOST=localhost uv run python -m tests.create_dummy_imaging_projects

Uses the project + cohort save/submit helpers from `debug_prelaunch_task` so
auth, error handling, and trust submission stay in one place. The xray query
mirrors flip-fl-base's `tutorials/image_classification/xray_classification/query.sql`;
the spleen query mirrors `tutorials/image_segmentation/3d_spleen_segmentation/query.sql`.
"""

import requests

from tests.debug_prelaunch_task import (
    add_project_query,
    AUTH_TOKEN,
    create_unstaged_project,
    submit_query_to_trusts,
)

XRAY_QUERY = """\
WITH feature_observation AS (
    SELECT
        ife.image_occurrence_id,
        ife.image_feature_concept_id,
        ife.anatomic_site_concept_id,
        ife.image_feature_event_id
    FROM
        omop.image_feature ife
    WHERE
        ife.image_feature_event_field_concept_id = 1147304
),
observation_value AS (
    SELECT
        fo.image_occurrence_id,
        MAX(anatomic_site_concept_id) AS anatomic_site_concept_id,
        MAX(CASE WHEN image_feature_concept_id = 4215818 THEN value_concept.concept_name END) AS effusion,
        MAX(CASE WHEN image_feature_concept_id = 4196943 THEN value_concept.concept_name END) AS edema,
        MAX(CASE WHEN image_feature_concept_id = 40481136 THEN value_concept.concept_name END) AS normal_lungs
    FROM
        feature_observation fo
        JOIN omop.observation o ON o.observation_id = fo.image_feature_event_id
        JOIN omop.concept value_concept ON value_concept.concept_id = o.value_as_concept_id
    GROUP BY
        fo.image_occurrence_id
)
SELECT
    io.accession_id,
    io.image_occurrence_date AS "Image date",
    modality_concept.concept_name AS "Modality",
    io_anatomic_site_concept.concept_name AS "Image occurrence anatomy",
    ife_anatomic_site_concept.concept_name AS "Image feature anatomy",
    ov.effusion AS "Effusion",
    ov.edema AS "Edema",
    ov.normal_lungs AS "Lungs in normal arrangement"
FROM
    omop.image_occurrence io
    JOIN observation_value ov ON ov.image_occurrence_id = io.image_occurrence_id
    JOIN omop.concept modality_concept ON modality_concept.concept_id = io.modality_concept_id
    JOIN omop.concept io_anatomic_site_concept ON io.anatomic_site_concept_id = io_anatomic_site_concept.concept_id
    JOIN omop.concept ife_anatomic_site_concept ON ov.anatomic_site_concept_id = ife_anatomic_site_concept.concept_id
LIMIT 300
"""

SPLEEN_QUERY = """\
SELECT *
FROM omop.image_occurrence
WHERE modality_concept_id = 4300757
LIMIT 50
"""

# (project name, description, query name, query body). Mix of xray and spleen
# so the dev list shows a representative spread of imaging projects.
DUMMY_PROJECTS = [
    (
        "Chest X-ray Effusion Detection",
        "Chest x-rays labelled for pleural effusion presence/absence.",
        "Chest x-ray observations",
        XRAY_QUERY,
    ),
    (
        "3D Spleen Segmentation",
        "Abdominal CT cohort for 3D spleen segmentation training.",
        "Abdominal CT image occurrences",
        SPLEEN_QUERY,
    ),
    (
        "Chest X-ray Pulmonary Edema Triage",
        "Chest x-rays labelled for pulmonary edema for triage scoring.",
        "Chest x-ray observations",
        XRAY_QUERY,
    ),
    (
        "Abdominal CT Spleen Volumetry",
        "CT cohort for measuring spleen volume from segmentation masks.",
        "Abdominal CT image occurrences",
        SPLEEN_QUERY,
    ),
    (
        "Chest X-ray Normal-Lung Screening",
        "Chest x-rays with labels for normal lung arrangement.",
        "Chest x-ray observations",
        XRAY_QUERY,
    ),
]


def create_unstaged_project_with(client, name, description, query_name, query):
    project_id = create_unstaged_project(client, name, description)
    if project_id is None:
        return None

    query_info = {"query": query, "name": query_name, "project_id": str(project_id)}
    save_response = add_project_query(client, query_info)
    if save_response is None:
        return project_id

    query_id = save_response.json().get("query_id")
    if not query_id:
        return project_id

    submit_query_to_trusts(
        client,
        {
            "authenticationToken": AUTH_TOKEN.get("Authorization", ""),
            "query": query,
            "name": query_name,
            "project_id": str(project_id),
            "query_id": str(query_id),
        },
    )
    return project_id


if __name__ == "__main__":
    print("Seeding 5 dummy medical-imaging projects with cohort queries…")
    print("=" * 60)

    client = requests.Session()
    client.headers.update({"Content-Type": "application/json"})

    created = []
    for name, description, query_name, query in DUMMY_PROJECTS:
        pid = create_unstaged_project_with(client, name, description, query_name, query)
        created.append((name, pid))

    print("\n" + "=" * 60)
    print("Summary:")
    for name, pid in created:
        marker = "✅" if pid else "❌"
        print(f"  {marker} {name}: {pid or 'failed'}")
