.. _flip-omop:

#############
OMOP Database
#############

FLIP requires data from all participating Trusts to be stored in a standardised format so that a single query can be federated between the sites to generate a cohort of data. Aggregate statistics are returned as results on the platform and, if and when the project is approved, the underlying data at each site will be retrieved and provided for local model training.

The `Observational Health Data Sciences and Informatics (OHDSI) <https://www.ohdsi.org/>`_ `Observational Medical Outcomes Partnership (OMOP) Common Data Model (CDM) <https://www.ohdsi.org/data-standardization/>`_ has been selected as the format for standardised data storage, so the database in each Trust's local instance FLIP is commonly referred to as the *OMOP Database* or *OMOP*.

To prepare the standardised data to be ingested into each Trust's OMOP Database, resources are provided during the onboarding stage.

Schema
======

The database implements OMOP CDM 5.4 in the ``omop`` schema, extended with the
`MI-CDM medical imaging tables <https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11031512/>`_:
``image_occurrence`` (one row per imaging study/series, carrying the ``accession_id``
that links a cohort row to its DICOM data in the Trust PACS) and ``image_feature``
(findings derived from those images). Cohort queries join these against the standard
clinical tables (``person``, ``visit_occurrence``, ``procedure_occurrence``, ...) —
see :ref:`the cohort query guide <create-cohort-query>`.

Access control
==============

Cohort queries issued by the Data Access API connect as the ``data_analyst_reader``
role: SELECT-only on the ``omop`` schema, with write/DDL rights explicitly revoked
and a statement timeout applied. These grants (``trust/omop-db/files/create_readonly_users.sql``)
are the database half of the Data Access API's SQL validation defence-in-depth.

Mocked instance for development
===============================

Development and staging Trust stacks run a mocked OMOP database:
``ghcr.io/londonaicentre/omop-db``, a PostgreSQL image whose build source lives
in-repo at ``trust/omop-db/`` (schema init chain, read-only roles, vocabulary
load). Its synthetic cohort rows are maintained as a single canonical CSV
dataset on the public Hugging Face dataset
`aicentreflip/trust-data <https://huggingface.co/datasets/aicentreflip/trust-data>`_,
deterministically split across however many mock Trusts are stood up; dev
stacks download ready-populated database volumes from the same dataset. The
OMOP vocabularies (SNOMED CT, LOINC, ...) are licensed material and are kept
out of every published artifact — the image and data volumes ship vocab-free,
and each environment streams the vocabulary bundle it is licensed to use into
its running database as a one-time seeding step — see
``trust/omop-db/README.md`` for the build, populate and seeding workflow.
