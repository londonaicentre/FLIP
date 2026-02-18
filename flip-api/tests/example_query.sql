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

WITH gender_concept AS (
    SELECT concept_id, concept_name
    FROM omop.concept
    WHERE concept_name in ('Male', 'Female')
),
type_concept AS(
    SELECT concept_id, concept_name
	FROM omop.concept
	WHERE domain_id = 'Type Concept'
),
body_structure_concept AS (
    SELECT concept_id, concept_name
    FROM omop.concept
    WHERE concept_class_id = 'Body Structure'
	AND standard_concept = 'S'
),
procedure_concept AS (
    SELECT concept_id, concept_name
    FROM omop.concept
    WHERE domain_id = 'Procedure'
    AND concept_class_id in ('Procedure', 'Clinical Observation')
	AND standard_concept = 'S'
),
measurement_concept AS (
    SELECT concept_id, concept_name
	FROM omop.concept
	WHERE domain_id = 'Measurement'
),
image_feature_measurement AS (
    SELECT
        f.image_occurrence_id,
        f.image_feature_concept_id,
		mc.concept_name AS image_feature_concept_name,
        f.image_feature_event_id,
        m.measurement_concept_id,
        m.value_as_number,
        m.value_source_value
    FROM omop.image_feature f
    JOIN omop.measurement m
      ON m.measurement_id = f.image_feature_event_id
	LEFT JOIN measurement_concept mc
	  ON mc.concept_id = f.image_feature_concept_id
    WHERE f.image_feature_event_field_concept_id = 1147330
),
dicom_attributes AS (
    SELECT
        ifm.image_occurrence_id,
        MAX(CASE WHEN ifm.image_feature_concept_name = 'Slice Thickness'
                 THEN ifm.value_as_number END) AS slice_thickness_mm,
        MAX(CASE WHEN ifm.image_feature_concept_name = 'Manufacturer'
                 THEN ifm.value_source_value END) AS manufacturer
    FROM image_feature_measurement ifm
    GROUP BY ifm.image_occurrence_id
)
SELECT
    -- person
    p.person_id,
    p.person_source_value AS "Patient ID",
    gender_concept.concept_name AS "Gender",
	-- visit occurrence
    v.visit_start_date AS "Visit date",
    visit_type_concept.concept_name AS "Visit type",
	-- procedure occurrence
	pr.procedure_occurrence_id AS "Procedure ID",
    pr.procedure_date AS "Procedure date",
    procedure_concept.concept_name AS "Procedure",
    -- image occurrence
	io.accession_id,
    io.image_occurrence_date AS "Image date"	,
    modality_concept.concept_name as "Modality",
    io_anatomic_site_concept.concept_name as "Image occurrence anatomy",
	da.slice_thickness_mm AS "Slice thickness (mm)",
	da.manufacturer AS "Manufacturer"
FROM omop.person p
LEFT JOIN omop.visit_occurrence v
  ON v.person_id = p.person_id
LEFT JOIN omop.procedure_occurrence pr
  ON pr.visit_occurrence_id = v.visit_occurrence_id
LEFT JOIN omop.image_occurrence io
  ON io.procedure_occurrence_id = pr.procedure_occurrence_id
LEFT JOIN dicom_attributes da
  ON da.image_occurrence_id = io.image_occurrence_id
-- concepts
LEFT JOIN gender_concept
  ON p.gender_concept_id = gender_concept.concept_id
LEFT JOIN type_concept visit_type_concept
  ON visit_type_concept.concept_id = v.visit_type_concept_id
LEFT JOIN procedure_concept
  ON pr.procedure_concept_id = procedure_concept.concept_id
LEFT JOIN body_structure_concept io_anatomic_site_concept
  ON io.anatomic_site_concept_id = io_anatomic_site_concept.concept_id
LEFT JOIN procedure_concept modality_concept
  ON io.modality_concept_id = modality_concept.concept_id
WHERE
    procedure_concept.concept_name = 'CT Spleen'