JOB_TYPE=standard_client_api
# Fallback dataset used for BOTH sites when the per-site vars below are unset —
# points at the small smoke-test set (make -C fl-tutorials download-xray-data).
DEV_IMAGES_DIR=../../data/xrays_mini_300/accession-resources/
DEV_DATAFRAME=../../data/xrays_mini_300/dataframe.csv
# Paper-replication baseline: give each simulated site its own cohort
# (site-1 = UK, site-2 = Thailand). Uncomment and point at the synthetic data.
#SITE1_IMAGES_DIR=/path/to/uk/accession-resources
#SITE1_DATAFRAME=/path/to/uk/dataframe.csv
#SITE2_IMAGES_DIR=/path/to/thai/accession-resources
#SITE2_DATAFRAME=/path/to/thai/dataframe.csv
FLIP_PROJECT_ID=
FLIP_QUERY=
