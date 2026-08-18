JOB_TYPE=standard
DEV_IMAGES_DIR=../../data/spleen/images
DEV_DATAFRAME=../../data/spleen/dataframe.csv
# Any value works for local sim: LOCAL_DEV ignores project_id (data comes from
# DEV_DATAFRAME/DEV_IMAGES_DIR) and `make sim` runs the job directly, handing the placeholder
# straight to the trainer. Export paths substitute it into the recipe's "--project_id" task arg,
# so keep it non-empty there. In production the FLIP-API injects the real project UUID.
FLIP_PROJECT_ID=dev
FLIP_QUERY=

# Optional local-run knobs (Makefile defaults: NUM_ROUNDS=2, N_CLIENTS=2; CLI overrides win, e.g. `make run NUM_ROUNDS=10`)
# NUM_ROUNDS=10
# N_CLIENTS=2
