#!/bin/bash

# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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
# $1 branch of the base app repo
# $2 URL of the base app repo
# $3 job type
# $4 path to app to test

echo "Cloning base application repository..."
mkdir -p ./tmp/base_application_files
git clone --branch $1 $2 ./tmp/base_application_files
cp -r ./tmp/base_application_files/src/$3/app ./tmp/
rm -rf ./tmp/base_application_files

# Copying base application files (e.g. trainer.py, validator.py, etc.)
echo "Copying your application into the testing app..."
for f in $4/app_files/*; do
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


