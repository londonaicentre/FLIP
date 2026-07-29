-- Copyright 2024 Guy’s and St Thomas’ NHS Foundation Trust
--
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--
--     http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.

set schema 'omop';

/*postgresql OMOP CDM Indices

  There are no unique indices created because it is assumed that the primary key constraints have been run prior to
  implementing indices.
*/


/************************

Standardized clinical data

************************/
DO $$
BEGIN

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_person_id')
  THEN
    CREATE INDEX idx_person_id  ON person  (person_id ASC);
    CLUSTER person  USING idx_person_id ;
    CREATE INDEX idx_gender ON person (gender_concept_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_observation_period_id_1')
  THEN
    CREATE INDEX idx_observation_period_id_1  ON observation_period  (person_id ASC);
    CLUSTER observation_period  USING idx_observation_period_id_1 ;
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_visit_person_id_1')
  THEN
    CREATE INDEX idx_visit_person_id_1  ON visit_occurrence  (person_id ASC);
    CLUSTER visit_occurrence  USING idx_visit_person_id_1 ;
    CREATE INDEX idx_visit_concept_id_1 ON visit_occurrence (visit_concept_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_visit_det_person_id_1')
  THEN
    CREATE INDEX idx_visit_det_person_id_1  ON visit_detail  (person_id ASC);
    CLUSTER visit_detail  USING idx_visit_det_person_id_1 ;
    CREATE INDEX idx_visit_det_concept_id_1 ON visit_detail (visit_detail_concept_id ASC);
    CREATE INDEX idx_visit_det_occ_id ON visit_detail (visit_occurrence_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_condition_person_id_1')
  THEN
    CREATE INDEX idx_condition_person_id_1  ON condition_occurrence  (person_id ASC);
    CLUSTER condition_occurrence  USING idx_condition_person_id_1 ;
    CREATE INDEX idx_condition_concept_id_1 ON condition_occurrence (condition_concept_id ASC);
    CREATE INDEX idx_condition_visit_id_1 ON condition_occurrence (visit_occurrence_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_drug_person_id_1')
  THEN
    CREATE INDEX idx_drug_person_id_1  ON drug_exposure  (person_id ASC);
    CLUSTER drug_exposure  USING idx_drug_person_id_1 ;
    CREATE INDEX idx_drug_concept_id_1 ON drug_exposure (drug_concept_id ASC);
    CREATE INDEX idx_drug_visit_id_1 ON drug_exposure (visit_occurrence_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_procedure_person_id_1')
  THEN
    CREATE INDEX idx_procedure_person_id_1  ON procedure_occurrence  (person_id ASC);
    CLUSTER procedure_occurrence  USING idx_procedure_person_id_1 ;
    CREATE INDEX idx_procedure_concept_id_1 ON procedure_occurrence (procedure_concept_id ASC);
    CREATE INDEX idx_procedure_visit_id_1 ON procedure_occurrence (visit_occurrence_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_device_person_id_1')
  THEN
    CREATE INDEX idx_device_person_id_1  ON device_exposure  (person_id ASC);
    CLUSTER device_exposure  USING idx_device_person_id_1 ;
    CREATE INDEX idx_device_concept_id_1 ON device_exposure (device_concept_id ASC);
    CREATE INDEX idx_device_visit_id_1 ON device_exposure (visit_occurrence_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_measurement_person_id_1')
  THEN
    CREATE INDEX idx_measurement_person_id_1  ON measurement  (person_id ASC);
    CLUSTER measurement  USING idx_measurement_person_id_1 ;
    CREATE INDEX idx_measurement_concept_id_1 ON measurement (measurement_concept_id ASC);
    CREATE INDEX idx_measurement_visit_id_1 ON measurement (visit_occurrence_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_observation_person_id_1')
  THEN
    CREATE INDEX idx_observation_person_id_1  ON observation  (person_id ASC);
    CLUSTER observation  USING idx_observation_person_id_1 ;
    CREATE INDEX idx_observation_concept_id_1 ON observation (observation_concept_id ASC);
    CREATE INDEX idx_observation_visit_id_1 ON observation (visit_occurrence_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_death_person_id_1')
  THEN
    CREATE INDEX idx_death_person_id_1  ON death  (person_id ASC);
    CLUSTER death  USING idx_death_person_id_1 ;
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_note_person_id_1')
  THEN
    CREATE INDEX idx_note_person_id_1  ON note  (person_id ASC);
    CLUSTER note  USING idx_note_person_id_1 ;
    CREATE INDEX idx_note_concept_id_1 ON note (note_type_concept_id ASC);
    CREATE INDEX idx_note_visit_id_1 ON note (visit_occurrence_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_note_nlp_note_id_1')
  THEN
    CREATE INDEX idx_note_nlp_note_id_1  ON note_nlp  (note_id ASC);
    CLUSTER note_nlp  USING idx_note_nlp_note_id_1 ;
    CREATE INDEX idx_note_nlp_concept_id_1 ON note_nlp (note_nlp_concept_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_specimen_person_id_1')
  THEN
    CREATE INDEX idx_specimen_person_id_1  ON specimen  (person_id ASC);
    CLUSTER specimen  USING idx_specimen_person_id_1 ;
    CREATE INDEX idx_specimen_concept_id_1 ON specimen (specimen_concept_id ASC);
  END IF;


  CREATE INDEX IF NOT EXISTS idx_fact_relationship_id1 ON fact_relationship (domain_concept_id_1 ASC);
  CREATE INDEX IF NOT EXISTS idx_fact_relationship_id2 ON fact_relationship (domain_concept_id_2 ASC);
  CREATE INDEX IF NOT EXISTS idx_fact_relationship_id3 ON fact_relationship (relationship_concept_id ASC);

  /************************

  Standardized health system data

  ************************/

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_location_id_1')
  THEN
    CREATE INDEX idx_location_id_1  ON location  (location_id ASC);
    CLUSTER location  USING idx_location_id_1 ;
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_care_site_id_1')
  THEN
    CREATE INDEX idx_care_site_id_1  ON care_site  (care_site_id ASC);
    CLUSTER care_site  USING idx_care_site_id_1 ;
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_provider_id_1')
  THEN
    CREATE INDEX idx_provider_id_1  ON provider  (provider_id ASC);
    CLUSTER provider  USING idx_provider_id_1 ;
  END IF;

  /************************

  Standardized health economics

  ************************/

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_period_person_id_1')
  THEN
    CREATE INDEX idx_period_person_id_1  ON payer_plan_period  (person_id ASC);
    CLUSTER payer_plan_period  USING idx_period_person_id_1 ;
  END IF;

  CREATE INDEX IF NOT EXISTS idx_cost_event_id  ON cost (cost_event_id ASC);

  /************************

  Standardized derived elements

  ************************/

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_drug_era_person_id_1')
  THEN
    CREATE INDEX idx_drug_era_person_id_1  ON drug_era  (person_id ASC);
    CLUSTER drug_era  USING idx_drug_era_person_id_1 ;
    CREATE INDEX idx_drug_era_concept_id_1 ON drug_era (drug_concept_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_dose_era_person_id_1')
  THEN
    CREATE INDEX idx_dose_era_person_id_1  ON dose_era  (person_id ASC);
    CLUSTER dose_era  USING idx_dose_era_person_id_1 ;
    CREATE INDEX idx_dose_era_concept_id_1 ON dose_era (drug_concept_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_condition_era_person_id_1')
  THEN
    CREATE INDEX idx_condition_era_person_id_1  ON condition_era  (person_id ASC);
    CLUSTER condition_era  USING idx_condition_era_person_id_1 ;
    CREATE INDEX idx_condition_era_concept_id_1 ON condition_era (condition_concept_id ASC);
  END IF;

  /**************************

  Standardized meta-data

  ***************************/

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_metadata_concept_id_1')
  THEN
    CREATE INDEX idx_metadata_concept_id_1  ON metadata  (metadata_concept_id ASC);
    CLUSTER metadata  USING idx_metadata_concept_id_1 ;
  END IF;

  /**************************

  Standardized vocabularies

  ***************************/

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_concept_concept_id')
  THEN
    CREATE INDEX idx_concept_concept_id  ON concept  (concept_id ASC);
    CLUSTER concept  USING idx_concept_concept_id ;
    CREATE INDEX idx_concept_code ON concept (concept_code ASC);
    CREATE INDEX idx_concept_vocabluary_id ON concept (vocabulary_id ASC);
    CREATE INDEX idx_concept_domain_id ON concept (domain_id ASC);
    CREATE INDEX idx_concept_class_id ON concept (concept_class_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_vocabulary_vocabulary_id')
  THEN
    CREATE INDEX idx_vocabulary_vocabulary_id  ON vocabulary  (vocabulary_id ASC);
    CLUSTER vocabulary  USING idx_vocabulary_vocabulary_id ;
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_domain_domain_id')
  THEN
    CREATE INDEX idx_domain_domain_id  ON domain  (domain_id ASC);
    CLUSTER domain  USING idx_domain_domain_id ;
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_concept_class_class_id')
  THEN
    CREATE INDEX idx_concept_class_class_id  ON concept_class  (concept_class_id ASC);
    CLUSTER concept_class  USING idx_concept_class_class_id ;
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_concept_relationship_id_1')
  THEN
    CREATE INDEX idx_concept_relationship_id_1  ON concept_relationship  (concept_id_1 ASC);
    CLUSTER concept_relationship  USING idx_concept_relationship_id_1 ;
    CREATE INDEX idx_concept_relationship_id_2 ON concept_relationship (concept_id_2 ASC);
    CREATE INDEX idx_concept_relationship_id_3 ON concept_relationship (relationship_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_relationship_rel_id')
  THEN
    CREATE INDEX idx_relationship_rel_id  ON relationship  (relationship_id ASC);
    CLUSTER relationship  USING idx_relationship_rel_id ;
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_concept_synonym_id')
  THEN
    CREATE INDEX idx_concept_synonym_id  ON concept_synonym  (concept_id ASC);
    CLUSTER concept_synonym  USING idx_concept_synonym_id ;
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_concept_ancestor_id_1')
  THEN
    CREATE INDEX idx_concept_ancestor_id_1  ON concept_ancestor  (ancestor_concept_id ASC);
    CLUSTER concept_ancestor  USING idx_concept_ancestor_id_1 ;
    CREATE INDEX idx_concept_ancestor_id_2 ON concept_ancestor (descendant_concept_id ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_source_to_concept_map_3')
  THEN
    CREATE INDEX idx_source_to_concept_map_3  ON source_to_concept_map  (target_concept_id ASC);
    CLUSTER source_to_concept_map  USING idx_source_to_concept_map_3 ;
    CREATE INDEX idx_source_to_concept_map_1 ON source_to_concept_map (source_vocabulary_id ASC);
    CREATE INDEX idx_source_to_concept_map_2 ON source_to_concept_map (target_vocabulary_id ASC);
    CREATE INDEX idx_source_to_concept_map_c ON source_to_concept_map (source_code ASC);
  END IF;

  IF NOT EXISTS(SELECT relname FROM PG_CLASS WHERE relname = 'idx_drug_strength_id_1')
  THEN
    CREATE INDEX idx_drug_strength_id_1  ON drug_strength  (drug_concept_id ASC);
    CLUSTER drug_strength  USING idx_drug_strength_id_1 ;
    CREATE INDEX idx_drug_strength_id_2 ON drug_strength (ingredient_concept_id ASC);
  END IF;

  --Additional v6.0 indices

  --CREATE CLUSTERED INDEX IF NOT EXISTS idx_survey_person_id_1 ON survey_conduct (person_id ASC);


  --CREATE CLUSTERED INDEX IF NOT EXISTS idx_episode_person_id_1 ON episode (person_id ASC);
  --CREATE INDEX IF NOT EXISTS idx_episode_concept_id_1 ON episode (episode_concept_id ASC);

  --CREATE CLUSTERED INDEX IF NOT EXISTS idx_episode_event_id_1 ON episode_event (episode_id ASC);
  --CREATE INDEX IF NOT EXISTS idx_ee_field_concept_id_1 ON episode_event (event_field_concept_id ASC);

END$$;
