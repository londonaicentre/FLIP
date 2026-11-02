JOB_TYPE=standard
# Training data (DATASET=arkplus, the default): the Ark+ training splits, one folder per site.
# `make -C fl-tutorials download-arkplus-finetuning-data`
SITE1_IMAGES_DIR=../../../data/arkplus/site1/accession-resources
SITE1_DATAFRAME=../../../data/arkplus/site1/sample_get_dataframe_response.csv
SITE2_IMAGES_DIR=../../../data/arkplus/site2/accession-resources
SITE2_DATAFRAME=../../../data/arkplus/site2/sample_get_dataframe_response.csv
# Code-testing data (DATASET=mini): 300 X-rays, halved between the sites. Too small to train on.
# `make -C fl-tutorials download-xray-data`
DEV_IMAGES_DIR=../../../data/xrays_mini_300/accession-resources/
DEV_DATAFRAME=../../../data/xrays_mini_300/dataframe.csv
# Any value works for local sim: LOCAL_DEV ignores project_id (data comes from
# the paths above) and `make sim` runs the job directly, handing the placeholder
# straight to the trainer. Export paths substitute it into the recipe's "--project_id" task arg,
# so keep it non-empty there. In production the FLIP-API injects the real project UUID.
FLIP_PROJECT_ID=dev
FLIP_QUERY=

# Optional local-run knobs (Makefile defaults: NUM_ROUNDS=1, N_CLIENTS=2; CLI overrides win, e.g. `make run NUM_ROUNDS=2`)
# NUM_ROUNDS=2
# N_CLIENTS=2
