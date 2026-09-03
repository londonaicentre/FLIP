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

-- EHR risk-prediction cohort: one row per person, pivoting pre-diagnosis clinical history
-- into the binary/count feature columns config.json's FEATURES lists, plus the label.
--
-- Codes are matched on the *_source_value SNOMED strings Synthea emits, so the query needs no
-- concept/vocabulary tables and runs on a vocabulary-free trust.
-- Features count only events strictly BEFORE the person's first type-2-diabetes diagnosis
-- (no label leakage); for never-diagnosed persons the whole history counts — see the
-- tutorial README for what a rigorous index-date design would add.
--
-- The cohort is scoped to persons with at least one recorded condition (the final
-- WHERE EXISTS). On a real trust that is a sensible "has clinical history" inclusion
-- criterion; on the FLIP dev mock it also excludes the imaging-only persons the other
-- tutorials seed (they carry visits but no conditions), so this query returns exactly the
-- Synthea EHR cohort loaded by `make -C trust load-synthea-ehr`. build_synthea_dataframe.py
-- applies the same has-a-condition filter, so the local-sim CSV and the deployed query agree.
--
-- accession_id is the person_id cast to text. Nothing reads it any more for a project created
-- with "Includes imaging data" off (the hub dispatches no imaging stage and the dev client no
-- longer requires the column); it is kept only until #1130 removes it. The app itself ignores it.
-- Ages are computed against 2023, the Synthea dataset's export year, so results are
-- deterministic (see utils/build_synthea_dataframe.py, which mirrors this feature logic).
--
-- PORTABILITY (#1148). Two things about a SNOMED code are decided by whoever built the OMOP
-- dataset, not by the clinical record, and getting either wrong costs a feature *silently* —
-- the cohort still returns the right number of rows and the column is simply 0 for everyone:
--
--   1. WHICH TABLE a code lands in. The BMI codes are SNOMED *findings*, so a domain-aware ETL
--      (OHDSI ETL-Synthea) routes them to OBSERVATION, while the AWS Synthea-in-OMOP export put
--      every code in CONDITION_OCCURRENCE with concept_id 0. The risk-factor lookups therefore
--      read BOTH tables. Measured: read only condition_occurrence against a properly ETL'd
--      dataset and has_obesity / has_severe_obesity are 0 for every person.
--   2. WHICH CODE a concept carries across Synthea versions. Essential hypertension is 38341003
--      in the published 1k export and 59621000 from Synthea v3.3.0, so both are accepted.
--
-- fl-tutorials/tests asserts every code here matches at least one person, because held-out AUROC
-- does NOT catch this: with three of the nine features dead it still scored 0.941.
WITH risk_factor_events AS (
    -- One event stream over both domain tables — see PORTABILITY note 1 above.
    SELECT
        co.person_id,
        co.condition_source_value AS source_value,
        co.condition_start_date AS event_date
    FROM
        omop.condition_occurrence co
    UNION ALL
    SELECT
        ob.person_id,
        ob.observation_source_value AS source_value,
        ob.observation_date AS event_date
    FROM
        omop.observation ob
),
first_dx AS (
    SELECT
        rf.person_id,
        MIN(rf.event_date) AS dx_date
    FROM
        risk_factor_events rf
    WHERE
        rf.source_value = '44054006' -- SNOMED: type 2 diabetes mellitus
    GROUP BY
        rf.person_id
),
prior_risk_factors AS (
    -- Pivot each person's pre-diagnosis history into binary risk-factor flags.
    SELECT
        rf.person_id,
        MAX(CASE WHEN rf.source_value = '15777000' THEN 1 ELSE 0 END) AS has_prediabetes,
        MAX(CASE WHEN rf.source_value = '162864005' THEN 1 ELSE 0 END) AS has_obesity,
        MAX(CASE WHEN rf.source_value = '408512008' THEN 1 ELSE 0 END) AS has_severe_obesity,
        -- Both spellings of essential hypertension — see PORTABILITY note 2 above.
        MAX(CASE WHEN rf.source_value IN ('38341003', '59621000') THEN 1 ELSE 0 END) AS has_hypertension,
        MAX(CASE WHEN rf.source_value = '55822004' THEN 1 ELSE 0 END) AS has_hyperlipidemia
    FROM
        risk_factor_events rf
        LEFT JOIN first_dx fd ON fd.person_id = rf.person_id
    WHERE
        fd.dx_date IS NULL
        OR rf.event_date < fd.dx_date
    GROUP BY
        rf.person_id
),
prior_condition_counts AS (
    -- Deliberately condition_occurrence ONLY, unlike the flag lookups above. Folding
    -- observation in here would count Synthea's socioeconomic findings (employment status,
    -- housing, social isolation) as comorbidities, so the feature would mean one thing on a
    -- domain-aware dataset and another on a flat one. A comorbidity count must stay conditions.
    SELECT
        co.person_id,
        COUNT(DISTINCT co.condition_source_value) AS n_prior_conditions
    FROM
        omop.condition_occurrence co
        LEFT JOIN first_dx fd ON fd.person_id = co.person_id
    WHERE
        fd.dx_date IS NULL
        OR co.condition_start_date < fd.dx_date
    GROUP BY
        co.person_id
),
prior_visits AS (
    SELECT
        vo.person_id,
        COUNT(*) AS n_prior_visits
    FROM
        omop.visit_occurrence vo
        LEFT JOIN first_dx fd ON fd.person_id = vo.person_id
    WHERE
        fd.dx_date IS NULL
        OR vo.visit_start_date < fd.dx_date
    GROUP BY
        vo.person_id
)
SELECT
    p.person_id,
    CAST(p.person_id AS VARCHAR) AS accession_id,
    2023 - p.year_of_birth AS age,
    CASE WHEN p.gender_concept_id = 8532 THEN 1 ELSE 0 END AS is_female,
    COALESCE(rf.has_prediabetes, 0) AS has_prediabetes,
    COALESCE(rf.has_obesity, 0) AS has_obesity,
    COALESCE(rf.has_severe_obesity, 0) AS has_severe_obesity,
    COALESCE(rf.has_hypertension, 0) AS has_hypertension,
    COALESCE(rf.has_hyperlipidemia, 0) AS has_hyperlipidemia,
    COALESCE(pcc.n_prior_conditions, 0) AS n_prior_conditions,
    COALESCE(pv.n_prior_visits, 0) AS n_prior_visits,
    CASE WHEN fd.person_id IS NOT NULL THEN 1 ELSE 0 END AS label_t2dm
FROM
    omop.person p
    LEFT JOIN prior_risk_factors rf ON rf.person_id = p.person_id
    LEFT JOIN prior_condition_counts pcc ON pcc.person_id = p.person_id
    LEFT JOIN prior_visits pv ON pv.person_id = p.person_id
    LEFT JOIN first_dx fd ON fd.person_id = p.person_id
WHERE
    EXISTS (SELECT 1 FROM omop.condition_occurrence coe WHERE coe.person_id = p.person_id)
-- Load-bearing, not cosmetic: without it the LIMIT below picks an unspecified 5000 rows in an
-- unspecified order, so two fetches of the same cohort can disagree. The Flower app fetches once
-- for training and again for evaluation and splits on person_id, so a differing row set would
-- shrink the held-out split rather than corrupt it -- but a stable order keeps the two fetches
-- identical, which is what makes a run reproducible.
ORDER BY
    p.person_id
LIMIT 5000
