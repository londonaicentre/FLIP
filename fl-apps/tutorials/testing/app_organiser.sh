#!/bin/bash
set -e

# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Arguments:
JOB_TYPE=$1
APP_PATH=$2
DEV_IMAGES_DIR=$3
DEV_DATAFRAME=$4

# Check images folder and dataframe file exist
if [ ! -d "$DEV_IMAGES_DIR" ]; then
  echo "Error: DEV_IMAGES_DIR directory '$DEV_IMAGES_DIR' does not exist."
  exit 1
fi

if [ ! -f "$DEV_DATAFRAME" ]; then
  echo "Error: DEV_DATAFRAME file '$DEV_DATAFRAME' does not exist."
  exit 1
fi

# Copy base application files (e.g. config folder, other custom files, etc.)
mkdir -p ./tmp
cp -r ../../src/$JOB_TYPE/app ./tmp/

# Copy app files (e.g. trainer.py, validator.py, etc.)
echo "Copying your app files into the testing app..."
mkdir -p ./tmp/app/custom
for f in $APP_PATH/app_files/*; do
  if [ -f "$f" ]; then
    cp "$f" ./tmp/app/custom/
  fi
done

# After copying files, update global_rounds in config_fed_server.json if GLOBAL_ROUNDS exists in custom config
CUSTOM_CONFIG="./tmp/app/custom/config.json"
SERVER_CONFIG="./tmp/app/config/config_fed_server.json"

if [ -f "$CUSTOM_CONFIG" ] && [ -f "$SERVER_CONFIG" ]; then
  echo "Checking for GLOBAL_ROUNDS in $CUSTOM_CONFIG..."
  # Extract GLOBAL_ROUNDS as a number, or null if not present or not a number
  GLOBAL_ROUNDS=$(jq '.GLOBAL_ROUNDS // empty' "$CUSTOM_CONFIG")
  echo "Extracted GLOBAL_ROUNDS: '$GLOBAL_ROUNDS'"
  # Remove whitespace
  GLOBAL_ROUNDS=$(echo "$GLOBAL_ROUNDS" | xargs)
  # Check if it's a valid number (integer)
  if [[ "$GLOBAL_ROUNDS" =~ ^[0-9]+$ ]]; then
    jq ".global_rounds = $GLOBAL_ROUNDS" "$SERVER_CONFIG" > "$SERVER_CONFIG.tmp" && mv "$SERVER_CONFIG.tmp" "$SERVER_CONFIG"
    echo "Set global_rounds in config_fed_server.json to $GLOBAL_ROUNDS"
  else
    echo "GLOBAL_ROUNDS not set or not a valid integer in $CUSTOM_CONFIG. No changes made to config_fed_server.json"
  fi
fi


