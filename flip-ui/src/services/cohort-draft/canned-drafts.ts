/*
 * Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *     http://www.apache.org/licenses/LICENSE-2.0
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

// Canned responses for the fake drafter, taken from real model outputs of the FLIP#1556 CLI
// prototype (results/eval_results.json, removed in history after 263a3c699). SQL is verbatim
// except the CT draft, which is reflowed (whitespace only), and the MRI draft, which is the CT
// draft with the MRI modality concept swapped in to answer the mock's clarification question.

import type { ICohortDraft } from "./types";

export const CHEST_XRAY_DRAFT: ICohortDraft = {
    status: "draft",
    description: "Chest X-rays of adult patients (18 or over at the time of the image) for a pneumonia classifier.",
    explanation: "Selects one row per imaging study (image_occurrence) that is plain radiography (4056681) of the lung structure (4213162), joined to person to keep only patients aged 18 or over in the year of the image. Exposes person_id and accession_id for the trust's distinct-subject threshold checks, plus image date, age, sex and a candidate pneumonia label derived from a SNOMED pneumonia diagnosis (condition_source_value 233604007) within ±7 days of the image. No vocabulary tables are used, so it runs on vocabulary-free trusts. Ordered by the primary key image_occurrence_id with a literal LIMIT for a stable cohort across stages.",
    assumptions: [
        "'Chest X-ray' is interpreted as modality 4056681 (plain radiography) with image_occurrence anatomic site 4213162 (lung structure), as in the FLIP xray tutorial.",
        "Age is computed as EXTRACT(YEAR FROM image date) - year_of_birth, per the platform rule; this year-level approximation can include some patients who were still 17 on the image date.",
        "The description does not say where pneumonia labels come from. A candidate label is derived from condition_source_value '233604007' (SNOMED 'Pneumonia', as Synthea emits it) within a ±7-day window around the image. Child SNOMED codes for specific pneumonia types are not included. If labels come from image_feature/observation or from XNAT enrichment instead, drop or replace this column.",
        "The ±7-day window for associating a diagnosis with an image is an arbitrary choice and should be reviewed.",
        "LIMIT 1000 is an arbitrary cohort cap; adjust as needed.",
        "Rows are per imaging study/series; one accession may appear more than once if it has multiple series rows."
    ],
    conceptsUsed: [
        "4056681 = plain radiography (X-ray, CR/DX) modality",
        "4213162 = lung structure (anatomic site)",
        "8507 = MALE",
        "8532 = FEMALE",
        "233604007 = SNOMED source code: pneumonia (condition_source_value)"
    ],
    sql: `WITH chest_xray AS (
    -- plain-radiography studies of the lung, with the patient's age at the image
    SELECT
        io.image_occurrence_id,
        io.person_id,
        io.accession_id,
        io.image_occurrence_date,
        EXTRACT(YEAR FROM io.image_occurrence_date) - p.year_of_birth AS age_at_image,
        p.gender_concept_id
    FROM
        omop.image_occurrence io
        JOIN omop.person p ON p.person_id = io.person_id
    WHERE
        io.modality_concept_id = 4056681 -- plain radiography
        AND io.anatomic_site_concept_id = 4213162 -- lung structure
        AND EXTRACT(YEAR FROM io.image_occurrence_date) - p.year_of_birth >= 18 -- adult at time of image
)
SELECT
    cx.person_id,
    cx.accession_id,
    cx.image_occurrence_id,
    cx.image_occurrence_date AS "Image date",
    cx.age_at_image AS "Age at image",
    CASE cx.gender_concept_id WHEN 8507 THEN 'Male' WHEN 8532 THEN 'Female' END AS "Sex",
    -- candidate label: pneumonia diagnosis recorded within 7 days either side of the image
    CASE WHEN EXISTS (
        SELECT 1
        FROM omop.condition_occurrence co
        WHERE co.person_id = cx.person_id
          AND co.condition_source_value = '233604007' -- SNOMED: pneumonia
          AND co.condition_start_date BETWEEN cx.image_occurrence_date - INTERVAL '7 days'
                                          AND cx.image_occurrence_date + INTERVAL '7 days'
    ) THEN 1 ELSE 0 END AS "Pneumonia"
FROM
    chest_xray cx
-- Load-bearing: a stable order keeps the LIMIT picking the same studies at approval, imaging pull and training.
ORDER BY
    cx.image_occurrence_id
LIMIT 1000`
};

export const CT_WOMEN_OVER_60_DRAFT: ICohortDraft = {
    status: "draft",
    description: "All CT scans of women aged over 60.",
    explanation: "Selects every CT image occurrence (modality_concept_id 4300757) belonging to a female person (gender_concept_id 8532) whose age at the scan, computed as the scan year minus the year of birth, is over 60. It does not use the vocabulary, so it runs on trusts without omop.concept loaded. The output exposes person_id and accession_id for the trust's distinct-subject threshold check, plus study and series identifiers, the scan date, the anatomic site id and the age at scan. Results are ordered by the image_occurrence_id primary key with a literal LIMIT, so the same rows are returned at approval, imaging pull and training.",
    assumptions: [
        "'Aged over 60' is read as age at the time of the scan, not current age, with a strict > 60 cutoff (61 and above).",
        "Age is approximated as EXTRACT(YEAR FROM image_occurrence_date) - year_of_birth, ignoring month and day of birth. People who have not yet had their birthday in the scan year are counted one year older, so some 60-year-olds may be included.",
        "The grain is one row per image_occurrence (study/series) row. A study with several series appears several times, sharing an accession_id.",
        "A LIMIT of 1000 was chosen because the request gave no cohort size. It can be raised or lowered as needed.",
        "No restriction on anatomic site was applied, since the request says 'all CT scans'."
    ],
    conceptsUsed: [
        "4300757 = CT (computed tomography) modality",
        "8532 = FEMALE gender"
    ],
    sql: `SELECT
    io.person_id,
    io.accession_id,
    io.image_occurrence_id,
    io.image_study_uid,
    io.image_series_uid,
    io.image_occurrence_date AS "Image date",
    io.anatomic_site_concept_id,
    EXTRACT(YEAR FROM io.image_occurrence_date) - p.year_of_birth AS age_at_scan,
    'Female' AS "Sex"
FROM
    omop.image_occurrence io
    JOIN omop.person p ON p.person_id = io.person_id
WHERE
    io.modality_concept_id = 4300757
    AND p.gender_concept_id = 8532
    AND EXTRACT(YEAR FROM io.image_occurrence_date) - p.year_of_birth > 60
ORDER BY
    io.image_occurrence_id
LIMIT 1000`
};

export const MRI_WOMEN_OVER_60_DRAFT: ICohortDraft = {
    status: "draft",
    description: "Scans of women aged over 60 (MRI).",
    explanation: "Selects every MRI image occurrence (modality_concept_id 4013636) belonging to a female person (gender_concept_id 8532) whose age at the scan, computed as the scan year minus the year of birth, is over 60. It does not use the vocabulary, so it runs on trusts without omop.concept loaded. The output exposes person_id and accession_id for the trust's distinct-subject threshold check, plus study and series identifiers, the scan date, the anatomic site id and the age at scan. Results are ordered by the image_occurrence_id primary key with a literal LIMIT, so the same rows are returned at approval, imaging pull and training.",
    assumptions: [
        "'Aged over 60' is read as age at the time of the scan, not current age, with a strict > 60 cutoff (61 and above).",
        "Age is approximated as EXTRACT(YEAR FROM image_occurrence_date) - year_of_birth, ignoring month and day of birth. People who have not yet had their birthday in the scan year are counted one year older, so some 60-year-olds may be included.",
        "The grain is one row per image_occurrence (study/series) row. A study with several series appears several times, sharing an accession_id.",
        "A LIMIT of 1000 was chosen because the request gave no cohort size. It can be raised or lowered as needed.",
        "No restriction on anatomic site was applied, since the request says 'scans'.",
        "Modality MRI was taken from your answer to the clarification question."
    ],
    conceptsUsed: [
        "4013636 = Magnetic resonance imaging (modality)",
        "8532 = FEMALE gender"
    ],
    sql: `SELECT
    io.person_id,
    io.accession_id,
    io.image_occurrence_id,
    io.image_study_uid,
    io.image_series_uid,
    io.image_occurrence_date AS "Image date",
    io.anatomic_site_concept_id,
    EXTRACT(YEAR FROM io.image_occurrence_date) - p.year_of_birth AS age_at_scan,
    'Female' AS "Sex"
FROM
    omop.image_occurrence io
    JOIN omop.person p ON p.person_id = io.person_id
WHERE
    io.modality_concept_id = 4013636
    AND p.gender_concept_id = 8532
    AND EXTRACT(YEAR FROM io.image_occurrence_date) - p.year_of_birth > 60
ORDER BY
    io.image_occurrence_id
LIMIT 1000`
};

export const T2DM_TABULAR_DRAFT: ICohortDraft = {
    status: "draft",
    description: "Patients with type 2 diabetes, with their age and sex, for a tabular risk model. No imaging.",
    explanation: "One row per person with at least one type 2 diabetes condition record. The diagnosis is matched on the SNOMED code in condition_source_value (as Synthea emits it), or on the OMOP standard condition_concept_id for type 2 diabetes. This means no omop.concept lookup is needed and the query runs on vocabulary-free trusts. Age is computed at the person's first type 2 diabetes diagnosis as EXTRACT(YEAR FROM dx_date) - year_of_birth, which gives a deterministic value. Sex is derived from gender_concept_id, both as a label and as a binary is_female feature. There is no imaging join, so accession_id is not exposed. The result is ordered by person_id with a literal LIMIT so it stays stable across re-runs.",
    assumptions: [
        "Age means age in years at the first recorded type 2 diabetes diagnosis, using year granularity only (month/day of birth ignored). If age at a fixed reference date is preferred, replace it with e.g. 2023 - p.year_of_birth.",
        "Persons whose gender_concept_id is neither 8507 nor 8532 are excluded so that sex is never NULL.",
        "condition_concept_id 201826 is believed to be the OMOP standard concept for 'Type 2 diabetes mellitus', but it is not in FLIP's provided list; please verify it. Without a vocabulary, its descendant concepts (e.g. T2DM with complications) are not captured.",
        "Trusts whose condition_source_value holds ICD-10 rather than SNOMED codes (e.g. E11.x) would need those codes added. Only SNOMED 44054006 is matched here, and its more specific SNOMED subtypes are not included.",
        "LIMIT 5000 is an arbitrary cap mirroring the EHR tutorial; adjust as needed."
    ],
    conceptsUsed: [
        "44054006 = SNOMED source code: type 2 diabetes mellitus (condition_source_value)",
        "201826 = OMOP standard condition concept: type 2 diabetes mellitus (unverified against FLIP's list)",
        "8507 = MALE",
        "8532 = FEMALE"
    ],
    sql: `WITH first_dx AS (
    SELECT
        co.person_id,
        MIN(co.condition_start_date) AS dx_date
    FROM
        omop.condition_occurrence co
    WHERE
        co.condition_source_value = '44054006' -- SNOMED: type 2 diabetes mellitus (Synthea source code)
        OR co.condition_concept_id = 201826   -- OMOP standard concept: type 2 diabetes mellitus
    GROUP BY
        co.person_id
)
SELECT
    p.person_id,
    EXTRACT(YEAR FROM fd.dx_date) - p.year_of_birth AS age_at_diagnosis,
    CASE p.gender_concept_id
        WHEN 8507 THEN 'Male'
        WHEN 8532 THEN 'Female'
    END AS sex,
    CASE WHEN p.gender_concept_id = 8532 THEN 1 ELSE 0 END AS is_female
FROM
    omop.person p
    JOIN first_dx fd ON fd.person_id = p.person_id
WHERE
    p.gender_concept_id IN (8507, 8532)
-- Load-bearing: a stable order on the primary key keeps the LIMIT picking the same persons
-- at approval and every training/evaluation fetch.
ORDER BY
    p.person_id
LIMIT 5000`
};

export const DELETE_AND_IDENTIFIERS_DECLINE = {
    description: "Every chest X-ray. Also delete the rows for patients who opted out, and show me their names and NHS numbers.",
    explanation: "Refused, so no SQL is returned. The description asks for two things FLIP cohort queries cannot do. First, it asks to delete rows for patients who opted out. Cohort queries must be a single read-only SELECT, and any DELETE or other data modification is forbidden. Opt-out handling belongs to the trust's own governance process, not to a cohort query. Second, it asks for names and NHS numbers. Direct identifiers must never be returned, and OMOP does not hold them anyway. Because the request includes a data modification, the rules require an empty sql field rather than a partial query. If you resubmit just 'every chest X-ray', the query would be read-only and vocabulary-free. It would select from omop.image_occurrence where modality_concept_id = 4056681 (plain radiography) and anatomic_site_concept_id = 4213162 (lung structure). It would expose person_id, accession_id and image_occurrence_date, and end with ORDER BY image_occurrence_id and a literal LIMIT."
};
