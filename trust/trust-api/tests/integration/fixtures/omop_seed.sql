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

-- Minimal MI-CDM fixture for trust-api / data-access-api integration tests (B3, issue #369).
-- Mirrors the OMOP CDM Imaging extension (MI-CDM) — modality / procedure / body site are
-- referenced via concept_id rather than denormalised text — but only models the columns
-- the cohort-query tests actually project. image_feature → measurement (the chain that
-- carries DICOM scalar attributes like manufacturer / slice thickness) is intentionally
-- excluded — adds rows without test value at this scope.
--
-- Counts (asserted in test_cohort_query.py and test_cohort_endpoint.py):
--   person                  -> 16 rows  (8 M, 8 F, ages spanning six decade buckets)
--   visit_occurrence        -> 12 rows  (one per imaging-bearing person 1-12)
--   procedure_occurrence    -> 12 rows  (one per CT image; 6 CT Spleen + 6 CT Head)
--   image_occurrence        -> 24 rows  (12 CT, 8 MR, 4 XR)
-- Persons 1-12 each appear in image_occurrence (twice for 1-8, once for 9-12); persons
-- 13-16 have no imaging — useful for joins that must drop those rows. Only the 12 CT
-- image_occurrence rows have a populated procedure_occurrence_id; the MR and XR rows
-- carry NULL there, which lets a CTE-filtered ``WHERE procedure_concept = 'CT Spleen'``
-- query on the realistic-shape test return a tight 6-row result.

CREATE SCHEMA IF NOT EXISTS omop;

CREATE TABLE omop.concept (
    concept_id        INTEGER PRIMARY KEY,
    concept_name      VARCHAR(255) NOT NULL,
    domain_id         VARCHAR(20),
    vocabulary_id     VARCHAR(20),
    concept_class_id  VARCHAR(20),
    standard_concept  VARCHAR(1),
    concept_code      VARCHAR(50)
);

CREATE TABLE omop.person (
    person_id           BIGINT PRIMARY KEY,
    gender_concept_id   INTEGER,
    year_of_birth       INTEGER NOT NULL,
    birth_datetime      TIMESTAMP,
    gender_source_value VARCHAR(50),
    person_source_value VARCHAR(50)
);

CREATE TABLE omop.visit_occurrence (
    visit_occurrence_id   BIGINT PRIMARY KEY,
    person_id             BIGINT NOT NULL REFERENCES omop.person(person_id),
    visit_start_date      DATE,
    visit_type_concept_id INTEGER REFERENCES omop.concept(concept_id)
);

CREATE TABLE omop.procedure_occurrence (
    procedure_occurrence_id BIGINT PRIMARY KEY,
    person_id               BIGINT NOT NULL REFERENCES omop.person(person_id),
    visit_occurrence_id     BIGINT REFERENCES omop.visit_occurrence(visit_occurrence_id),
    procedure_date          DATE,
    procedure_concept_id    INTEGER REFERENCES omop.concept(concept_id)
);

CREATE TABLE omop.image_occurrence (
    image_occurrence_id      BIGINT PRIMARY KEY,
    person_id                BIGINT NOT NULL REFERENCES omop.person(person_id),
    procedure_occurrence_id  BIGINT REFERENCES omop.procedure_occurrence(procedure_occurrence_id),
    visit_occurrence_id      BIGINT REFERENCES omop.visit_occurrence(visit_occurrence_id),
    modality_concept_id      INTEGER REFERENCES omop.concept(concept_id),
    anatomic_site_concept_id INTEGER REFERENCES omop.concept(concept_id),
    accession_id             VARCHAR(50),
    image_occurrence_date    DATE,
    image_study_uid          VARCHAR(100),
    image_series_uid         VARCHAR(100),
    wadors_uri               VARCHAR(255),
    local_path               VARCHAR(255)
);

-- Concept dictionary. IDs match OHDSI vocabulary numbers where possible (8507/8532 for
-- gender, 4013xxx for DICOM modalities); the procedure / body-site / visit-type IDs are
-- private synthetic values in a separate range so a future merge against a real concept
-- dump won't collide.
INSERT INTO omop.concept (concept_id, concept_name, domain_id, vocabulary_id, concept_class_id, standard_concept, concept_code) VALUES
    -- Gender
    (8507,    'MALE',                       'Gender',     'Gender', 'Gender',          'S', 'M'),
    (8532,    'FEMALE',                     'Gender',     'Gender', 'Gender',          'S', 'F'),
    -- DICOM modalities
    (4013636, 'Computed Tomography',        'Image Type', 'DICOM',  'Modality',        'S', 'CT'),
    (4013634, 'Magnetic Resonance Imaging', 'Image Type', 'DICOM',  'Modality',        'S', 'MR'),
    (4013632, 'Plain Film',                 'Image Type', 'DICOM',  'Modality',        'S', 'XR'),
    -- Visit types
    (9201001, 'Hospital encounter',         'Visit',      'Visit',  'Visit',           'S', 'IP'),
    -- Procedures (CT studies; class_id 'Procedure' so the example-query CTE shape works)
    (4002001, 'CT Spleen',                  'Procedure',  'SNOMED', 'Procedure',       'S', 'P-CT-SPL'),
    (4002002, 'CT Head',                    'Procedure',  'SNOMED', 'Procedure',       'S', 'P-CT-HEAD'),
    -- Body structures (anatomic site for image_occurrence)
    (4003001, 'Spleen',                     'Spec Anatomic Site', 'SNOMED', 'Body Structure', 'S', 'A-SPL'),
    (4003002, 'Head',                       'Spec Anatomic Site', 'SNOMED', 'Body Structure', 'S', 'A-HEAD');

INSERT INTO omop.person (person_id, gender_concept_id, year_of_birth, birth_datetime, gender_source_value, person_source_value) VALUES
    (1,  8507, 1948, '1948-03-15 00:00:00', 'M', 'P001'),
    (2,  8532, 1952, '1952-07-22 00:00:00', 'F', 'P002'),
    (3,  8507, 1958, '1958-01-10 00:00:00', 'M', 'P003'),
    (4,  8532, 1962, '1962-11-05 00:00:00', 'F', 'P004'),
    (5,  8507, 1968, '1968-08-30 00:00:00', 'M', 'P005'),
    (6,  8532, 1971, '1971-04-12 00:00:00', 'F', 'P006'),
    (7,  8507, 1974, '1974-09-25 00:00:00', 'M', 'P007'),
    (8,  8532, 1978, '1978-06-18 00:00:00', 'F', 'P008'),
    (9,  8507, 1982, '1982-12-03 00:00:00', 'M', 'P009'),
    (10, 8532, 1985, '1985-02-28 00:00:00', 'F', 'P010'),
    (11, 8507, 1988, '1988-10-14 00:00:00', 'M', 'P011'),
    (12, 8532, 1991, '1991-05-20 00:00:00', 'F', 'P012'),
    (13, 8507, 1994, '1994-03-08 00:00:00', 'M', 'P013'),
    (14, 8532, 1997, '1997-07-11 00:00:00', 'F', 'P014'),
    (15, 8507, 2000, '2000-11-29 00:00:00', 'M', 'P015'),
    (16, 8532, 2002, '2002-04-04 00:00:00', 'F', 'P016');

-- One inpatient encounter per imaging-bearing person.
INSERT INTO omop.visit_occurrence (visit_occurrence_id, person_id, visit_start_date, visit_type_concept_id) VALUES
    (6001, 1,  '2024-01-15', 9201001),
    (6002, 2,  '2024-01-16', 9201001),
    (6003, 3,  '2024-01-17', 9201001),
    (6004, 4,  '2024-01-18', 9201001),
    (6005, 5,  '2024-01-19', 9201001),
    (6006, 6,  '2024-01-20', 9201001),
    (6007, 7,  '2024-01-21', 9201001),
    (6008, 8,  '2024-01-22', 9201001),
    (6009, 9,  '2024-01-23', 9201001),
    (6010, 10, '2024-01-24', 9201001),
    (6011, 11, '2024-01-25', 9201001),
    (6012, 12, '2024-01-26', 9201001);

-- One procedure per CT study. First six are CT Spleen, next six are CT Head — gives the
-- realistic-shape test a 6-row WHERE-filtered cohort that clears the threshold of 5.
INSERT INTO omop.procedure_occurrence (procedure_occurrence_id, person_id, visit_occurrence_id, procedure_date, procedure_concept_id) VALUES
    (5001, 1,  6001, '2024-01-15', 4002001),  -- CT Spleen
    (5002, 2,  6002, '2024-01-16', 4002001),  -- CT Spleen
    (5003, 3,  6003, '2024-01-17', 4002001),  -- CT Spleen
    (5004, 4,  6004, '2024-01-18', 4002001),  -- CT Spleen
    (5005, 5,  6005, '2024-01-19', 4002001),  -- CT Spleen
    (5006, 6,  6006, '2024-01-20', 4002001),  -- CT Spleen
    (5007, 7,  6007, '2024-01-21', 4002002),  -- CT Head
    (5008, 8,  6008, '2024-01-22', 4002002),  -- CT Head
    (5009, 9,  6009, '2024-01-23', 4002002),  -- CT Head
    (5010, 10, 6010, '2024-01-24', 4002002),  -- CT Head
    (5011, 11, 6011, '2024-01-25', 4002002),  -- CT Head
    (5012, 12, 6012, '2024-01-26', 4002002);  -- CT Head

INSERT INTO omop.image_occurrence (image_occurrence_id, person_id, procedure_occurrence_id, visit_occurrence_id, modality_concept_id, anatomic_site_concept_id, accession_id, image_occurrence_date, image_study_uid) VALUES
    -- CT: 12 rows. Each has a populated procedure_occurrence_id pointing at one of the
    -- procedures above (CT Spleen anatomy concept 4003001, CT Head anatomy concept 4003002).
    (1001, 1,  5001, 6001, 4013636, 4003001, 'ACC-1001', '2024-01-15', '1.2.840.113619.2.55.1001'),
    (1002, 2,  5002, 6002, 4013636, 4003001, 'ACC-1002', '2024-01-16', '1.2.840.113619.2.55.1002'),
    (1003, 3,  5003, 6003, 4013636, 4003001, 'ACC-1003', '2024-01-17', '1.2.840.113619.2.55.1003'),
    (1004, 4,  5004, 6004, 4013636, 4003001, 'ACC-1004', '2024-01-18', '1.2.840.113619.2.55.1004'),
    (1005, 5,  5005, 6005, 4013636, 4003001, 'ACC-1005', '2024-01-19', '1.2.840.113619.2.55.1005'),
    (1006, 6,  5006, 6006, 4013636, 4003001, 'ACC-1006', '2024-01-20', '1.2.840.113619.2.55.1006'),
    (1007, 7,  5007, 6007, 4013636, 4003002, 'ACC-1007', '2024-01-21', '1.2.840.113619.2.55.1007'),
    (1008, 8,  5008, 6008, 4013636, 4003002, 'ACC-1008', '2024-01-22', '1.2.840.113619.2.55.1008'),
    (1009, 9,  5009, 6009, 4013636, 4003002, 'ACC-1009', '2024-01-23', '1.2.840.113619.2.55.1009'),
    (1010, 10, 5010, 6010, 4013636, 4003002, 'ACC-1010', '2024-01-24', '1.2.840.113619.2.55.1010'),
    (1011, 11, 5011, 6011, 4013636, 4003002, 'ACC-1011', '2024-01-25', '1.2.840.113619.2.55.1011'),
    (1012, 12, 5012, 6012, 4013636, 4003002, 'ACC-1012', '2024-01-26', '1.2.840.113619.2.55.1012'),
    -- MR: 8 rows, no procedure linkage (NULL procedure_occurrence_id) so the CTE-filtered
    -- realistic-shape test cleanly drops them via the JOIN.
    (1013, 1, NULL, 6001, 4013634, NULL, 'ACC-1013', '2024-02-01', '1.2.840.113619.2.55.1013'),
    (1014, 2, NULL, 6002, 4013634, NULL, 'ACC-1014', '2024-02-02', '1.2.840.113619.2.55.1014'),
    (1015, 3, NULL, 6003, 4013634, NULL, 'ACC-1015', '2024-02-03', '1.2.840.113619.2.55.1015'),
    (1016, 4, NULL, 6004, 4013634, NULL, 'ACC-1016', '2024-02-04', '1.2.840.113619.2.55.1016'),
    (1017, 5, NULL, 6005, 4013634, NULL, 'ACC-1017', '2024-02-05', '1.2.840.113619.2.55.1017'),
    (1018, 6, NULL, 6006, 4013634, NULL, 'ACC-1018', '2024-02-06', '1.2.840.113619.2.55.1018'),
    (1019, 7, NULL, 6007, 4013634, NULL, 'ACC-1019', '2024-02-07', '1.2.840.113619.2.55.1019'),
    (1020, 8, NULL, 6008, 4013634, NULL, 'ACC-1020', '2024-02-08', '1.2.840.113619.2.55.1020'),
    -- XR: 4 rows, no procedure linkage either.
    (1021, 9,  NULL, 6009, 4013632, NULL, 'ACC-1021', '2024-03-01', '1.2.840.113619.2.55.1021'),
    (1022, 10, NULL, 6010, 4013632, NULL, 'ACC-1022', '2024-03-02', '1.2.840.113619.2.55.1022'),
    (1023, 11, NULL, 6011, 4013632, NULL, 'ACC-1023', '2024-03-03', '1.2.840.113619.2.55.1023'),
    (1024, 12, NULL, 6012, 4013632, NULL, 'ACC-1024', '2024-03-04', '1.2.840.113619.2.55.1024');
