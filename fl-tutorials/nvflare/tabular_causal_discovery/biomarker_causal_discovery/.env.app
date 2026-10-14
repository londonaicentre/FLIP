JOB_TYPE=standard
SITE1_DATAFRAME=../../../data/ad_biomarkers/site1/dataframe.csv
SITE2_DATAFRAME=../../../data/ad_biomarkers/site2/dataframe.csv
DEV_DATAFRAME=../../../data/ad_biomarkers/dataframe.csv
FLIP_PROJECT_ID=
FLIP_QUERY=

# Optional local-run knobs (Makefile defaults: NUM_ROUNDS=2, N_CLIENTS=2; CLI overrides win, e.g. `make run NUM_ROUNDS=6`).
# pooled_statistics needs 2 rounds; layer_voting needs (max conditioning-set size + 2).
# NUM_ROUNDS=2
# N_CLIENTS=2
