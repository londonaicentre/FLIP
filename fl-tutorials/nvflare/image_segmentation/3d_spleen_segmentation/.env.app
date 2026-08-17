JOB_TYPE=standard
DEV_IMAGES_DIR=../../data/spleen/images
DEV_DATAFRAME=../../data/spleen/dataframe.csv
# Any non-empty value works locally: LOCAL_DEV ignores project_id (data comes from
# DEV_DATAFRAME/DEV_IMAGES_DIR), but the recipe's "--project_id {project_id}" task arg
# needs a token to substitute — an empty value whitespace-splits away and crashes the
# trainer's argparse. In production the FLIP-API injects the real project UUID.
FLIP_PROJECT_ID=dev
FLIP_QUERY=

# Optional local-run knobs (Makefile defaults: NUM_ROUNDS=2, N_CLIENTS=2; CLI overrides win, e.g. `make run NUM_ROUNDS=10`)
# NUM_ROUNDS=10
# N_CLIENTS=2
