-- Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--     http://www.apache.org/licenses/LICENSE-2.0
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.

-- Biomarker causal-discovery cohort: one row per person with the baseline value of every
-- variable the causal graph is learned over (config.json's VARIABLES). Tabular only — no
-- accession_id, the project runs with imaging off.
--
-- Biomarkers, brain volumes, the cognitive score and the APOE4 allele count are stored as OMOP
-- measurements and matched on measurement_source_value (the site's own code for the assay or
-- the FreeSurfer region), so the query needs no concept/vocabulary tables. Years of education
-- is an observation. "Baseline" is each person's earliest measurement date; where several values
-- share that date the largest is taken, so the result is deterministic.
--
-- The local-simulator CSV (datasets/ad_biomarkers/make_adni_cohort.py, built from a licence
-- holder's own ADNI download) carries the same columns; tests/datasets/ad_biomarkers/ runs this
-- query on SQLite over OMOP tables written from an invented frame of that shape and diffs the two,
-- so the CSV contract and a deployed fetch cannot drift apart silently.
WITH baseline AS (
    SELECT
        m.person_id,
        m.measurement_source_value,
        MIN(m.measurement_date) AS baseline_date
    FROM
        omop.measurement m
    WHERE
        m.measurement_source_value IN (
            'APOE4_COPIES', 'PLASMA_AB42_AB40_RATIO', 'PLASMA_PTAU217', 'PLASMA_NFL',
            'PLASMA_GFAP', 'ICV_MM3', 'HIPPOCAMPUS_MM3', 'ADAS_COG13'
        )
    GROUP BY
        m.person_id, m.measurement_source_value
),
values_at_baseline AS (
    SELECT
        m.person_id,
        MAX(CASE WHEN m.measurement_source_value = 'APOE4_COPIES' THEN m.value_as_number END) AS apoe4_copies,
        MAX(CASE WHEN m.measurement_source_value = 'PLASMA_AB42_AB40_RATIO' THEN m.value_as_number END) AS plasma_ab42_ab40_ratio,
        MAX(CASE WHEN m.measurement_source_value = 'PLASMA_PTAU217' THEN m.value_as_number END) AS plasma_ptau217,
        MAX(CASE WHEN m.measurement_source_value = 'PLASMA_NFL' THEN m.value_as_number END) AS plasma_nfl,
        MAX(CASE WHEN m.measurement_source_value = 'PLASMA_GFAP' THEN m.value_as_number END) AS plasma_gfap,
        MAX(CASE WHEN m.measurement_source_value = 'ICV_MM3' THEN m.value_as_number END) AS icv_mm3,
        MAX(CASE WHEN m.measurement_source_value = 'HIPPOCAMPUS_MM3' THEN m.value_as_number END) AS hippocampus_mm3,
        MAX(CASE WHEN m.measurement_source_value = 'ADAS_COG13' THEN m.value_as_number END) AS adas_cog13,
        MIN(CASE WHEN m.measurement_source_value = 'ICV_MM3' THEN m.measurement_date END) AS mri_date
    FROM
        omop.measurement m
        JOIN baseline b
            ON b.person_id = m.person_id
            AND b.measurement_source_value = m.measurement_source_value
            AND b.baseline_date = m.measurement_date
    GROUP BY
        m.person_id
),
education AS (
    SELECT
        o.person_id,
        MAX(o.value_as_number) AS education_years
    FROM
        omop.observation o
    WHERE
        o.observation_source_value = 'EDUCATION_YEARS'
    GROUP BY
        o.person_id
)
SELECT
    p.person_id,
    CAST(SUBSTR(CAST(v.mri_date AS TEXT), 1, 4) AS INTEGER) - p.year_of_birth AS age,
    CASE WHEN p.gender_concept_id = 8532 THEN 1 ELSE 0 END AS is_female,
    e.education_years,
    v.apoe4_copies,
    v.plasma_ab42_ab40_ratio,
    v.plasma_ptau217,
    v.plasma_nfl,
    v.plasma_gfap,
    v.icv_mm3,
    v.hippocampus_mm3,
    v.adas_cog13
FROM
    omop.person p
    JOIN values_at_baseline v ON v.person_id = p.person_id
    JOIN education e ON e.person_id = p.person_id
WHERE
    v.apoe4_copies IS NOT NULL
    AND v.plasma_ab42_ab40_ratio IS NOT NULL
    AND v.plasma_ptau217 IS NOT NULL
    AND v.plasma_nfl IS NOT NULL
    AND v.plasma_gfap IS NOT NULL
    AND v.icv_mm3 IS NOT NULL
    AND v.hippocampus_mm3 IS NOT NULL
    AND v.adas_cog13 IS NOT NULL
-- Deterministic order so two fetches of the cohort agree row for row (the bootstrap streams
-- resample row positions).
ORDER BY
    p.person_id
LIMIT 5000
