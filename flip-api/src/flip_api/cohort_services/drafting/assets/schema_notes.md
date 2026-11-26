Notes: OMOP holds no names, addresses or NHS numbers. image_occurrence is one row per study/series and
accession_id has no unique constraint. *_source_value columns are varchar (SNOMED codes for conditions in
Synthea data); *_concept_id columns are integers. condition_era/drug_era dates are TIMESTAMP.
concept and concept_ancestor are often only partly loaded: many trusts have NO core vocabulary.
image_feature links to another table through image_feature_event_id AND image_feature_event_field_concept_id:
an event id alone is not a safe join.
